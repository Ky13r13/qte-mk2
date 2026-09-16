#include "qte/engine/backtest.hpp"

#include <algorithm>
#include <cmath>
#include <map>
#include <iomanip>
#include <locale>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>

namespace qte::engine {
namespace {

[[nodiscard]] std::string normalized_config(const BacktestConfig& config) {
    const auto optional_double = [](const std::optional<double> value) {
        if (!value.has_value()) {
            return std::string{"disabled"};
        }
        std::ostringstream output;
        output.imbue(std::locale::classic());
        output << std::setprecision(17) << *value;
        return output.str();
    };
    std::ostringstream output;
    output.imbue(std::locale::classic());
    output << std::setprecision(17)
           << "initial_cash=" << config.initial_cash()
           << ";commission_bps=" << config.execution_costs().commission_bps()
           << ";spread_bps=" << config.execution_costs().spread_bps()
           << ";slippage_bps=" << config.execution_costs().slippage_bps()
           << ";max_order_quantity=";
    if (config.risk_limits().max_order_quantity().has_value()) {
        output << config.risk_limits().max_order_quantity()->value();
    } else {
        output << "disabled";
    }
    output << ";max_symbol_allocation="
           << optional_double(config.risk_limits().max_symbol_allocation())
           << ";max_gross_leverage="
           << optional_double(config.risk_limits().max_gross_leverage())
           << ";allow_short=" << (config.risk_limits().allow_short() ? "true" : "false")
           << ";cash_floor=" << optional_double(config.risk_limits().cash_floor())
           << ";history_capacity=" << config.history_capacity()
           << ";random_seed=" << config.random_seed()
           << ";build_identity=" << config.build_identity();
    return output.str();
}

class RunState final {
public:
    RunState(
        const BacktestConfig& config,
        const market_data::ValidatedDataset& dataset,
        std::shared_ptr<strategy::Strategy> strategy)
        : config_(config),
          dataset_(dataset),
          schedule_(dataset),
          history_(dataset.instruments(), config.history_capacity()),
          portfolio_(
              config.initial_cash(),
              dataset.metadata().valuation_currency(),
              dataset.instruments()),
          execution_(config.execution_costs()),
          risk_(config.risk_limits()),
          strategy_(std::move(strategy)) {
        for (const auto& instrument : dataset.instruments()) {
            instruments_.emplace(instrument.symbol().value(), instrument);
        }
    }

    [[nodiscard]] BacktestResults run() {
        if (schedule_.slices().empty()) {
            throw std::invalid_argument("validated dataset produced no event slices");
        }
        current_time_ = schedule_.slices().front().timestamp();
        sample_equity();
        strategy_.start(portfolio_.snapshot(), history_.snapshot());
        process_commands(strategy_.take_commands());

        for (const auto& slice : schedule_.slices()) {
            current_time_ = slice.timestamp();
            publish_closes(slice);
            dispatch_bars(slice);
            process_commands(strategy_.take_commands());
            const auto committed = process_openings(slice);
            dispatch_fills(committed);
            process_commands(strategy_.take_commands());
            sample_equity();
        }

        cancel_end_of_data();
        strategy_.end(portfolio_.snapshot(), history_.snapshot());
        const auto unexpected = strategy_.take_commands();
        if (!unexpected.empty()) {
            throw std::logic_error("on_end produced commands despite read-only context");
        }
        return build_results();
    }

private:
    [[nodiscard]] core::EventSequence next_sequence() {
        return sequences_.next();
    }

    [[nodiscard]] std::vector<risk::PendingOrderSnapshot> pending_snapshots() const {
        std::vector<risk::PendingOrderSnapshot> result;
        result.reserve(pending_.size());
        for (const auto& [id, snapshot] : pending_) {
            static_cast<void>(id);
            result.push_back(snapshot);
        }
        return result;
    }

    void publish_closes(const market_data::TimeSlice& slice) {
        for (const auto& bar : slice.completed_bars()) {
            portfolio_.mark(
                bar.symbol,
                portfolio::ValuationMark::create(
                    slice.timestamp(), next_sequence(), bar.close));
        }
        history_.publish(slice.completed_bars());
    }

    void dispatch_bars(const market_data::TimeSlice& slice) {
        const auto snapshot = portfolio_.snapshot();
        const auto history = history_.snapshot();
        for (const auto& bar : slice.completed_bars()) {
            strategy_.bar(bar, snapshot, history);
        }
    }

