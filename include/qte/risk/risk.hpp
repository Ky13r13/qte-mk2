#pragma once

#include "qte/execution/open_only.hpp"
#include "qte/portfolio/portfolio.hpp"

#include <cstdint>
#include <limits>
#include <optional>
#include <span>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

namespace qte::risk {

class InvalidRiskInput final : public std::invalid_argument {
public:
    using std::invalid_argument::invalid_argument;
};

class RiskCalculationError final : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

class RiskLimits final {
public:
    [[nodiscard]] static RiskLimits create(
        std::optional<std::int64_t> max_order_quantity =
            std::numeric_limits<std::int64_t>::max(),
        std::optional<double> max_symbol_allocation = 1.0,
        std::optional<double> max_gross_leverage = 1.0,
        bool allow_short = false,
        std::optional<double> cash_floor = 0.0);

    [[nodiscard]] std::optional<core::ShareAmount> max_order_quantity() const noexcept {
        return max_order_quantity_;
    }
    [[nodiscard]] std::optional<double> max_symbol_allocation() const noexcept {
        return max_symbol_allocation_;
    }
    [[nodiscard]] std::optional<double> max_gross_leverage() const noexcept {
        return max_gross_leverage_;
    }
    [[nodiscard]] bool allow_short() const noexcept { return allow_short_; }
    [[nodiscard]] std::optional<double> cash_floor() const noexcept {
        return cash_floor_;
    }

private:
    RiskLimits(
        std::optional<core::ShareAmount> max_order_quantity,
        std::optional<double> max_symbol_allocation,
        std::optional<double> max_gross_leverage,
        bool allow_short,
        std::optional<double> cash_floor)
        : max_order_quantity_(max_order_quantity),
          max_symbol_allocation_(max_symbol_allocation),
          max_gross_leverage_(max_gross_leverage),
          allow_short_(allow_short),
          cash_floor_(cash_floor) {}

    std::optional<core::ShareAmount> max_order_quantity_;
    std::optional<double> max_symbol_allocation_;
    std::optional<double> max_gross_leverage_;
    bool allow_short_;
    std::optional<double> cash_floor_;
};

// An accepted order's immutable risk reservation. The per-share buy reserve
// includes its estimated execution notional and proportional estimated fee.
class PendingOrderSnapshot final {
public:
    [[nodiscard]] static PendingOrderSnapshot capture(
        const orders::OrderRecord& order,
        double estimated_execution_price,
        double estimated_commission);

    [[nodiscard]] core::OrderId order_id() const noexcept { return order_id_; }
    [[nodiscard]] const market_data::Symbol& symbol() const noexcept { return symbol_; }
    [[nodiscard]] orders::OrderSide side() const noexcept { return side_; }
    [[nodiscard]] core::ShareAmount remaining_quantity() const noexcept {
        return remaining_quantity_;
    }
    [[nodiscard]] double reserved_cost_per_share() const noexcept {
        return reserved_cost_per_share_;
    }

private:
    PendingOrderSnapshot(
        core::OrderId order_id,
        market_data::Symbol symbol,
        orders::OrderSide side,
        core::ShareAmount remaining_quantity,
        double reserved_cost_per_share)
        : order_id_(order_id),
          symbol_(std::move(symbol)),
          side_(side),
          remaining_quantity_(remaining_quantity),
          reserved_cost_per_share_(reserved_cost_per_share) {}

    core::OrderId order_id_;
    market_data::Symbol symbol_;
    orders::OrderSide side_;
    core::ShareAmount remaining_quantity_;
    double reserved_cost_per_share_;
};

enum class RiskRejectionCode {
    max_order_quantity,
    missing_positive_mark,
    nonpositive_equity,
    short_not_allowed,
    cash_floor,
    max_symbol_allocation,
    max_gross_leverage,
};

class RiskDecision final {
public:
    [[nodiscard]] static RiskDecision approve();
    [[nodiscard]] static RiskDecision reject(
        RiskRejectionCode code,
        std::string reason);

    [[nodiscard]] bool approved() const noexcept { return approved_; }
    [[nodiscard]] std::optional<RiskRejectionCode> rejection_code() const noexcept {
        return rejection_code_;
    }
    [[nodiscard]] const std::string& reason() const noexcept { return reason_; }

private:
    RiskDecision(
        bool approved,
        std::optional<RiskRejectionCode> rejection_code,
        std::string reason)
        : approved_(approved),
          rejection_code_(rejection_code),
          reason_(std::move(reason)) {}

    bool approved_;
    std::optional<RiskRejectionCode> rejection_code_;
    std::string reason_;
};

class RiskManager final {
public:
    explicit RiskManager(RiskLimits limits) : limits_(std::move(limits)) {}

    [[nodiscard]] const RiskLimits& limits() const noexcept { return limits_; }

    [[nodiscard]] RiskDecision evaluate_submission(
        const orders::OrderRequest& request,
        const market_data::InstrumentSpec& instrument,
        const portfolio::PortfolioSnapshot& portfolio,
        std::span<const PendingOrderSnapshot> pending_orders,
        double estimated_execution_price,
        double estimated_commission) const;

    [[nodiscard]] RiskDecision evaluate_execution(
        const execution::FillCandidate& candidate,
        const portfolio::PortfolioSnapshot& portfolio,
        std::span<const PendingOrderSnapshot> pending_orders) const;

private:
    RiskLimits limits_;
};

[[nodiscard]] std::string_view to_string(RiskRejectionCode code) noexcept;

}  // namespace qte::risk
