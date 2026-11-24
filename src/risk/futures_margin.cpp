#include "qte/risk/futures_margin.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <set>
#include <utility>

namespace qte::risk {
namespace {

void require_identity(const std::string& value, const char* const name) {
    if (value.empty() || value.size() > 256 || value.front() == ' ' ||
        value.back() == ' ' || !std::all_of(value.begin(), value.end(), [](const unsigned char c) {
            return c >= 0x20 && c <= 0x7e;
        })) {
        throw InvalidFuturesMarginInput(
            std::string{name} + " must be nonempty trimmed printable ASCII at most 256 characters");
    }
}

void require_money(const double value, const char* const name, const bool positive) {
    if (!std::isfinite(value) || (positive ? value <= 0.0 : value < 0.0)) {
        throw InvalidFuturesMarginInput(
            std::string{name} + (positive ? " must be finite and positive"
                                         : " must be finite and non-negative"));
    }
}

[[nodiscard]] std::uint64_t add_quantity(
    const std::uint64_t left, const std::uint64_t right) {
    if (right > std::numeric_limits<std::uint64_t>::max() - left) {
        throw FuturesMarginCalculationError("pending futures quantity overflow");
    }
    return left + right;
}

[[nodiscard]] double add_money(const double left, const double right) {
    const double result = left + right;
    if (!std::isfinite(result)) {
        throw FuturesMarginCalculationError("futures margin calculation overflow");
    }
    return result;
}

[[nodiscard]] double multiply_money(const std::uint64_t quantity, const double rate) {
    const double result = static_cast<double>(quantity) * rate;
    if (!std::isfinite(result)) {
        throw FuturesMarginCalculationError("futures margin calculation overflow");
    }
    return result;
}

[[nodiscard]] std::uint64_t projected_magnitude(
    const std::int64_t current, const std::uint64_t amount, const bool buy) {
    const auto magnitude = current < 0
        ? static_cast<std::uint64_t>(-(current + 1)) + 1
        : static_cast<std::uint64_t>(current);
    const bool same_direction = (buy && current >= 0) || (!buy && current <= 0);
    return same_direction ? add_quantity(magnitude, amount)
                          : amount >= magnitude ? amount - magnitude : magnitude - amount;
}

[[nodiscard]] bool nonreversing_reduction(
    const orders::OrderRecord& order, const core::ShareQuantity inventory,
    const std::span<const PendingFuturesOrder> other_pending) {
    if (inventory.is_zero()) return false;
    const auto closing_side = inventory.value() > 0 ? orders::OrderSide::sell
                                                    : orders::OrderSide::buy;
    if (order.request().side != closing_side) return false;
    auto reserved = order.remaining_quantity().magnitude();
    for (const auto& pending : other_pending) {
        if (pending.symbol() != order.request().symbol) continue;
        if (pending.side() != closing_side) return false;
        reserved = add_quantity(reserved, pending.remaining_quantity().as_quantity().magnitude());
    }
    return reserved <= inventory.magnitude();
}

[[nodiscard]] bool fresh_mark(
    const portfolio::Position& position, const market_data::Timestamp now,
    const std::chrono::nanoseconds maximum_age) {
    const auto& mark = position.valuation_mark();
    if (!mark || mark->price() <= 0.0 || mark->effective_at() > now) return false;
    const auto age = static_cast<std::uint64_t>(now.time_since_epoch().count()) -
                     static_cast<std::uint64_t>(mark->effective_at().time_since_epoch().count());
    return age <= static_cast<std::uint64_t>(maximum_age.count());
}

}  // namespace

FuturesMarginSchedule::FuturesMarginSchedule(
    core::Currency valuation_currency, std::string source_id,
    std::string vintage_id, const market_data::Timestamp reference_at,
    const market_data::Timestamp available_at,
    const market_data::Timestamp valid_until,
    std::vector<FuturesMarginRate> rates)
    : valuation_currency_(std::move(valuation_currency)),
      source_id_(std::move(source_id)), vintage_id_(std::move(vintage_id)),
      reference_at_(reference_at), available_at_(available_at),
      valid_until_(valid_until), rates_(std::move(rates)) {}

FuturesMarginSchedule FuturesMarginSchedule::create(
    core::Currency valuation_currency, std::string source_id,
    std::string vintage_id, const market_data::Timestamp reference_at,
    const market_data::Timestamp available_at,
    const market_data::Timestamp valid_until,
    std::vector<FuturesMarginRate> rates,
    const std::vector<market_data::InstrumentSpec>& universe) {
    require_identity(source_id, "source_id");
    require_identity(vintage_id, "vintage_id");
    if (reference_at > available_at || available_at >= valid_until) {
        throw InvalidFuturesMarginInput(
            "margin timestamps must satisfy reference <= available < valid_until");
    }
    if (universe.empty() || rates.empty()) {
        throw InvalidFuturesMarginInput("futures margin schedule requires a nonempty universe");
    }
    std::map<std::string, const market_data::InstrumentSpec*> instruments;
    for (const auto& instrument : universe) {
        if (instrument.kind() != market_data::InstrumentKind::future ||
            instrument.quote_currency() != valuation_currency ||
            !instruments.emplace(instrument.symbol().value(), &instrument).second) {
            throw InvalidFuturesMarginInput(
                "margin universe must contain unique futures in the valuation currency");
        }
    }
    std::set<std::string> qualified;
    for (const auto& rate : rates) {
        require_money(rate.initial_per_contract, "initial margin", true);
        require_money(rate.maintenance_per_contract, "maintenance margin", true);
        require_money(rate.one_way_fee_per_contract, "one-way fee", false);
        if (rate.initial_per_contract < rate.maintenance_per_contract) {
            throw InvalidFuturesMarginInput(
                "initial margin must be at least maintenance margin");
        }
        const auto found = instruments.find(rate.symbol.value());
        if (found == instruments.end() || rate.currency != valuation_currency ||
            rate.contract_multiplier <= 0 ||
            rate.contract_multiplier != found->second->contract_multiplier() ||
            !qualified.insert(rate.symbol.value()).second) {
            throw InvalidFuturesMarginInput(
                "margin rate must uniquely qualify a declared futures instrument");
        }
    }
    if (qualified.size() != instruments.size()) {
        throw InvalidFuturesMarginInput(
            "margin schedule must qualify every declared futures instrument");
    }
    return FuturesMarginSchedule{
        std::move(valuation_currency), std::move(source_id), std::move(vintage_id),
        reference_at, available_at, valid_until, std::move(rates)};
}

PendingFuturesOrder PendingFuturesOrder::capture(const orders::OrderRecord& order) {
    if (order.status() != orders::OrderStatus::open &&
        order.status() != orders::OrderStatus::partially_filled) {
        throw InvalidFuturesMarginInput(
            "pending futures order must be accepted and nonterminal");
    }
    if (order.remaining_quantity().value() <= 0) {
        throw InvalidFuturesMarginInput(
            "pending futures order must have positive remaining quantity");
    }
    return PendingFuturesOrder{
        order.id(), order.request().symbol, order.request().side,
        core::ShareAmount::from_count(order.remaining_quantity().value()),
        order.submitted_at()};
}

PendingFuturesOrder PendingFuturesOrder::submission_candidate(
    const orders::OrderRecord& order) {
    return PendingFuturesOrder{
        order.id(), order.request().symbol, order.request().side,
        core::ShareAmount::from_count(order.remaining_quantity().value()),
        order.submitted_at()};
}

FuturesMarginEvaluator::FuturesMarginEvaluator(
    FuturesMarginSchedule schedule, const double cash_buffer,
    const std::chrono::nanoseconds max_mark_age)
    : schedule_(std::move(schedule)), cash_buffer_(cash_buffer),
      max_mark_age_(max_mark_age) {
    require_money(cash_buffer_, "cash buffer", false);
    if (max_mark_age_.count() < 0) {
        throw InvalidFuturesMarginInput("maximum mark age must be non-negative");
    }
    for (const auto& rate : schedule_.rates()) {
        rates_.emplace(rate.symbol.value(), rate);
    }
}

FuturesMarginReport FuturesMarginEvaluator::evaluate(
    const portfolio::Portfolio& ledger,
    const std::span<const PendingFuturesOrder> pending_orders,
    const market_data::Timestamp now) const {
    return evaluate_snapshot(ledger, ledger.snapshot(), pending_orders, now);
}

FuturesMarginReport FuturesMarginEvaluator::evaluate_snapshot(
    const portfolio::Portfolio& ledger, const portfolio::PortfolioSnapshot& snapshot,
    const std::span<const PendingFuturesOrder> pending_orders,
    const market_data::Timestamp now) const {
    if (now < schedule_.available_at() || now >= schedule_.valid_until()) {
        throw InvalidFuturesMarginInput("margin schedule is unavailable or expired");
    }
    if (ledger.valuation_currency() != schedule_.valuation_currency() ||
        ledger.instrument_count() != rates_.size()) {
        throw InvalidFuturesMarginInput("portfolio does not match margin schedule universe");
    }
    if (ledger.last_event_at().has_value() && *ledger.last_event_at() > now) {
        throw InvalidFuturesMarginInput(
            "margin evaluation cannot precede the portfolio's latest event");
    }
    if (!std::isfinite(snapshot.cash()) || !snapshot.equity().has_value() ||
        !std::isfinite(*snapshot.equity())) {
        throw InvalidFuturesMarginInput("portfolio cash and equity must be available and finite");
    }

    struct State final {
        std::int64_t quantity{};
        std::uint64_t buys{};
        std::uint64_t sells{};
    };
    std::map<std::string, State> states;
    double current_initial = 0.0;
    double current_maintenance = 0.0;
    for (const auto& position : snapshot.positions()) {
        const auto rate = rates_.find(position.symbol.value());
        const auto* owned = ledger.position(position.symbol);
        if (rate == rates_.end() || owned == nullptr ||
            position.instrument_kind != market_data::InstrumentKind::future ||
            owned->kind() != market_data::InstrumentKind::future ||
            owned->contract_multiplier() != rate->second.contract_multiplier ||
            !states.emplace(position.symbol.value(), State{position.quantity.value()}).second) {
            throw InvalidFuturesMarginInput("portfolio position is not qualified by margin schedule");
        }
        if (!position.quantity.is_zero()) {
            if (!position.mark_price.has_value() || !position.mark_timestamp.has_value() ||
                !std::isfinite(*position.mark_price) || *position.mark_price <= 0.0 ||
                *position.mark_timestamp > now) {
                throw InvalidFuturesMarginInput(
                    "nonzero futures position requires a visible positive finite mark");
            }
            const auto now_count = now.time_since_epoch().count();
            const auto mark_count = position.mark_timestamp->time_since_epoch().count();
            const auto age = static_cast<std::uint64_t>(now_count) -
                             static_cast<std::uint64_t>(mark_count);
            if (age > static_cast<std::uint64_t>(max_mark_age_.count())) {
                throw InvalidFuturesMarginInput("futures position mark is stale");
            }
        }
        const auto quantity = position.quantity.magnitude();
        current_initial = add_money(
            current_initial, multiply_money(quantity, rate->second.initial_per_contract));
        current_maintenance = add_money(
            current_maintenance,
            multiply_money(quantity, rate->second.maintenance_per_contract));
    }
    if (states.size() != rates_.size()) {
        throw InvalidFuturesMarginInput("portfolio is missing a scheduled futures position");
    }

    std::set<std::uint64_t> order_ids;
    for (const auto& pending : pending_orders) {
        if (!order_ids.insert(pending.order_id().value()).second) {
            throw InvalidFuturesMarginInput("pending futures order IDs must be unique");
        }
        if (pending.submitted_at() > now) {
            throw InvalidFuturesMarginInput("pending futures order is not yet submitted");
        }
        const auto rate = rates_.find(pending.symbol().value());
        auto state = states.find(pending.symbol().value());
        if (rate == rates_.end() || state == states.end()) {
            throw InvalidFuturesMarginInput("pending futures order symbol is unknown");
        }
        const auto quantity = static_cast<std::uint64_t>(
            pending.remaining_quantity().value());
        if (pending.side() == orders::OrderSide::buy) {
            state->second.buys = add_quantity(state->second.buys, quantity);
        } else if (pending.side() == orders::OrderSide::sell) {
            state->second.sells = add_quantity(state->second.sells, quantity);
        } else {
            throw InvalidFuturesMarginInput("pending futures order side is unsupported");
        }
    }

    double projected_initial = 0.0;
    double pending_fees = 0.0;
    for (const auto& [symbol, state] : states) {
        const auto& rate = rates_.at(symbol);
        const auto current = state.quantity < 0
            ? static_cast<std::uint64_t>(-(state.quantity + 1)) + 1
            : static_cast<std::uint64_t>(state.quantity);
        const auto bought = projected_magnitude(state.quantity, state.buys, true);
        const auto sold = projected_magnitude(state.quantity, state.sells, false);
        const auto worst = std::max({current, bought, sold});
        if (worst > static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max())) {
            throw FuturesMarginCalculationError(
                "projected futures quantity exceeds canonical range");
        }
        projected_initial = add_money(
            projected_initial, multiply_money(worst, rate.initial_per_contract));
        pending_fees = add_money(
            pending_fees,
            multiply_money(add_quantity(state.buys, state.sells),
                           rate.one_way_fee_per_contract));
    }
    const double additional = projected_initial - current_initial;
    if (!std::isfinite(additional) || additional < 0.0) {
        throw FuturesMarginCalculationError("projected futures margin is inconsistent");
    }
    const double collateral = std::min(snapshot.cash(), *snapshot.equity());
    const double available = collateral - projected_initial - pending_fees - cash_buffer_;
    const double maintenance_excess = collateral - current_maintenance - cash_buffer_;
    if (!std::isfinite(collateral) || !std::isfinite(available) ||
        !std::isfinite(maintenance_excess)) {
        throw FuturesMarginCalculationError("futures collateral calculation overflow");
    }
    return FuturesMarginReport{
        current_initial, current_maintenance, projected_initial, additional,
        pending_fees, collateral, available, maintenance_excess,
        available < 0.0, maintenance_excess < 0.0};
}

