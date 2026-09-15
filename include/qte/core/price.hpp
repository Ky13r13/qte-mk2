#pragma once

namespace qte::core {

// A positive price canonicalized to an instrument's configured tick grid.
class TickPrice final {
public:
    [[nodiscard]] double value() const noexcept { return value_; }

    friend bool operator==(const TickPrice&, const TickPrice&) = default;

private:
    friend class PriceGrid;
    explicit constexpr TickPrice(const double value) noexcept : value_(value) {}

    double value_;
};

class PriceGrid final {
public:
    [[nodiscard]] static PriceGrid from_tick_size(double tick_size);

    [[nodiscard]] double tick_size() const noexcept { return tick_size_; }

    // Accepts a positive price only when it is on-grid within representation
    // tolerance, and returns the canonical grid value.
    [[nodiscard]] TickPrice canonicalize(double price) const;

    // Adverse rounding for future synthesized execution prices.
    [[nodiscard]] TickPrice round_up(double price) const;
    [[nodiscard]] TickPrice round_down(double price) const;

    friend bool operator==(const PriceGrid&, const PriceGrid&) = default;

private:
    explicit constexpr PriceGrid(const double tick_size) noexcept : tick_size_(tick_size) {}

    [[nodiscard]] TickPrice round(double price, bool upward) const;
    [[nodiscard]] TickPrice from_tick_count(double tick_count) const;

    double tick_size_;
};

}  // namespace qte::core
