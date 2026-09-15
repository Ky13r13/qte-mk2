#pragma once

#include "qte/orders/order.hpp"

#include <cstddef>
#include <stdexcept>
#include <string>
#include <vector>

namespace qte::orders {

// An execution fact. Construction validates intrinsic fields; committed fills
// are exposed through FillJournal as read-only values.
class Fill final {
public:
    Fill(const Fill&) = default;
    Fill(Fill&&) noexcept = default;
    Fill& operator=(const Fill&) = delete;
    Fill& operator=(Fill&&) = delete;

    [[nodiscard]] static Fill create(
        core::FillId id,
        core::OrderId order_id,
        market_data::Symbol symbol,
        OrderSide side,
        core::ShareAmount quantity,
        market_data::Timestamp effective_at,
        core::EventSequence effective_sequence,
        double reference_open,
        core::TickPrice executed_price,
        double commission);

    [[nodiscard]] core::FillId id() const noexcept { return id_; }
    [[nodiscard]] core::OrderId order_id() const noexcept { return order_id_; }
    [[nodiscard]] const market_data::Symbol& symbol() const noexcept { return symbol_; }
    [[nodiscard]] OrderSide side() const noexcept { return side_; }
    [[nodiscard]] core::ShareAmount quantity() const noexcept { return quantity_; }
    [[nodiscard]] market_data::Timestamp effective_at() const noexcept {
        return effective_at_;
    }
    [[nodiscard]] core::EventSequence effective_sequence() const noexcept {
        return effective_sequence_;
    }
    [[nodiscard]] double reference_open() const noexcept { return reference_open_; }
    [[nodiscard]] core::TickPrice executed_price() const noexcept {
        return executed_price_;
    }
    [[nodiscard]] double gross_notional() const noexcept { return gross_notional_; }
    [[nodiscard]] double commission() const noexcept { return commission_; }

private:
    Fill(
        core::FillId id,
        core::OrderId order_id,
        market_data::Symbol symbol,
        OrderSide side,
        core::ShareAmount quantity,
        market_data::Timestamp effective_at,
        core::EventSequence effective_sequence,
        double reference_open,
        core::TickPrice executed_price,
        double gross_notional,
        double commission);

    core::FillId id_;
    core::OrderId order_id_;
    market_data::Symbol symbol_;
    OrderSide side_;
    core::ShareAmount quantity_;
    market_data::Timestamp effective_at_;
    core::EventSequence effective_sequence_;
    double reference_open_;
    core::TickPrice executed_price_;
    double gross_notional_;
    double commission_;
};

enum class FillValidationCode {
    order_not_fillable,
    order_id_mismatch,
    symbol_mismatch,
    side_mismatch,
    instrument_symbol_mismatch,
    execution_price_not_on_instrument_grid,
    effective_sequence_not_after_eligibility,
    effective_timestamp_before_submission,
    quantity_exceeds_remaining,
};

struct FillValidationError final {
    FillValidationCode code;
    std::string message;

    friend bool operator==(const FillValidationError&, const FillValidationError&) = default;
};

class InvalidFill final : public std::invalid_argument {
public:
    explicit InvalidFill(std::vector<FillValidationError> errors);

    [[nodiscard]] const std::vector<FillValidationError>& errors() const noexcept {
        return errors_;
    }

private:
    std::vector<FillValidationError> errors_;
};

[[nodiscard]] std::vector<FillValidationError> validate(
    const Fill& fill,
    const OrderRecord& order,
    const market_data::InstrumentSpec& instrument);
void require_valid(
    const Fill& fill,
    const OrderRecord& order,
    const market_data::InstrumentSpec& instrument);

class DuplicateFill final : public std::logic_error {
public:
    using std::logic_error::logic_error;
};

class OutOfSequenceFill final : public std::logic_error {
public:
    using std::logic_error::logic_error;
};

// The first commit boundary for fills. IDs are globally contiguous per journal,
// and validation completes before either the journal or order is mutated.
class FillJournal final {
public:
    void commit(
        OrderRecord& order,
        const Fill& fill,
        const market_data::InstrumentSpec& instrument);

    [[nodiscard]] std::size_t size() const noexcept { return fills_.size(); }
    [[nodiscard]] bool empty() const noexcept { return fills_.empty(); }
    [[nodiscard]] bool contains(core::FillId id) const noexcept;
    [[nodiscard]] const std::vector<Fill>& fills() const noexcept { return fills_; }

private:
    std::vector<Fill> fills_;
};

}  // namespace qte::orders
