#include "qte/orders/fill.hpp"

#include <cmath>
#include <cstdint>
#include <limits>
#include <sstream>
#include <utility>

namespace qte::orders {
namespace {

[[nodiscard]] bool valid_side(const OrderSide side) noexcept {
    switch (side) {
        case OrderSide::buy:
        case OrderSide::sell:
            return true;
    }
    return false;
}

[[nodiscard]] std::string validation_message(
    const std::vector<FillValidationError>& errors) {
    std::ostringstream message;
    message << "invalid fill";
    bool first = true;
    for (const auto& error : errors) {
        message << (first ? ": " : "; ") << error.message;
        first = false;
    }
    return message.str();
}

}  // namespace

Fill Fill::create(
    const core::FillId id,
    const core::OrderId order_id,
    market_data::Symbol symbol,
    const OrderSide side,
    const core::ShareAmount quantity,
    const market_data::Timestamp effective_at,
    const core::EventSequence effective_sequence,
    const double reference_open,
    const core::TickPrice executed_price,
    const double commission) {
    if (!valid_side(side)) {
        throw std::invalid_argument("fill side is unsupported");
    }
    if (!std::isfinite(reference_open) || reference_open <= 0.0) {
        throw std::invalid_argument("reference open must be finite and positive");
    }
    if (!std::isfinite(commission) || commission < 0.0) {
        throw std::invalid_argument("commission must be finite and non-negative");
    }

    const double gross_notional =
        static_cast<double>(quantity.value()) * executed_price.value();
    if (!std::isfinite(gross_notional) || gross_notional <= 0.0) {
        throw std::out_of_range("fill gross notional is outside the supported range");
    }

    return Fill{
        id,
        order_id,
        std::move(symbol),
        side,
        quantity,
        effective_at,
        effective_sequence,
        reference_open,
        executed_price,
        gross_notional,
        commission,
    };
}

Fill::Fill(
    const core::FillId id,
    const core::OrderId order_id,
    market_data::Symbol symbol,
    const OrderSide side,
    const core::ShareAmount quantity,
    const market_data::Timestamp effective_at,
    const core::EventSequence effective_sequence,
    const double reference_open,
    const core::TickPrice executed_price,
    const double gross_notional,
    const double commission)
    : id_(id),
      order_id_(order_id),
      symbol_(std::move(symbol)),
      side_(side),
      quantity_(quantity),
      effective_at_(effective_at),
      effective_sequence_(effective_sequence),
      reference_open_(reference_open),
      executed_price_(executed_price),
      gross_notional_(gross_notional),
      commission_(commission) {}

InvalidFill::InvalidFill(std::vector<FillValidationError> errors)
    : std::invalid_argument(validation_message(errors)), errors_(std::move(errors)) {}

std::vector<FillValidationError> validate(
    const Fill& fill,
    const OrderRecord& order,
    const market_data::InstrumentSpec& instrument) {
    std::vector<FillValidationError> errors;

    if (order.status() != OrderStatus::open &&
        order.status() != OrderStatus::partially_filled) {
        errors.push_back({
            FillValidationCode::order_not_fillable,
            "order is not open for fills",
        });
    }
    if (fill.order_id() != order.id()) {
        errors.push_back({
            FillValidationCode::order_id_mismatch,
            "fill order ID does not match order",
        });
    }
    if (fill.symbol() != order.request().symbol) {
        errors.push_back({
            FillValidationCode::symbol_mismatch,
            "fill symbol does not match order",
        });
    }
    if (fill.side() != order.request().side) {
        errors.push_back({
            FillValidationCode::side_mismatch,
            "fill side does not match order",
        });
    }
    if (fill.symbol() != instrument.symbol()) {
        errors.push_back({
            FillValidationCode::instrument_symbol_mismatch,
            "fill symbol does not match instrument metadata",
        });
    }
    try {
        static_cast<void>(
            instrument.price_grid().canonicalize(fill.executed_price().value()));
    } catch (const std::invalid_argument&) {
        errors.push_back({
            FillValidationCode::execution_price_not_on_instrument_grid,
            "executed price is not valid on the instrument tick grid",
        });
    } catch (const std::out_of_range&) {
        errors.push_back({
            FillValidationCode::execution_price_not_on_instrument_grid,
            "executed price is not valid on the instrument tick grid",
        });
    }
    if (fill.effective_sequence() <= order.eligible_after_sequence()) {
        errors.push_back({
            FillValidationCode::effective_sequence_not_after_eligibility,
            "fill sequence must be after the order eligibility sequence",
        });
    }
    if (fill.effective_at() < order.submitted_at()) {
        errors.push_back({
            FillValidationCode::effective_timestamp_before_submission,
            "fill timestamp cannot precede order submission",
        });
    }
    if (fill.quantity().value() > order.remaining_quantity().value()) {
        errors.push_back({
            FillValidationCode::quantity_exceeds_remaining,
            "fill quantity exceeds the remaining order quantity",
        });
    }

    return errors;
}

void require_valid(
    const Fill& fill,
    const OrderRecord& order,
    const market_data::InstrumentSpec& instrument) {
    auto errors = validate(fill, order, instrument);
    if (!errors.empty()) {
        throw InvalidFill(std::move(errors));
    }
}

bool FillJournal::contains(const core::FillId id) const noexcept {
    return id.value() <= fills_.size();
}

void FillJournal::commit(
    OrderRecord& order,
    const Fill& fill,
    const market_data::InstrumentSpec& instrument) {
    const auto committed = static_cast<std::uint64_t>(fills_.size());
    if (fill.id().value() <= committed) {
        throw DuplicateFill("fill ID has already been committed");
    }
    if (committed == std::numeric_limits<std::uint64_t>::max() ||
        fill.id().value() != committed + 1) {
        throw OutOfSequenceFill("fill ID is not the next journal sequence");
    }

    require_valid(fill, order, instrument);

    fills_.push_back(fill);
    try {
        order.record_fill_quantity(fill.quantity());
    } catch (...) {
        fills_.pop_back();
        throw;
    }
}

}  // namespace qte::orders
