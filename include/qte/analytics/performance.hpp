#pragma once

#include "qte/engine/backtest.hpp"

#include <cstddef>
#include <optional>
#include <string>
#include <vector>

namespace qte::analytics {

class Metric final {
public:
    [[nodiscard]] static Metric defined(double value);
    [[nodiscard]] static Metric undefined(std::string reason);

    [[nodiscard]] const std::optional<double>& value() const noexcept { return value_; }
    [[nodiscard]] const std::string& undefined_reason() const noexcept { return reason_; }

private:
    Metric(std::optional<double> value, std::string reason)
        : value_(value), reason_(std::move(reason)) {}

    std::optional<double> value_;
    std::string reason_;
};

struct AnnualizationConfig final {
    double periods_per_year;
    double annual_risk_free_rate{0.0};
};

struct SamplingConfig final {
    std::vector<market_data::Timestamp> timestamps;
    std::chrono::nanoseconds max_staleness;
};

[[nodiscard]] std::vector<engine::EquityPoint> sample_equity(
    const std::vector<engine::EquityPoint>& events, const SamplingConfig& sampling);

struct PerformanceReport final {
    std::vector<double> returns;
    Metric total_return;
    Metric maximum_drawdown;
    std::size_t trade_count;
    Metric win_rate;
    Metric profit_factor;
    Metric average_winning_trade;
    Metric average_losing_trade;
    Metric expectancy;
    Metric turnover;
    Metric average_gross_exposure;
    Metric annualized_return;
    Metric annualized_volatility;
    Metric sharpe_ratio;
    Metric sortino_ratio;
    Metric calmar_ratio;
};

[[nodiscard]] PerformanceReport analyze(
    const engine::BacktestResults& results,
    std::optional<AnnualizationConfig> annualization = std::nullopt,
    std::optional<SamplingConfig> sampling = std::nullopt);

}  // namespace qte::analytics
