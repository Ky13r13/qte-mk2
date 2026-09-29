#pragma once

#include "qte/market_data/bar.hpp"
#include "qte/market_data/schedule.hpp"
#include "qte/orders/fill.hpp"
#include "qte/portfolio/portfolio.hpp"

#include <cstdint>
#include <memory>
#include <optional>
#include <stdexcept>
#include <utility>
#include <variant>
#include <vector>

namespace qte::strategy {

namespace detail {
class StrategyContextState;
}

class StrategyContextError final : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

class StrategyLifecycleError final : public std::logic_error {
public:
    using std::logic_error::logic_error;
};

enum class CommandReceiptStatus {
    pending,
};

class SubmissionReceipt final {
public:
    [[nodiscard]] core::OrderId order_id() const noexcept { return order_id_; }
    [[nodiscard]] CommandReceiptStatus status() const noexcept { return status_; }

private:
    friend class StrategyContext;
    friend class detail::StrategyContextState;
    explicit SubmissionReceipt(core::OrderId order_id)
        : order_id_(order_id), status_(CommandReceiptStatus::pending) {}

    core::OrderId order_id_;
    CommandReceiptStatus status_;
};

class CancellationReceipt final {
public:
    [[nodiscard]] core::OrderId order_id() const noexcept { return order_id_; }
    [[nodiscard]] CommandReceiptStatus status() const noexcept { return status_; }

private:
    friend class StrategyContext;
    friend class detail::StrategyContextState;
    explicit CancellationReceipt(core::OrderId order_id)
        : order_id_(order_id), status_(CommandReceiptStatus::pending) {}

    core::OrderId order_id_;
    CommandReceiptStatus status_;
};

struct SubmitOrderCommand final {
    std::uint64_t issue_sequence;
    core::OrderId order_id;
    orders::OrderRequest request;
};

struct CancelOrderCommand final {
    std::uint64_t issue_sequence;
    core::OrderId order_id;
};

using StrategyCommand = std::variant<SubmitOrderCommand, CancelOrderCommand>;

struct OrderUpdate final {
    core::OrderId order_id;
    market_data::Symbol symbol;
    orders::OrderStatus status;
    core::ShareQuantity filled_quantity;
    core::ShareQuantity remaining_quantity;
    market_data::Timestamp timestamp;
    std::string reason;
};

// A callback-scoped capability. Copies are safe to retain but become unusable
// immediately after their originating callback returns.
class StrategyContext final {
public:
    StrategyContext(const StrategyContext&) = default;
    StrategyContext& operator=(const StrategyContext&) = default;
    StrategyContext(StrategyContext&&) noexcept = default;
    StrategyContext& operator=(StrategyContext&&) noexcept = default;

    [[nodiscard]] portfolio::PortfolioSnapshot portfolio() const;
    [[nodiscard]] std::optional<portfolio::PositionSnapshot> position(
        const market_data::Symbol& symbol) const;
    [[nodiscard]] std::vector<market_data::Bar> history(
        const market_data::Symbol& symbol) const;
    [[nodiscard]] SubmissionReceipt submit_order(orders::OrderRequest request);
    [[nodiscard]] CancellationReceipt cancel_order(core::OrderId order_id);

private:
    friend class StrategyTestDriver;
    StrategyContext(
        std::weak_ptr<detail::StrategyContextState> state,
        std::uint64_t generation)
        : state_(std::move(state)), generation_(generation) {}

    std::weak_ptr<detail::StrategyContextState> state_;
    std::uint64_t generation_;
};

class Strategy {
public:
    virtual ~Strategy() = default;

    virtual void on_start(StrategyContext& context) {
        static_cast<void>(context);
    }
    virtual void on_bar(StrategyContext& context, const market_data::Bar& bar) = 0;
    virtual void on_fill(StrategyContext& context, const orders::Fill& fill) {
        static_cast<void>(context);
        static_cast<void>(fill);
    }
    virtual void on_end(StrategyContext& context) {
        static_cast<void>(context);
    }
    virtual void on_order_update(StrategyContext&, const OrderUpdate&) {}
};

// M6-only synchronous callback harness. It owns exactly one strategy and models
// lifecycle/context/command semantics without implementing replay or execution.
class StrategyTestDriver final {
public:
    explicit StrategyTestDriver(std::shared_ptr<Strategy> strategy);
    ~StrategyTestDriver();

    StrategyTestDriver(const StrategyTestDriver&) = delete;
    StrategyTestDriver& operator=(const StrategyTestDriver&) = delete;
    StrategyTestDriver(StrategyTestDriver&&) = delete;
    StrategyTestDriver& operator=(StrategyTestDriver&&) = delete;

    void start(
        portfolio::PortfolioSnapshot snapshot,
        market_data::BarHistorySnapshot history = {});
    void bar(
        const market_data::Bar& bar,
        portfolio::PortfolioSnapshot snapshot,
        market_data::BarHistorySnapshot history = {});
    void fill(
        const orders::Fill& fill,
        portfolio::PortfolioSnapshot snapshot,
        market_data::BarHistorySnapshot history = {});
    void end(
        portfolio::PortfolioSnapshot snapshot,
        market_data::BarHistorySnapshot history = {});
    void order_update(const OrderUpdate& update,
                      portfolio::PortfolioSnapshot snapshot,
                      market_data::BarHistorySnapshot history = {});

    [[nodiscard]] std::vector<StrategyCommand> buffered_commands() const;
    [[nodiscard]] std::vector<StrategyCommand> take_commands();
    [[nodiscard]] bool failed() const noexcept;
    [[nodiscard]] bool ended() const noexcept;

private:
    enum class Lifecycle {
        ready,
        running,
        ended,
        failed,
    };

    void require_not_in_callback() const;
    [[nodiscard]] StrategyContext begin_callback(
        portfolio::PortfolioSnapshot snapshot,
        market_data::BarHistorySnapshot history,
        bool allow_mutation);
    void finish_callback() noexcept;
    void fail_callback() noexcept;

    std::shared_ptr<Strategy> strategy_;
    std::shared_ptr<detail::StrategyContextState> state_;
    Lifecycle lifecycle_{Lifecycle::ready};
};

}  // namespace qte::strategy
