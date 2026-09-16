#include "qte/engine/backtest.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace {

using namespace std::chrono_literals;
using qte::core::Currency;
using qte::core::OrderId;
using qte::core::PriceGrid;
using qte::core::ShareAmount;
using qte::engine::BacktestConfig;
using qte::engine::BacktestEngine;
using qte::engine::BacktestResults;
using qte::engine::OrderEventKind;
using qte::market_data::Bar;
using qte::market_data::BarStream;
using qte::market_data::CorporateActionCoverage;
using qte::market_data::DatasetInput;
using qte::market_data::DatasetMetadata;
using qte::market_data::InstrumentSpec;
using qte::market_data::PriceAdjustmentMode;
using qte::market_data::Symbol;
using qte::market_data::Timestamp;
using qte::market_data::ValidatedDataset;
using qte::orders::Fill;
using qte::orders::OrderCancellationReason;
using qte::orders::OrderRejectionReason;
using qte::orders::OrderRequest;
using qte::orders::OrderSide;
using qte::orders::OrderStatus;
using qte::orders::OrderType;
using qte::orders::TimeInForce;
using qte::strategy::Strategy;
using qte::strategy::StrategyContext;

int failures = 0;

void check(const bool condition, const std::string_view expression, const int line) {
    if (!condition) {
        std::cerr << "line " << line << ": check failed: " << expression << '\n';
        ++failures;
    }
}

#define CHECK(expression) check((expression), #expression, __LINE__)

template <typename Exception, typename Callable>
void check_throws(Callable&& callable, const std::string_view expression, const int line) {
    try {
        std::forward<Callable>(callable)();
        check(false, expression, line);
    } catch (const Exception&) {
    } catch (...) {
        check(false, expression, line);
    }
}