FuturesMarginAdmission FuturesMarginEvaluator::evaluate_submission(
    const orders::OrderRecord& candidate,
    const portfolio::Portfolio& ledger,
    const std::span<const PendingFuturesOrder> pending_orders,
    const market_data::Timestamp now) const {
    if (candidate.status() != orders::OrderStatus::new_order) {
        throw InvalidFuturesMarginInput("futures submission candidate must be new");
    }
    const auto* instrument = ledger.instrument(candidate.request().symbol);
    if (instrument == nullptr || instrument->kind() != market_data::InstrumentKind::future) {
        throw InvalidFuturesMarginInput("futures submission instrument is unknown or unsupported");
    }
    orders::require_valid(candidate.request(), *instrument);
    if (candidate.submitted_at() > now) {
        throw InvalidFuturesMarginInput("futures submission candidate is not yet submitted");
    }
    for (const auto& pending : pending_orders) {
        if (pending.order_id() == candidate.id()) {
            throw InvalidFuturesMarginInput(
                "futures submission candidate duplicates a pending order");
        }
    }

    // A missing target mark is a typed, short-circuit denial, including when
    // flat. Other schedule/ledger/reservation errors are validated below once
    // the target is priceable; no projection is returned for this denial.
    const auto* position = ledger.position(candidate.request().symbol);
    if (!position || !fresh_mark(*position, now, max_mark_age_)) {
        return {FuturesAdmissionCode::missing_fresh_mark, std::nullopt};
    }

    // This validates the schedule, ledger universe and clock, and every existing
    // reservation before candidate-specific margin policy is considered.
    static_cast<void>(evaluate(ledger, pending_orders, now));
    if (!instrument->last_trade().has_value() || now >= *instrument->last_trade()) {
        return FuturesMarginAdmission{FuturesAdmissionCode::contract_closed, std::nullopt};
    }

    std::vector<PendingFuturesOrder> projected_orders{
        pending_orders.begin(), pending_orders.end()};
    projected_orders.push_back(PendingFuturesOrder::submission_candidate(candidate));
    const auto projected = evaluate(ledger, projected_orders, now);
    if (!projected.initial_breach && !projected.maintenance_breach) {
        return FuturesMarginAdmission{FuturesAdmissionCode::approved, projected};
    }

    const bool protected_reduction = nonreversing_reduction(
        candidate, position->quantity(), pending_orders);
    if (protected_reduction) {
        return FuturesMarginAdmission{FuturesAdmissionCode::reduction_only, projected};
    }
    return FuturesMarginAdmission{
        projected.maintenance_breach ? FuturesAdmissionCode::maintenance_margin
                                     : FuturesAdmissionCode::initial_margin,
        projected};
}

