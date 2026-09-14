#include "qte/core/quantity.hpp"

#include <cmath>
#include <limits>
#include <stdexcept>

namespace qte::core {
namespace {

constexpr auto kMaximum = std::numeric_limits<std::int64_t>::max();
constexpr auto kMinimum = -kMaximum;
constexpr double kExclusiveDoubleLimit = 9'223'372'036'854'775'808.0;

[[nodiscard]] std::int64_t checked_numeric_conversion(const double value) {
    if (!std::isfinite(value)) {
        throw std::invalid_argument("share quantity must be finite");
    }
    if (std::trunc(value) != value) {
        throw std::invalid_argument("share quantity must be a whole number");
    }
    if (value <= -kExclusiveDoubleLimit || value >= kExclusiveDoubleLimit) {
        throw std::out_of_range("share quantity is outside the supported range");
    }
    return static_cast<std::int64_t>(value);
}

}  // namespace

ShareQuantity ShareQuantity::zero() noexcept {
    return ShareQuantity{0};
}

ShareQuantity ShareQuantity::from_raw(const std::int64_t value) {
    if (value == std::numeric_limits<std::int64_t>::min()) {
        throw std::out_of_range("INT64_MIN is not a supported share quantity");
    }
    return ShareQuantity{value};
}

ShareQuantity ShareQuantity::from_numeric(const double value) {
    return from_raw(checked_numeric_conversion(value));
}

std::uint64_t ShareQuantity::magnitude() const noexcept {
    const auto magnitude = value_ < 0 ? -value_ : value_;
    return static_cast<std::uint64_t>(magnitude);
}

ShareQuantity ShareQuantity::checked_add(const ShareQuantity other) const {
    if ((other.value_ > 0 && value_ > kMaximum - other.value_) ||
        (other.value_ < 0 && value_ < kMinimum - other.value_)) {
        throw std::overflow_error("share quantity addition overflow");
    }
    return ShareQuantity{value_ + other.value_};
}

ShareQuantity ShareQuantity::checked_subtract(const ShareQuantity other) const {
    return checked_add(other.negated());
}

ShareQuantity ShareQuantity::negated() const noexcept {
    return ShareQuantity{-value_};
}

ShareAmount ShareAmount::from_count(const std::int64_t value) {
    if (value <= 0) {
        throw std::invalid_argument("share amount must be positive");
    }
    return ShareAmount{value};
}

ShareAmount ShareAmount::from_numeric(const double value) {
    return from_count(checked_numeric_conversion(value));
}

ShareQuantity ShareAmount::as_quantity() const {
    return ShareQuantity::from_raw(value_);
}

}  // namespace qte::core
