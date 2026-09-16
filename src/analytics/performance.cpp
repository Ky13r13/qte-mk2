#include "qte/analytics/performance.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <limits>
#include <numeric>
#include <stdexcept>
#include <utility>

namespace qte::analytics {
namespace {

[[nodiscard]] Metric unavailable(const char* reason) {
    return Metric::undefined(reason);
}

[[nodiscard]] double mean(const std::vector<double>& values) {
    return std::accumulate(values.begin(), values.end(), 0.0) /
           static_cast<double>(values.size());
}

[[nodiscard]] std::optional<double> sample_deviation(
    const std::vector<double>& values) {
    if (values.size() < 2) {
        return std::nullopt;
    }
    const auto average = mean(values);
    double squared_sum = 0.0;
    for (const auto value : values) {
        const auto difference = value - average;
        squared_sum += difference * difference;
    }
    return std::sqrt(squared_sum / static_cast<double>(values.size() - 1));
}

[[nodiscard]] bool finite_positive(const double value) {
    return std::isfinite(value) && value > 0.0;
}

}  // namespace

Metric Metric::defined(const double value) {
    if (!std::isfinite(value)) {
        throw std::invalid_argument("defined metric must be finite");
    }
    return Metric{value, {}};
}

Metric Metric::undefined(std::string reason) {
    if (reason.empty()) {
        throw std::invalid_argument("undefined metric requires a reason");
    }
    return Metric{std::nullopt, std::move(reason)};
}

PerformanceReport analyze(
    const engine::BacktestResults& results,
    const std::optional<AnnualizationConfig> annualization) {
    const auto& curve = results.equity_curve();
    if (curve.empty()) {
        throw std::invalid_argument("analytics requires at least one equity point");
    }

    std::vector<double> returns;
    returns.reserve(curve.size() - 1);
    bool return_series_valid = true;
    for (std::size_t index = 1; index < curve.size(); ++index) {
        if (!finite_positive(curve[index - 1].equity)) {
            return_series_valid = false;
            break;
        }
        const auto value = curve[index].equity / curve[index - 1].equity - 1.0;
        if (!std::isfinite(value)) {
            return_series_valid = false;
            break;
        }
        returns.push_back(value);
    }
    if (!return_series_valid) {
        returns.clear();
    }

    Metric total_return = unavailable("starting equity must be positive");
    if (finite_positive(curve.front().equity)) {
        total_return = Metric::defined(
            curve.back().equity / curve.front().equity - 1.0);
    }

    Metric maximum_drawdown = unavailable("equity requires a positive running peak");
    double peak = curve.front().equity;
    double maximum_loss = 0.0;
    bool drawdown_valid = finite_positive(peak);
    for (const auto& point : curve) {
        if (!std::isfinite(point.equity) || !finite_positive(peak)) {
            drawdown_valid = false;
            break;
        }
        peak = std::max(peak, point.equity);
        maximum_loss = std::max(maximum_loss, 1.0 - point.equity / peak);
    }
    if (drawdown_valid) {
        maximum_drawdown = Metric::defined(maximum_loss);
    }

    double winning_sum = 0.0;
    double losing_sum = 0.0;
    std::size_t winning_count = 0;
    std::size_t losing_count = 0;
    double episode_sum = 0.0;
    for (const auto& trade : results.trades()) {
        const auto pnl = trade.net_realized_pnl();
        episode_sum += pnl;
        if (pnl > 0.0) {
            winning_sum += pnl;
            ++winning_count;
        } else if (pnl < 0.0) {
            losing_sum += pnl;
            ++losing_count;
        }
    }
    const auto trade_count = results.trades().size();
    const auto win_rate = trade_count == 0
        ? unavailable("win rate requires at least one closed trade")
        : Metric::defined(static_cast<double>(winning_count) /
                          static_cast<double>(trade_count));
    const auto profit_factor = losing_count == 0
        ? unavailable("profit factor requires at least one losing trade")
        : Metric::defined(winning_sum / std::abs(losing_sum));
    const auto average_win = winning_count == 0
        ? unavailable("average winning trade requires a winning trade")
        : Metric::defined(winning_sum / static_cast<double>(winning_count));
    const auto average_loss = losing_count == 0
        ? unavailable("average losing trade requires a losing trade")
        : Metric::defined(losing_sum / static_cast<double>(losing_count));
    const auto expectancy = trade_count == 0
        ? unavailable("expectancy requires at least one closed trade")
        : Metric::defined(episode_sum / static_cast<double>(trade_count));

    double filled_notional = 0.0;
    for (const auto& fill : results.fills()) {
        filled_notional += std::abs(fill.gross_notional());
    }
    const auto turnover = finite_positive(curve.front().equity)
        ? Metric::defined(filled_notional / curve.front().equity)
        : unavailable("turnover requires positive initial equity");

    Metric average_exposure = unavailable("average exposure requires positive elapsed time");
    double weighted_exposure = 0.0;
    std::chrono::nanoseconds elapsed{0};
    bool exposure_valid = true;
    for (std::size_t index = 1; index < curve.size(); ++index) {
        const auto duration = curve[index].timestamp - curve[index - 1].timestamp;
        if (duration < std::chrono::nanoseconds::zero() ||
            !finite_positive(curve[index - 1].equity) ||
            !std::isfinite(curve[index - 1].gross_exposure)) {
            exposure_valid = false;
            break;
        }
        weighted_exposure +=
            (curve[index - 1].gross_exposure / curve[index - 1].equity) *
            static_cast<double>(duration.count());
        elapsed += duration;
    }
    if (exposure_valid && elapsed > std::chrono::nanoseconds::zero()) {
        average_exposure = Metric::defined(
            weighted_exposure / static_cast<double>(elapsed.count()));
    }

    auto annual_return = unavailable("annualization was not requested");
    auto volatility = unavailable("annualization was not requested");
    auto sharpe = unavailable("annualization was not requested");
    auto sortino = unavailable("annualization was not requested");
    auto calmar = unavailable("annualization was not requested");
    if (annualization.has_value()) {
        const auto periods = annualization->periods_per_year;
        const auto risk_free = annualization->annual_risk_free_rate;
        if (!finite_positive(periods) || !std::isfinite(risk_free) || risk_free <= -1.0) {
            throw std::invalid_argument("invalid annualization configuration");
        }
        bool equally_spaced = curve.size() >= 2;
        auto spacing = curve.size() >= 2
            ? curve[1].timestamp - curve[0].timestamp
            : std::chrono::nanoseconds::zero();
        equally_spaced = equally_spaced && spacing > std::chrono::nanoseconds::zero();
        for (std::size_t index = 2; index < curve.size(); ++index) {
            equally_spaced = equally_spaced &&
                curve[index].timestamp - curve[index - 1].timestamp == spacing;
        }
        if (!equally_spaced) {
            annual_return = unavailable("annualization requires equally spaced positive timestamps");
            volatility = unavailable("annualization requires equally spaced positive timestamps");
            sharpe = unavailable("annualization requires equally spaced positive timestamps");
            sortino = unavailable("annualization requires equally spaced positive timestamps");
            calmar = unavailable("annualization requires equally spaced positive timestamps");
        } else if (!return_series_valid) {
            annual_return = unavailable("annualization requires positive prior equity");
            volatility = unavailable("annualization requires positive prior equity");
            sharpe = unavailable("annualization requires positive prior equity");
            sortino = unavailable("annualization requires positive prior equity");
            calmar = unavailable("annualization requires positive prior equity");
        } else {
            const auto elapsed_years =
                static_cast<double>((curve.back().timestamp - curve.front().timestamp).count()) /
                (365.25 * 24.0 * 60.0 * 60.0 * 1.0e9);
            if (finite_positive(curve.front().equity) &&
                finite_positive(curve.back().equity) && elapsed_years > 0.0) {
                annual_return = Metric::defined(std::pow(
                    curve.back().equity / curve.front().equity,
                    1.0 / elapsed_years) - 1.0);
            } else {
                annual_return = unavailable("annual return requires positive endpoints and elapsed time");
            }
            const auto deviation = sample_deviation(returns);
            if (deviation.has_value()) {
                volatility = Metric::defined(*deviation * std::sqrt(periods));
                const auto periodic_rf = std::pow(1.0 + risk_free, 1.0 / periods) - 1.0;
                std::vector<double> excess;
                excess.reserve(returns.size());
                for (const auto value : returns) {
                    excess.push_back(value - periodic_rf);
                }
                const auto excess_deviation = sample_deviation(excess);
                if (excess_deviation.has_value() && *excess_deviation > 0.0) {
                    sharpe = Metric::defined(
                        mean(excess) / *excess_deviation * std::sqrt(periods));
                } else {
                    sharpe = unavailable("Sharpe ratio requires nonzero sample deviation");
                }
            } else {
                volatility = unavailable("volatility requires at least two returns");
                sharpe = unavailable("Sharpe ratio requires at least two returns");
            }
            double downside_squared = 0.0;
            for (const auto value : returns) {
                const auto downside = std::min(value, 0.0);
                downside_squared += downside * downside;
            }
            const auto downside_rms = returns.empty()
                ? 0.0
                : std::sqrt(downside_squared / static_cast<double>(returns.size()));
            if (downside_rms > 0.0) {
                sortino = Metric::defined(
                    mean(returns) / downside_rms * std::sqrt(periods));
            } else {
                sortino = unavailable("Sortino ratio requires nonzero downside deviation");
            }
            if (annual_return.value().has_value() &&
                maximum_drawdown.value().has_value() &&
                *maximum_drawdown.value() > 0.0) {
                calmar = Metric::defined(
                    *annual_return.value() / *maximum_drawdown.value());
            } else {
                calmar = unavailable("Calmar ratio requires annual return and nonzero drawdown");
            }
        }
    }

    return PerformanceReport{
        std::move(returns), total_return, maximum_drawdown, trade_count,
        win_rate, profit_factor, average_win, average_loss, expectancy,
        turnover, average_exposure, annual_return, volatility, sharpe,
        sortino, calmar};
}

}  // namespace qte::analytics