    void process_commands(const std::vector<strategy::StrategyCommand>& commands) {
        for (const auto& command : commands) {
            std::visit([this](const auto& value) { process_command(value); }, command);
        }
    }

    void process_command(const strategy::SubmitOrderCommand& command) {
        if (command.issue_sequence != next_command_issue_) {
            throw std::logic_error("strategy command issue sequence is not contiguous");
        }
        ++next_command_issue_;
        if (command.order_id.value() != next_order_id_) {
            throw std::logic_error("strategy order IDs are not contiguous");
        }
        ++next_order_id_;

        const auto submission_sequence = next_sequence();
        auto order = std::make_unique<orders::OrderRecord>(
            command.order_id,
            command.request,
            current_time_,
            submission_sequence,
            submission_sequence);
        std::string detail;
        const auto instrument = instruments_.find(command.request.symbol.value());
        if (instrument == instruments_.end()) {
            order->reject(orders::OrderRejectionReason::invalid_request);
            detail = "symbol is outside the dataset universe";
        } else if (!orders::validate(command.request, instrument->second).empty()) {
            order->reject(orders::OrderRejectionReason::invalid_request);
            detail = "order request does not satisfy instrument metadata";
        } else {
            const auto* position = portfolio_.position(command.request.symbol);
            if (position == nullptr || !position->valuation_mark().has_value() ||
                position->valuation_mark()->price() <= 0.0) {
                order->reject(orders::OrderRejectionReason::no_reference_price);
                detail = "no positive visible reference price";
            } else {
                const auto estimate = execution_.estimate(
                    command.request.side,
                    command.request.quantity,
                    instrument->second,
                    position->valuation_mark()->price());
                const auto pending = pending_snapshots();
                const auto decision = risk_.evaluate_submission(
                    command.request,
                    instrument->second,
                    portfolio_.snapshot(),
                    pending,
                    estimate.executed_price().value(),
                    estimate.commission());
                if (!decision.approved()) {
                    order->reject(orders::OrderRejectionReason::risk);
                    detail = decision.reason();
                } else {
                    order->open(instrument->second);
                    pending_.emplace(
                        command.order_id.value(),
                        risk::PendingOrderSnapshot::capture(
                            *order,
                            estimate.executed_price().value(),
                            estimate.commission()));
                    detail = "accepted";
                }
            }
        }

        const bool accepted = order->status() == orders::OrderStatus::open;
        order_details_[command.order_id.value()] = detail;
        orders_.emplace(command.order_id.value(), std::move(order));
        order_events_.push_back(OrderEvent{
            current_time_,
            submission_sequence,
            command.order_id,
            accepted ? OrderEventKind::accepted : OrderEventKind::rejected,
            detail,
        });
    }

    void process_command(const strategy::CancelOrderCommand& command) {
        if (command.issue_sequence != next_command_issue_) {
            throw std::logic_error("strategy command issue sequence is not contiguous");
        }
        ++next_command_issue_;
        const auto sequence = next_sequence();
        const auto found = orders_.find(command.order_id.value());
        if (found == orders_.end()) {
            order_events_.push_back(OrderEvent{
                current_time_, sequence, command.order_id,
                OrderEventKind::cancel_unknown, "unknown order ID"});
            return;
        }
        const auto result = found->second->cancel(
            orders::OrderCancellationReason::user_requested);
        if (result == orders::CancelResult::canceled) {
            pending_.erase(command.order_id.value());
            order_details_[command.order_id.value()] = "user requested cancellation";
        }
        order_events_.push_back(OrderEvent{
            current_time_,
            sequence,
            command.order_id,
            result == orders::CancelResult::canceled
                ? OrderEventKind::canceled
                : OrderEventKind::cancel_noop,
            result == orders::CancelResult::canceled ? "canceled" : "already terminal",
        });
    }