#define CHECK_THROWS_AS(expression, exception_type) \
    check_throws<exception_type>([&] { static_cast<void>(expression); }, #expression, __LINE__)

[[nodiscard]] bool near(const double left, const double right) {
    return std::abs(left - right) <= 1.0e-9;
}

[[nodiscard]] InstrumentSpec instrument(const char* const symbol) {
    return InstrumentSpec{
        Symbol{symbol}, Currency::usd(), PriceGrid::from_tick_size(0.01)};
}

[[nodiscard]] Bar bar(
    const char* const symbol,
    const std::int64_t start,
    const double open,
    const double close) {
    return Bar{
        Symbol{symbol},
        Timestamp{std::chrono::hours{start}},
        Timestamp{std::chrono::hours{start + 1}},
        open,
        std::max(open, close) + 1.0,
        std::min(open, close) - 1.0,
        close,
        1000.0,
    };
}

[[nodiscard]] ValidatedDataset one_symbol_dataset(
    const std::vector<Bar>& bars,
    const char* const source = "one-symbol") {
    return qte::market_data::preflight(DatasetInput{
        DatasetMetadata::create(
            1h,
            Currency::usd(),
            PriceAdjustmentMode::unadjusted,
            CorporateActionCoverage::action_free,
            "shares",
            source),
        {instrument("SPY")},
        {BarStream{Symbol{"SPY"}, bars}},
    });
}

[[nodiscard]] OrderRequest market_request(
    const char* const symbol,
    const OrderSide side,
    const std::int64_t quantity) {
    return OrderRequest{
        .symbol = Symbol{symbol},
        .side = side,
        .quantity = ShareAmount::from_count(quantity),
        .type = OrderType::market,
        .limit_price = std::nullopt,
        .stop_price = std::nullopt,
        .time_in_force = TimeInForce::good_til_canceled,
    };
}

[[nodiscard]] BacktestEngine engine(
    const double initial_cash = 1000.0,
    const std::size_t history_capacity = 8) {
    return BacktestEngine{BacktestConfig::create(
        initial_cash,
        qte::execution::ExecutionCosts::create(),
        qte::risk::RiskLimits::create(),
        history_capacity,
        42,
        "test-build")};
}

struct RoundTripState final {
    std::vector<std::string> trace;
    std::vector<std::size_t> history_sizes;
    bool buy_submitted{false};
    bool sell_submitted{false};
};

class RoundTripStrategy final : public Strategy {
public:
    explicit RoundTripStrategy(RoundTripState& state) : state_(state) {}

    void on_start(StrategyContext& context) override {
        state_.trace.emplace_back("start");
        static_cast<void>(
            context.submit_order(market_request("SPY", OrderSide::buy, 1)));
    }

    void on_bar(StrategyContext& context, const Bar& value) override {
        const auto hour = std::chrono::duration_cast<std::chrono::hours>(
            value.start_time.time_since_epoch()).count();
        state_.trace.push_back("bar:" + std::to_string(hour));
        state_.history_sizes.push_back(context.history(Symbol{"SPY"}).size());
        if (!state_.buy_submitted) {
            static_cast<void>(
                context.submit_order(market_request("SPY", OrderSide::buy, 5)));
            state_.buy_submitted = true;
        }
    }

    void on_fill(StrategyContext& context, const Fill& value) override {
        state_.trace.push_back(
            value.side() == OrderSide::buy ? "fill:buy" : "fill:sell");
        static_cast<void>(context.cancel_order(value.order_id()));
        if (value.side() == OrderSide::buy && !state_.sell_submitted) {
            static_cast<void>(
                context.submit_order(market_request("SPY", OrderSide::sell, 5)));
            state_.sell_submitted = true;
        }
    }

    void on_end(StrategyContext&) override { state_.trace.emplace_back("end"); }

private:
    RoundTripState& state_;
};

void one_symbol_replay_respects_phases_and_next_open_execution() {
    const auto data = one_symbol_dataset({
        bar("SPY", 0, 100.0, 100.0),
        bar("SPY", 1, 110.0, 110.0),
        bar("SPY", 2, 120.0, 120.0),
    });
    RoundTripState state;
    const auto results = engine().run(
        data, std::make_unique<RoundTripStrategy>(state));

    CHECK(results.orders().size() == 3);
    if (results.orders().size() == 3) {
        CHECK(results.orders()[0].status == OrderStatus::rejected);
        CHECK(results.orders()[0].rejection_reason ==
              std::optional{OrderRejectionReason::no_reference_price});
        CHECK(results.orders()[1].status == OrderStatus::filled);
        CHECK(results.orders()[2].status == OrderStatus::filled);
    }
    CHECK(results.fills().size() == 2);
    if (results.fills().size() == 2) {
        CHECK(results.fills()[0].executed_price().value() == 110.0);
        CHECK(results.fills()[0].effective_at() == Timestamp{1h});
        CHECK(results.fills()[1].executed_price().value() == 120.0);
        CHECK(results.fills()[1].effective_at() == Timestamp{2h});
    }
    CHECK(results.trades().size() == 1);
    if (results.trades().size() == 1) {
        CHECK(near(results.trades().front().net_realized_pnl(), 50.0));
    }
    CHECK(results.equity_curve().size() == 5);
    if (!results.equity_curve().empty()) {
        CHECK(near(results.equity_curve().back().equity, 1050.0));
    }
    CHECK(state.history_sizes == std::vector<std::size_t>({1, 2, 3}));
    const std::vector<std::string> expected_trace{
        "start", "bar:0", "fill:buy", "bar:1", "fill:sell", "bar:2", "end"};
    CHECK(state.trace == expected_trace);

    std::size_t terminal_cancel_noops = 0;
    for (const auto& event : results.order_events()) {
        if (event.kind == OrderEventKind::cancel_noop) {
            ++terminal_cancel_noops;
        }
    }
    CHECK(terminal_cancel_noops == 2);
}

class SubmitCancelStrategy final : public Strategy {
public:
    void on_bar(StrategyContext& context, const Bar&) override {
        if (done_) {
            return;
        }
        const auto receipt =
            context.submit_order(market_request("SPY", OrderSide::buy, 1));
        static_cast<void>(context.cancel_order(receipt.order_id()));
        static_cast<void>(context.cancel_order(OrderId{999}));
        done_ = true;
    }

private:
    bool done_{false};
};

void cancellation_before_open_and_unknown_cancel_are_audited() {
    const auto data = one_symbol_dataset({
        bar("SPY", 0, 100.0, 100.0),
        bar("SPY", 1, 101.0, 101.0),
    });
    const auto results = engine().run(
        data, std::make_unique<SubmitCancelStrategy>());
    CHECK(results.orders().size() == 1);
    if (results.orders().size() == 1) {
        CHECK(results.orders().front().status == OrderStatus::canceled);
        CHECK(results.orders().front().cancellation_reason ==
              std::optional{OrderCancellationReason::user_requested});
    }
    CHECK(results.fills().empty());
    bool unknown_seen = false;
    for (const auto& event : results.order_events()) {
        unknown_seen = unknown_seen || event.kind == OrderEventKind::cancel_unknown;
    }
    CHECK(unknown_seen);
}

class BuyOnceStrategy final : public Strategy {
public:
    explicit BuyOnceStrategy(const std::int64_t quantity) : quantity_(quantity) {}

    void on_bar(StrategyContext& context, const Bar&) override {
        if (!done_) {
            static_cast<void>(context.submit_order(
                market_request("SPY", OrderSide::buy, quantity_)));
            done_ = true;
        }
    }

private:
    std::int64_t quantity_;
    bool done_{false};
};

void execution_gap_can_cancel_an_accepted_order_without_a_fill() {
    const auto data = one_symbol_dataset({
        bar("SPY", 0, 100.0, 100.0),
        bar("SPY", 1, 200.0, 200.0),
    });
    const auto results = engine().run(data, std::make_unique<BuyOnceStrategy>(10));
    CHECK(results.orders().size() == 1);
    if (results.orders().size() == 1) {
        CHECK(results.orders().front().status == OrderStatus::canceled);
        CHECK(results.orders().front().cancellation_reason ==
              std::optional{OrderCancellationReason::execution_risk});
    }
    CHECK(results.fills().empty());
    CHECK(!results.equity_curve().empty());
    if (!results.equity_curve().empty()) {
        CHECK(near(results.equity_curve().back().equity, 1000.0));
    }
}

class UnfillableLimitStrategy final : public Strategy {
public:
    void on_bar(StrategyContext& context, const Bar&) override {
        if (done_) {
            return;
        }
        auto request = market_request("SPY", OrderSide::buy, 1);
        request.type = OrderType::limit;
        request.limit_price = PriceGrid::from_tick_size(0.01).canonicalize(1.0);
        static_cast<void>(context.submit_order(std::move(request)));
        done_ = true;
    }

private:
    bool done_{false};
};

void outstanding_orders_cancel_at_end_without_liquidation() {
    const auto data = one_symbol_dataset({
        bar("SPY", 0, 100.0, 100.0),
        bar("SPY", 1, 101.0, 101.0),
    });
    const auto results = engine().run(
        data, std::make_unique<UnfillableLimitStrategy>());
    CHECK(results.orders().size() == 1);
    if (results.orders().size() == 1) {
        CHECK(results.orders().front().status == OrderStatus::canceled);
        CHECK(results.orders().front().cancellation_reason ==
              std::optional{OrderCancellationReason::end_of_data});
    }
    CHECK(results.fills().empty());
}

[[nodiscard]] ValidatedDataset multi_dataset(const bool reverse_streams) {
    DatasetInput input{
        DatasetMetadata::create(
            1h,
            Currency::usd(),
            PriceAdjustmentMode::unadjusted,
            CorporateActionCoverage::action_free,
            "shares",
            "multi-symbol"),
        {instrument("SPY"), instrument("QQQ")},
        {
            BarStream{Symbol{"SPY"}, {
                bar("SPY", 0, 100.0, 100.0),
                bar("SPY", 1, 100.0, 100.0)}},
            BarStream{Symbol{"QQQ"}, {
                bar("QQQ", 0, 100.0, 100.0),
                bar("QQQ", 1, 100.0, 100.0),
                bar("QQQ", 2, 100.0, 100.0)}},
        },
    };
    if (reverse_streams) {
        std::swap(input.streams[0], input.streams[1]);
    }
    return qte::market_data::preflight(std::move(input));
}

struct CompetingState final {
    bool saw_complete_batch{true};
    std::vector<std::string> callback_order;
};

class CompetingStrategy final : public Strategy {
public:
    explicit CompetingStrategy(CompetingState& state) : state_(state) {}

    void on_bar(StrategyContext& context, const Bar& value) override {
        if (value.start_time != Timestamp{0h}) {
            return;
        }
        state_.callback_order.push_back(value.symbol.value());
        state_.saw_complete_batch = state_.saw_complete_batch &&
            context.history(Symbol{"QQQ"}).size() == 1 &&
            context.history(Symbol{"SPY"}).size() == 1;
        static_cast<void>(context.submit_order(
            market_request(value.symbol.value().c_str(), OrderSide::buy, 10)));
    }

private:
    CompetingState& state_;
};

void multisymbol_priority_results_and_manifest_are_reproducible() {
    CompetingState first_state;
    CompetingState second_state;
    const auto first = engine().run(
        multi_dataset(false), std::make_unique<CompetingStrategy>(first_state));
    const auto second = engine().run(
        multi_dataset(true), std::make_unique<CompetingStrategy>(second_state));

    CHECK(first_state.saw_complete_batch);
    CHECK(first_state.callback_order == std::vector<std::string>({"QQQ", "SPY"}));
    CHECK(first.orders().size() == 2);
    if (first.orders().size() == 2) {
        CHECK(first.orders()[0].request.symbol == Symbol{"QQQ"});
        CHECK(first.orders()[0].status == OrderStatus::filled);
        CHECK(first.orders()[1].request.symbol == Symbol{"SPY"});
        CHECK(first.orders()[1].status == OrderStatus::rejected);
        CHECK(first.orders()[1].rejection_reason ==
              std::optional{OrderRejectionReason::risk});
    }
    CHECK(first.fills().size() == 1);
    CHECK(first.manifest().dataset_hash == second.manifest().dataset_hash);
    CHECK(first.manifest().dataset_hash_algorithm == "fnv1a64-v1");
    CHECK(first.manifest().source_id == "multi-symbol");
    CHECK(first.manifest().execution_model == "open_only_v1");
    CHECK(first.manifest().bar_schema_version == 1);
    CHECK(first.manifest().random_seed == 42);
    CHECK(first.manifest().build_identity == "test-build");
    CHECK(first.manifest().dataset_gap_count == 0);
    CHECK(!first.manifest().normalized_config.empty());
    CHECK(first.manifest().normalized_config == second.manifest().normalized_config);
    CHECK(first.equity_curve().size() == second.equity_curve().size());
    if (first.equity_curve().size() == second.equity_curve().size()) {
        for (std::size_t index = 0; index < first.equity_curve().size(); ++index) {
            CHECK(first.equity_curve()[index].timestamp ==
                  second.equity_curve()[index].timestamp);
            CHECK(first.equity_curve()[index].equity ==
                  second.equity_curve()[index].equity);
        }
    }
}

void final_positions_expose_stale_mark_time_and_open_trades_stay_open() {
    const auto results = engine().run(
        multi_dataset(false), std::make_unique<BuyOnceStrategy>(1));
    const auto position = std::find_if(
        results.positions().begin(),
        results.positions().end(),
        [](const auto& value) { return value.symbol == Symbol{"SPY"}; });
    CHECK(position != results.positions().end());
    if (position != results.positions().end()) {
        CHECK(position->quantity.value() == 1);
        CHECK(position->mark_timestamp == std::optional{Timestamp{2h}});
    }
    CHECK(!results.equity_curve().empty());
    if (!results.equity_curve().empty()) {
        CHECK(results.equity_curve().back().timestamp == Timestamp{3h});
    }
    CHECK(results.trades().empty());
    CHECK(results.open_trades().size() == 1);
    if (results.open_trades().size() == 1) {
        CHECK(results.open_trades().front().symbol() == Symbol{"SPY"});
    }
}

class ThrowingStrategy final : public Strategy {
public:
    void on_bar(StrategyContext&, const Bar&) override {
        throw std::runtime_error("deliberate strategy failure");
    }
};

void strategy_exceptions_abort_the_run() {
    const auto data = one_symbol_dataset({bar("SPY", 0, 100.0, 100.0)});
    CHECK_THROWS_AS(
        engine().run(data, std::make_unique<ThrowingStrategy>()),
        std::runtime_error);
}

}  // namespace

int main() {
    one_symbol_replay_respects_phases_and_next_open_execution();
    cancellation_before_open_and_unknown_cancel_are_audited();
    execution_gap_can_cancel_an_accepted_order_without_a_fill();
    outstanding_orders_cancel_at_end_without_liquidation();
    multisymbol_priority_results_and_manifest_are_reproducible();
    final_positions_expose_stale_mark_time_and_open_trades_stay_open();
    strategy_exceptions_abort_the_run();

    if (failures != 0) {
        std::cerr << failures << " backtest test(s) failed\n";
        return EXIT_FAILURE;
    }
    return EXIT_SUCCESS;
}
