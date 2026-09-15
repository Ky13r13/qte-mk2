#include "qte/execution/open_only.hpp"

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
#include <utility>

namespace {

using namespace std::chrono_literals;
using qte::core::Currency;
using qte::core::EventSequence;
using qte::core::OrderId;
using qte::core::PriceGrid;
using qte::core::ShareAmount;
using qte::execution::ExecutionCalculationError;
using qte::execution::ExecutionCosts;
using qte::execution::FillCandidate;
using qte::execution::InvalidExecutionInput;
using qte::execution::MarketOpen;
using qte::execution::OpenOnlyExecutionModel;
using qte::market_data::Bar;
using qte::market_data::InstrumentSpec;
using qte::market_data::Symbol;
using qte::market_data::Timestamp;
using qte::orders::OrderCancellationReason;
using qte::orders::OrderRecord;
using qte::orders::OrderRequest;
using qte::orders::OrderSide;
using qte::orders::OrderType;
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

[[nodiscard]] bool near(const double left, const double right) {
    return std::abs(left - right) <= 1.0e-12 * std::max(1.0, std::abs(right));
}

[[nodiscard]] InstrumentSpec instrument(
    const char* const symbol = "SPY",
    const double tick_size = 0.01) {
    return InstrumentSpec{
        Symbol{symbol},
        Currency::usd(),
        PriceGrid::from_tick_size(tick_size),
    };
}

[[nodiscard]] OrderRecord order(
    const OrderSide side = OrderSide::buy,
    const OrderType type = OrderType::market,
    const std::int64_t quantity = 10,
    const std::optional<double> limit = std::nullopt,
    const std::optional<double> stop = std::nullopt,
    const char* const symbol = "SPY",
    const double tick_size = 0.01,
    const bool open = true) {
    const auto grid = PriceGrid::from_tick_size(tick_size);
    OrderRecord result{
        OrderId{1},
        OrderRequest{
            .symbol = Symbol{symbol},
            .side = side,
            .quantity = ShareAmount::from_count(quantity),
            .type = type,
            .limit_price = limit.has_value()
                               ? std::optional{grid.canonicalize(*limit)}
                               : std::nullopt,
            .stop_price = stop.has_value()
                              ? std::optional{grid.canonicalize(*stop)}
                              : std::nullopt,
            .time_in_force = TimeInForce::good_til_canceled,
        },
        Timestamp{100ns},
        EventSequence{10},
        EventSequence{10},
    };
    if (open) {
        result.open(instrument(symbol, tick_size));
    }
    return result;
}

[[nodiscard]] MarketOpen market_open(
    const double price = 100.0,
    const std::uint64_t sequence = 11,
    const Timestamp timestamp = Timestamp{101ns},
    const char* const symbol = "SPY") {
    return MarketOpen::create(
        Symbol{symbol}, timestamp, EventSequence{sequence}, price);
}

void check_candidate(
    const std::optional<FillCandidate>& candidate,
    const std::int64_t quantity,
    const double reference_open,
    const double executed_price,
    const double gross_notional,
    const double commission,
    const int line) {
    check(candidate.has_value(), "candidate exists", line);
    if (!candidate.has_value()) {
        return;
    }
    check(candidate->order_id() == OrderId{1}, "candidate order ID", line);
    check(candidate->symbol() == Symbol{"SPY"}, "candidate symbol", line);
    check(candidate->quantity().value() == quantity, "candidate quantity", line);
    check(near(candidate->reference_open(), reference_open), "candidate reference open", line);
    check(near(candidate->executed_price().value(), executed_price), "candidate price", line);
    check(near(candidate->gross_notional(), gross_notional), "candidate gross notional", line);
    check(near(candidate->commission(), commission), "candidate commission", line);
}

#define CHECK_CANDIDATE(candidate, quantity, reference, price, notional, commission) \
    check_candidate((candidate), (quantity), (reference), (price), (notional), \
                    (commission), __LINE__)

void cost_configuration_has_strict_boundaries() {
    const auto zero = ExecutionCosts::create();
    CHECK(zero.commission_bps() == 0.0);
    CHECK(zero.spread_bps() == 0.0);
    CHECK(zero.slippage_bps() == 0.0);

    const double nan = std::numeric_limits<double>::quiet_NaN();
    const double infinity = std::numeric_limits<double>::infinity();
    CHECK_THROWS_AS(ExecutionCosts::create(-1.0, 0.0, 0.0), InvalidExecutionInput);
    CHECK_THROWS_AS(ExecutionCosts::create(nan, 0.0, 0.0), InvalidExecutionInput);
    CHECK_THROWS_AS(ExecutionCosts::create(infinity, 0.0, 0.0), InvalidExecutionInput);
    CHECK_THROWS_AS(ExecutionCosts::create(0.0, -1.0, 0.0), InvalidExecutionInput);
    CHECK_THROWS_AS(ExecutionCosts::create(0.0, nan, 0.0), InvalidExecutionInput);
    CHECK_THROWS_AS(ExecutionCosts::create(0.0, infinity, 0.0), InvalidExecutionInput);
    CHECK_THROWS_AS(ExecutionCosts::create(0.0, 0.0, -1.0), InvalidExecutionInput);
    CHECK_THROWS_AS(ExecutionCosts::create(0.0, 0.0, nan), InvalidExecutionInput);
    CHECK_THROWS_AS(ExecutionCosts::create(0.0, 0.0, infinity), InvalidExecutionInput);
}

void market_open_rejects_nonpositive_and_nonfinite_prices() {
    CHECK_THROWS_AS(market_open(0.0), InvalidExecutionInput);
    CHECK_THROWS_AS(market_open(-1.0), InvalidExecutionInput);
    CHECK_THROWS_AS(
        market_open(std::numeric_limits<double>::quiet_NaN()),
        InvalidExecutionInput);
    CHECK_THROWS_AS(
        market_open(std::numeric_limits<double>::infinity()),
        InvalidExecutionInput);
}

void market_orders_fill_remaining_quantity_with_adverse_tick_rounding() {
    const OpenOnlyExecutionModel model{ExecutionCosts::create()};
    const auto five_cent_grid = instrument("SPY", 0.05);

    auto buy = order(OrderSide::buy, OrderType::market, 10, std::nullopt,
                     std::nullopt, "SPY", 0.05);
    CHECK_CANDIDATE(
        model.evaluate(buy, five_cent_grid, market_open(100.02)),
        10, 100.02, 100.05, 1000.50, 0.0);
    const auto buy_candidate = model.evaluate(buy, five_cent_grid, market_open(100.02));
    CHECK(buy_candidate.has_value());
    if (buy_candidate.has_value()) {
        CHECK(buy_candidate->side() == OrderSide::buy);
        CHECK(buy_candidate->effective_at() == Timestamp{101ns});
        CHECK(buy_candidate->effective_sequence() == EventSequence{11});
    }

    auto sell = order(OrderSide::sell, OrderType::market, 10, std::nullopt,
                      std::nullopt, "SPY", 0.05);
    CHECK_CANDIDATE(
        model.evaluate(sell, five_cent_grid, market_open(100.02)),
        10, 100.02, 100.00, 1000.00, 0.0);
    const auto sell_candidate = model.evaluate(sell, five_cent_grid, market_open(100.02));
    CHECK(sell_candidate.has_value());
    if (sell_candidate.has_value()) {
        CHECK(sell_candidate->side() == OrderSide::sell);
    }

    buy.record_fill_quantity(ShareAmount::from_count(4));
    CHECK_CANDIDATE(
        model.evaluate(buy, five_cent_grid, market_open(101.02, 12)),
        6, 101.02, 101.05, 606.30, 0.0);
    CHECK(buy.status() == qte::orders::OrderStatus::partially_filled);
    CHECK(buy.filled_quantity().value() == 4);
    CHECK(buy.remaining_quantity().value() == 6);
}

void costs_and_commission_are_applied_once_and_explicitly() {
    const OpenOnlyExecutionModel model{ExecutionCosts::create(10.0, 20.0, 20.0)};
    const auto spy = instrument();
    auto buy = order();
    auto sell = order(OrderSide::sell);

    CHECK_CANDIDATE(
        model.evaluate(buy, spy, market_open(100.0)),
        10, 100.0, 100.30, 1003.0, 1.003);
    CHECK_CANDIDATE(
        model.evaluate(sell, spy, market_open(100.0)),
        10, 100.0, 99.70, 997.0, 0.997);
}

void limit_rules_are_inclusive_and_costs_can_prevent_a_fill() {
    const auto spy = instrument();
    const OpenOnlyExecutionModel free_model{ExecutionCosts::create()};

    auto buy = order(OrderSide::buy, OrderType::limit, 10, 100.0);
    CHECK(free_model.evaluate(buy, spy, market_open(100.01)) == std::nullopt);
    CHECK_CANDIDATE(
        free_model.evaluate(buy, spy, market_open(100.0)),
        10, 100.0, 100.0, 1000.0, 0.0);
    CHECK_CANDIDATE(
        free_model.evaluate(buy, spy, market_open(95.0)),
        10, 95.0, 95.0, 950.0, 0.0);

    auto sell = order(OrderSide::sell, OrderType::limit, 10, 100.0);
    CHECK(free_model.evaluate(sell, spy, market_open(99.99)) == std::nullopt);
    CHECK_CANDIDATE(
        free_model.evaluate(sell, spy, market_open(100.0)),
        10, 100.0, 100.0, 1000.0, 0.0);
    CHECK_CANDIDATE(
        free_model.evaluate(sell, spy, market_open(105.0)),
        10, 105.0, 105.0, 1050.0, 0.0);

    const OpenOnlyExecutionModel costly{ExecutionCosts::create(0.0, 0.0, 2.0)};
    CHECK(costly.evaluate(buy, spy, market_open(100.0)) == std::nullopt);
    CHECK(costly.evaluate(sell, spy, market_open(100.0)) == std::nullopt);
}

void eligibility_and_component_boundaries_are_explicit() {
    const OpenOnlyExecutionModel model{ExecutionCosts::create()};
    const auto spy = instrument();
    auto active = order();

    CHECK(model.evaluate(active, spy, market_open(100.0, 10)) == std::nullopt);
    CHECK(model.evaluate(active, spy, market_open(100.0, 9)) == std::nullopt);
    CHECK_CANDIDATE(
        model.evaluate(active, spy, market_open(100.0, 11, Timestamp{100ns})),
        10, 100.0, 100.0, 1000.0, 0.0);
    CHECK_THROWS_AS(
        model.evaluate(active, spy, market_open(100.0, 11, Timestamp{99ns})),
        InvalidExecutionInput);
    CHECK_THROWS_AS(
        model.evaluate(active, spy, market_open(100.0, 11, Timestamp{101ns}, "QQQ")),
        InvalidExecutionInput);
    CHECK_THROWS_AS(
        model.evaluate(active, instrument("QQQ"), market_open()),
        InvalidExecutionInput);

    auto not_open = order(OrderSide::buy, OrderType::market, 10, std::nullopt,
                          std::nullopt, "SPY", 0.01, false);
    CHECK_THROWS_AS(model.evaluate(not_open, spy, market_open()), InvalidExecutionInput);

    auto canceled = order();
    static_cast<void>(canceled.cancel(OrderCancellationReason::user_requested));
    CHECK_THROWS_AS(model.evaluate(canceled, spy, market_open()), InvalidExecutionInput);

    auto stop = order(OrderSide::buy, OrderType::stop, 10, std::nullopt, 100.0);
    CHECK(model.evaluate(stop, spy, market_open(101.0, 10)) == std::nullopt);
    CHECK(!stop.stop_triggered());
}

void stop_orders_trigger_inclusively_then_behave_as_market_orders() {
    const OpenOnlyExecutionModel model{ExecutionCosts::create()};
    const auto spy = instrument();

    auto buy = order(OrderSide::buy, OrderType::stop, 10, std::nullopt, 100.0);
    CHECK(model.evaluate(buy, spy, market_open(99.99)) == std::nullopt);
    CHECK(!buy.stop_triggered());
    CHECK_CANDIDATE(
        model.evaluate(buy, spy, market_open(100.0)),
        10, 100.0, 100.0, 1000.0, 0.0);
    CHECK(buy.stop_triggered());
    CHECK_CANDIDATE(
        model.evaluate(buy, spy, market_open(90.0, 12)),
        10, 90.0, 90.0, 900.0, 0.0);

    auto buy_gap = order(OrderSide::buy, OrderType::stop, 10, std::nullopt, 100.0);
    CHECK_CANDIDATE(
        model.evaluate(buy_gap, spy, market_open(105.0)),
        10, 105.0, 105.0, 1050.0, 0.0);
    CHECK(buy_gap.stop_triggered());

    auto sell = order(OrderSide::sell, OrderType::stop, 10, std::nullopt, 100.0);
    CHECK(model.evaluate(sell, spy, market_open(100.01)) == std::nullopt);
    CHECK(!sell.stop_triggered());
    CHECK_CANDIDATE(
        model.evaluate(sell, spy, market_open(100.0)),
        10, 100.0, 100.0, 1000.0, 0.0);
    CHECK(sell.stop_triggered());
    CHECK_CANDIDATE(
        model.evaluate(sell, spy, market_open(110.0, 12)),
        10, 110.0, 110.0, 1100.0, 0.0);

    auto sell_gap = order(OrderSide::sell, OrderType::stop, 10, std::nullopt, 100.0);
    CHECK_CANDIDATE(
        model.evaluate(sell_gap, spy, market_open(95.0)),
        10, 95.0, 95.0, 950.0, 0.0);
    CHECK(sell_gap.stop_triggered());
}

void stop_limit_triggers_persist_across_unfilled_opens() {
    const OpenOnlyExecutionModel model{ExecutionCosts::create()};
    const auto spy = instrument();

    auto buy_same_open = order(
        OrderSide::buy, OrderType::stop_limit, 10, 101.0, 100.0);
    CHECK_CANDIDATE(
        model.evaluate(buy_same_open, spy, market_open(100.0)),
        10, 100.0, 100.0, 1000.0, 0.0);
    CHECK(buy_same_open.stop_triggered());

    auto sell_same_open = order(
        OrderSide::sell, OrderType::stop_limit, 10, 99.0, 100.0);
    CHECK_CANDIDATE(
        model.evaluate(sell_same_open, spy, market_open(100.0)),
        10, 100.0, 100.0, 1000.0, 0.0);
    CHECK(sell_same_open.stop_triggered());

    auto buy_gap = order(
        OrderSide::buy, OrderType::stop_limit, 10, 101.0, 100.0);
    CHECK(model.evaluate(buy_gap, spy, market_open(102.0)) == std::nullopt);
    CHECK(buy_gap.stop_triggered());
    CHECK_CANDIDATE(
        model.evaluate(buy_gap, spy, market_open(100.50, 12)),
        10, 100.50, 100.50, 1005.0, 0.0);

    auto sell_gap = order(
        OrderSide::sell, OrderType::stop_limit, 10, 99.0, 100.0);
    CHECK(model.evaluate(sell_gap, spy, market_open(98.0)) == std::nullopt);
    CHECK(sell_gap.stop_triggered());
    CHECK_CANDIDATE(
        model.evaluate(sell_gap, spy, market_open(99.50, 12)),
        10, 99.50, 99.50, 995.0, 0.0);
}

void stop_limit_cost_breaches_leave_triggered_orders_open() {
    const OpenOnlyExecutionModel model{ExecutionCosts::create(0.0, 0.0, 2.0)};
    const auto spy = instrument();

    auto buy = order(
        OrderSide::buy, OrderType::stop_limit, 10, 100.0, 100.0);
    CHECK(model.evaluate(buy, spy, market_open(100.0)) == std::nullopt);
    CHECK(buy.stop_triggered());
    CHECK_CANDIDATE(
        model.evaluate(buy, spy, market_open(99.90, 12)),
        10, 99.90, 99.92, 999.20, 0.0);

    auto sell = order(
        OrderSide::sell, OrderType::stop_limit, 10, 100.0, 100.0);
    CHECK(model.evaluate(sell, spy, market_open(100.0)) == std::nullopt);
    CHECK(sell.stop_triggered());
    CHECK(model.evaluate(sell, spy, market_open(100.10, 12)).has_value());
}

void partially_filled_stop_orders_retain_trigger_state_and_remaining_quantity() {
    const OpenOnlyExecutionModel model{ExecutionCosts::create()};
    const auto spy = instrument();
    auto stop = order(OrderSide::buy, OrderType::stop, 10, std::nullopt, 100.0);

    CHECK(model.evaluate(stop, spy, market_open(100.0)).has_value());
    CHECK(stop.stop_triggered());
    stop.record_fill_quantity(ShareAmount::from_count(4));

    CHECK_CANDIDATE(
        model.evaluate(stop, spy, market_open(90.0, 12)),
        6, 90.0, 90.0, 540.0, 0.0);
    CHECK(stop.stop_triggered());
}

void full_future_bar_fields_cannot_affect_open_only_results() {
    const Bar quiet{
        Symbol{"SPY"}, Timestamp{100ns}, Timestamp{200ns},
        100.02, 101.0, 99.0, 100.50, 10.0,
    };
    const Bar extreme{
        Symbol{"SPY"}, Timestamp{100ns}, Timestamp{200ns},
        100.02, 1000.0, 1.0, 900.0, 1'000'000.0,
    };
    const OpenOnlyExecutionModel model{ExecutionCosts::create(5.0, 10.0, 10.0)};
    const auto spy = instrument("SPY", 0.05);
    auto buy = order(OrderSide::buy, OrderType::market, 10, std::nullopt,
                     std::nullopt, "SPY", 0.05);

    const auto first = model.evaluate(
        buy, spy, market_open(quiet.open, 11, quiet.start_time));
    const auto second = model.evaluate(
        buy, spy, market_open(extreme.open, 11, extreme.start_time));
    CHECK(first.has_value());
    CHECK(second.has_value());
    if (first.has_value() && second.has_value()) {
        CHECK(first->quantity() == second->quantity());
        CHECK(first->reference_open() == second->reference_open());
        CHECK(first->executed_price() == second->executed_price());
        CHECK(first->gross_notional() == second->gross_notional());
        CHECK(first->commission() == second->commission());
    }

    const Bar false_intrabar_trigger{
        Symbol{"SPY"}, Timestamp{100ns}, Timestamp{200ns},
        99.0, 101.0, 98.0, 100.50, 10.0,
    };
    const Bar quiet_below_stop{
        Symbol{"SPY"}, Timestamp{100ns}, Timestamp{200ns},
        99.0, 99.5, 98.5, 99.25, 10.0,
    };
    auto first_stop = order(
        OrderSide::buy, OrderType::stop, 10, std::nullopt, 100.0);
    auto second_stop = order(
        OrderSide::buy, OrderType::stop, 10, std::nullopt, 100.0);
    CHECK(model.evaluate(
              first_stop,
              instrument(),
              market_open(false_intrabar_trigger.open, 11,
                          false_intrabar_trigger.start_time)) == std::nullopt);
    CHECK(model.evaluate(
              second_stop,
              instrument(),
              market_open(quiet_below_stop.open, 11,
                          quiet_below_stop.start_time)) == std::nullopt);
    CHECK(!first_stop.stop_triggered());
    CHECK(!second_stop.stop_triggered());
}

void impossible_cost_calculations_fail_instead_of_emitting_candidates() {
    const auto spy = instrument();
    auto sell = order(OrderSide::sell);
    const OpenOnlyExecutionModel consumes_entire_price{
        ExecutionCosts::create(0.0, 0.0, 10'000.0)};
    CHECK_THROWS_AS(
        consumes_entire_price.evaluate(sell, spy, market_open()),
        ExecutionCalculationError);

    auto buy = order();
    const OpenOnlyExecutionModel doubles_price{
        ExecutionCosts::create(0.0, 0.0, 10'000.0)};
    CHECK_THROWS_AS(
        doubles_price.evaluate(
            buy,
            spy,
            market_open(std::numeric_limits<double>::max())),
        ExecutionCalculationError);

    CHECK_THROWS_AS(
        OpenOnlyExecutionModel{ExecutionCosts::create()}.evaluate(
            buy, spy, market_open(1.0e15)),
        ExecutionCalculationError);

    auto stop = order(OrderSide::sell, OrderType::stop, 10, std::nullopt, 100.0);
    CHECK_THROWS_AS(
        consumes_entire_price.evaluate(stop, spy, market_open(99.0)),
        ExecutionCalculationError);
    CHECK(!stop.stop_triggered());
}

void model_identity_is_stable() {
    CHECK(OpenOnlyExecutionModel::model_id() == "open_only_v1");
}

}  // namespace

int main() {
    cost_configuration_has_strict_boundaries();
    market_open_rejects_nonpositive_and_nonfinite_prices();
    market_orders_fill_remaining_quantity_with_adverse_tick_rounding();
    costs_and_commission_are_applied_once_and_explicitly();
    limit_rules_are_inclusive_and_costs_can_prevent_a_fill();
    eligibility_and_component_boundaries_are_explicit();
    stop_orders_trigger_inclusively_then_behave_as_market_orders();
    stop_limit_triggers_persist_across_unfilled_opens();
    stop_limit_cost_breaches_leave_triggered_orders_open();
    partially_filled_stop_orders_retain_trigger_state_and_remaining_quantity();
    full_future_bar_fields_cannot_affect_open_only_results();
    impossible_cost_calculations_fail_instead_of_emitting_candidates();
    model_identity_is_stable();

    if (failures != 0) {
        std::cerr << failures << " test assertion(s) failed\n";
        return EXIT_FAILURE;
    }

    std::cout << "all open-only execution tests passed\n";
    return EXIT_SUCCESS;
}
