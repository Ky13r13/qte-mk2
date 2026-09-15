#include "qte/portfolio/position.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>

namespace qte::portfolio {
namespace {

[[nodiscard]] bool same_nonzero_sign(
    const core::ShareQuantity left,
    const core::ShareQuantity right) noexcept {
    return (left.value() > 0 && right.value() > 0) ||
           (left.value() < 0 && right.value() < 0);
}

[[nodiscard]] core::ShareQuantity signed_fill_delta(
    const orders::Fill& fill) {
    const auto magnitude = fill.quantity().as_quantity();
    return fill.side() == orders::OrderSide::buy ? magnitude
                                                  : magnitude.negated();
}

[[nodiscard]] std::optional<double> market_value_for(
    const core::ShareQuantity quantity,
    const std::optional<ValuationMark>& mark) noexcept {
    if (quantity.is_zero()) {
        return 0.0;
    }
    if (!mark.has_value()) {
        return std::nullopt;
    }
    return static_cast<double>(quantity.value()) * mark->price();
}

[[nodiscard]] std::optional<double> unrealized_for(
    const core::ShareQuantity quantity,
    const double average_entry_price,
    const std::optional<ValuationMark>& mark) noexcept {
    if (quantity.is_zero()) {
        return 0.0;
    }
    if (!mark.has_value()) {
        return std::nullopt;
    }
    return static_cast<double>(quantity.value()) *
           (mark->price() - average_entry_price);
}

}  // namespace

ValuationMark ValuationMark::create(
    const market_data::Timestamp effective_at,
    const core::EventSequence sequence,
    const double price) {
    if (!std::isfinite(price) || price <= 0.0) {
        throw InvalidPositionInput("valuation mark must be finite and positive");
    }
    return ValuationMark{effective_at, sequence, price};
}

std::optional<double> Position::market_value() const noexcept {
    return market_value_for(quantity_, valuation_mark_);
}

std::optional<double> Position::unrealized_pnl() const noexcept {
    return unrealized_for(quantity_, average_entry_price_, valuation_mark_);
}

std::optional<double> Position::net_pnl() const noexcept {
    const auto unrealized = unrealized_pnl();
    if (!unrealized.has_value()) {
        return std::nullopt;
    }
    return realized_gross_sum_ + *unrealized - commission_sum_;
}

PositionTransition Position::apply_fill(const orders::Fill& fill) {
    if (fill.symbol() != symbol_) {
        throw InvalidPositionInput("fill symbol does not match position symbol");
    }

    const auto delta = signed_fill_delta(fill);
    const auto new_quantity = quantity_.checked_add(delta);
    const auto previous_quantity = quantity_;
    auto opened_quantity = core::ShareQuantity::zero();
    auto closed_quantity = core::ShareQuantity::zero();
    double new_average_entry_price = average_entry_price_;
    double realized_increment = 0.0;

    if (quantity_.is_zero()) {
        new_average_entry_price = fill.executed_price().value();
        opened_quantity = core::ShareQuantity::from_raw(
            static_cast<std::int64_t>(delta.magnitude()));
    } else if (same_nonzero_sign(quantity_, delta)) {
        const double added_weight =
            static_cast<double>(delta.magnitude()) /
            static_cast<double>(new_quantity.magnitude());
        new_average_entry_price = average_entry_price_ +
            added_weight * (fill.executed_price().value() - average_entry_price_);
        opened_quantity = core::ShareQuantity::from_raw(
            static_cast<std::int64_t>(delta.magnitude()));
    } else {
        const auto closed_amount = std::min(quantity_.magnitude(), delta.magnitude());
        closed_quantity = core::ShareQuantity::from_raw(
            static_cast<std::int64_t>(closed_amount));
        const double direction = quantity_.value() > 0 ? 1.0 : -1.0;
        realized_increment = static_cast<double>(closed_amount) *
            (fill.executed_price().value() - average_entry_price_) * direction;

        if (new_quantity.is_zero()) {
            new_average_entry_price = 0.0;
        } else if (!same_nonzero_sign(quantity_, new_quantity)) {
            new_average_entry_price = fill.executed_price().value();
            opened_quantity = core::ShareQuantity::from_raw(
                static_cast<std::int64_t>(new_quantity.magnitude()));
        }
    }

    if (!std::isfinite(new_average_entry_price) ||
        !std::isfinite(realized_increment)) {
        throw PositionAccountingError("position cost basis or realized PnL overflow");
    }

    double new_realized_sum = realized_gross_sum_;
    double new_realized_compensation = realized_gross_compensation_;
    add_compensated(
        realized_increment, new_realized_sum, new_realized_compensation);

    double new_commission_sum = commission_sum_;
    double new_commission_compensation = commission_compensation_;
    add_compensated(
        fill.commission(), new_commission_sum, new_commission_compensation);

    require_finite_state(
        new_quantity,
        new_average_entry_price,
        new_realized_sum,
        new_commission_sum,
        valuation_mark_);

    quantity_ = new_quantity;
    average_entry_price_ = new_average_entry_price;
    realized_gross_sum_ = new_realized_sum;
    realized_gross_compensation_ = new_realized_compensation;
    commission_sum_ = new_commission_sum;
    commission_compensation_ = new_commission_compensation;
    return PositionTransition{
        previous_quantity,
        new_quantity,
        opened_quantity,
        closed_quantity,
        realized_increment,
    };
}

void Position::mark(ValuationMark mark) {
    if (valuation_mark_.has_value()) {
        if (mark.effective_at() < valuation_mark_->effective_at()) {
            throw InvalidPositionInput("valuation mark timestamp moved backwards");
        }
        if (mark.sequence() <= valuation_mark_->sequence()) {
            throw InvalidPositionInput("valuation mark sequence must increase");
        }
    }

    const std::optional<ValuationMark> prospective_mark{std::move(mark)};
    require_finite_state(
        quantity_,
        average_entry_price_,
        realized_gross_sum_,
        commission_sum_,
        prospective_mark);
    valuation_mark_ = prospective_mark;
}

void Position::add_compensated(
    const double value,
    double& sum,
    double& compensation) {
    const double adjusted = value - compensation;
    const double next_sum = sum + adjusted;
    const double next_compensation = (next_sum - sum) - adjusted;
    if (!std::isfinite(adjusted) || !std::isfinite(next_sum) ||
        !std::isfinite(next_compensation)) {
        throw PositionAccountingError("position cumulative total overflow");
    }
    sum = next_sum;
    compensation = next_compensation;
}

void Position::require_finite_state(
    const core::ShareQuantity quantity,
    const double average_entry_price,
    const double realized_gross_pnl,
    const double commissions_paid,
    const std::optional<ValuationMark>& mark) {
    if (!std::isfinite(realized_gross_pnl) ||
        !std::isfinite(commissions_paid) || commissions_paid < 0.0) {
        throw PositionAccountingError("position cumulative totals are invalid");
    }
    if ((quantity.is_zero() && average_entry_price != 0.0) ||
        (!quantity.is_zero() &&
         (!std::isfinite(average_entry_price) || average_entry_price <= 0.0))) {
        throw PositionAccountingError("position average entry price invariant failed");
    }

    const auto market_value = market_value_for(quantity, mark);
    const auto unrealized = unrealized_for(quantity, average_entry_price, mark);
    if ((market_value.has_value() && !std::isfinite(*market_value)) ||
        (unrealized.has_value() && !std::isfinite(*unrealized))) {
        throw PositionAccountingError("position valuation overflow");
    }
    if (unrealized.has_value()) {
        const double net_pnl = realized_gross_pnl + *unrealized - commissions_paid;
        if (!std::isfinite(net_pnl)) {
            throw PositionAccountingError("position net PnL overflow");
        }
    }
}

}  // namespace qte::portfolio
