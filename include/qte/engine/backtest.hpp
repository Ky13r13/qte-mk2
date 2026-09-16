#pragma once

#include "qte/execution/open_only.hpp"
#include "qte/market_data/schedule.hpp"
#include "qte/risk/risk.hpp"
#include "qte/strategy/strategy.hpp"

#include <cstddef>
#include <cstdint>
#include <atomic>
#include <memory>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace qte::engine {

class BacktestConfig final {
public:
    [[nodiscard]] static BacktestConfig create(
        double initial_cash,
        execution::ExecutionCosts execution_costs = execution::ExecutionCosts::create(),
        risk::RiskLimits risk_limits = risk::RiskLimits::create(),
        std::size_t history_capacity = 256,
        std::uint64_t random_seed = 0,
        std::string build_identity = "unversioned-source");

    [[nodiscard]] double initial_cash() const noexcept { return initial_cash_; }
    [[nodiscard]] const execution::ExecutionCosts& execution_costs() const noexcept {
        return execution_costs_;
    }
    [[nodiscard]] const risk::RiskLimits& risk_limits() const noexcept {
        return risk_limits_;
    }
    [[nodiscard]] std::size_t history_capacity() const noexcept {
        return history_capacity_;
    }
    [[nodiscard]] std::uint64_t random_seed() const noexcept { return random_seed_; }
    [[nodiscard]] const std::string& build_identity() const noexcept {
        return build_identity_;
    }

private:
    BacktestConfig(
        double initial_cash,
        execution::ExecutionCosts execution_costs,
        risk::RiskLimits risk_limits,
        std::size_t history_capacity,
        std::uint64_t random_seed,
        std::string build_identity);

    double initial_cash_;
    execution::ExecutionCosts execution_costs_;
    risk::RiskLimits risk_limits_;
    std::size_t history_capacity_;
    std::uint64_t random_seed_;
    std::string build_identity_;
};

struct EquityPoint final {
    market_data::Timestamp timestamp;
    core::EventSequence sequence;
    double equity;
    double gross_exposure;
};

struct OrderSnapshot final {
    core::OrderId id;
    orders::OrderRequest request;
    market_data::Timestamp submitted_at;
    core::EventSequence submission_sequence;
    core::EventSequence eligible_after_sequence;
    orders::OrderStatus status;
    core::ShareQuantity filled_quantity;
    core::ShareQuantity remaining_quantity;
    bool stop_triggered;
    std::optional<orders::OrderRejectionReason> rejection_reason;
    std::optional<orders::OrderCancellationReason> cancellation_reason;
    std::string detail;
};

enum class OrderEventKind {
    accepted,
    rejected,
    canceled,
    cancel_noop,
    cancel_unknown,
    filled,
};

struct OrderEvent final {
    market_data::Timestamp timestamp;
    core::EventSequence sequence;
    std::optional<core::OrderId> order_id;
    OrderEventKind kind;
    std::string detail;
};

struct RunManifest final {
    std::string dataset_hash;
    std::string dataset_hash_algorithm;
    std::string source_id;
    std::string execution_model;
    std::uint32_t bar_schema_version;
    std::uint64_t random_seed;
    std::string build_identity;
    double initial_cash;
    execution::ExecutionCosts execution_costs;
    risk::RiskLimits risk_limits;
    std::size_t history_capacity;
    std::size_t dataset_gap_count;
    std::string normalized_config;
};

class BacktestResults final {
public:
    BacktestResults(
        std::vector<EquityPoint> equity_curve,
        std::vector<OrderSnapshot> orders,
        std::vector<OrderEvent> order_events,
        std::vector<orders::Fill> fills,
        std::vector<portfolio::TradeEpisode> trades,
        std::vector<portfolio::TradeEpisode> open_trades,
        std::vector<portfolio::PositionSnapshot> positions,
        RunManifest manifest);

    [[nodiscard]] const std::vector<EquityPoint>& equity_curve() const noexcept {
        return equity_curve_;
    }
    [[nodiscard]] const std::vector<OrderSnapshot>& orders() const noexcept {
        return orders_;
    }
    [[nodiscard]] const std::vector<OrderEvent>& order_events() const noexcept {
        return order_events_;
    }
    [[nodiscard]] const std::vector<orders::Fill>& fills() const noexcept {
        return fills_;
    }
    [[nodiscard]] const std::vector<portfolio::TradeEpisode>& trades() const noexcept {
        return trades_;
    }
    [[nodiscard]] const std::vector<portfolio::TradeEpisode>& open_trades() const noexcept {
        return open_trades_;
    }
    [[nodiscard]] const std::vector<portfolio::PositionSnapshot>& positions() const noexcept {
        return positions_;
    }
    [[nodiscard]] const RunManifest& manifest() const noexcept { return manifest_; }

private:
    std::vector<EquityPoint> equity_curve_;
    std::vector<OrderSnapshot> orders_;
    std::vector<OrderEvent> order_events_;
    std::vector<orders::Fill> fills_;
    std::vector<portfolio::TradeEpisode> trades_;
    std::vector<portfolio::TradeEpisode> open_trades_;
    std::vector<portfolio::PositionSnapshot> positions_;
    RunManifest manifest_;
};

class BacktestEngine final {
public:
    explicit BacktestEngine(BacktestConfig config)
        : config_(std::move(config)), running_(std::make_shared<std::atomic_bool>(false)) {}

    [[nodiscard]] BacktestResults run(
        const market_data::ValidatedDataset& dataset,
        std::shared_ptr<strategy::Strategy> strategy) const;

private:
    BacktestConfig config_;
    std::shared_ptr<std::atomic_bool> running_;
};

}  // namespace qte::engine
