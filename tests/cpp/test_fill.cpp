#include "qte/orders/fill.hpp"

#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string_view>
#include <type_traits>
#include <utility>
#include <vector>

namespace {

using namespace std::chrono_literals;
using qte::core::Currency;
using qte::core::EventSequence;
using qte::core::FillId;
using qte::core::OrderId;
using qte::core::PriceGrid;
using qte::core::ShareAmount;
using qte::market_data::InstrumentSpec;
using qte::market_data::Symbol;
using qte::market_data::Timestamp;
using qte::orders::DuplicateFill;
using qte::orders::Fill;
using qte::orders::FillJournal;
using qte::orders::FillValidationCode;
using qte::orders::FillValidationError;
using qte::orders::InvalidFill;
using qte::orders::OrderCancellationReason;
using qte::orders::OrderRecord;
using qte::orders::OrderRequest;
using qte::orders::OrderSide;
using qte::orders::OrderStatus;
using qte::orders::OrderType;
using qte::orders::OutOfSequenceFill;
using qte::orders::TimeInForce;

int failures = 0;

void check(const bool condition, const std::string_view expression, const int line) {
    if (!condition) {
        std::cerr << "line " << line << ": check failed: " << expression << '\n';
        ++failures;
    }
}

template <typename Exception, typename Function>
void check_throws(
    Function&& function,
    const std::string_view expression,
    const int line) {
    try {
        function();
        std::cerr << "line " << line << ": expected exception from: " << expression << '\n';
        ++failures;
    } catch (const Exception&) {
    } catch (...) {
        std::cerr << "line " << line << ": wrong exception from: " << expression << '\n';
        ++failures;
    }
}

#define CHECK(expression) check((expression), #expression, __LINE__)
#define CHECK_THROWS_AS(expression, exception_type) \
    check_throws<exception_type>([&] { static_cast<void>(expression); }, #expression, __LINE__)

[[nodiscard]] InstrumentSpec instrument(
    const char* const symbol = "SPY",
    const double tick_size = 0.01) {
    return InstrumentSpec{
        Symbol{symbol},
        Currency::usd(),
        PriceGrid::from_tick_size(tick_size),
    };
}

[[nodiscard]] OrderRecord open_order(
    const std::uint64_t id = 1,
    const char* const symbol = "SPY",
    const OrderSide side = OrderSide::buy,
    const std::int64_t quantity = 10) {
    OrderRecord order{
        OrderId{id},
        OrderRequest{
            .symbol = Symbol{symbol},
            .side = side,
            .quantity = ShareAmount::from_count(quantity),
            .type = OrderType::market,
            .limit_price = std::nullopt,
            .stop_price = std::nullopt,
            .time_in_force = TimeInForce::good_til_canceled,
        },
        Timestamp{100ns},
        EventSequence{10},
        EventSequence{10},
    };
    order.open(instrument(symbol));
    return order;
}

[[nodiscard]] Fill fill(
    const std::uint64_t fill_id = 1,
    const std::uint64_t order_id = 1,
    const char* const symbol = "SPY",
    const OrderSide side = OrderSide::buy,
    const std::int64_t quantity = 4,
    const std::uint64_t sequence = 11,
    const Timestamp effective_at = Timestamp{101ns},
    const double executed_price = 100.25,
    const double reference_open = 100.20,
    const double commission = 0.50,
    const double price_tick = 0.01) {
    return Fill::create(
        FillId{fill_id},
        OrderId{order_id},
        Symbol{symbol},
        side,
        ShareAmount::from_count(quantity),
        effective_at,
        EventSequence{sequence},
        reference_open,
        PriceGrid::from_tick_size(price_tick).canonicalize(executed_price),
        commission);
}

[[nodiscard]] bool has_code(
    const std::vector<FillValidationError>& errors,
    const FillValidationCode code) {
    for (const auto& error : errors) {
        if (error.code == code) {
            return true;
        }
    }
    return false;
}

void check_order_quantities(
    const OrderRecord& order,
    const std::int64_t filled,
    const std::int64_t remaining,
    const int line) {
    check(order.filled_quantity().value() == filled, "expected filled quantity", line);
    check(order.remaining_quantity().value() == remaining, "expected remaining quantity", line);
    check(
        order.filled_quantity().value() + order.remaining_quantity().value() ==
            order.request().quantity.value(),
        "filled plus remaining equals requested",
        line);
}

#define CHECK_ORDER_QUANTITIES(order, filled, remaining) \
    check_order_quantities((order), (filled), (remaining), __LINE__)

void fill_value_preserves_validated_execution_facts() {
    static_assert(std::is_copy_constructible_v<Fill>);
    static_assert(!std::is_copy_assignable_v<Fill>);
    static_assert(!std::is_move_assignable_v<Fill>);

    const auto actual = fill(7, 9, "QQQ", OrderSide::sell, 3, 44, Timestamp{500ns});

    CHECK(actual.id() == FillId{7});
    CHECK(actual.order_id() == OrderId{9});
    CHECK(actual.symbol() == Symbol{"QQQ"});
    CHECK(actual.side() == OrderSide::sell);
    CHECK(actual.quantity().value() == 3);
    CHECK(actual.effective_at() == Timestamp{500ns});
    CHECK(actual.effective_sequence() == EventSequence{44});
    CHECK(actual.reference_open() == 100.20);
    CHECK(actual.executed_price().value() == 100.25);
    CHECK(actual.gross_notional() == 300.75);
    CHECK(actual.commission() == 0.50);
}

void intrinsic_numeric_boundaries_are_rejected() {
    CHECK_THROWS_AS(fill(1, 1, "SPY", OrderSide::buy, 1, 11, Timestamp{101ns},
                         100.25, 0.0),
                    std::invalid_argument);
    CHECK_THROWS_AS(fill(1, 1, "SPY", OrderSide::buy, 1, 11, Timestamp{101ns},
                         100.25, -1.0),
                    std::invalid_argument);
    CHECK_THROWS_AS(fill(1, 1, "SPY", OrderSide::buy, 1, 11, Timestamp{101ns},
                         100.25, std::numeric_limits<double>::quiet_NaN()),
                    std::invalid_argument);
    CHECK_THROWS_AS(fill(1, 1, "SPY", OrderSide::buy, 1, 11, Timestamp{101ns},
                         100.25, std::numeric_limits<double>::infinity()),
                    std::invalid_argument);

    CHECK_THROWS_AS(fill(1, 1, "SPY", OrderSide::buy, 1, 11, Timestamp{101ns},
                         100.25, 100.20, -0.01),
                    std::invalid_argument);
    CHECK_THROWS_AS(fill(1, 1, "SPY", OrderSide::buy, 1, 11, Timestamp{101ns},
                         100.25, 100.20, std::numeric_limits<double>::quiet_NaN()),
                    std::invalid_argument);
    CHECK_THROWS_AS(fill(1, 1, "SPY", OrderSide::buy, 1, 11, Timestamp{101ns},
                         100.25, 100.20, std::numeric_limits<double>::infinity()),
                    std::invalid_argument);
    CHECK_THROWS_AS(fill(1, 1, "SPY", static_cast<OrderSide>(99)),
                    std::invalid_argument);

    const auto huge_grid = PriceGrid::from_tick_size(1.0e293);
    CHECK_THROWS_AS(
        Fill::create(
            FillId{1},
            OrderId{1},
            Symbol{"SPY"},
            OrderSide::buy,
            ShareAmount::from_count(std::numeric_limits<std::int64_t>::max()),
            Timestamp{101ns},
            EventSequence{11},
            1.0,
            huge_grid.canonicalize(1.0e293),
            0.0),
        std::out_of_range);
}

void valid_commits_are_append_only_and_drive_order_state() {
    auto order = open_order();
    FillJournal journal;

    journal.commit(order, fill(), instrument());
    CHECK(journal.size() == 1);
    CHECK(!journal.empty());
    CHECK(journal.contains(FillId{1}));
    CHECK(!journal.contains(FillId{2}));
    CHECK(order.status() == OrderStatus::partially_filled);
    CHECK_ORDER_QUANTITIES(order, 4, 6);

    journal.commit(order, fill(2, 1, "SPY", OrderSide::buy, 6, 12), instrument());
    CHECK(journal.size() == 2);
    CHECK(journal.contains(FillId{2}));
    CHECK(order.status() == OrderStatus::filled);
    CHECK_ORDER_QUANTITIES(order, 10, 0);

    const auto& committed = journal.fills();
    CHECK(committed.size() == 2);
    if (committed.size() == 2) {
        CHECK(committed[0].id() == FillId{1});
        CHECK(committed[1].id() == FillId{2});
    }
}

void metadata_and_instrument_mismatches_are_explicit() {
    const auto spy = instrument();
    const auto off_grid = fill(
        1, 1, "SPY", OrderSide::buy, 4, 11, Timestamp{101ns}, 100.02,
        100.20, 0.50, 0.03);
    struct Case final {
        Fill candidate;
        InstrumentSpec fill_instrument;
        FillValidationCode code;
    };
    const std::vector cases{
        Case{fill(1, 2), spy, FillValidationCode::order_id_mismatch},
        Case{fill(1, 1, "QQQ"), spy, FillValidationCode::symbol_mismatch},
        Case{fill(1, 1, "SPY", OrderSide::sell), spy, FillValidationCode::side_mismatch},
        Case{fill(), instrument("QQQ"), FillValidationCode::instrument_symbol_mismatch},
        Case{off_grid,
             instrument("SPY", 0.05),
             FillValidationCode::execution_price_not_on_instrument_grid},
    };

    for (const auto& test_case : cases) {
        auto order = open_order();
        FillJournal journal;
        CHECK(has_code(
            qte::orders::validate(test_case.candidate, order, test_case.fill_instrument),
            test_case.code));
        CHECK_THROWS_AS(
            journal.commit(order, test_case.candidate, test_case.fill_instrument),
            InvalidFill);
        CHECK(journal.empty());
        CHECK(order.status() == OrderStatus::open);
        CHECK_ORDER_QUANTITIES(order, 0, 10);
    }
}

void time_quantity_and_terminal_failures_do_not_mutate() {
    struct Case final {
        Fill candidate;
        FillValidationCode code;
    };
    const std::vector cases{
        Case{fill(1, 1, "SPY", OrderSide::buy, 4, 10),
             FillValidationCode::effective_sequence_not_after_eligibility},
        Case{fill(1, 1, "SPY", OrderSide::buy, 4, 9),
             FillValidationCode::effective_sequence_not_after_eligibility},
        Case{fill(1, 1, "SPY", OrderSide::buy, 4, 11, Timestamp{99ns}),
             FillValidationCode::effective_timestamp_before_submission},
        Case{fill(1, 1, "SPY", OrderSide::buy, 11),
             FillValidationCode::quantity_exceeds_remaining},
    };

    for (const auto& test_case : cases) {
        auto order = open_order();
        FillJournal journal;
        CHECK(has_code(
            qte::orders::validate(test_case.candidate, order, instrument()),
            test_case.code));
        CHECK_THROWS_AS(journal.commit(order, test_case.candidate, instrument()), InvalidFill);
        CHECK(journal.empty());
        CHECK(order.status() == OrderStatus::open);
        CHECK_ORDER_QUANTITIES(order, 0, 10);
    }

    auto canceled = open_order();
    static_cast<void>(canceled.cancel(OrderCancellationReason::user_requested));
    FillJournal canceled_journal;
    CHECK(has_code(
        qte::orders::validate(fill(), canceled, instrument()),
        FillValidationCode::order_not_fillable));
    CHECK_THROWS_AS(canceled_journal.commit(canceled, fill(), instrument()), InvalidFill);
    CHECK(canceled_journal.empty());
    CHECK(canceled.status() == OrderStatus::canceled);
    CHECK_ORDER_QUANTITIES(canceled, 0, 10);
}

void equal_timestamp_is_allowed_when_sequence_is_later() {
    auto order = open_order();
    FillJournal journal;
    journal.commit(
        order,
        fill(1, 1, "SPY", OrderSide::buy, 10, 11, Timestamp{100ns}),
        instrument());
    CHECK(order.status() == OrderStatus::filled);
    CHECK(journal.size() == 1);
}

void duplicate_and_out_of_sequence_ids_are_rejected_first() {
    auto first_order = open_order();
    FillJournal journal;
    const auto first = fill(1, 1, "SPY", OrderSide::buy, 4);
    journal.commit(first_order, first, instrument());

    CHECK_THROWS_AS(journal.commit(first_order, first, instrument()), DuplicateFill);
    CHECK(journal.size() == 1);
    CHECK(first_order.status() == OrderStatus::partially_filled);
    CHECK_ORDER_QUANTITIES(first_order, 4, 6);

    auto another_order = open_order(2);
    CHECK_THROWS_AS(
        journal.commit(
            another_order,
            fill(3, 2, "SPY", OrderSide::buy, 2),
            instrument()),
        OutOfSequenceFill);
    CHECK(journal.size() == 1);
    CHECK_ORDER_QUANTITIES(another_order, 0, 10);

    CHECK_THROWS_AS(
        journal.commit(
            another_order,
            fill(1, 2, "SPY", OrderSide::buy, 2),
            instrument()),
        DuplicateFill);
    CHECK(journal.size() == 1);
    CHECK_ORDER_QUANTITIES(another_order, 0, 10);

    journal.commit(
        another_order,
        fill(2, 2, "SPY", OrderSide::buy, 2),
        instrument());
    CHECK(journal.size() == 2);
    CHECK_ORDER_QUANTITIES(another_order, 2, 8);
}

}  // namespace

int main() {
    fill_value_preserves_validated_execution_facts();
    intrinsic_numeric_boundaries_are_rejected();
    valid_commits_are_append_only_and_drive_order_state();
    metadata_and_instrument_mismatches_are_explicit();
    time_quantity_and_terminal_failures_do_not_mutate();
    equal_timestamp_is_allowed_when_sequence_is_later();
    duplicate_and_out_of_sequence_ids_are_rejected_first();

    if (failures != 0) {
        std::cerr << failures << " test assertion(s) failed\n";
        return EXIT_FAILURE;
    }

    std::cout << "all fill tests passed\n";
    return EXIT_SUCCESS;
}
