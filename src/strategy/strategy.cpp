#include "qte/strategy/strategy.hpp"

#include <limits>
#include <mutex>
#include <string>
#include <thread>
#include <utility>

namespace qte::strategy {
namespace detail {

class StrategyContextState final {
public:
    [[nodiscard]] std::uint64_t activate(
        portfolio::PortfolioSnapshot snapshot,
        market_data::BarHistorySnapshot history,
        const bool allow_mutation) {
        const std::lock_guard lock{mutex_};
        if (active_) {
            throw StrategyLifecycleError("strategy callback reentrancy is not allowed");
        }
        if (generation_ == std::numeric_limits<std::uint64_t>::max()) {
            throw StrategyLifecycleError("strategy callback generation exhausted");
        }
        ++generation_;
        active_ = true;
        allow_mutation_ = allow_mutation;
        callback_thread_ = std::this_thread::get_id();
        snapshot_ = std::move(snapshot);
        history_ = std::move(history);
        return generation_;
    }

    void deactivate() noexcept {
        const std::lock_guard lock{mutex_};
        active_ = false;
        allow_mutation_ = false;
        snapshot_.reset();
        history_.reset();
    }

    [[nodiscard]] bool active() const {
        const std::lock_guard lock{mutex_};
        return active_;
    }

    [[nodiscard]] portfolio::PortfolioSnapshot read(
        const std::uint64_t generation) const {
        const std::lock_guard lock{mutex_};
        require_access(generation);
        if (!snapshot_.has_value()) {
            throw StrategyContextError("strategy context has no portfolio snapshot");
        }
        return *snapshot_;
    }

    [[nodiscard]] market_data::BarHistorySnapshot read_history(
        const std::uint64_t generation) const {
        const std::lock_guard lock{mutex_};
        require_access(generation);
        if (!history_.has_value()) {
            throw StrategyContextError("strategy context has no bar history snapshot");
        }
        return *history_;
    }

    [[nodiscard]] SubmissionReceipt submit(
        const std::uint64_t generation,
        orders::OrderRequest request) {
        const std::lock_guard lock{mutex_};
        require_mutation(generation);
        orders::require_valid(request);
        const core::OrderId order_id = order_ids_.next();
        commands_.push_back(SubmitOrderCommand{
            next_issue_sequence(), order_id, std::move(request)});
        return SubmissionReceipt{order_id};
    }

    [[nodiscard]] CancellationReceipt cancel(
        const std::uint64_t generation,
        const core::OrderId order_id) {
        const std::lock_guard lock{mutex_};
        require_mutation(generation);
        commands_.push_back(CancelOrderCommand{next_issue_sequence(), order_id});
        return CancellationReceipt{order_id};
    }

    [[nodiscard]] std::vector<StrategyCommand> commands() const {
        const std::lock_guard lock{mutex_};
        return commands_;
    }

    [[nodiscard]] std::vector<StrategyCommand> take_commands() {
        const std::lock_guard lock{mutex_};
        auto result = std::move(commands_);
        commands_.clear();
        return result;
    }

private:
    void require_access(const std::uint64_t generation) const {
        if (!active_ || generation != generation_) {
            throw StrategyContextError("strategy context is expired");
        }
        if (std::this_thread::get_id() != callback_thread_) {
            throw StrategyContextError(
                "strategy context cannot be used from another thread");
        }
    }

    void require_mutation(const std::uint64_t generation) const {
        require_access(generation);
        if (!allow_mutation_) {
            throw StrategyContextError("strategy commands are forbidden during on_end or on_order_update");
        }
    }

    [[nodiscard]] std::uint64_t next_issue_sequence() {
        if (next_issue_sequence_ == 0) {
            throw StrategyLifecycleError("strategy command sequence exhausted");
        }
        const std::uint64_t result = next_issue_sequence_;
        if (next_issue_sequence_ == std::numeric_limits<std::uint64_t>::max()) {
            next_issue_sequence_ = 0;
        } else {
            ++next_issue_sequence_;
        }
        return result;
    }

