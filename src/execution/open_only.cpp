#include "qte/execution/open_only.hpp"

#include <cmath>
#include <string>
#include <utility>

namespace qte::execution {
namespace {

constexpr double kBasisPointsPerUnit = 10'000.0;

void require_non_negative_finite(const double value, const char* const name) {
    if (!std::isfinite(value) || value < 0.0) {
        throw InvalidExecutionInput(
            std::string{name} + " must be finite and non-negative");
    }
}

[[nodiscard]] bool is_fillable(const orders::OrderStatus status) noexcept {
    return status == orders::OrderStatus::open ||
           status == orders::OrderStatus::partially_filled;
}

[[nodiscard]] bool stop_is_reached(
    const orders::OrderSide side,
    const double open,
    const core::TickPrice stop) noexcept {
    return side == orders::OrderSide::buy ? open >= stop.value()
                                          : open <= stop.value();
}

[[nodiscard]] bool limit_is_satisfied(
    const orders::OrderSide side,
    const double price,
    const core::TickPrice limit) noexcept {
    return side == orders::OrderSide::buy ? price <= limit.value()
                                          : price >= limit.value();
}

}  // namespace

ExecutionCosts ExecutionCosts::create(
    const double commission_bps,
    const double spread_bps,
    const double slippage_bps) {
    require_non_negative_finite(commission_bps, "commission basis points");
    require_non_negative_finite(spread_bps, "spread basis points");
    require_non_negative_finite(slippage_bps, "slippage basis points");
    return ExecutionCosts{commission_bps, spread_bps, slippage_bps};
}

MarketOpen MarketOpen::create(
    market_data::Symbol symbol,
    const market_data::Timestamp timestamp,
    const core::EventSequence sequence,
    const double price) {
    if (!std::isfinite(price) || price <= 0.0) {
        throw InvalidExecutionInput("market open price must be finite and positive");
    }
    return MarketOpen{std::move(symbol), timestamp, sequence, price};
}

MarketOpen::MarketOpen(
    market_data::Symbol symbol,
    const market_data::Timestamp timestamp,
    const core::EventSequence sequence,
    const double price)
    : symbol_(std::move(symbol)),
      timestamp_(timestamp),
      sequence_(sequence),
      price_(price) {}

std::optional<FillCandidate> OpenOnlyExecutionModel::evaluate(
    orders::OrderRecord& order,
    const market_data::InstrumentSpec& instrument,
    const MarketOpen& market_open) const {
    if (!is_fillable(order.status())) {
        throw InvalidExecutionInput("order must be open or partially filled");
    }
    if (order.request().symbol != instrument.symbol()) {
        throw InvalidExecutionInput("order symbol does not match instrument metadata");
    }
    if (market_open.symbol() != instrument.symbol()) {
        throw InvalidExecutionInput("market-open symbol does not match instrument metadata");
    }
    if (market_open.timestamp() < order.submitted_at()) {
        throw InvalidExecutionInput("market open cannot precede order submission time");
    }
    if (market_open.sequence() <= order.eligible_after_sequence()) {
        return std::nullopt;
    }

    const auto& request = order.request();
    if (request.side != orders::OrderSide::buy &&
        request.side != orders::OrderSide::sell) {
        throw InvalidExecutionInput("order side is unsupported");
    }
    bool requires_limit = false;
    bool trigger_now = false;
    switch (request.type) {
        case orders::OrderType::market:
            break;
        case orders::OrderType::limit:
            if (!request.limit_price.has_value()) {
                throw InvalidExecutionInput("limit order has no limit price");
            }
            requires_limit = true;
            break;
        case orders::OrderType::stop:
            if (!request.stop_price.has_value()) {
                throw InvalidExecutionInput("stop order has no stop price");
            }
            if (!order.stop_triggered()) {
                if (!stop_is_reached(
                        request.side, market_open.price(), *request.stop_price)) {
                    return std::nullopt;
                }
                trigger_now = true;
            }
            break;
        case orders::OrderType::stop_limit:
            if (!request.stop_price.has_value() ||
                !request.limit_price.has_value()) {
                throw InvalidExecutionInput(
                    "stop-limit order requires stop and limit prices");
            }
            if (!order.stop_triggered()) {
                if (!stop_is_reached(
                        request.side, market_open.price(), *request.stop_price)) {
                    return std::nullopt;
                }
                trigger_now = true;
            }
            requires_limit = true;
            break;
        default:
            throw UnsupportedExecutionOrder("order type is unsupported");
    }

    if (requires_limit &&
        !limit_is_satisfied(
            request.side, market_open.price(), *request.limit_price)) {
        if (trigger_now) {
            static_cast<void>(order.mark_stop_triggered());
        }
        return std::nullopt;
    }

    const auto estimate = this->estimate(
        request.side,
        core::ShareAmount::from_count(order.remaining_quantity().value()),
        instrument,
        market_open.price());
    const core::TickPrice executed_price = estimate.executed_price();

    if (requires_limit &&
        !limit_is_satisfied(request.side, executed_price.value(), *request.limit_price)) {
        if (trigger_now) {
            static_cast<void>(order.mark_stop_triggered());
        }
        return std::nullopt;
    }

    const auto quantity = core::ShareAmount::from_count(
        order.remaining_quantity().value());
    FillCandidate candidate{
        order.id(),
        request.symbol,
        request.side,
        quantity,
        market_open.timestamp(),
        market_open.sequence(),
        market_open.price(),
        executed_price,
        estimate.gross_notional(),
        estimate.commission(),
    };
    if (trigger_now) {
        static_cast<void>(order.mark_stop_triggered());
    }
    return candidate;
}

ExecutionEstimate OpenOnlyExecutionModel::estimate(
    const orders::OrderSide side,
    const core::ShareAmount quantity,
    const market_data::InstrumentSpec& instrument,
    const double reference_price) const {
    if (!std::isfinite(reference_price) || reference_price <= 0.0) {
        throw InvalidExecutionInput("execution reference price must be finite and positive");
    }
    if (side != orders::OrderSide::buy && side != orders::OrderSide::sell) {
        throw InvalidExecutionInput("execution side is unsupported");
    }
    const double adverse_bps = costs_.spread_bps() / 2.0 + costs_.slippage_bps();
    const double adverse_fraction = adverse_bps / kBasisPointsPerUnit;
    const double side_multiplier = side == orders::OrderSide::buy
                                       ? 1.0 + adverse_fraction
                                       : 1.0 - adverse_fraction;
    const double adjusted_price = reference_price * side_multiplier;
    if (!std::isfinite(adjusted_price) || adjusted_price <= 0.0) {
        throw ExecutionCalculationError(
            "cost-adjusted execution price is not finite and positive");
    }

    const core::TickPrice executed_price = [&] {
        try {
            return side == orders::OrderSide::buy
                       ? instrument.price_grid().round_up(adjusted_price)
                       : instrument.price_grid().round_down(adjusted_price);
        } catch (const std::invalid_argument&) {
            throw ExecutionCalculationError(
                "cost-adjusted execution price cannot be represented on the tick grid");
        } catch (const std::out_of_range&) {
            throw ExecutionCalculationError(
                "cost-adjusted execution price cannot be represented on the tick grid");
        }
    }();

    const double gross_notional =
        static_cast<double>(quantity.value()) * executed_price.value();
    const double commission =
        gross_notional * (costs_.commission_bps() / kBasisPointsPerUnit);
    if (!std::isfinite(gross_notional) || gross_notional <= 0.0 ||
        !std::isfinite(commission) || commission < 0.0) {
        throw ExecutionCalculationError(
            "execution notional or commission is outside the supported range");
    }

    return ExecutionEstimate{executed_price, gross_notional, commission};
}

}  // namespace qte::execution
