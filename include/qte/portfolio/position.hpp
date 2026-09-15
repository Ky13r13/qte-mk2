#pragma once

#include "qte/orders/fill.hpp"

#include <optional>
#include <stdexcept>
#include <utility>

namespace qte::portfolio {

class ValuationMark final {
public:
    [[nodiscard]] static ValuationMark create(
        market_data::Timestamp effective_at,
        core::EventSequence sequence,
        double price);

    [[nodiscard]] market_data::Timestamp effective_at() const noexcept {
        return effective_at_;
    }
    [[nodiscard]] core::EventSequence sequence() const noexcept { return sequence_; }
    [[nodiscard]] double price() const noexcept { return price_; }

private:
    ValuationMark(
        market_data::Timestamp effective_at,
        core::EventSequence sequence,
        double price)
        : effective_at_(effective_at), sequence_(sequence), price_(price) {}

    market_data::Timestamp effective_at_;
    core::EventSequence sequence_;
    double price_;
};

class PositionAccountingError final : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

class InvalidPositionInput final : public std::invalid_argument {
public:
    using std::invalid_argument::invalid_argument;
};

class PositionTransition final {
public:
    [[nodiscard]] core::ShareQuantity previous_quantity() const noexcept {
        return previous_quantity_;
    }
    [[nodiscard]] core::ShareQuantity new_quantity() const noexcept {
        return new_quantity_;
    }
    [[nodiscard]] core::ShareQuantity opened_quantity() const noexcept {
        return opened_quantity_;
    }
    [[nodiscard]] core::ShareQuantity closed_quantity() const noexcept {
        return closed_quantity_;
    }
    [[nodiscard]] double realized_gross_pnl() const noexcept {
        return realized_gross_pnl_;
    }
    [[nodiscard]] bool is_reversal() const noexcept {
        return !previous_quantity_.is_zero() && !new_quantity_.is_zero() &&
            ((previous_quantity_.value() > 0) != (new_quantity_.value() > 0));
    }

private:
    friend class Position;
    PositionTransition(
        core::ShareQuantity previous_quantity,
        core::ShareQuantity new_quantity,
        core::ShareQuantity opened_quantity,
        core::ShareQuantity closed_quantity,
        double realized_gross_pnl)
        : previous_quantity_(previous_quantity),
          new_quantity_(new_quantity),
          opened_quantity_(opened_quantity),
          closed_quantity_(closed_quantity),
          realized_gross_pnl_(realized_gross_pnl) {}

    core::ShareQuantity previous_quantity_;
    core::ShareQuantity new_quantity_;
    core::ShareQuantity opened_quantity_;
    core::ShareQuantity closed_quantity_;
    double realized_gross_pnl_;
};

// Average-cost inventory for one canonical symbol. This component deliberately
// owns no cash and performs no fill-ID deduplication; those belong to the M4b
// portfolio ledger commit boundary.
class Position final {
public:
    explicit Position(market_data::Symbol symbol) : symbol_(std::move(symbol)) {}
    Position(const Position&) = default;
    Position& operator=(const Position&) = default;
    Position(Position&&) noexcept = default;
    Position& operator=(Position&&) noexcept = default;

    [[nodiscard]] const market_data::Symbol& symbol() const noexcept { return symbol_; }
    [[nodiscard]] core::ShareQuantity quantity() const noexcept { return quantity_; }
    [[nodiscard]] double average_entry_price() const noexcept {
        return average_entry_price_;
    }
    [[nodiscard]] double realized_gross_pnl() const noexcept {
        return realized_gross_sum_;
    }
    [[nodiscard]] double commissions_paid() const noexcept {
        return commission_sum_;
    }
    [[nodiscard]] bool is_flat() const noexcept { return quantity_.is_zero(); }
    [[nodiscard]] const std::optional<ValuationMark>& valuation_mark() const noexcept {
        return valuation_mark_;
    }

    // Flat market value and unrealized PnL are known to be zero without a mark.
    // Open unmarked positions return nullopt rather than fabricating a valuation.
    [[nodiscard]] std::optional<double> market_value() const noexcept;
    [[nodiscard]] std::optional<double> unrealized_pnl() const noexcept;
    [[nodiscard]] std::optional<double> net_pnl() const noexcept;

    PositionTransition apply_fill(const orders::Fill& fill);
    void mark(ValuationMark mark);

private:
    static void add_compensated(
        double value,
        double& sum,
        double& compensation);
    static void require_finite_state(
        core::ShareQuantity quantity,
        double average_entry_price,
        double realized_gross_pnl,
        double commissions_paid,
        const std::optional<ValuationMark>& mark);

    market_data::Symbol symbol_;
    core::ShareQuantity quantity_{core::ShareQuantity::zero()};
    double average_entry_price_{0.0};
    double realized_gross_sum_{0.0};
    double realized_gross_compensation_{0.0};
    double commission_sum_{0.0};
    double commission_compensation_{0.0};
    std::optional<ValuationMark> valuation_mark_;
};

}  // namespace qte::portfolio
