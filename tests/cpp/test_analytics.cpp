#include "qte/analytics/performance.hpp"

#include <chrono>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <optional>
#include <string_view>
#include <utility>
#include <vector>

namespace {

using namespace std::chrono_literals;
using namespace qte;

int failures = 0;

void check(const bool condition, const std::string_view expression, const int line) {
    if (!condition) {
        std::cerr << "line " << line << ": check failed: " << expression << '\n';
        ++failures;
    }
}

#define CHECK(expression) check((expression), #expression, __LINE__)

[[nodiscard]] bool near(const double left, const double right) {
    return std::abs(left - right) <= 1.0e-9 * std::max(1.0, std::abs(right));
}

[[nodiscard]] market_data::Bar bar(
    const std::int64_t hour,
    const double open) {
    return market_data::Bar{
        market_data::Symbol{"SPY"},
        market_data::Timestamp{std::chrono::hours{hour}},
        market_data::Timestamp{std::chrono::hours{hour + 1}},
        open, open + 1.0, open - 1.0, open, 1000.0};
}

[[nodiscard]] market_data::ValidatedDataset dataset() {
    const auto instrument = market_data::InstrumentSpec{
        market_data::Symbol{"SPY"}, core::Currency::usd(),
        core::PriceGrid::from_tick_size(0.01)};
    return market_data::preflight(market_data::DatasetInput{
        market_data::DatasetMetadata::create(
            1h, core::Currency::usd(),
            market_data::PriceAdjustmentMode::unadjusted,
            market_data::CorporateActionCoverage::action_free,
            "shares", "analytics-fixture"),
        {instrument},
        {market_data::BarStream{
            market_data::Symbol{"SPY"},
            {bar(0, 100.0), bar(1, 110.0), bar(2, 120.0)}}},
    });
}

[[nodiscard]] orders::OrderRequest request(const orders::OrderSide side) {
    return orders::OrderRequest{
        .symbol = market_data::Symbol{"SPY"},
        .side = side,
        .quantity = core::ShareAmount::from_count(5),
        .type = orders::OrderType::market,
        .limit_price = std::nullopt,
        .stop_price = std::nullopt,
        .time_in_force = orders::TimeInForce::good_til_canceled,
    };
}

class RoundTrip final : public strategy::Strategy {
public:
    void on_bar(strategy::StrategyContext& context, const market_data::Bar&) override {
        if (!bought_) {
            static_cast<void>(context.submit_order(request(orders::OrderSide::buy)));
            bought_ = true;
        }
    }

