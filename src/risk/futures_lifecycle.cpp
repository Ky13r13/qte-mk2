#include "qte/risk/futures_lifecycle.hpp"

#include <utility>

namespace qte::risk {
namespace {

[[nodiscard]] bool same_fill(const orders::Fill& left, const orders::Fill& right) noexcept {
    return left.id() == right.id() && left.order_id() == right.order_id() &&
           left.symbol() == right.symbol() && left.side() == right.side() &&
           left.quantity() == right.quantity() &&
           left.effective_at() == right.effective_at() &&
           left.effective_sequence() == right.effective_sequence() &&
           left.reference_open() == right.reference_open() &&
           left.executed_price() == right.executed_price() &&
           left.commission() == right.commission() &&
           left.contract_multiplier() == right.contract_multiplier();
}

}  // namespace

FuturesOrderLifecycle::FuturesOrderLifecycle(
    portfolio::Portfolio ledger, FuturesMarginEvaluator evaluator)
    : ledger_(std::move(ledger)), evaluator_(std::move(evaluator)) {
    // Constructing the evaluator alone does not tie it to this ledger. Check
    // the universe and currency once so later errors concern events, not setup.
    if (ledger_.valuation_currency() != evaluator_.schedule().valuation_currency() ||
        ledger_.instrument_count() != evaluator_.schedule().rates().size()) {
        throw InvalidFuturesMarginInput("lifecycle ledger does not match margin schedule universe");
    }
    for (const auto& rate : evaluator_.schedule().rates()) {
        const auto* instrument = ledger_.instrument(rate.symbol);
        if (instrument == nullptr ||
            instrument->kind() != market_data::InstrumentKind::future ||
            instrument->contract_multiplier() != rate.contract_multiplier) {
            throw InvalidFuturesMarginInput(
                "lifecycle ledger does not match margin schedule universe");
        }
    }
    if (ledger_.fill_count() != 0) {
        // Prior fills would belong to orders this owner never admitted.
        throw InvalidFuturesMarginInput("lifecycle ledger must not contain unowned fills");
    }
}

FuturesMarginAdmission FuturesOrderLifecycle::submit(
    orders::OrderRecord&& candidate, const market_data::Timestamp now) {
    if (orders_.contains(candidate.id().value())) {
        throw FuturesLifecycleError("futures order ID was already submitted");
    }
    const auto admission = evaluator_.evaluate_submission(
        candidate, ledger_, pending_except(std::nullopt), now);
    const auto* instrument = ledger_.instrument(candidate.request().symbol);

    // Take ownership only after every check has passed. The evaluator already
    // validated the request against this instrument, so open() and reject()
    // cannot fail on content here.
    Entry entry{std::move(candidate), admission.code};
    if (admission.approved()) {
        entry.order.open(*instrument);
    } else {
        entry.order.reject(orders::OrderRejectionReason::risk);
    }
    const auto id = entry.order.id().value();
    orders_.emplace(id, std::move(entry));
    return admission;
}

FuturesFillDecision FuturesOrderLifecycle::apply_fill(const orders::Fill& proposed) {
    if (ledger_.contains_fill(proposed.id())) {
        const auto& committed =
            ledger_.fills()[static_cast<std::size_t>(proposed.id().value() - 1)];
        if (!same_fill(committed, proposed)) {
            throw FuturesLifecycleError("fill ID was committed with different content");
        }
        return {FuturesFillOutcome::already_applied, std::nullopt};
    }
    const auto found = orders_.find(proposed.order_id().value());
    if (found == orders_.end()) {
        throw FuturesLifecycleError("fill references an order this lifecycle does not own");
    }
    auto& entry = found->second;
    if (entry.order.is_terminal()) {
        throw FuturesLifecycleError("fill references a terminal futures order");
    }

    const auto admission = evaluator_.evaluate_fill(
        entry.order, entry.submission_code, proposed, ledger_,
        pending_except(entry.order.id()));
    if (!admission.approved()) {
        return {FuturesFillOutcome::denied, admission};
    }

    // Portfolio stages every check before its noexcept commit, so a throw here
    // leaves both objects unchanged. evaluate_fill already validated the fill
    // against this order, so recording its quantity cannot fail afterwards.
    ledger_.apply_fill(proposed);
    entry.order.record_fill_quantity(proposed.quantity());
    return {FuturesFillOutcome::applied, admission};
}

orders::CancelResult FuturesOrderLifecycle::cancel(
    const core::OrderId id, const orders::OrderCancellationReason reason) {
    const auto found = orders_.find(id.value());
    if (found == orders_.end()) {
        throw FuturesLifecycleError("cancel references an order this lifecycle does not own");
    }
    return found->second.order.cancel(reason);
}

void FuturesOrderLifecycle::mark(
    const market_data::Symbol& symbol, portfolio::ValuationMark mark) {
    ledger_.mark(symbol, std::move(mark));
}

portfolio::SettlementApplyResult FuturesOrderLifecycle::apply_settlement(
    const portfolio::VariationSettlement& settlement) {
    return ledger_.apply_settlement(settlement);
}

const orders::OrderRecord* FuturesOrderLifecycle::order(const core::OrderId id) const noexcept {
    const auto found = orders_.find(id.value());
    return found == orders_.end() ? nullptr : &found->second.order;
}

std::optional<FuturesAdmissionCode> FuturesOrderLifecycle::submission_code(
    const core::OrderId id) const noexcept {
    const auto found = orders_.find(id.value());
    if (found == orders_.end()) return std::nullopt;
    return found->second.submission_code;
}

std::vector<PendingFuturesOrder> FuturesOrderLifecycle::pending() const {
    return pending_except(std::nullopt);
}

FuturesMarginReport FuturesOrderLifecycle::report(const market_data::Timestamp now) const {
    return evaluator_.evaluate(ledger_, pending(), now);
}

std::vector<PendingFuturesOrder> FuturesOrderLifecycle::pending_except(
    const std::optional<core::OrderId> excluded) const {
    std::vector<PendingFuturesOrder> result;
    for (const auto& [id, entry] : orders_) {
        if (entry.order.is_terminal() || (excluded && *excluded == entry.order.id())) continue;
        result.push_back(PendingFuturesOrder::capture(entry.order));
    }
    return result;
}

}  // namespace qte::risk