    bool active_{false};
    bool allow_mutation_{false};
    std::uint64_t generation_{0};
    std::thread::id callback_thread_;
    std::optional<portfolio::PortfolioSnapshot> snapshot_;
    std::optional<market_data::BarHistorySnapshot> history_;
    core::OrderIdGenerator order_ids_;
    std::uint64_t next_issue_sequence_{1};
    std::vector<StrategyCommand> commands_;
    mutable std::mutex mutex_;
};

}  // namespace detail

namespace {

[[nodiscard]] std::shared_ptr<detail::StrategyContextState> lock_state(
    const std::weak_ptr<detail::StrategyContextState>& state) {
    auto locked = state.lock();
    if (!locked) {
        throw StrategyContextError("strategy context owner no longer exists");
    }
    return locked;
}

}  // namespace

portfolio::PortfolioSnapshot StrategyContext::portfolio() const {
    return lock_state(state_)->read(generation_);
}

std::optional<portfolio::PositionSnapshot> StrategyContext::position(
    const market_data::Symbol& symbol) const {
    const auto snapshot = lock_state(state_)->read(generation_);
    for (const auto& position : snapshot.positions()) {
        if (position.symbol == symbol) {
            return position;
        }
    }
    return std::nullopt;
}

std::vector<market_data::Bar> StrategyContext::history(
    const market_data::Symbol& symbol) const {
    return lock_state(state_)->read_history(generation_).bars(symbol);
}

SubmissionReceipt StrategyContext::submit_order(orders::OrderRequest request) {
    return lock_state(state_)->submit(generation_, std::move(request));
}

CancellationReceipt StrategyContext::cancel_order(const core::OrderId order_id) {
    return lock_state(state_)->cancel(generation_, order_id);
}

StrategyTestDriver::StrategyTestDriver(std::shared_ptr<Strategy> strategy)
    : strategy_(std::move(strategy)),
      state_(std::make_shared<detail::StrategyContextState>()) {
    if (!strategy_) {
        throw std::invalid_argument("strategy test driver requires a strategy");
    }
}

StrategyTestDriver::~StrategyTestDriver() = default;

void StrategyTestDriver::order_update(
    const OrderUpdate& update, portfolio::PortfolioSnapshot snapshot,
    market_data::BarHistorySnapshot history) {
    require_not_in_callback();
    if (lifecycle_ != Lifecycle::running) {
        throw StrategyLifecycleError("on_order_update requires a running strategy");
    }
    auto context = begin_callback(std::move(snapshot), std::move(history), false);
    try {
        strategy_->on_order_update(context, update);
        finish_callback();
    } catch (...) {
        fail_callback();
        throw;
    }
}

void StrategyTestDriver::require_not_in_callback() const {
    if (state_->active()) {
        throw StrategyLifecycleError("strategy callback reentrancy is not allowed");
    }
}

StrategyContext StrategyTestDriver::begin_callback(
    portfolio::PortfolioSnapshot snapshot,
    market_data::BarHistorySnapshot history,
    const bool allow_mutation) {
    const std::uint64_t generation =
        state_->activate(std::move(snapshot), std::move(history), allow_mutation);
    return StrategyContext{state_, generation};
}

void StrategyTestDriver::finish_callback() noexcept {
    state_->deactivate();
}

void StrategyTestDriver::fail_callback() noexcept {
    state_->deactivate();
    lifecycle_ = Lifecycle::failed;
}

void StrategyTestDriver::start(
    portfolio::PortfolioSnapshot snapshot,
    market_data::BarHistorySnapshot history) {
    require_not_in_callback();
    if (lifecycle_ != Lifecycle::ready) {
        throw StrategyLifecycleError("on_start must be the first and only start callback");
    }
    lifecycle_ = Lifecycle::running;
    auto context = begin_callback(std::move(snapshot), std::move(history), true);
    try {
        strategy_->on_start(context);
        finish_callback();
    } catch (...) {
        fail_callback();
        throw;
    }
}

void StrategyTestDriver::bar(
    const market_data::Bar& bar,
    portfolio::PortfolioSnapshot snapshot,
    market_data::BarHistorySnapshot history) {
    require_not_in_callback();
    if (lifecycle_ != Lifecycle::running) {
        throw StrategyLifecycleError("on_bar requires a running strategy");
    }
    market_data::require_valid(bar);
    auto context = begin_callback(std::move(snapshot), std::move(history), true);
    try {
        strategy_->on_bar(context, bar);
        finish_callback();
    } catch (...) {
        fail_callback();
        throw;
    }
}

void StrategyTestDriver::fill(
    const orders::Fill& fill,
    portfolio::PortfolioSnapshot snapshot,
    market_data::BarHistorySnapshot history) {
    require_not_in_callback();
    if (lifecycle_ != Lifecycle::running) {
        throw StrategyLifecycleError("on_fill requires a running strategy");
    }
    auto context = begin_callback(std::move(snapshot), std::move(history), true);
    try {
        strategy_->on_fill(context, fill);
        finish_callback();
    } catch (...) {
        fail_callback();
        throw;
    }
}

void StrategyTestDriver::end(
    portfolio::PortfolioSnapshot snapshot,
    market_data::BarHistorySnapshot history) {
    require_not_in_callback();
    if (lifecycle_ != Lifecycle::running) {
        throw StrategyLifecycleError("on_end requires a running strategy");
    }
    auto context = begin_callback(std::move(snapshot), std::move(history), false);
    try {
        strategy_->on_end(context);
        finish_callback();
        lifecycle_ = Lifecycle::ended;
    } catch (...) {
        fail_callback();
        throw;
    }
}

std::vector<StrategyCommand> StrategyTestDriver::buffered_commands() const {
    require_not_in_callback();
    if (lifecycle_ == Lifecycle::failed) {
        throw StrategyLifecycleError("failed strategy has no processable commands");
    }
    return state_->commands();
}

std::vector<StrategyCommand> StrategyTestDriver::take_commands() {
    require_not_in_callback();
    if (lifecycle_ == Lifecycle::failed) {
        throw StrategyLifecycleError("failed strategy has no processable commands");
    }
    return state_->take_commands();
}

bool StrategyTestDriver::failed() const noexcept {
    return lifecycle_ == Lifecycle::failed;
}

bool StrategyTestDriver::ended() const noexcept {
    return lifecycle_ == Lifecycle::ended;
}

}  // namespace qte::strategy
