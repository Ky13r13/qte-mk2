#include "qte/market_data/bar.hpp"

#include <algorithm>
#include <cmath>
#include <sstream>
#include <utility>

namespace qte::market_data {
namespace {

[[nodiscard]] std::string validation_message(
    const std::vector<BarValidationError>& errors) {
    std::ostringstream message;
    message << "invalid bar";

    bool first = true;
    for (const auto& error : errors) {
        message << (first ? ": " : "; ") << error.message;
        first = false;
    }

    return message.str();
}

}  // namespace

Symbol::Symbol(std::string value) : value_(std::move(value)) {
    if (value_.empty()) {
        throw std::invalid_argument("symbol must not be empty");
    }
}

InvalidBar::InvalidBar(std::vector<BarValidationError> errors)
    : std::invalid_argument(validation_message(errors)), errors_(std::move(errors)) {}

std::vector<BarValidationError> validate(const Bar& bar) {
    std::vector<BarValidationError> errors;

    if (bar.end_time <= bar.start_time) {
        errors.push_back({
            BarValidationCode::invalid_interval,
            "end_time must be later than start_time",
        });
    }

    const bool prices_are_finite = std::isfinite(bar.open) &&
                                   std::isfinite(bar.high) &&
                                   std::isfinite(bar.low) &&
                                   std::isfinite(bar.close);
    if (!prices_are_finite) {
        errors.push_back({
            BarValidationCode::non_finite_price,
            "OHLC prices must be finite",
        });
    } else {
        if (bar.open < 0.0 || bar.high < 0.0 || bar.low < 0.0 || bar.close < 0.0) {
            errors.push_back({
                BarValidationCode::negative_price,
                "OHLC prices must be non-negative",
            });
        }

        const double highest_component = std::max({bar.open, bar.low, bar.close});
        const double lowest_component = std::min({bar.open, bar.high, bar.close});
        if (bar.high < highest_component || bar.low > lowest_component) {
            errors.push_back({
                BarValidationCode::inconsistent_ohlc,
                "high and low must bound open and close",
            });
        }
    }

    if (!std::isfinite(bar.volume)) {
        errors.push_back({
            BarValidationCode::non_finite_volume,
            "volume must be finite",
        });
    } else if (bar.volume < 0.0) {
        errors.push_back({
            BarValidationCode::negative_volume,
            "volume must be non-negative",
        });
    }

    return errors;
}

void require_valid(const Bar& bar) {
    auto errors = validate(bar);
    if (!errors.empty()) {
        throw InvalidBar(std::move(errors));
    }
}

std::string_view to_string(const BarValidationCode code) noexcept {
    switch (code) {
        case BarValidationCode::invalid_interval:
            return "invalid_interval";
        case BarValidationCode::non_finite_price:
            return "non_finite_price";
        case BarValidationCode::negative_price:
            return "negative_price";
        case BarValidationCode::inconsistent_ohlc:
            return "inconsistent_ohlc";
        case BarValidationCode::non_finite_volume:
            return "non_finite_volume";
        case BarValidationCode::negative_volume:
            return "negative_volume";
    }

    return "unknown";
}

}  // namespace qte::market_data
