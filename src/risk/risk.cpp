#include "qte/risk/risk.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <map>
#include <set>
#include <string>

namespace qte::risk {
namespace {

struct SymbolRiskState final {
    core::ShareQuantity quantity{core::ShareQuantity::zero()};
    std::optional<double> mark;
    std::int64_t pending_buys{0};
    std::int64_t pending_sells{0};
};

using StateMap = std::map<std::string, SymbolRiskState>;

void require_optional_non_negative(
    const std::optional<double> value,
    const char* const name) {
    if (value.has_value() && (!std::isfinite(*value) || *value < 0.0)) {
        throw InvalidRiskInput(std::string{name} + " must be finite and non-negative");
    }
}

void require_estimate(const double price, const double commission) {
    if (!std::isfinite(price) || price <= 0.0) {
        throw InvalidRiskInput("estimated execution price must be finite and positive");
    }
    if (!std::isfinite(commission) || commission < 0.0) {
        throw InvalidRiskInput("estimated commission must be finite and non-negative");
    }
}

[[nodiscard]] StateMap make_states(const portfolio::PortfolioSnapshot& portfolio) {
    StateMap states;
    for (const auto& position : portfolio.positions()) {
        if (position.mark_price.has_value() &&
            (!std::isfinite(*position.mark_price) || *position.mark_price <= 0.0)) {
            throw InvalidRiskInput("portfolio mark must be finite and positive");
        }
        const auto [unused, inserted] = states.emplace(
            position.symbol.value(),
            SymbolRiskState{position.quantity, position.mark_price, 0, 0});
        static_cast<void>(unused);
        if (!inserted) {
            throw InvalidRiskInput("portfolio snapshot contains duplicate symbols");
        }
    }
    if (states.empty()) {
        throw InvalidRiskInput("portfolio snapshot has no instruments");
    }
    return states;
}

void add_amount(std::int64_t& total, const core::ShareAmount amount) {
    if (amount.value() > std::numeric_limits<std::int64_t>::max() - total) {
        throw RiskCalculationError("pending share quantity overflow");
    }
    total += amount.value();
}

void add_pending(
    StateMap& states,
    const PendingOrderSnapshot& pending,
    const std::int64_t quantity) {
    const auto found = states.find(pending.symbol().value());
    if (found == states.end()) {
        throw InvalidRiskInput("pending order symbol is outside portfolio universe");
    }
    if (quantity <= 0) {
        return;
    }
    const auto amount = core::ShareAmount::from_count(quantity);
    if (pending.side() == orders::OrderSide::buy) {
        add_amount(found->second.pending_buys, amount);
    } else if (pending.side() == orders::OrderSide::sell) {
        add_amount(found->second.pending_sells, amount);
    } else {
        throw InvalidRiskInput("pending order side is unsupported");
    }
}

void require_unique_pending_ids(
    const std::span<const PendingOrderSnapshot> pending_orders) {
    std::set<std::uint64_t> ids;
    for (const auto& pending : pending_orders) {
        if (!ids.insert(pending.order_id().value()).second) {
            throw InvalidRiskInput("pending order IDs must be unique");
        }
    }
}

[[nodiscard]] core::ShareQuantity checked_add(
    const core::ShareQuantity left,
    const core::ShareQuantity right) {
    try {
        return left.checked_add(right);
    } catch (const std::overflow_error&) {
        throw RiskCalculationError("projected share quantity overflow");
    }
}

[[nodiscard]] core::ShareQuantity checked_subtract(
    const core::ShareQuantity left,
    const core::ShareQuantity right) {
    try {
        return left.checked_subtract(right);
    } catch (const std::overflow_error&) {
        throw RiskCalculationError("projected share quantity overflow");
    }
}

[[nodiscard]] double pending_buy_reserve(
    std::span<const PendingOrderSnapshot> pending_orders,
    const std::optional<core::OrderId> executing_order = std::nullopt,
    const std::int64_t executing_remainder = 0) {
    double total = 0.0;
    for (const auto& pending : pending_orders) {
        if (pending.side() != orders::OrderSide::buy) {
            continue;
        }
        const std::int64_t quantity =
            executing_order.has_value() && pending.order_id() == *executing_order
                ? executing_remainder
                : pending.remaining_quantity().value();
        const double reserve =
            static_cast<double>(quantity) * pending.reserved_cost_per_share();
        total += reserve;
        if (!std::isfinite(reserve) || !std::isfinite(total)) {
            throw RiskCalculationError("pending buy reservation overflow");
        }
    }
    return total;
}

[[nodiscard]] bool is_pure_reduction(
    const core::ShareQuantity current,
    const orders::OrderSide side,
    const core::ShareAmount amount) noexcept {
    if (current.is_zero()) {
        return false;
    }
    const bool opposing =
        (current.value() > 0 && side == orders::OrderSide::sell) ||
        (current.value() < 0 && side == orders::OrderSide::buy);
    return opposing && static_cast<std::uint64_t>(amount.value()) <= current.magnitude();
}

[[nodiscard]] RiskDecision reject(
    const RiskRejectionCode code,
    const char* const reason) {
    return RiskDecision::reject(code, reason);
}

[[nodiscard]] RiskDecision check_limits(
    const RiskLimits& limits,
    const StateMap& states,
    const double available_cash,
    const std::optional<double> equity,
    const bool pure_reduction,
    const std::string& reduction_symbol,
    const bool reducing_short) {
    if (limits.cash_floor().has_value() &&
        available_cash < *limits.cash_floor()) {
        return reject(RiskRejectionCode::cash_floor, "cash floor would be breached");
    }

    for (const auto& [symbol, state] : states) {
        static_cast<void>(symbol);
        if ((!state.quantity.is_zero() || state.pending_buys != 0 ||
             state.pending_sells != 0) && !state.mark.has_value()) {
            return reject(
                RiskRejectionCode::missing_positive_mark,
                "position or pending order has no positive reference mark");
        }
    }

    double gross_potential = 0.0;
    for (const auto& [symbol, state] : states) {
        const auto buy_quantity = core::ShareQuantity::from_raw(state.pending_buys);
        const auto sell_quantity = core::ShareQuantity::from_raw(state.pending_sells);
        static_cast<void>(checked_add(state.quantity, buy_quantity));
        const auto potential_short = checked_subtract(state.quantity, sell_quantity);

        const bool permitted_short_reduction =
            pure_reduction && reducing_short && symbol == reduction_symbol &&
            state.pending_sells == 0;
        if (!limits.allow_short() && potential_short.value() < 0 &&
            !permitted_short_reduction) {
            return reject(
                RiskRejectionCode::short_not_allowed,
                "accepted sells could create a short position");
        }
    }

    // A non-reversing reduction is allowed through pre-existing equity,
    // leverage, allocation, or short-policy breaches after cash and short
    // constraints have passed.
    if (pure_reduction) {
        return RiskDecision::approve();
    }
    if (!equity.has_value()) {
        return reject(
            RiskRejectionCode::missing_positive_mark,
            "portfolio equity is unavailable because a position is unmarked");
    }
    if (*equity <= 0.0) {
        return reject(
            RiskRejectionCode::nonpositive_equity,
            "portfolio equity must be positive");
    }

    for (const auto& [symbol, state] : states) {
        static_cast<void>(symbol);
        const auto potential_long = checked_add(
            state.quantity, core::ShareQuantity::from_raw(state.pending_buys));
        const auto potential_short = checked_subtract(
            state.quantity, core::ShareQuantity::from_raw(state.pending_sells));
        const std::uint64_t potential_magnitude =
            std::max(potential_long.magnitude(), potential_short.magnitude());
        if (potential_magnitude == 0) {
            continue;
        }
        if (!state.mark.has_value()) {
            return reject(
                RiskRejectionCode::missing_positive_mark,
                "potential exposure has no positive reference mark");
        }
        const double potential_value =
            static_cast<double>(potential_magnitude) * *state.mark;
        gross_potential += potential_value;
        if (!std::isfinite(potential_value) || !std::isfinite(gross_potential)) {
            throw RiskCalculationError("potential exposure overflow");
        }
        if (limits.max_symbol_allocation().has_value() &&
            potential_value > *equity * *limits.max_symbol_allocation()) {
            return reject(
                RiskRejectionCode::max_symbol_allocation,
                "maximum symbol allocation would be breached");
        }
    }

    if (limits.max_gross_leverage().has_value() &&
        gross_potential > *equity * *limits.max_gross_leverage()) {
        return reject(
            RiskRejectionCode::max_gross_leverage,
            "maximum gross leverage would be breached");
    }
    return RiskDecision::approve();
}

}  // namespace

RiskLimits RiskLimits::create(
    const std::optional<std::int64_t> max_order_quantity,
    const std::optional<double> max_symbol_allocation,
    const std::optional<double> max_gross_leverage,
    const bool allow_short,
    const std::optional<double> cash_floor) {
    std::optional<core::ShareAmount> maximum;
    if (max_order_quantity.has_value()) {
        try {
            maximum = core::ShareAmount::from_count(*max_order_quantity);
        } catch (const std::invalid_argument& error) {
            throw InvalidRiskInput(error.what());
        }
    }
    require_optional_non_negative(max_symbol_allocation, "maximum symbol allocation");
    require_optional_non_negative(max_gross_leverage, "maximum gross leverage");
    if (cash_floor.has_value() && !std::isfinite(*cash_floor)) {
        throw InvalidRiskInput("cash floor must be finite when enabled");
    }
    return RiskLimits{
        maximum,
        max_symbol_allocation,
        max_gross_leverage,
        allow_short,
        cash_floor,
    };
}

PendingOrderSnapshot PendingOrderSnapshot::capture(
    const orders::OrderRecord& order,
    const double estimated_execution_price,
    const double estimated_commission) {
    if (order.status() != orders::OrderStatus::open &&
        order.status() != orders::OrderStatus::partially_filled) {
        throw InvalidRiskInput("pending snapshot requires an open or partially filled order");
    }
    require_estimate(estimated_execution_price, estimated_commission);
    const auto remaining = core::ShareAmount::from_count(
        order.remaining_quantity().value());
    const double fee_per_share =
        estimated_commission / static_cast<double>(remaining.value());
    const double reserved_cost_per_share = estimated_execution_price + fee_per_share;
    if (!std::isfinite(reserved_cost_per_share) || reserved_cost_per_share <= 0.0) {
        throw RiskCalculationError("pending order reservation is not representable");
    }
    return PendingOrderSnapshot{
        order.id(),
        order.request().symbol,
        order.request().side,
        remaining,
        reserved_cost_per_share,
    };
}

RiskDecision RiskDecision::approve() {
    return RiskDecision{true, std::nullopt, {}};
}

RiskDecision RiskDecision::reject(
    const RiskRejectionCode code,
    std::string reason) {
    return RiskDecision{false, code, std::move(reason)};
}

RiskDecision RiskManager::evaluate_submission(
    const orders::OrderRequest& request,
    const market_data::InstrumentSpec& instrument,
    const portfolio::PortfolioSnapshot& portfolio,
    const std::span<const PendingOrderSnapshot> pending_orders,
    const double estimated_execution_price,
    const double estimated_commission) const {
    try {
        orders::require_valid(request, instrument);
    } catch (const orders::InvalidOrderRequest& error) {
        throw InvalidRiskInput(error.what());
    }
    require_estimate(estimated_execution_price, estimated_commission);
    if (limits_.max_order_quantity().has_value() &&
        request.quantity > *limits_.max_order_quantity()) {
        return reject(
            RiskRejectionCode::max_order_quantity,
            "maximum order quantity would be breached");
    }

    auto states = make_states(portfolio);
    require_unique_pending_ids(pending_orders);
    const auto requested = states.find(request.symbol.value());
    if (requested == states.end()) {
        throw InvalidRiskInput("order symbol is outside portfolio universe");
    }
    if (!requested->second.mark.has_value()) {
        return reject(
            RiskRejectionCode::missing_positive_mark,
            "new order has no positive reference mark");
    }

    for (const auto& pending : pending_orders) {
        add_pending(states, pending, pending.remaining_quantity().value());
    }
    if (request.side == orders::OrderSide::buy) {
        add_amount(requested->second.pending_buys, request.quantity);
    } else {
        add_amount(requested->second.pending_sells, request.quantity);
    }

    double available_cash = portfolio.cash() - pending_buy_reserve(pending_orders);
    if (request.side == orders::OrderSide::buy) {
        const double reservation =
            static_cast<double>(request.quantity.value()) * estimated_execution_price +
            estimated_commission;
        available_cash -= reservation;
        if (!std::isfinite(reservation) || !std::isfinite(available_cash)) {
            throw RiskCalculationError("submission cash reservation overflow");
        }
    }
    const bool reduction = is_pure_reduction(
        requested->second.quantity, request.side, request.quantity);
    return check_limits(
        limits_,
        states,
        available_cash,
        portfolio.equity(),
        reduction,
        request.symbol.value(),
        requested->second.quantity.value() < 0);
}

RiskDecision RiskManager::evaluate_execution(
    const execution::FillCandidate& candidate,
    const portfolio::PortfolioSnapshot& portfolio,
    const std::span<const PendingOrderSnapshot> pending_orders) const {
    if (limits_.max_order_quantity().has_value() &&
        candidate.quantity() > *limits_.max_order_quantity()) {
        return reject(
            RiskRejectionCode::max_order_quantity,
            "maximum order quantity would be breached at execution");
    }
    auto states = make_states(portfolio);
    const auto affected = states.find(candidate.symbol().value());
    if (affected == states.end()) {
        throw InvalidRiskInput("fill candidate symbol is outside portfolio universe");
    }
    if (!affected->second.mark.has_value()) {
        return reject(
            RiskRejectionCode::missing_positive_mark,
            "fill candidate has no positive current mark");
    }

    const PendingOrderSnapshot* executing = nullptr;
    for (const auto& pending : pending_orders) {
        if (pending.order_id() == candidate.order_id()) {
            if (executing != nullptr) {
                throw InvalidRiskInput("executing order appears more than once in pending snapshot");
            }
            executing = &pending;
        }
    }
    if (executing == nullptr || executing->symbol() != candidate.symbol() ||
        executing->side() != candidate.side() ||
        candidate.quantity().value() > executing->remaining_quantity().value()) {
        throw InvalidRiskInput("fill candidate does not match its pending order snapshot");
    }

    const auto current_quantity = affected->second.quantity;
    const auto signed_fill = candidate.side() == orders::OrderSide::buy
                                 ? candidate.quantity().as_quantity()
                                 : candidate.quantity().as_quantity().negated();
    affected->second.quantity = checked_add(current_quantity, signed_fill);
    const std::int64_t executing_remainder =
        executing->remaining_quantity().value() - candidate.quantity().value();
    for (const auto& pending : pending_orders) {
        const std::int64_t remaining = pending.order_id() == candidate.order_id()
                                           ? executing_remainder
                                           : pending.remaining_quantity().value();
        add_pending(states, pending, remaining);
    }

    const double signed_notional = candidate.side() == orders::OrderSide::buy
                                       ? -candidate.gross_notional()
                                       : candidate.gross_notional();
    const double projected_cash =
        portfolio.cash() + signed_notional - candidate.commission();
    if (!std::isfinite(projected_cash)) {
        throw RiskCalculationError("fill-time cash projection overflow");
    }
    const double available_cash = projected_cash - pending_buy_reserve(
        pending_orders, candidate.order_id(), executing_remainder);
    if (!std::isfinite(available_cash)) {
        throw RiskCalculationError("fill-time cash reservation overflow");
    }

    double market_value = 0.0;
    bool valuation_available = true;
    for (const auto& [symbol, state] : states) {
        static_cast<void>(symbol);
        if (state.quantity.is_zero()) {
            continue;
        }
        if (!state.mark.has_value()) {
            valuation_available = false;
            continue;
        }
        market_value += static_cast<double>(state.quantity.value()) * *state.mark;
        if (!std::isfinite(market_value)) {
            throw RiskCalculationError("fill-time equity projection overflow");
        }
    }
    const std::optional<double> projected_equity = valuation_available
        ? std::optional{projected_cash + market_value}
        : std::nullopt;
    if (projected_equity.has_value() && !std::isfinite(*projected_equity)) {
        throw RiskCalculationError("fill-time equity projection overflow");
    }

    const bool reduction = is_pure_reduction(
        current_quantity, candidate.side(), candidate.quantity());
    return check_limits(
        limits_,
        states,
        available_cash,
        projected_equity,
        reduction,
        candidate.symbol().value(),
        current_quantity.value() < 0);
}

std::string_view to_string(const RiskRejectionCode code) noexcept {
    switch (code) {
        case RiskRejectionCode::max_order_quantity: return "max_order_quantity";
        case RiskRejectionCode::missing_positive_mark: return "missing_positive_mark";
        case RiskRejectionCode::nonpositive_equity: return "nonpositive_equity";
        case RiskRejectionCode::short_not_allowed: return "short_not_allowed";
        case RiskRejectionCode::cash_floor: return "cash_floor";
        case RiskRejectionCode::max_symbol_allocation: return "max_symbol_allocation";
        case RiskRejectionCode::max_gross_leverage: return "max_gross_leverage";
    }
    return "unknown";
}

}  // namespace qte::risk
