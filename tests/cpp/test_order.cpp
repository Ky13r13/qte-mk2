#include "qte/orders/order.hpp"

#include <array>
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string_view>
#include <utility>
#include <vector>

namespace {

using namespace std::chrono_literals;
using qte::core::EventSequence;
using qte::core::OrderId;
using qte::core::PriceGrid;
using qte::core::ShareAmount;
using qte::core::Currency;
using qte::market_data::InstrumentSpec;
using qte::market_data::Symbol;
using qte::market_data::Timestamp;
using qte::orders::CancelResult;
using qte::orders::InvalidOrderRequest;
using qte::orders::InvalidOrderTransition;
using qte::orders::OrderCancellationReason;
using qte::orders::OrderRecord;
using qte::orders::OrderRejectionReason;
using qte::orders::OrderRequest;
using qte::orders::OrderSide;
using qte::orders::OrderStatus;
using qte::orders::OrderType;
using qte::orders::OrderValidationCode;
using qte::orders::OrderValidationError;
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

[[nodiscard]] OrderRequest request(
    const OrderType type = OrderType::market,
    const bool has_limit = false,
    const bool has_stop = false) {
    const auto grid = PriceGrid::from_tick_size(0.01);
    return OrderRequest{
        .symbol = Symbol{"SPY"},
        .side = OrderSide::buy,
        .quantity = ShareAmount::from_count(10),
        .type = type,
        .limit_price = has_limit
                           ? std::optional{grid.canonicalize(100.00)}
                           : std::nullopt,
        .stop_price = has_stop
                          ? std::optional{grid.canonicalize(101.00)}
                          : std::nullopt,
        .time_in_force = TimeInForce::good_til_canceled,
    };
}

[[nodiscard]] InstrumentSpec instrument(
    const double tick_size = 0.01,
    const char* const symbol = "SPY") {
    return InstrumentSpec{
        Symbol{symbol},
        Currency::usd(),
        PriceGrid::from_tick_size(tick_size),
    };
}

[[nodiscard]] OrderRecord new_record(
    OrderRequest order_request = request(),
    const std::uint64_t id = 1,
    const std::uint64_t submitted_sequence = 10,
    const std::uint64_t eligible_after_sequence = 10) {
    return OrderRecord{
        OrderId{id},
        std::move(order_request),
        Timestamp{100ns},
        EventSequence{submitted_sequence},
        EventSequence{eligible_after_sequence},
    };
}

[[nodiscard]] OrderRecord open_record(
    OrderRequest order_request = request(),
    const std::uint64_t id = 1) {
    auto order = new_record(std::move(order_request), id);
    order.open(instrument());
    return order;
}

[[nodiscard]] bool has_code(
    const std::vector<OrderValidationError>& errors,
    const OrderValidationCode code) {
    for (const auto& error : errors) {
        if (error.code == code) {
            return true;
        }
    }
    return false;
}

void check_quantity_invariant(const OrderRecord& order, const int line) {
    const auto filled = order.filled_quantity().value();
    const auto remaining = order.remaining_quantity().value();
    check(filled >= 0, "filled quantity is non-negative", line);
    check(remaining >= 0, "remaining quantity is non-negative", line);
    check(
        filled + remaining == order.request().quantity.value(),
        "filled + remaining == requested",
        line);
}

#define CHECK_QUANTITY_INVARIANT(order) check_quantity_invariant((order), __LINE__)

void request_price_matrix_is_explicit() {
    struct Case final {
        OrderType type;
        bool limit;
        bool stop;
        bool valid;
    };
    constexpr std::array cases{
        Case{OrderType::market, false, false, true},
        Case{OrderType::market, true, false, false},
        Case{OrderType::market, false, true, false},
        Case{OrderType::market, true, true, false},
        Case{OrderType::limit, false, false, false},
        Case{OrderType::limit, true, false, true},
        Case{OrderType::limit, false, true, false},
        Case{OrderType::limit, true, true, false},
        Case{OrderType::stop, false, false, false},
        Case{OrderType::stop, true, false, false},
        Case{OrderType::stop, false, true, true},
        Case{OrderType::stop, true, true, false},
        Case{OrderType::stop_limit, false, false, false},
        Case{OrderType::stop_limit, true, false, false},
        Case{OrderType::stop_limit, false, true, false},
        Case{OrderType::stop_limit, true, true, true},
    };

    for (const auto& test_case : cases) {
        CHECK(qte::orders::validate(
                  request(test_case.type, test_case.limit, test_case.stop)).empty() ==
              test_case.valid);
    }
}

void request_validation_reports_specific_errors() {
    CHECK(has_code(
        qte::orders::validate(request(OrderType::limit)),
        OrderValidationCode::missing_limit_price));
    CHECK(has_code(
        qte::orders::validate(request(OrderType::stop)),
        OrderValidationCode::missing_stop_price));
    CHECK(has_code(
        qte::orders::validate(request(OrderType::market, true, true)),
        OrderValidationCode::unexpected_limit_price));
    CHECK(has_code(
        qte::orders::validate(request(OrderType::market, true, true)),
        OrderValidationCode::unexpected_stop_price));

    auto unusual_stop_limit = request(OrderType::stop_limit, true, true);
    const auto grid = PriceGrid::from_tick_size(0.01);
    unusual_stop_limit.limit_price = grid.canonicalize(90.00);
    unusual_stop_limit.stop_price = grid.canonicalize(100.00);
    CHECK(qte::orders::validate(unusual_stop_limit).empty());
}

void unsupported_enum_values_are_rejected() {
    auto invalid_side = request();
    invalid_side.side = static_cast<OrderSide>(99);
    CHECK(has_code(
        qte::orders::validate(invalid_side),
        OrderValidationCode::unsupported_side));

    auto invalid_type = request();
    invalid_type.type = static_cast<OrderType>(99);
    CHECK(has_code(
        qte::orders::validate(invalid_type),
        OrderValidationCode::unsupported_type));

    auto invalid_tif = request();
    invalid_tif.time_in_force = static_cast<TimeInForce>(99);
    CHECK(has_code(
        qte::orders::validate(invalid_tif),
        OrderValidationCode::unsupported_time_in_force));
}

void request_is_validated_against_its_instrument() {
    const auto spy = instrument(0.05);
    auto symbol_mismatch = request();
    symbol_mismatch.symbol = Symbol{"QQQ"};
    CHECK(has_code(
        qte::orders::validate(symbol_mismatch, spy),
        OrderValidationCode::instrument_symbol_mismatch));

    auto off_grid_limit = request(OrderType::limit, true, false);
    off_grid_limit.limit_price = PriceGrid::from_tick_size(0.03).canonicalize(100.02);
    CHECK(has_code(
        qte::orders::validate(off_grid_limit, spy),
        OrderValidationCode::limit_price_not_on_instrument_grid));

    auto off_grid_stop = request(OrderType::stop, false, true);
    off_grid_stop.stop_price = PriceGrid::from_tick_size(0.03).canonicalize(100.02);
    CHECK(has_code(
        qte::orders::validate(off_grid_stop, spy),
        OrderValidationCode::stop_price_not_on_instrument_grid));

    auto off_grid_record = new_record(std::move(off_grid_limit));
    CHECK_THROWS_AS(off_grid_record.open(spy), InvalidOrderRequest);
    CHECK(off_grid_record.status() == OrderStatus::new_order);
    CHECK_QUANTITY_INVARIANT(off_grid_record);
}

void new_record_preserves_identity_and_quantities() {
    const auto order = new_record(request(OrderType::limit, true, false), 7, 20, 21);

    CHECK(order.id() == OrderId{7});
    CHECK(order.request().symbol == Symbol{"SPY"});
    CHECK(order.submitted_at() == Timestamp{100ns});
    CHECK(order.submission_sequence() == EventSequence{20});
    CHECK(order.eligible_after_sequence() == EventSequence{21});
    CHECK(order.status() == OrderStatus::new_order);
    CHECK(order.filled_quantity().value() == 0);
    CHECK(order.remaining_quantity().value() == 10);
    CHECK(!order.is_terminal());
    CHECK_QUANTITY_INVARIANT(order);
    CHECK_THROWS_AS(new_record(request(), 1, 11, 10), std::invalid_argument);
}

void valid_and_invalid_opening_paths_do_not_hide_rejections() {
    auto valid = new_record();
    valid.open(instrument());
    CHECK(valid.status() == OrderStatus::open);
    CHECK_QUANTITY_INVARIANT(valid);
    CHECK_THROWS_AS(valid.open(instrument()), InvalidOrderTransition);
    CHECK_THROWS_AS(valid.reject(OrderRejectionReason::risk), InvalidOrderTransition);

    auto invalid = new_record(request(OrderType::limit));
    CHECK_THROWS_AS(invalid.open(instrument()), InvalidOrderRequest);
    CHECK(invalid.status() == OrderStatus::new_order);
    invalid.reject(OrderRejectionReason::invalid_request);
    CHECK(invalid.status() == OrderStatus::rejected);
    CHECK(invalid.rejection_reason() == OrderRejectionReason::invalid_request);
    CHECK(invalid.id() == OrderId{1});
    CHECK_QUANTITY_INVARIANT(invalid);

    auto risk_rejected = new_record(request(), 2);
    risk_rejected.reject(OrderRejectionReason::risk);
    CHECK(risk_rejected.status() == OrderStatus::rejected);
    CHECK(risk_rejected.rejection_reason() == OrderRejectionReason::risk);
    CHECK(risk_rejected.is_terminal());
    CHECK_QUANTITY_INVARIANT(risk_rejected);
}

void fill_quantity_drives_partial_and_complete_states() {
    auto order = open_record();
    order.record_fill_quantity(ShareAmount::from_count(4));
    CHECK(order.status() == OrderStatus::partially_filled);
    CHECK(order.filled_quantity().value() == 4);
    CHECK(order.remaining_quantity().value() == 6);
    CHECK_QUANTITY_INVARIANT(order);
    CHECK_THROWS_AS(order.open(instrument()), InvalidOrderTransition);
    CHECK_THROWS_AS(order.reject(OrderRejectionReason::risk), InvalidOrderTransition);

    order.record_fill_quantity(ShareAmount::from_count(6));
    CHECK(order.status() == OrderStatus::filled);
    CHECK(order.filled_quantity().value() == 10);
    CHECK(order.remaining_quantity().value() == 0);
    CHECK(order.is_terminal());
    CHECK_QUANTITY_INVARIANT(order);
    CHECK_THROWS_AS(
        order.record_fill_quantity(ShareAmount::from_count(1)),
        InvalidOrderTransition);
}

void overfill_and_invalid_state_fills_leave_state_unchanged() {
    auto order = open_record();
    CHECK_THROWS_AS(
        order.record_fill_quantity(ShareAmount::from_count(11)),
        std::invalid_argument);
    CHECK(order.status() == OrderStatus::open);
    CHECK(order.filled_quantity().value() == 0);
    CHECK(order.remaining_quantity().value() == 10);
    CHECK_QUANTITY_INVARIANT(order);

    auto new_order = new_record();
    CHECK_THROWS_AS(
        new_order.record_fill_quantity(ShareAmount::from_count(1)),
        InvalidOrderTransition);
    CHECK(new_order.status() == OrderStatus::new_order);
    CHECK_QUANTITY_INVARIANT(new_order);
}

void cancellation_is_idempotent_and_preserves_partial_quantity() {
    auto order = open_record();
    order.record_fill_quantity(ShareAmount::from_count(3));

    CHECK(order.cancel(OrderCancellationReason::user_requested) == CancelResult::canceled);
    CHECK(order.status() == OrderStatus::canceled);
    CHECK(order.filled_quantity().value() == 3);
    CHECK(order.remaining_quantity().value() == 7);
    CHECK(order.cancellation_reason() == OrderCancellationReason::user_requested);
    CHECK_QUANTITY_INVARIANT(order);
    CHECK(order.cancel(OrderCancellationReason::end_of_data) ==
          CancelResult::already_terminal);
    CHECK(order.cancellation_reason() == OrderCancellationReason::user_requested);

    auto new_order = new_record();
    CHECK_THROWS_AS(
        new_order.cancel(OrderCancellationReason::user_requested),
        InvalidOrderTransition);
}

void terminal_orders_reject_mutation_but_duplicate_cancel_is_a_noop() {
    auto filled = open_record();
    filled.record_fill_quantity(ShareAmount::from_count(10));
    CHECK(filled.cancel(OrderCancellationReason::end_of_data) ==
          CancelResult::already_terminal);
    CHECK_THROWS_AS(filled.open(instrument()), InvalidOrderTransition);
    CHECK_THROWS_AS(filled.reject(OrderRejectionReason::risk), InvalidOrderTransition);
    CHECK_THROWS_AS(filled.mark_stop_triggered(), InvalidOrderTransition);
    CHECK_QUANTITY_INVARIANT(filled);

    auto canceled = open_record(request(), 2);
    static_cast<void>(canceled.cancel(OrderCancellationReason::execution_risk));
    CHECK(canceled.cancel(OrderCancellationReason::user_requested) ==
          CancelResult::already_terminal);
    CHECK_THROWS_AS(
        canceled.record_fill_quantity(ShareAmount::from_count(1)),
        InvalidOrderTransition);
    CHECK_THROWS_AS(canceled.open(instrument()), InvalidOrderTransition);
    CHECK_THROWS_AS(canceled.reject(OrderRejectionReason::risk), InvalidOrderTransition);
    CHECK_THROWS_AS(canceled.mark_stop_triggered(), InvalidOrderTransition);
    CHECK_QUANTITY_INVARIANT(canceled);

    auto rejected = new_record(request(), 3);
    rejected.reject(OrderRejectionReason::risk);
    CHECK(rejected.cancel(OrderCancellationReason::user_requested) ==
          CancelResult::already_terminal);
    CHECK_THROWS_AS(
        rejected.record_fill_quantity(ShareAmount::from_count(1)),
        InvalidOrderTransition);
    CHECK_THROWS_AS(rejected.open(instrument()), InvalidOrderTransition);
    CHECK_THROWS_AS(rejected.reject(OrderRejectionReason::risk), InvalidOrderTransition);
    CHECK_THROWS_AS(rejected.mark_stop_triggered(), InvalidOrderTransition);
    CHECK_QUANTITY_INVARIANT(rejected);
}

void stop_trigger_is_persistent_and_type_checked() {
    auto stop = open_record(request(OrderType::stop, false, true));
    CHECK(!stop.stop_triggered());
    CHECK(stop.mark_stop_triggered());
    CHECK(stop.stop_triggered());
    CHECK_QUANTITY_INVARIANT(stop);

    auto stop_limit = open_record(request(OrderType::stop_limit, true, true), 2);
    CHECK(stop_limit.mark_stop_triggered());
    CHECK(stop_limit.stop_triggered());
    CHECK(!stop.mark_stop_triggered());
    stop.record_fill_quantity(ShareAmount::from_count(3));
    CHECK(stop.stop_triggered());

    auto market = open_record();
    CHECK_THROWS_AS(market.mark_stop_triggered(), InvalidOrderTransition);
    CHECK(!market.stop_triggered());

    static_cast<void>(stop.cancel(OrderCancellationReason::end_of_data));
    CHECK_THROWS_AS(stop.mark_stop_triggered(), InvalidOrderTransition);
}

void invalid_reason_values_do_not_mutate_records() {
    auto new_order = new_record();
    CHECK_THROWS_AS(
        new_order.reject(static_cast<OrderRejectionReason>(99)),
        std::invalid_argument);
    CHECK(new_order.status() == OrderStatus::new_order);

    auto open_order = open_record();
    CHECK_THROWS_AS(
        open_order.cancel(static_cast<OrderCancellationReason>(99)),
        std::invalid_argument);
    CHECK(open_order.status() == OrderStatus::open);
}

void status_names_are_stable() {
    CHECK(qte::orders::to_string(OrderStatus::new_order) == "NEW");
    CHECK(qte::orders::to_string(OrderStatus::open) == "OPEN");
    CHECK(qte::orders::to_string(OrderStatus::partially_filled) == "PARTIALLY_FILLED");
    CHECK(qte::orders::to_string(OrderStatus::filled) == "FILLED");
    CHECK(qte::orders::to_string(OrderStatus::canceled) == "CANCELED");
    CHECK(qte::orders::to_string(OrderStatus::rejected) == "REJECTED");
}

}  // namespace

int main() {
    request_price_matrix_is_explicit();
    request_validation_reports_specific_errors();
    unsupported_enum_values_are_rejected();
    request_is_validated_against_its_instrument();
    new_record_preserves_identity_and_quantities();
    valid_and_invalid_opening_paths_do_not_hide_rejections();
    fill_quantity_drives_partial_and_complete_states();
    overfill_and_invalid_state_fills_leave_state_unchanged();
    cancellation_is_idempotent_and_preserves_partial_quantity();
    terminal_orders_reject_mutation_but_duplicate_cancel_is_a_noop();
    stop_trigger_is_persistent_and_type_checked();
    invalid_reason_values_do_not_mutate_records();
    status_names_are_stable();

    if (failures != 0) {
        std::cerr << failures << " test assertion(s) failed\n";
        return EXIT_FAILURE;
    }

    std::cout << "all order tests passed\n";
    return EXIT_SUCCESS;
}
