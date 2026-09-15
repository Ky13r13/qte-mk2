#pragma once

#include "qte/orders/order.hpp"

#include <optional>
#include <stdexcept>
#include <string_view>
#include <utility>

namespace qte::execution {

inline constexpr std::string_view kOpenOnlyModelId = "open_only_v1";

// Full quoted spread plus one-sided adverse slippage and commission, all in
// basis points. Construction is the validation boundary for cost assumptions.
class ExecutionCosts final {
public:
    [[nodiscard]] static ExecutionCosts create(
        double commission_bps = 0.0,
        double spread_bps = 0.0,
        double slippage_bps = 0.0);

    [[nodiscard]] double commission_bps() const noexcept { return commission_bps_; }
    [[nodiscard]] double spread_bps() const noexcept { return spread_bps_; }
    [[nodiscard]] double slippage_bps() const noexcept { return slippage_bps_; }

private:
    ExecutionCosts(double commission_bps, double spread_bps, double slippage_bps)
        : commission_bps_(commission_bps),
          spread_bps_(spread_bps),
          slippage_bps_(slippage_bps) {}

    double commission_bps_;
    double spread_bps_;
    double slippage_bps_;
};

// The only market information visible to the baseline execution model. A full
// in-progress Bar is intentionally not accepted by this API.
class MarketOpen final {
public:
    [[nodiscard]] static MarketOpen create(
        market_data::Symbol symbol,
        market_data::Timestamp timestamp,
        core::EventSequence sequence,
        double price);

    [[nodiscard]] const market_data::Symbol& symbol() const noexcept { return symbol_; }
    [[nodiscard]] market_data::Timestamp timestamp() const noexcept { return timestamp_; }
    [[nodiscard]] core::EventSequence sequence() const noexcept { return sequence_; }
    [[nodiscard]] double price() const noexcept { return price_; }

private:
    MarketOpen(
        market_data::Symbol symbol,
        market_data::Timestamp timestamp,
        core::EventSequence sequence,
        double price);

    market_data::Symbol symbol_;
    market_data::Timestamp timestamp_;
    core::EventSequence sequence_;
    double price_;
};

// A pure execution proposal. It has no fill ID and cannot change order or
// portfolio state; risk and the engine will validate and commit it later.
class FillCandidate final {
public:
    [[nodiscard]] core::OrderId order_id() const noexcept { return order_id_; }
    [[nodiscard]] const market_data::Symbol& symbol() const noexcept { return symbol_; }
    [[nodiscard]] orders::OrderSide side() const noexcept { return side_; }
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
    friend class OpenOnlyExecutionModel;
    FillCandidate(
        core::OrderId order_id,
        market_data::Symbol symbol,
        orders::OrderSide side,
        core::ShareAmount quantity,
        market_data::Timestamp effective_at,
        core::EventSequence effective_sequence,
        double reference_open,
        core::TickPrice executed_price,
        double gross_notional,
        double commission)
        : order_id_(order_id),
          symbol_(std::move(symbol)),
          side_(side),
          quantity_(quantity),
          effective_at_(effective_at),
          effective_sequence_(effective_sequence),
          reference_open_(reference_open),
          executed_price_(executed_price),
          gross_notional_(gross_notional),
          commission_(commission) {}

    core::OrderId order_id_;
    market_data::Symbol symbol_;
    orders::OrderSide side_;
    core::ShareAmount quantity_;
    market_data::Timestamp effective_at_;
    core::EventSequence effective_sequence_;
    double reference_open_;
    core::TickPrice executed_price_;
    double gross_notional_;
    double commission_;
};

class InvalidExecutionInput final : public std::invalid_argument {
public:
    using std::invalid_argument::invalid_argument;
};

class UnsupportedExecutionOrder final : public std::logic_error {
public:
    using std::logic_error::logic_error;
};

class ExecutionCalculationError final : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

class OpenOnlyExecutionModel final {
public:
    explicit OpenOnlyExecutionModel(ExecutionCosts costs) : costs_(costs) {}

    [[nodiscard]] static constexpr std::string_view model_id() noexcept {
        return kOpenOnlyModelId;
    }
    [[nodiscard]] const ExecutionCosts& costs() const noexcept { return costs_; }

    // nullopt is the ordinary "not eligible, not triggered, or limit not
    // satisfied" result. Stop triggers are persisted on the engine-owned order
    // even when a stop-limit does not produce a candidate. No other order state
    // and no portfolio state is changed here.
    [[nodiscard]] std::optional<FillCandidate> evaluate(
        orders::OrderRecord& order,
        const market_data::InstrumentSpec& instrument,
        const MarketOpen& market_open) const;

private:
    ExecutionCosts costs_;
};

}  // namespace qte::execution
