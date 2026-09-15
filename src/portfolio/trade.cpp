#include "qte/portfolio/trade.hpp"

#include "qte/portfolio/portfolio.hpp"

#include <cmath>
#include <limits>
#include <stdexcept>
#include <utility>

namespace qte::portfolio {

TradeEpisode TradeEpisode::start(
    market_data::Symbol symbol,
    const PositionDirection direction,
    const orders::Fill& opening_fill,
    const std::uint64_t opened_quantity,
    const double allocated_commission) {
    return TradeEpisode{
        std::move(symbol),
        direction,
        opening_fill,
        opened_quantity,
        allocated_commission,
    };
}

TradeEpisode::TradeEpisode(
    market_data::Symbol symbol,
    const PositionDirection direction,
    const orders::Fill& opening_fill,
    const std::uint64_t opened_quantity,
    const double allocated_commission)
    : symbol_(std::move(symbol)),
      direction_(direction),
      opening_fill_id_(opening_fill.id()),
      opened_at_(opening_fill.effective_at()),
      opening_sequence_(opening_fill.effective_sequence()) {
    add_opened(opened_quantity, allocated_commission);
}

std::optional<TradeOutcome> TradeEpisode::outcome() const noexcept {
    if (!is_closed()) {
        return std::nullopt;
    }
    const double net = net_realized_pnl();
    if (net > 0.0) {
        return TradeOutcome::winning;
    }
    if (net < 0.0) {
        return TradeOutcome::losing;
    }
    return TradeOutcome::breakeven;
}

void TradeEpisode::add_opened(
    const std::uint64_t quantity,
    const double allocated_commission) {
    if (!std::isfinite(allocated_commission) || allocated_commission < 0.0) {
        throw PortfolioAccountingError("trade commission allocation is invalid");
    }
    if (quantity == 0 ||
        quantity > std::numeric_limits<std::uint64_t>::max() - opened_quantity_) {
        throw PortfolioAccountingError("trade opened quantity overflow");
    }
    double commission_sum = commission_sum_;
    double commission_compensation = commission_compensation_;
    add_compensated(
        allocated_commission, commission_sum, commission_compensation);
    if (!std::isfinite(realized_gross_sum_ - commission_sum)) {
        throw PortfolioAccountingError("trade net realized PnL overflow");
    }

    opened_quantity_ += quantity;
    commission_sum_ = commission_sum;
    commission_compensation_ = commission_compensation;
}

void TradeEpisode::add_closed(
    const std::uint64_t quantity,
    const double realized_gross_pnl,
    const double allocated_commission) {
    if (!std::isfinite(allocated_commission) || allocated_commission < 0.0) {
        throw PortfolioAccountingError("trade commission allocation is invalid");
    }
    if (quantity == 0 || quantity > remaining_quantity()) {
        throw PortfolioAccountingError("trade closed quantity is invalid");
    }
    double realized_sum = realized_gross_sum_;
    double realized_compensation = realized_gross_compensation_;
    add_compensated(
        realized_gross_pnl, realized_sum, realized_compensation);
    double commission_sum = commission_sum_;
    double commission_compensation = commission_compensation_;
    add_compensated(
        allocated_commission, commission_sum, commission_compensation);
    if (!std::isfinite(realized_sum - commission_sum)) {
        throw PortfolioAccountingError("trade net realized PnL overflow");
    }

    closed_quantity_ += quantity;
    realized_gross_sum_ = realized_sum;
    realized_gross_compensation_ = realized_compensation;
    commission_sum_ = commission_sum;
    commission_compensation_ = commission_compensation;
}

void TradeEpisode::close(const orders::Fill& closing_fill) {
    if (is_closed() || remaining_quantity() != 0) {
        throw PortfolioAccountingError("trade cannot close with remaining quantity");
    }
    closing_fill_id_ = closing_fill.id();
    closed_at_ = closing_fill.effective_at();
    closing_sequence_ = closing_fill.effective_sequence();
}

void TradeEpisode::add_compensated(
    const double value,
    double& sum,
    double& compensation) {
    if (!std::isfinite(value)) {
        throw PortfolioAccountingError("trade cumulative value is not finite");
    }
    const double adjusted = value - compensation;
    const double next_sum = sum + adjusted;
    const double next_compensation = (next_sum - sum) - adjusted;
    if (!std::isfinite(adjusted) || !std::isfinite(next_sum) ||
        !std::isfinite(next_compensation)) {
        throw PortfolioAccountingError("trade cumulative value overflow");
    }
    sum = next_sum;
    compensation = next_compensation;
}

}  // namespace qte::portfolio