    void on_fill(strategy::StrategyContext& context, const orders::Fill& fill) override {
        if (fill.side() == orders::OrderSide::buy) {
            static_cast<void>(context.submit_order(request(orders::OrderSide::sell)));
        }
    }

private:
    bool bought_{false};
};

[[nodiscard]] engine::BacktestResults replay() {
    return engine::BacktestEngine{engine::BacktestConfig::create(1000.0)}.run(
        dataset(), std::make_unique<RoundTrip>());
}

void unannualized_metrics_are_hand_calculated() {
    const auto results = replay();
    const auto report = analytics::analyze(results);
    CHECK(report.returns.size() == 4);
    CHECK(report.total_return.value().has_value());
    CHECK(report.maximum_drawdown.value().has_value());
    CHECK(report.turnover.value().has_value());
    CHECK(report.average_gross_exposure.value().has_value());
    if (report.total_return.value().has_value()) {
        CHECK(near(*report.total_return.value(), 0.05));
    }
    if (report.maximum_drawdown.value().has_value()) {
        CHECK(near(*report.maximum_drawdown.value(), 0.0));
    }
    if (report.turnover.value().has_value()) {
        CHECK(near(*report.turnover.value(), 1.15));
    }
    if (report.average_gross_exposure.value().has_value()) {
        CHECK(near(*report.average_gross_exposure.value(), 0.55 / 3.0));
    }
    CHECK(report.trade_count == 1);
    CHECK(report.win_rate.value() == std::optional{1.0});
    CHECK(!report.profit_factor.value().has_value());
    CHECK(report.average_winning_trade.value() == std::optional{50.0});
    CHECK(!report.average_losing_trade.value().has_value());
    CHECK(report.expectancy.value() == std::optional{50.0});
    CHECK(!report.annualized_return.value().has_value());
}

void annualized_metrics_require_an_explicit_regular_grid() {
    const auto base = replay();
    auto regular = engine::BacktestResults{
        {
            {market_data::Timestamp{0ns}, core::EventSequence{1}, 100.0, 0.0},
            {market_data::Timestamp{24h}, core::EventSequence{2}, 110.0, 0.0},
            {market_data::Timestamp{48h}, core::EventSequence{3}, 99.0, 0.0},
        },
        base.orders(), base.order_events(), base.fills(), base.trades(),
        base.open_trades(), base.positions(), base.manifest()};
    const auto report = analytics::analyze(
        regular, analytics::AnnualizationConfig{252.0, 0.0});
    CHECK(report.annualized_volatility.value().has_value());
    CHECK(report.sharpe_ratio.value().has_value());
    CHECK(report.sortino_ratio.value().has_value());
    CHECK(report.annualized_return.value().has_value());
    CHECK(report.calmar_ratio.value().has_value());
    if (report.annualized_volatility.value().has_value()) {
        CHECK(near(*report.annualized_volatility.value(), 0.2 * std::sqrt(126.0)));
    }
    if (report.sharpe_ratio.value().has_value()) {
        CHECK(near(*report.sharpe_ratio.value(), 0.0));
    }
    if (report.sortino_ratio.value().has_value()) {
        CHECK(near(*report.sortino_ratio.value(), 0.0));
    }

    const auto irregular = analytics::analyze(
        base, analytics::AnnualizationConfig{252.0, 0.0});
    CHECK(!irregular.annualized_return.value().has_value());
    CHECK(irregular.annualized_return.undefined_reason().find("equally spaced") !=
          std::string::npos);
}

void zero_trade_and_open_trade_cases_stay_undefined() {
    class BuyOnly final : public strategy::Strategy {
    public:
        void on_bar(strategy::StrategyContext& context, const market_data::Bar&) override {
            if (!done_) {
                static_cast<void>(context.submit_order(request(orders::OrderSide::buy)));
                done_ = true;
            }
        }
    private:
        bool done_{false};
    };
    const auto results = engine::BacktestEngine{
        engine::BacktestConfig::create(1000.0)}.run(
            dataset(), std::make_unique<BuyOnly>());
    const auto report = analytics::analyze(results);
    CHECK(report.trade_count == 0);
    CHECK(results.open_trades().size() == 1);
    CHECK(!report.win_rate.value().has_value());
    CHECK(!report.expectancy.value().has_value());
}

void explicit_samples_resolve_duplicate_timestamps_and_gaps() {
    const auto results = replay();
    const analytics::SamplingConfig sampling{
        {market_data::Timestamp{0h}, market_data::Timestamp{1h},
         market_data::Timestamp{2h}, market_data::Timestamp{3h}}, 0ns};
    const auto points = analytics::sample_equity(results.equity_curve(), sampling);
    CHECK(points.size() == 4);
    const auto report = analytics::analyze(results, analytics::AnnualizationConfig{8766}, sampling);
    CHECK(report.returns.size() == 3);
    CHECK(report.annualized_volatility.value().has_value());
    const std::vector<engine::EquityPoint> events{
        {market_data::Timestamp{0h}, core::EventSequence{1}, 100, 0},
        {market_data::Timestamp{0h}, core::EventSequence{2}, 101, 0},
        {market_data::Timestamp{2h}, core::EventSequence{3}, 105, 0}};
    const analytics::SamplingConfig gaps{
        {market_data::Timestamp{0h},market_data::Timestamp{1h},market_data::Timestamp{2h}},1h};
    const auto carried = analytics::sample_equity(events,gaps);
    CHECK(carried.size() == 3);
    if (carried.size() == 3) {
        CHECK(carried[0].equity == 101);
        CHECK(carried[1].equity == 101);
        CHECK(carried[2].equity == 105);
    }
    bool rejected = false;
    try {
        static_cast<void>(analytics::sample_equity(events, {gaps.timestamps,0ns}));
    } catch (const std::invalid_argument&) { rejected = true; }
    CHECK(rejected);
    const engine::BacktestResults short_run{
        {{market_data::Timestamp{0ns},core::EventSequence{1},100,0},
         {market_data::Timestamp{1ns},core::EventSequence{2},101,0}},
        {},{},{},{},{},{},results.manifest()};
    const auto overflow = analytics::analyze(short_run, analytics::AnnualizationConfig{252});
    CHECK(!overflow.annualized_return.value().has_value());
    CHECK(overflow.annualized_return.undefined_reason() == "annualized return exceeds numeric range");
}

}  // namespace

int main() {
    unannualized_metrics_are_hand_calculated();
    annualized_metrics_require_an_explicit_regular_grid();
    zero_trade_and_open_trade_cases_stay_undefined();
    explicit_samples_resolve_duplicate_timestamps_and_gaps();
    if (failures != 0) {
        std::cerr << failures << " analytics test(s) failed\n";
        return EXIT_FAILURE;
    }
    return EXIT_SUCCESS;
}