    [[nodiscard]] std::vector<orders::Fill> process_openings(
        const market_data::TimeSlice& slice) {
        std::map<std::string, std::pair<market_data::OpeningObservation, core::EventSequence>>
            openings;
        for (const auto& opening : slice.openings()) {
            const auto sequence = next_sequence();
            portfolio_.mark(
                opening.symbol,
                portfolio::ValuationMark::create(
                    opening.timestamp, sequence, opening.price));
            openings.emplace(opening.symbol.value(), std::pair{opening, sequence});
        }

        std::vector<orders::Fill> committed;
        for (auto& [id, order_ptr] : orders_) {
            auto& order = *order_ptr;
            if (order.status() != orders::OrderStatus::open &&
                order.status() != orders::OrderStatus::partially_filled) {
                continue;
            }
            const auto opening = openings.find(order.request().symbol.value());
            if (opening == openings.end()) {
                continue;
            }
            const auto instrument = instruments_.find(order.request().symbol.value());
            if (instrument == instruments_.end()) {
                throw std::logic_error("accepted order instrument disappeared");
            }
            auto candidate = execution_.evaluate(
                order,
                instrument->second,
                execution::MarketOpen::create(
                    opening->second.first.symbol,
                    opening->second.first.timestamp,
                    opening->second.second,
                    opening->second.first.price));
            if (!candidate.has_value()) {
                continue;
            }
            const auto pending = pending_snapshots();
            const auto decision = risk_.evaluate_execution(
                *candidate, portfolio_.snapshot(), pending);
            if (!decision.approved()) {
                static_cast<void>(order.cancel(
                    orders::OrderCancellationReason::execution_risk));
                pending_.erase(id);
                order_details_[id] = decision.reason();
                order_events_.push_back(OrderEvent{
                    current_time_, next_sequence(), order.id(),
                    OrderEventKind::canceled, decision.reason()});
                continue;
            }

            const auto fill_sequence = next_sequence();
            auto fill = orders::Fill::create(
                fill_ids_.next(),
                candidate->order_id(),
                candidate->symbol(),
                candidate->side(),
                candidate->quantity(),
                current_time_,
                fill_sequence,
                candidate->reference_open(),
                candidate->executed_price(),
                candidate->commission());
            orders::require_valid(fill, order, instrument->second);
            portfolio_.apply_fill(fill);
            order.record_fill_quantity(fill.quantity());
            pending_.erase(id);
            order_details_[id] = "filled";
            order_events_.push_back(OrderEvent{
                current_time_, fill_sequence, order.id(),
                OrderEventKind::filled, "filled"});
            committed.push_back(fill);
        }
        return committed;
    }

    void dispatch_fills(const std::vector<orders::Fill>& fills) {
        const auto snapshot = portfolio_.snapshot();
        const auto history = history_.snapshot();
        for (const auto& fill : fills) {
            strategy_.fill(fill, snapshot, history);
        }
    }

    void sample_equity() {
        const auto equity = portfolio_.equity();
        const auto gross_exposure = portfolio_.gross_exposure();
        if (!equity.has_value() || !gross_exposure.has_value()) {
            throw std::logic_error("portfolio equity unavailable at sample boundary");
        }
        equity_curve_.push_back(EquityPoint{
            current_time_, next_sequence(), *equity, *gross_exposure});
    }

    void cancel_end_of_data() {
        for (auto& [id, order] : orders_) {
            if (order->status() != orders::OrderStatus::open &&
                order->status() != orders::OrderStatus::partially_filled) {
                continue;
            }
            static_cast<void>(order->cancel(
                orders::OrderCancellationReason::end_of_data));
            pending_.erase(id);
            order_details_[id] = "end of data";
            order_events_.push_back(OrderEvent{
                current_time_, next_sequence(), order->id(),
                OrderEventKind::canceled, "end of data"});
        }
    }

    [[nodiscard]] BacktestResults build_results() const {
        std::vector<OrderSnapshot> order_snapshots;
        order_snapshots.reserve(orders_.size());
        for (const auto& [id, order] : orders_) {
            order_snapshots.push_back(OrderSnapshot{
                order->id(),
                order->request(),
                order->submitted_at(),
                order->submission_sequence(),
                order->eligible_after_sequence(),
                order->status(),
                order->filled_quantity(),
                order->remaining_quantity(),
                order->stop_triggered(),
                order->rejection_reason(),
                order->cancellation_reason(),
                order_details_.at(id),
            });
        }
        const auto final_snapshot = portfolio_.snapshot();
        std::vector<portfolio::TradeEpisode> open_trades;
        for (const auto& instrument : dataset_.instruments()) {
            const auto* trade = portfolio_.open_trade(instrument.symbol());
            if (trade != nullptr) {
                open_trades.push_back(*trade);
            }
        }
        return BacktestResults{
            equity_curve_,
            std::move(order_snapshots),
            order_events_,
            portfolio_.fills(),
            portfolio_.closed_trades(),
            std::move(open_trades),
            final_snapshot.positions(),
            RunManifest{
                market_data::canonical_hash(dataset_),
                "fnv1a64-v1",
                dataset_.metadata().source_id(),
                std::string{execution::OpenOnlyExecutionModel::model_id()},
                market_data::kBarSchemaVersion,
                config_.random_seed(),
                config_.build_identity(),
                config_.initial_cash(),
                config_.execution_costs(),
                config_.risk_limits(),
                config_.history_capacity(),
                dataset_.gaps().size(),
                normalized_config(config_),
            },
        };
    }

