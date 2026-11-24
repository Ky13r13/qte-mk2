#pragma once

#include "qte/orders/fill.hpp"
#include "qte/orders/order.hpp"
#include "qte/portfolio/portfolio.hpp"
#include "qte/risk/futures_margin.hpp"

#include <cstdint>
#include <map>
#include <optional>
#include <stdexcept>
#include <vector>

namespace qte::risk {

// Raised for lifecycle misuse: unknown or duplicate order IDs, fills against
// terminal orders, or a redelivered fill ID whose content differs.
class FuturesLifecycleError final : public std::logic_error {
public:
    using std::logic_error::logic_error;
};

enum class FuturesFillOutcome {
    applied,
    already_applied,
    denied,
};

struct FuturesFillDecision final {
    FuturesFillOutcome outcome;
    // Present for applied and denied proposals; absent for exact redelivery.
    std::optional<FuturesMarginAdmission> admission;
};

// The single owner of futures pending-order state for one research ledger
// (ADR 0013 D2b.2b). It owns the Portfolio, every submitted OrderRecord and
// each order's original submission decision, so reservations are always
// captured from one event frontier and fills cannot bypass admission.
//
// Submission, fill and cancellation either complete on both the order and the
// ledger or mutate neither. Denials are typed results; malformed inputs throw
// before mutation. This is a synthetic-execution owner: it neither produces
// execution prices nor talks to a broker.
class FuturesOrderLifecycle final {
public:
    FuturesOrderLifecycle(portfolio::Portfolio ledger, FuturesMarginEvaluator evaluator);

    FuturesOrderLifecycle(const FuturesOrderLifecycle&) = delete;
    FuturesOrderLifecycle& operator=(const FuturesOrderLifecycle&) = delete;
    FuturesOrderLifecycle(FuturesOrderLifecycle&&) noexcept = default;
    FuturesOrderLifecycle& operator=(FuturesOrderLifecycle&&) noexcept = default;

    [[nodiscard]] const portfolio::Portfolio& ledger() const noexcept { return ledger_; }
    [[nodiscard]] const FuturesMarginEvaluator& evaluator() const noexcept {
        return evaluator_;
    }

    // Admits a NEW order against every accepted remainder. Approved and
    // reduction-only orders open; denials are retained as risk rejections.
    // If validation throws, the caller's candidate is left unchanged.
    FuturesMarginAdmission submit(orders::OrderRecord&& candidate, market_data::Timestamp now);

    // Rechecks a simulated execution with the order's original submission code
    // and all other remainders, then commits ledger and order together. An
    // identical redelivery of a committed fill ID returns already_applied.
    FuturesFillDecision apply_fill(const orders::Fill& proposed);

    // An acknowledged cancellation releases the order's reservation.
    orders::CancelResult cancel(core::OrderId id, orders::OrderCancellationReason reason);

    void mark(const market_data::Symbol& symbol, portfolio::ValuationMark mark);
    portfolio::SettlementApplyResult apply_settlement(
        const portfolio::VariationSettlement& settlement);

    [[nodiscard]] const orders::OrderRecord* order(core::OrderId id) const noexcept;
    [[nodiscard]] std::optional<FuturesAdmissionCode> submission_code(
        core::OrderId id) const noexcept;
    [[nodiscard]] std::size_t order_count() const noexcept { return orders_.size(); }
    // Remainders of accepted nonterminal orders, ordered by order ID.
    [[nodiscard]] std::vector<PendingFuturesOrder> pending() const;
    [[nodiscard]] FuturesMarginReport report(market_data::Timestamp now) const;

private:
    struct Entry final {
        orders::OrderRecord order;
        FuturesAdmissionCode submission_code;
    };

    [[nodiscard]] std::vector<PendingFuturesOrder> pending_except(
        std::optional<core::OrderId> excluded) const;

    portfolio::Portfolio ledger_;
    FuturesMarginEvaluator evaluator_;
    std::map<std::uint64_t, Entry> orders_;
};

}  // namespace qte::risk