FuturesMarginAdmission FuturesMarginEvaluator::evaluate_fill(
    const orders::OrderRecord& order, const FuturesAdmissionCode submission_code,
    const orders::Fill& proposed, const portfolio::Portfolio& ledger,
    const std::span<const PendingFuturesOrder> other_pending) const {
    if (submission_code != FuturesAdmissionCode::approved &&
        submission_code != FuturesAdmissionCode::reduction_only) {
        throw InvalidFuturesMarginInput("proposed fill requires an approved submission");
    }
    const auto* instrument = ledger.instrument(order.request().symbol);
    if (!instrument || instrument->kind() != market_data::InstrumentKind::future) {
        throw InvalidFuturesMarginInput("proposed fill must reference a ledger future");
    }
    orders::require_valid(order.request(), *instrument);
    orders::require_valid(proposed, order, *instrument);
    for (const auto& pending : other_pending) {
        if (pending.order_id() == order.id()) {
            throw InvalidFuturesMarginInput("other reservations include the proposed fill order");
        }
    }

    const auto now = proposed.effective_at();
    const auto& position = *ledger.position(instrument->symbol());
    if (!fresh_mark(position, now, max_mark_age_)) {
        return {FuturesAdmissionCode::missing_fresh_mark, std::nullopt};
    }
    std::vector<PendingFuturesOrder> remaining(other_pending.begin(), other_pending.end());
    remaining.push_back(PendingFuturesOrder::capture(order));
    const auto before = evaluate(ledger, remaining, now);
    if (now >= instrument->last_trade().value()) {
        return {FuturesAdmissionCode::contract_closed, std::nullopt};
    }
    remaining.pop_back();
    const auto rest = order.remaining_quantity().value() - proposed.quantity().value();
    if (rest > 0) {
        remaining.push_back(PendingFuturesOrder{
            order.id(), order.request().symbol, order.request().side,
            core::ShareAmount::from_count(rest), order.submitted_at()});
    }
    const auto projected = evaluate_snapshot(ledger, ledger.preview_fill(proposed), remaining, now);
    const bool reducing = nonreversing_reduction(order, position.quantity(), other_pending);
    if (submission_code == FuturesAdmissionCode::reduction_only && !reducing) {
        return {FuturesAdmissionCode::reduction_conflict, projected};
    }
    if ((before.maintenance_breach || before.initial_breach) && !reducing) {
        return {before.maintenance_breach ? FuturesAdmissionCode::maintenance_margin
                                          : FuturesAdmissionCode::initial_margin, projected};
    }
    if (!projected.initial_breach && !projected.maintenance_breach) {
        return {FuturesAdmissionCode::approved, projected};
    }
    if (reducing) return {FuturesAdmissionCode::reduction_only, projected};
    return {projected.maintenance_breach ? FuturesAdmissionCode::maintenance_margin
                                         : FuturesAdmissionCode::initial_margin, projected};
}

}  // namespace qte::risk