    const BacktestConfig& config_;
    const market_data::ValidatedDataset& dataset_;
    market_data::DeterministicSchedule schedule_;
    market_data::BoundedBarHistory history_;
    portfolio::Portfolio portfolio_;
    execution::OpenOnlyExecutionModel execution_;
    risk::RiskManager risk_;
    strategy::StrategyTestDriver strategy_;
    std::map<std::string, market_data::InstrumentSpec> instruments_;
    std::map<std::uint64_t, std::unique_ptr<orders::OrderRecord>> orders_;
    std::map<std::uint64_t, risk::PendingOrderSnapshot> pending_;
    std::map<std::uint64_t, std::string> order_details_;
    std::vector<OrderEvent> order_events_;
    std::vector<EquityPoint> equity_curve_;
    core::EventSequenceGenerator sequences_;
    core::FillIdGenerator fill_ids_;
    market_data::Timestamp current_time_{};
    std::uint64_t next_command_issue_{1};
    std::uint64_t next_order_id_{1};
};

}  // namespace

BacktestConfig BacktestConfig::create(
    const double initial_cash,
    execution::ExecutionCosts execution_costs,
    risk::RiskLimits risk_limits,
    const std::size_t history_capacity,
    const std::uint64_t random_seed,
    std::string build_identity) {
    if (!std::isfinite(initial_cash) || initial_cash < 0.0) {
        throw std::invalid_argument("backtest initial cash must be finite and non-negative");
    }
    if (history_capacity == 0) {
        throw std::invalid_argument("backtest history capacity must be positive");
    }
    if (build_identity.empty()) {
        throw std::invalid_argument("backtest build identity must not be empty");
    }
    return BacktestConfig{
        initial_cash,
        std::move(execution_costs),
        std::move(risk_limits),
        history_capacity,
        random_seed,
        std::move(build_identity),
    };
}

BacktestConfig::BacktestConfig(
    const double initial_cash,
    execution::ExecutionCosts execution_costs,
    risk::RiskLimits risk_limits,
    const std::size_t history_capacity,
    const std::uint64_t random_seed,
    std::string build_identity)
    : initial_cash_(initial_cash),
      execution_costs_(std::move(execution_costs)),
      risk_limits_(std::move(risk_limits)),
      history_capacity_(history_capacity),
      random_seed_(random_seed),
      build_identity_(std::move(build_identity)) {}

BacktestResults::BacktestResults(
    std::vector<EquityPoint> equity_curve,
    std::vector<OrderSnapshot> orders,
    std::vector<OrderEvent> order_events,
    std::vector<orders::Fill> fills,
    std::vector<portfolio::TradeEpisode> trades,
    std::vector<portfolio::TradeEpisode> open_trades,
    std::vector<portfolio::PositionSnapshot> positions,
    RunManifest manifest)
    : equity_curve_(std::move(equity_curve)),
      orders_(std::move(orders)),
      order_events_(std::move(order_events)),
      fills_(std::move(fills)),
      trades_(std::move(trades)),
      open_trades_(std::move(open_trades)),
      positions_(std::move(positions)),
      manifest_(std::move(manifest)) {}

BacktestResults BacktestEngine::run(
    const market_data::ValidatedDataset& dataset,
    std::shared_ptr<strategy::Strategy> strategy) const {
    bool expected = false;
    if (!running_->compare_exchange_strong(expected, true)) {
        throw std::logic_error("backtest engine run is not reentrant");
    }
    struct Reset final {
        std::shared_ptr<std::atomic_bool> flag;
        ~Reset() { flag->store(false); }
    } reset{running_};
    return RunState{config_, dataset, std::move(strategy)}.run();
}

}  // namespace qte::engine
