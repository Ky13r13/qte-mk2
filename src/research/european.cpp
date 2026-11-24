#include "qte/research/european.hpp"

#include <algorithm>
#include <cmath>
#include <numbers>
#include <stdexcept>

namespace qte::research {
EuropeanOption european(double forward, double strike, double volatility, double years, bool call) {
    if (!(forward > 0.0 && strike > 0.0 && volatility > 0.0 && years >= 0.0)
        || !std::isfinite(forward) || !std::isfinite(strike)
        || !std::isfinite(volatility) || !std::isfinite(years)) {
        throw std::invalid_argument("Invalid European option inputs");
    }
    if (years == 0.0) {
        return {std::max(0.0, call ? forward - strike : strike - forward), 0.0};
    }
    const double vol_time = volatility * std::sqrt(years);
    const double d1 = (std::log(forward / strike) + 0.5 * volatility * volatility * years) / vol_time;
    const double d2 = d1 - vol_time;
    const auto cdf = [](double x) { return 0.5 * std::erfc(-x / std::numbers::sqrt2); };
    const double price = call ? forward * cdf(d1) - strike * cdf(d2)
                              : strike * cdf(-d2) - forward * cdf(-d1);
    const double gamma = std::exp(-0.5 * d1 * d1)
        / (std::sqrt(2.0 * std::numbers::pi) * forward * vol_time);
    return {std::max(0.0, price), gamma};
}
}
