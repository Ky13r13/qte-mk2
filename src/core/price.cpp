#include "qte/core/price.hpp"

#include <cmath>
#include <stdexcept>
#include <string>

namespace qte::core {
namespace {

constexpr double kAlignmentToleranceInTicks = 1e-8;
constexpr double kMaximumExactTickCount = 9'007'199'254'740'991.0;  // 2^53 - 1

void require_positive_finite(const double value, const char* const name) {
    if (!std::isfinite(value) || value <= 0.0) {
        throw std::invalid_argument(std::string{name} + " must be finite and positive");
    }
}

}  // namespace

PriceGrid PriceGrid::from_tick_size(const double tick_size) {
    require_positive_finite(tick_size, "tick size");
    return PriceGrid{tick_size};
}

TickPrice PriceGrid::canonicalize(const double price) const {
    require_positive_finite(price, "price");

    const double raw_tick_count = price / tick_size_;
    if (!std::isfinite(raw_tick_count) || raw_tick_count > kMaximumExactTickCount) {
        throw std::out_of_range("price exceeds the exact tick-count range");
    }

    const double nearest_tick_count = std::round(raw_tick_count);
    if (nearest_tick_count < 1.0 ||
        std::abs(price - nearest_tick_count * tick_size_) >
            kAlignmentToleranceInTicks * tick_size_) {
        throw std::invalid_argument("price is not aligned to the tick grid");
    }
    return from_tick_count(nearest_tick_count);
}

TickPrice PriceGrid::round_up(const double price) const {
    return round(price, true);
}

TickPrice PriceGrid::round_down(const double price) const {
    return round(price, false);
}

TickPrice PriceGrid::round(const double price, const bool upward) const {
    require_positive_finite(price, "price");

    double raw_tick_count = price / tick_size_;
    if (!std::isfinite(raw_tick_count) || raw_tick_count > kMaximumExactTickCount) {
        throw std::out_of_range("price exceeds the exact tick-count range");
    }

    const double nearest_tick_count = std::round(raw_tick_count);
    if (nearest_tick_count >= 1.0 &&
        std::abs(price - nearest_tick_count * tick_size_) <=
            kAlignmentToleranceInTicks * tick_size_) {
        raw_tick_count = nearest_tick_count;
    }

    const double rounded_tick_count = upward ? std::ceil(raw_tick_count)
                                             : std::floor(raw_tick_count);
    if (rounded_tick_count < 1.0) {
        throw std::invalid_argument("rounded price must remain positive");
    }
    if (rounded_tick_count > kMaximumExactTickCount) {
        throw std::out_of_range("rounded price exceeds the exact tick-count range");
    }
    return from_tick_count(rounded_tick_count);
}

TickPrice PriceGrid::from_tick_count(const double tick_count) const {
    const double canonical_price = tick_count * tick_size_;
    if (!std::isfinite(canonical_price) || canonical_price <= 0.0) {
        throw std::out_of_range("canonical price is outside the supported range");
    }
    return TickPrice{canonical_price};
}

}  // namespace qte::core
