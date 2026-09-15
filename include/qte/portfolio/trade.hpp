#pragma once

#include "qte/orders/fill.hpp"

#include <cstdint>
#include <optional>

namespace qte::portfolio {

class Portfolio;

enum class PositionDirection {
    long_position,
    short_position,
};

enum class TradeOutcome {
    winning,
    losing,
    breakeven,
};

// One flat-to-flat position episode. Open episodes report realized activity and
// allocated fees to date but have no outcome until closed.
class TradeEpisode final {
public:
    TradeEpisode(const TradeEpisode&) = default;
    TradeEpisode& operator=(const TradeEpisode&) = default;
    TradeEpisode(TradeEpisode&&) noexcept = default;
    TradeEpisode& operator=(TradeEpisode&&) noexcept = default;

    [[nodiscard]] const market_data::Symbol& symbol() const noexcept { return symbol_; }
    [[nodiscard]] PositionDirection direction() const noexcept { return direction_; }
    [[nodiscard]] core::FillId opening_fill_id() const noexcept {
        return opening_fill_id_;
    }
    [[nodiscard]] market_data::Timestamp opened_at() const noexcept {
        return opened_at_;
    }
    [[nodiscard]] core::EventSequence opening_sequence() const noexcept {
        return opening_sequence_;
    }
    [[nodiscard]] const std::optional<core::FillId>& closing_fill_id() const noexcept {
        return closing_fill_id_;
    }
    [[nodiscard]] const std::optional<market_data::Timestamp>& closed_at() const noexcept {
        return closed_at_;
    }
    [[nodiscard]] const std::optional<core::EventSequence>& closing_sequence() const noexcept {
        return closing_sequence_;
    }
    [[nodiscard]] std::uint64_t opened_quantity() const noexcept {
        return opened_quantity_;
    }
    [[nodiscard]] std::uint64_t closed_quantity() const noexcept {
        return closed_quantity_;
    }
    [[nodiscard]] std::uint64_t remaining_quantity() const noexcept {
        return opened_quantity_ - closed_quantity_;
    }
    [[nodiscard]] double realized_gross_pnl() const noexcept {
        return realized_gross_sum_;
    }
    [[nodiscard]] double allocated_commissions() const noexcept {
        return commission_sum_;
    }
    [[nodiscard]] double net_realized_pnl() const noexcept {
        return realized_gross_sum_ - commission_sum_;
    }
    [[nodiscard]] bool is_closed() const noexcept {
        return closing_fill_id_.has_value();
    }
    [[nodiscard]] std::optional<TradeOutcome> outcome() const noexcept;

private:
    friend class Portfolio;

    [[nodiscard]] static TradeEpisode start(
        market_data::Symbol symbol,
        PositionDirection direction,
        const orders::Fill& opening_fill,
        std::uint64_t opened_quantity,
        double allocated_commission);
    TradeEpisode(
        market_data::Symbol symbol,
        PositionDirection direction,
        const orders::Fill& opening_fill,
        std::uint64_t opened_quantity,
        double allocated_commission);

    void add_opened(std::uint64_t quantity, double allocated_commission);
    void add_closed(
        std::uint64_t quantity,
        double realized_gross_pnl,
        double allocated_commission);
    void close(const orders::Fill& closing_fill);
    static void add_compensated(
        double value,
        double& sum,
        double& compensation);

    market_data::Symbol symbol_;
    PositionDirection direction_;
    core::FillId opening_fill_id_;
    market_data::Timestamp opened_at_;
    core::EventSequence opening_sequence_;
    std::optional<core::FillId> closing_fill_id_;
    std::optional<market_data::Timestamp> closed_at_;
    std::optional<core::EventSequence> closing_sequence_;
    std::uint64_t opened_quantity_{0};
    std::uint64_t closed_quantity_{0};
    double realized_gross_sum_{0.0};
    double realized_gross_compensation_{0.0};
    double commission_sum_{0.0};
    double commission_compensation_{0.0};
};

}  // namespace qte::portfolio
