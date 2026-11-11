#pragma once

#include "qte/orders/order.hpp"
#include "qte/portfolio/portfolio.hpp"

#include <chrono>
#include <cstdint>
#include <map>
#include <optional>
#include <span>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace qte::risk {

class InvalidFuturesMarginInput final : public std::invalid_argument {
public:
    using std::invalid_argument::invalid_argument;
};

class FuturesMarginCalculationError final : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

struct FuturesMarginRate final {
    market_data::Symbol symbol;
    core::Currency currency;
    std::int64_t contract_multiplier;
    double initial_per_contract;
    double maintenance_per_contract;
    double one_way_fee_per_contract;
};

class FuturesMarginSchedule final {
public:
    [[nodiscard]] static FuturesMarginSchedule create(
        core::Currency valuation_currency,
        std::string source_id,
        std::string vintage_id,
        market_data::Timestamp reference_at,
        market_data::Timestamp available_at,
        market_data::Timestamp valid_until,
        std::vector<FuturesMarginRate> rates,
        const std::vector<market_data::InstrumentSpec>& universe);

    [[nodiscard]] const core::Currency& valuation_currency() const noexcept {
        return valuation_currency_;
    }
    [[nodiscard]] const std::string& source_id() const noexcept { return source_id_; }
    [[nodiscard]] const std::string& vintage_id() const noexcept { return vintage_id_; }
    [[nodiscard]] market_data::Timestamp reference_at() const noexcept { return reference_at_; }
    [[nodiscard]] market_data::Timestamp available_at() const noexcept { return available_at_; }
    [[nodiscard]] market_data::Timestamp valid_until() const noexcept { return valid_until_; }
    [[nodiscard]] const std::vector<FuturesMarginRate>& rates() const noexcept { return rates_; }

private:
    friend class FuturesMarginEvaluator;
    FuturesMarginSchedule(
        core::Currency valuation_currency, std::string source_id,
        std::string vintage_id, market_data::Timestamp reference_at,
        market_data::Timestamp available_at, market_data::Timestamp valid_until,
        std::vector<FuturesMarginRate> rates);

    core::Currency valuation_currency_;
    std::string source_id_;
    std::string vintage_id_;
    market_data::Timestamp reference_at_;
    market_data::Timestamp available_at_;
    market_data::Timestamp valid_until_;
    std::vector<FuturesMarginRate> rates_;
};

class PendingFuturesOrder final {
public:
    [[nodiscard]] static PendingFuturesOrder capture(const orders::OrderRecord& order);

    [[nodiscard]] core::OrderId order_id() const noexcept { return order_id_; }
    [[nodiscard]] const market_data::Symbol& symbol() const noexcept { return symbol_; }
    [[nodiscard]] orders::OrderSide side() const noexcept { return side_; }
    [[nodiscard]] market_data::Timestamp submitted_at() const noexcept {
        return submitted_at_;
    }
    [[nodiscard]] core::ShareAmount remaining_quantity() const noexcept {
        return remaining_quantity_;
    }

private:
    friend class FuturesMarginEvaluator;
    [[nodiscard]] static PendingFuturesOrder submission_candidate(
        const orders::OrderRecord& order);

    PendingFuturesOrder(
        core::OrderId order_id, market_data::Symbol symbol, orders::OrderSide side,
        core::ShareAmount remaining_quantity, market_data::Timestamp submitted_at)
        : order_id_(order_id), symbol_(std::move(symbol)), side_(side),
          remaining_quantity_(remaining_quantity), submitted_at_(submitted_at) {}

    core::OrderId order_id_;
    market_data::Symbol symbol_;
    orders::OrderSide side_;
    core::ShareAmount remaining_quantity_;
    market_data::Timestamp submitted_at_;
};

struct FuturesMarginReport final {
    double current_initial;
    double current_maintenance;
    double projected_initial;
    double additional_pending_initial;
    double pending_fees;
    double eligible_collateral;
    double available_after_projected;
    double maintenance_excess;
    bool initial_breach;
    bool maintenance_breach;
};

enum class FuturesAdmissionCode {
    approved,
    reduction_only,
    initial_margin,
    maintenance_margin,
    missing_fresh_mark,
    contract_closed,
};

struct FuturesMarginAdmission final {
    FuturesAdmissionCode code;
    std::optional<FuturesMarginReport> projected;

    [[nodiscard]] bool approved() const noexcept {
        return code == FuturesAdmissionCode::approved ||
               code == FuturesAdmissionCode::reduction_only;
    }
};

class FuturesMarginEvaluator final {
public:
    FuturesMarginEvaluator(
        FuturesMarginSchedule schedule, double cash_buffer,
        std::chrono::nanoseconds max_mark_age);

    [[nodiscard]] const FuturesMarginSchedule& schedule() const noexcept {
        return schedule_;
    }
    [[nodiscard]] double cash_buffer() const noexcept { return cash_buffer_; }
    [[nodiscard]] std::chrono::nanoseconds max_mark_age() const noexcept {
        return max_mark_age_;
    }
    [[nodiscard]] FuturesMarginReport evaluate(
        const portfolio::Portfolio& portfolio,
        std::span<const PendingFuturesOrder> pending_orders,
        market_data::Timestamp now) const;
    [[nodiscard]] FuturesMarginAdmission evaluate_submission(
        const orders::OrderRecord& candidate,
        const portfolio::Portfolio& ledger,
        std::span<const PendingFuturesOrder> pending_orders,
        market_data::Timestamp now) const;

private:
    FuturesMarginSchedule schedule_;
    double cash_buffer_;
    std::chrono::nanoseconds max_mark_age_;
    std::map<std::string, FuturesMarginRate> rates_;
};

}  // namespace qte::risk
