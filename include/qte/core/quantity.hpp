#pragma once

#include <compare>
#include <cstdint>

namespace qte::core {

// Signed whole-share inventory or inventory change. INT64_MIN is deliberately
// excluded so negation and magnitude are always representable.
class ShareQuantity final {
public:
    [[nodiscard]] static ShareQuantity zero() noexcept;
    [[nodiscard]] static ShareQuantity from_raw(std::int64_t value);
    [[nodiscard]] static ShareQuantity from_numeric(double value);

    [[nodiscard]] std::int64_t value() const noexcept { return value_; }
    [[nodiscard]] std::uint64_t magnitude() const noexcept;
    [[nodiscard]] bool is_zero() const noexcept { return value_ == 0; }

    [[nodiscard]] ShareQuantity checked_add(ShareQuantity other) const;
    [[nodiscard]] ShareQuantity checked_subtract(ShareQuantity other) const;
    [[nodiscard]] ShareQuantity negated() const noexcept;

    friend auto operator<=>(const ShareQuantity&, const ShareQuantity&) = default;

private:
    explicit constexpr ShareQuantity(const std::int64_t value) noexcept : value_(value) {}

    std::int64_t value_;
};

// Strictly positive whole-share magnitude used by requests and fills.
class ShareAmount final {
public:
    [[nodiscard]] static ShareAmount from_count(std::int64_t value);
    [[nodiscard]] static ShareAmount from_numeric(double value);

    [[nodiscard]] std::int64_t value() const noexcept { return value_; }
    [[nodiscard]] ShareQuantity as_quantity() const;

    friend auto operator<=>(const ShareAmount&, const ShareAmount&) = default;

private:
    explicit constexpr ShareAmount(const std::int64_t value) noexcept : value_(value) {}

    std::int64_t value_;
};

}  // namespace qte::core
