#include "qte/portfolio/position.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <optional>
#include <stdexcept>
#include <string_view>

namespace {

using namespace std::chrono_literals;
using qte::core::EventSequence;
using qte::core::Currency;
using qte::core::FillId;
using qte::core::OrderId;
using qte::core::PriceGrid;
using qte::core::ShareAmount;
using qte::market_data::Symbol;
using qte::market_data::Timestamp;
using qte::market_data::InstrumentSpec;
using qte::market_data::OptionRight;
using qte::market_data::OptionSettlement;
using qte::orders::Fill;
using qte::orders::OrderSide;
using qte::portfolio::InvalidPositionInput;
using qte::portfolio::Position;
using qte::portfolio::PositionAccountingError;
using qte::portfolio::ValuationMark;

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

[[nodiscard]] bool near(const double left, const double right) {
    return std::abs(left - right) <=
        1.0e-9 + 1.0e-12 * std::max(std::abs(left), std::abs(right));
}

void check_optional_near(
    const std::optional<double>& actual,
    const double expected,
    const std::string_view expression,
    const int line) {
    check(actual.has_value(), expression, line);
    if (actual.has_value()) {
        check(near(*actual, expected), expression, line);
    }
}

#define CHECK_NEAR(actual, expected) check(near((actual), (expected)), #actual, __LINE__)
#define CHECK_OPTIONAL_NEAR(actual, expected) \
    check_optional_near((actual), (expected), #actual, __LINE__)

[[nodiscard]] Fill fill(
    const std::uint64_t id,
    const OrderSide side,
    const std::int64_t quantity,
    const double price,
    const double commission = 0.0,
    const char* const symbol = "SPY",
    const std::int64_t multiplier = 1) {
    const auto price_grid = PriceGrid::from_tick_size(price);
    return Fill::create(
        FillId{id},
        OrderId{id},
        Symbol{symbol},
        side,
        ShareAmount::from_count(quantity),
        Timestamp{std::chrono::nanoseconds{id}},
        EventSequence{id},
        price,
        price_grid.canonicalize(price),
        commission,
        multiplier);
}

[[nodiscard]] ValuationMark mark(
    const std::uint64_t sequence,
    const double price,
    const std::uint64_t timestamp = 100) {
    return ValuationMark::create(
        Timestamp{std::chrono::nanoseconds{timestamp}},
        EventSequence{sequence},
        price);
}

void empty_and_unmarked_states_are_explicit() {
    Position position{Symbol{"SPY"}};

    CHECK(position.symbol() == Symbol{"SPY"});
    CHECK(position.is_flat());
    CHECK(position.quantity().value() == 0);
    CHECK(position.average_entry_price() == 0.0);
    CHECK(position.realized_gross_pnl() == 0.0);
    CHECK(position.commissions_paid() == 0.0);
    CHECK(!position.valuation_mark().has_value());
    CHECK_OPTIONAL_NEAR(position.market_value(), 0.0);
    CHECK_OPTIONAL_NEAR(position.unrealized_pnl(), 0.0);
    CHECK_OPTIONAL_NEAR(position.net_pnl(), 0.0);

    position.apply_fill(fill(1, OrderSide::buy, 2, 10.0, 0.25));
    CHECK(!position.market_value().has_value());
    CHECK(!position.unrealized_pnl().has_value());
    CHECK(!position.net_pnl().has_value());
}

void derivative_positions_scale_pnl_and_separate_exposure() {
    const auto grid = PriceGrid::from_tick_size(0.25);
    const auto last = Timestamp{200ns};
    const auto expiry = Timestamp{300ns};
    const auto future = InstrumentSpec::future(
        Symbol{"NQ"}, Currency::usd(), grid, 20, last, expiry);
    Position hedge{future};
    hedge.apply_fill(fill(1, OrderSide::buy, 2, 100.0, 1.0, "NQ", 20));
    hedge.mark(mark(2, 110.0));
    CHECK_OPTIONAL_NEAR(hedge.market_value(), 400.0);
    CHECK_OPTIONAL_NEAR(hedge.unrealized_pnl(), 400.0);
    CHECK_OPTIONAL_NEAR(hedge.net_exposure(), 4400.0);
    CHECK_OPTIONAL_NEAR(hedge.gross_exposure(), 4400.0);
    hedge.apply_fill(fill(3, OrderSide::sell, 1, 115.0, 0.0, "NQ", 20));
    CHECK_NEAR(hedge.realized_gross_pnl(), 300.0);

    const auto option = InstrumentSpec::european_futures_option(
        Symbol{"NQC"}, Currency::usd(), grid, 20, last, expiry, Symbol{"NQ"},
        OptionRight::call, grid.canonicalize(100.0),
        OptionSettlement::futures_delivery);
    Position long_option{option};
    long_option.apply_fill(fill(1, OrderSide::buy, 2, 10.0, 0.0, "NQC", 20));
    long_option.mark(ValuationMark::option_value(
        Timestamp{100ns}, EventSequence{2}, 0.0));
    CHECK_OPTIONAL_NEAR(long_option.market_value(), 0.0);
    CHECK_OPTIONAL_NEAR(long_option.unrealized_pnl(), -400.0);
    CHECK_OPTIONAL_NEAR(long_option.net_exposure(), 0.0);
    CHECK_THROWS_AS(
        hedge.mark(ValuationMark::option_value(
            Timestamp{101ns}, EventSequence{3}, 0.0)),
        InvalidPositionInput);
}

void future_short_add_reduce_reverse_and_flat_are_multiplier_scaled() {
    const auto grid = PriceGrid::from_tick_size(0.25);
    const auto future = InstrumentSpec::future(
        Symbol{"NQ"}, Currency::usd(), grid, 20, Timestamp{200ns}, Timestamp{300ns});
    Position position{future};
    position.apply_fill(fill(1, OrderSide::sell, 2, 100.0, 0.0, "NQ", 20));
    position.apply_fill(fill(2, OrderSide::sell, 1, 90.0, 0.0, "NQ", 20));
    CHECK_NEAR(position.average_entry_price(), 290.0 / 3.0);
    position.apply_fill(fill(3, OrderSide::buy, 1, 80.0, 0.0, "NQ", 20));
    CHECK_NEAR(position.realized_gross_pnl(), 1000.0 / 3.0);
    const auto reversed = position.apply_fill(
        fill(4, OrderSide::buy, 3, 110.0, 0.0, "NQ", 20));
    CHECK(reversed.is_reversal());
    CHECK(position.quantity().value() == 1);
    CHECK_NEAR(position.realized_gross_pnl(), -200.0);
    position.apply_fill(fill(5, OrderSide::sell, 1, 120.0, 0.0, "NQ", 20));
    CHECK(position.is_flat());
    CHECK_NEAR(position.realized_gross_pnl(), 0.0);
}

void futures_variation_basis_is_separate_from_economic_entry() {
    const auto grid = PriceGrid::from_tick_size(0.25);
    const auto future = InstrumentSpec::future(
        Symbol{"NQ"}, Currency::usd(), grid, 20, Timestamp{200ns}, Timestamp{300ns});
    Position position{future};
    CHECK_OPTIONAL_NEAR(position.settlement_basis_price(), 0.0);
    CHECK_NEAR(position.settle_variation(100.0), 0.0);

    position.apply_fill(fill(1, OrderSide::buy, 2, 100.0, 0.0, "NQ", 20));
    CHECK_NEAR(position.average_entry_price(), 100.0);
    CHECK_OPTIONAL_NEAR(position.settlement_basis_price(), 100.0);
    CHECK_NEAR(position.settle_variation(110.0), 400.0);
    CHECK_NEAR(position.average_entry_price(), 100.0);
    CHECK_OPTIONAL_NEAR(position.settlement_basis_price(), 110.0);
    CHECK(!position.valuation_mark().has_value());
    position.mark(mark(2, 115.0));
    CHECK_OPTIONAL_NEAR(position.market_value(), 200.0);
    CHECK_OPTIONAL_NEAR(position.unrealized_pnl(), 600.0);

    position.apply_fill(fill(3, OrderSide::buy, 1, 120.0, 0.0, "NQ", 20));
    CHECK_NEAR(position.average_entry_price(), 320.0 / 3.0);
    CHECK_OPTIONAL_NEAR(position.settlement_basis_price(), 340.0 / 3.0);
    const auto reduced = position.apply_fill(
        fill(4, OrderSide::sell, 1, 130.0, 0.0, "NQ", 20));
    CHECK_NEAR(reduced.realized_gross_pnl(), 1400.0 / 3.0);
    CHECK_NEAR(reduced.realized_cash_pnl(), 1000.0 / 3.0);
    CHECK_OPTIONAL_NEAR(position.settlement_basis_price(), 340.0 / 3.0);
    CHECK_NEAR(position.settle_variation(120.0), 800.0 / 3.0);

    const auto reversed = position.apply_fill(
        fill(5, OrderSide::sell, 3, 125.0, 0.0, "NQ", 20));
    CHECK(reversed.is_reversal());
    CHECK_NEAR(reversed.realized_gross_pnl(), 2200.0 / 3.0);
    CHECK_NEAR(reversed.realized_cash_pnl(), 200.0);
    CHECK_NEAR(position.average_entry_price(), 125.0);
    CHECK_OPTIONAL_NEAR(position.settlement_basis_price(), 125.0);
    CHECK_NEAR(position.settle_variation(120.0), 100.0);
    const auto closed = position.apply_fill(
        fill(6, OrderSide::buy, 1, 110.0, 0.0, "NQ", 20));
    CHECK_NEAR(closed.realized_gross_pnl(), 300.0);
    CHECK_NEAR(closed.realized_cash_pnl(), 200.0);
    CHECK(position.is_flat());
    CHECK_OPTIONAL_NEAR(position.settlement_basis_price(), 0.0);
}

void variation_settlement_rejects_wrong_kind_and_rolls_back_overflow() {
    Position equity{Symbol{"SPY"}};
    CHECK(!equity.settlement_basis_price().has_value());
    CHECK_THROWS_AS(equity.settle_variation(100.0), InvalidPositionInput);

    const auto grid = PriceGrid::from_tick_size(1.0);
    const auto future = InstrumentSpec::future(
        Symbol{"NQ"}, Currency::usd(), grid, 20, Timestamp{200ns}, Timestamp{300ns});
    Position position{future};
    position.apply_fill(fill(
        1, OrderSide::buy, std::numeric_limits<std::int64_t>::max(),
        1.0, 0.0, "NQ", 20));
    CHECK_THROWS_AS(position.settle_variation(1.0e300), PositionAccountingError);
    CHECK_OPTIONAL_NEAR(position.settlement_basis_price(), 1.0);
    CHECK(position.quantity().value() == std::numeric_limits<std::int64_t>::max());
    CHECK_THROWS_AS(position.settle_variation(0.0), InvalidPositionInput);
    CHECK_THROWS_AS(
        position.settle_variation(std::numeric_limits<double>::quiet_NaN()),
        InvalidPositionInput);
}

void long_add_reduce_close_and_reverse_use_average_cost() {
    Position position{Symbol{"SPY"}};
    position.apply_fill(fill(1, OrderSide::buy, 10, 10.0, 1.0));
    CHECK(position.quantity().value() == 10);
    CHECK_NEAR(position.average_entry_price(), 10.0);
    CHECK_NEAR(position.realized_gross_pnl(), 0.0);

    position.apply_fill(fill(2, OrderSide::buy, 10, 12.0, 1.0));
    CHECK(position.quantity().value() == 20);
    CHECK_NEAR(position.average_entry_price(), 11.0);

    position.apply_fill(fill(3, OrderSide::sell, 5, 14.0, 1.0));
    CHECK(position.quantity().value() == 15);
    CHECK_NEAR(position.average_entry_price(), 11.0);
    CHECK_NEAR(position.realized_gross_pnl(), 15.0);

    position.apply_fill(fill(4, OrderSide::sell, 15, 9.0, 2.0));
    CHECK(position.is_flat());
    CHECK(position.quantity().value() == 0);
    CHECK(position.average_entry_price() == 0.0);
    CHECK_NEAR(position.realized_gross_pnl(), -15.0);
    CHECK_NEAR(position.commissions_paid(), 5.0);

    Position reversal{Symbol{"SPY"}};
    reversal.apply_fill(fill(1, OrderSide::buy, 10, 10.0));
    reversal.apply_fill(fill(2, OrderSide::sell, 15, 12.0));
    CHECK(reversal.quantity().value() == -5);
    CHECK_NEAR(reversal.average_entry_price(), 12.0);
    CHECK_NEAR(reversal.realized_gross_pnl(), 20.0);
}

void short_add_reduce_close_and_reverse_are_symmetric() {
    Position position{Symbol{"SPY"}};
    position.apply_fill(fill(1, OrderSide::sell, 10, 20.0));
    position.apply_fill(fill(2, OrderSide::sell, 10, 16.0));
    CHECK(position.quantity().value() == -20);
    CHECK_NEAR(position.average_entry_price(), 18.0);

    position.apply_fill(fill(3, OrderSide::buy, 5, 15.0));
    CHECK(position.quantity().value() == -15);
    CHECK_NEAR(position.average_entry_price(), 18.0);
    CHECK_NEAR(position.realized_gross_pnl(), 15.0);

    position.apply_fill(fill(4, OrderSide::buy, 15, 19.0));
    CHECK(position.is_flat());
    CHECK(position.average_entry_price() == 0.0);
    CHECK_NEAR(position.realized_gross_pnl(), 0.0);

    Position reversal{Symbol{"SPY"}};
    reversal.apply_fill(fill(1, OrderSide::sell, 10, 20.0));
    reversal.apply_fill(fill(2, OrderSide::buy, 15, 18.0));
    CHECK(reversal.quantity().value() == 5);
    CHECK_NEAR(reversal.average_entry_price(), 18.0);
    CHECK_NEAR(reversal.realized_gross_pnl(), 20.0);
}

void hand_calculated_six_fill_fixture_preserves_every_invariant() {
    struct Expected final {
        OrderSide side;
        std::int64_t fill_quantity;
        double fill_price;
        double fee;
        std::int64_t position_quantity;
        double average_price;
        double realized;
        double fees;
        double market_value;
        double unrealized;
        double net_pnl;
    };
    constexpr Expected rows[] = {
        {OrderSide::buy, 10, 10.0, 1.0, 10, 10.0, 0.0, 1.0, 100.0, 0.0, -1.0},
        {OrderSide::buy, 10, 12.0, 1.0, 20, 11.0, 0.0, 2.0, 240.0, 20.0, 18.0},
        {OrderSide::sell, 5, 14.0, 1.0, 15, 11.0, 15.0, 3.0, 210.0, 45.0, 57.0},
        {OrderSide::sell, 20, 9.0, 2.0, -5, 9.0, -15.0, 5.0, -45.0, 0.0, -20.0},
        {OrderSide::buy, 8, 8.0, 1.0, 3, 8.0, -10.0, 6.0, 24.0, 0.0, -16.0},
        {OrderSide::sell, 3, 10.0, 1.0, 0, 0.0, -4.0, 7.0, 0.0, 0.0, -11.0},
    };

    Position position{Symbol{"SPY"}};
    std::uint64_t id = 1;
    for (const auto& row : rows) {
        position.apply_fill(
            fill(id, row.side, row.fill_quantity, row.fill_price, row.fee));
        position.mark(mark(id * 2, row.fill_price, id * 100));

        CHECK(position.quantity().value() == row.position_quantity);
        CHECK_NEAR(position.average_entry_price(), row.average_price);
        CHECK_NEAR(position.realized_gross_pnl(), row.realized);
        CHECK_NEAR(position.commissions_paid(), row.fees);
        CHECK_OPTIONAL_NEAR(position.market_value(), row.market_value);
        CHECK_OPTIONAL_NEAR(position.unrealized_pnl(), row.unrealized);
        CHECK_OPTIONAL_NEAR(position.net_pnl(), row.net_pnl);
        CHECK(position.is_flat() == (row.position_quantity == 0));
        ++id;
    }
}

void marks_change_only_valuation_and_obey_event_order() {
    Position position{Symbol{"SPY"}};
    position.apply_fill(fill(1, OrderSide::sell, 10, 20.0, 0.5));
    position.mark(mark(10, 18.0, 100));

    CHECK(position.quantity().value() == -10);
    CHECK_NEAR(position.average_entry_price(), 20.0);
    CHECK_NEAR(position.realized_gross_pnl(), 0.0);
    CHECK_NEAR(position.commissions_paid(), 0.5);
    CHECK_OPTIONAL_NEAR(position.market_value(), -180.0);
    CHECK_OPTIONAL_NEAR(position.unrealized_pnl(), 20.0);
    CHECK_OPTIONAL_NEAR(position.net_pnl(), 19.5);

    position.mark(mark(11, 21.0, 100));
    CHECK(position.quantity().value() == -10);
    CHECK_NEAR(position.average_entry_price(), 20.0);
    CHECK_NEAR(position.realized_gross_pnl(), 0.0);
    CHECK_NEAR(position.commissions_paid(), 0.5);
    CHECK_OPTIONAL_NEAR(position.market_value(), -210.0);
    CHECK_OPTIONAL_NEAR(position.unrealized_pnl(), -10.0);

    CHECK_THROWS_AS(position.mark(mark(12, 19.0, 99)), InvalidPositionInput);
    CHECK_THROWS_AS(position.mark(mark(11, 19.0, 101)), InvalidPositionInput);
    CHECK_OPTIONAL_NEAR(position.market_value(), -210.0);
    CHECK(position.valuation_mark().has_value());
    if (position.valuation_mark().has_value()) {
        CHECK(position.valuation_mark()->effective_at() == Timestamp{100ns});
        CHECK(position.valuation_mark()->sequence() == EventSequence{11});
        CHECK_NEAR(position.valuation_mark()->price(), 21.0);
    }
}

void invalid_marks_and_symbol_mismatches_leave_state_unchanged() {
    CHECK_THROWS_AS(mark(1, 0.0), InvalidPositionInput);
    CHECK_THROWS_AS(mark(1, -1.0), InvalidPositionInput);
    CHECK_THROWS_AS(
        mark(1, std::numeric_limits<double>::quiet_NaN()),
        InvalidPositionInput);
    CHECK_THROWS_AS(
        mark(1, std::numeric_limits<double>::infinity()),
        InvalidPositionInput);

    Position position{Symbol{"SPY"}};
    CHECK_THROWS_AS(
        position.apply_fill(fill(1, OrderSide::buy, 2, 10.0, 1.0, "QQQ")),
        InvalidPositionInput);
    CHECK(position.is_flat());
    CHECK(position.average_entry_price() == 0.0);
    CHECK(position.realized_gross_pnl() == 0.0);
    CHECK(position.commissions_paid() == 0.0);
}

void accounting_overflow_is_rejected_without_partial_mutation() {
    Position quantity_overflow{Symbol{"SPY"}};
    quantity_overflow.apply_fill(fill(
        1,
        OrderSide::buy,
        std::numeric_limits<std::int64_t>::max(),
        1.0));
    CHECK_THROWS_AS(
        quantity_overflow.apply_fill(fill(2, OrderSide::buy, 1, 1.0)),
        std::overflow_error);
    CHECK(quantity_overflow.quantity().value() ==
          std::numeric_limits<std::int64_t>::max());
    CHECK_NEAR(quantity_overflow.average_entry_price(), 1.0);

    CHECK_THROWS_AS(
        quantity_overflow.mark(mark(1, 1.0e300)),
        PositionAccountingError);
    CHECK(!quantity_overflow.valuation_mark().has_value());

    Position realized_overflow{Symbol{"SPY"}};
    realized_overflow.apply_fill(fill(1, OrderSide::buy, 1, 1.0));
    realized_overflow.apply_fill(fill(2, OrderSide::sell, 1, 1.0e308));
    realized_overflow.apply_fill(fill(3, OrderSide::buy, 1, 1.0));
    CHECK_THROWS_AS(
        realized_overflow.apply_fill(fill(4, OrderSide::sell, 1, 1.0e308)),
        PositionAccountingError);
    CHECK(realized_overflow.quantity().value() == 1);
    CHECK_NEAR(realized_overflow.average_entry_price(), 1.0);
    CHECK_NEAR(realized_overflow.realized_gross_pnl(), 1.0e308);

    Position commission_overflow{Symbol{"SPY"}};
    commission_overflow.apply_fill(fill(
        1,
        OrderSide::buy,
        1,
        1.0,
        std::numeric_limits<double>::max()));
    CHECK_THROWS_AS(
        commission_overflow.apply_fill(fill(
            2,
            OrderSide::sell,
            1,
            1.0,
            std::numeric_limits<double>::max())),
        PositionAccountingError);
    CHECK(commission_overflow.quantity().value() == 1);
    CHECK_NEAR(commission_overflow.average_entry_price(), 1.0);
    CHECK(commission_overflow.commissions_paid() ==
          std::numeric_limits<double>::max());
}

void binary64_average_cost_and_compensated_fees_are_stable() {
    Position average{Symbol{"SPY"}};
    average.apply_fill(fill(1, OrderSide::buy, 1, 0.1));
    average.apply_fill(fill(2, OrderSide::buy, 2, 0.2));
    CHECK(average.quantity().value() == 3);
    CHECK_NEAR(average.average_entry_price(), 1.0 / 6.0);

    Position fees{Symbol{"SPY"}};
    fees.apply_fill(fill(1, OrderSide::buy, 1, 1.0, 1.0e16));
    fees.apply_fill(fill(2, OrderSide::sell, 1, 1.0, 1.0));
    fees.apply_fill(fill(3, OrderSide::buy, 1, 1.0, 1.0));
    CHECK(fees.commissions_paid() == 1.0e16 + 2.0);
}

void transitions_expose_accounting_facts_without_recomputing_cost_basis() {
    Position position{Symbol{"SPY"}};
    const auto opened = position.apply_fill(fill(1, OrderSide::buy, 10, 10.0));
    CHECK(opened.previous_quantity().value() == 0);
    CHECK(opened.new_quantity().value() == 10);
    CHECK(opened.opened_quantity().value() == 10);
    CHECK(opened.closed_quantity().value() == 0);
    CHECK_NEAR(opened.realized_gross_pnl(), 0.0);
    CHECK_NEAR(opened.realized_cash_pnl(), 0.0);
    CHECK(!opened.is_reversal());

    const auto scaled = position.apply_fill(fill(2, OrderSide::buy, 5, 12.0));
    CHECK(scaled.previous_quantity().value() == 10);
    CHECK(scaled.new_quantity().value() == 15);
    CHECK(scaled.opened_quantity().value() == 5);
    CHECK(scaled.closed_quantity().value() == 0);

    const auto reduced = position.apply_fill(fill(3, OrderSide::sell, 3, 14.0));
    CHECK(reduced.previous_quantity().value() == 15);
    CHECK(reduced.new_quantity().value() == 12);
    CHECK(reduced.opened_quantity().value() == 0);
    CHECK(reduced.closed_quantity().value() == 3);
    CHECK(!reduced.is_reversal());
    CHECK_NEAR(reduced.realized_cash_pnl(), reduced.realized_gross_pnl());

    const auto reversed = position.apply_fill(fill(4, OrderSide::sell, 20, 9.0));
    CHECK(reversed.previous_quantity().value() == 12);
    CHECK(reversed.new_quantity().value() == -8);
    CHECK(reversed.opened_quantity().value() == 8);
    CHECK(reversed.closed_quantity().value() == 12);
    CHECK(reversed.is_reversal());
    CHECK_NEAR(reversed.realized_cash_pnl(), reversed.realized_gross_pnl());
}

}  // namespace

int main() {
    empty_and_unmarked_states_are_explicit();
    derivative_positions_scale_pnl_and_separate_exposure();
    future_short_add_reduce_reverse_and_flat_are_multiplier_scaled();
    futures_variation_basis_is_separate_from_economic_entry();
    variation_settlement_rejects_wrong_kind_and_rolls_back_overflow();
    long_add_reduce_close_and_reverse_use_average_cost();
    short_add_reduce_close_and_reverse_are_symmetric();
    hand_calculated_six_fill_fixture_preserves_every_invariant();
    marks_change_only_valuation_and_obey_event_order();
    invalid_marks_and_symbol_mismatches_leave_state_unchanged();
    accounting_overflow_is_rejected_without_partial_mutation();
    binary64_average_cost_and_compensated_fees_are_stable();
    transitions_expose_accounting_facts_without_recomputing_cost_basis();

    if (failures != 0) {
        std::cerr << failures << " test assertion(s) failed\n";
        return EXIT_FAILURE;
    }

    std::cout << "all position tests passed\n";
    return EXIT_SUCCESS;
}
