#include "qte/strategy/strategy.hpp"

#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <variant>
#include <vector>

namespace {

using namespace std::chrono_literals;
using qte::core::Currency;
using qte::core::EventSequence;
using qte::core::FillId;
using qte::core::OrderId;
using qte::core::PriceGrid;
using qte::core::ShareAmount;
using qte::market_data::Bar;
using qte::market_data::InstrumentSpec;
using qte::market_data::Symbol;
using qte::market_data::Timestamp;
using qte::orders::Fill;
using qte::orders::InvalidOrderRequest;
using qte::orders::OrderRequest;
using qte::orders::OrderSide;
using qte::orders::OrderType;
using qte::orders::TimeInForce;
using qte::portfolio::Portfolio;
using qte::portfolio::PortfolioSnapshot;
using qte::strategy::CancelOrderCommand;
using qte::strategy::CommandReceiptStatus;
using qte::strategy::Strategy;
using qte::strategy::StrategyCommand;
using qte::strategy::StrategyContext;
using qte::strategy::StrategyContextError;
using qte::strategy::StrategyLifecycleError;
using qte::strategy::StrategyTestDriver;
using qte::strategy::SubmitOrderCommand;

int failures = 0;

void check(const bool condition, const std::string_view expression, const int line) {
    if (!condition) {
        std::cerr << "line " << line << ": check failed: " << expression << '\n';
        ++failures;
    }
}

template <typename Exception, typename Function>
void check_throws(
    Function&& function,
    const std::string_view expression,
    const int line) {
    try {
        function();
        std::cerr << "line " << line << ": expected exception from: "
                  << expression << '\n';
        ++failures;
    } catch (const Exception&) {
    } catch (...) {
        std::cerr << "line " << line << ": wrong exception from: "
                  << expression << '\n';
        ++failures;
    }
}

#define CHECK(expression) check((expression), #expression, __LINE__)
#define CHECK_THROWS_AS(expression, exception_type) \
    check_throws<exception_type>([&] { static_cast<void>(expression); }, #expression, __LINE__)

[[nodiscard]] InstrumentSpec instrument() {
    return InstrumentSpec{
        Symbol{"SPY"}, Currency::usd(), PriceGrid::from_tick_size(0.01)};
}

[[nodiscard]] Portfolio portfolio() {
    return Portfolio{1000.0, Currency::usd(), {instrument()}};
}

[[nodiscard]] OrderRequest market_order(
    const OrderSide side,
    const std::int64_t quantity) {
    return OrderRequest{
        .symbol = Symbol{"SPY"},
        .side = side,
        .quantity = ShareAmount::from_count(quantity),
        .type = OrderType::market,
        .limit_price = std::nullopt,
        .stop_price = std::nullopt,
        .time_in_force = TimeInForce::good_til_canceled,
    };
}

[[nodiscard]] Bar bar() {
    return Bar{
        Symbol{"SPY"},
        Timestamp{0ns},
        Timestamp{1ns},
        100.0,
        101.0,
        99.0,
        100.5,
        1000.0,
    };
}

[[nodiscard]] Fill fill() {
    return Fill::create(
        FillId{1},
        OrderId{1},
        Symbol{"SPY"},
        OrderSide::buy,
        ShareAmount::from_count(2),
        Timestamp{2ns},
        EventSequence{2},
        100.0,
        PriceGrid::from_tick_size(0.01).canonicalize(100.0),
        0.1);
}

struct RecordingState final {
    std::vector<std::string> callbacks;
    std::vector<OrderId> receipt_ids;
    std::vector<CommandReceiptStatus> receipt_statuses;
    std::optional<PortfolioSnapshot> retained_snapshot;
    std::optional<StrategyContext> retained_context;
    bool end_read_succeeded{false};
    bool end_submit_rejected{false};
    bool end_cancel_rejected{false};
    bool pending_order_absent_from_position{false};
};

class RecordingStrategy final : public Strategy {
public:
    explicit RecordingStrategy(RecordingState& state) : state_(state) {}

    void on_start(StrategyContext& context) override {
        state_.callbacks.emplace_back("start");
        state_.retained_snapshot = context.portfolio();
        const auto receipt = context.submit_order(market_order(OrderSide::buy, 2));
        state_.receipt_ids.push_back(receipt.order_id());
        state_.receipt_statuses.push_back(receipt.status());
    }

    void on_bar(StrategyContext& context, const Bar& value) override {
        state_.callbacks.emplace_back(
            value.symbol == Symbol{"SPY"} ? "bar:SPY" : "bar:other");
        state_.retained_context = context;
        const auto current_position = context.position(Symbol{"SPY"});
        state_.pending_order_absent_from_position =
            current_position.has_value() && current_position->quantity.is_zero();
        const auto receipt = context.submit_order(market_order(OrderSide::sell, 1));
        state_.receipt_ids.push_back(receipt.order_id());
        state_.receipt_statuses.push_back(receipt.status());
        const auto cancellation = context.cancel_order(state_.receipt_ids.front());
        state_.receipt_statuses.push_back(cancellation.status());
    }

    void on_fill(StrategyContext& context, const Fill& value) override {
        state_.callbacks.emplace_back(
            value.id() == FillId{1} ? "fill:1" : "fill:other");
        state_.retained_snapshot = context.portfolio();
        const auto receipt = context.submit_order(market_order(OrderSide::buy, 1));
        state_.receipt_ids.push_back(receipt.order_id());
        state_.receipt_statuses.push_back(receipt.status());
    }

    void on_end(StrategyContext& context) override {
        state_.callbacks.emplace_back("end");
        state_.end_read_succeeded = context.portfolio().cash() == 1000.0;
        try {
            static_cast<void>(
                context.submit_order(market_order(OrderSide::buy, 1)));
        } catch (const StrategyContextError&) {
            state_.end_submit_rejected = true;
        }
        try {
            static_cast<void>(context.cancel_order(OrderId{1}));
        } catch (const StrategyContextError&) {
            state_.end_cancel_rejected = true;
        }
    }

private:
    RecordingState& state_;
};

class NoopStrategy final : public Strategy {
public:
    void on_bar(StrategyContext&, const Bar&) override {}
};

void lifecycle_and_buffered_commands_are_deterministic() {
    RecordingState state;
    StrategyTestDriver driver{std::make_unique<RecordingStrategy>(state)};
    auto account = portfolio();

    driver.start(account.snapshot());
    CHECK(driver.buffered_commands().size() == 1);
    driver.bar(bar(), account.snapshot());
    CHECK(driver.buffered_commands().size() == 3);
    driver.fill(fill(), account.snapshot());
    CHECK(driver.buffered_commands().size() == 4);
    driver.end(account.snapshot());

    CHECK(driver.ended());
    CHECK(!driver.failed());
    const std::vector<std::string> expected{
        "start", "bar:SPY", "fill:1", "end"};
    CHECK(state.callbacks == expected);
    CHECK(state.receipt_ids ==
          std::vector<OrderId>({OrderId{1}, OrderId{2}, OrderId{3}}));
    CHECK(state.receipt_statuses.size() == 4);
    for (const auto status : state.receipt_statuses) {
        CHECK(status == CommandReceiptStatus::pending);
    }
    CHECK(state.end_read_succeeded);
    CHECK(state.end_submit_rejected);
    CHECK(state.end_cancel_rejected);
    CHECK(state.pending_order_absent_from_position);

    const auto commands = driver.take_commands();
    CHECK(commands.size() == 4);
    if (commands.size() == 4) {
        const auto* first = std::get_if<SubmitOrderCommand>(&commands[0]);
        const auto* second = std::get_if<SubmitOrderCommand>(&commands[1]);
        const auto* third = std::get_if<CancelOrderCommand>(&commands[2]);
        const auto* fourth = std::get_if<SubmitOrderCommand>(&commands[3]);
        CHECK(first != nullptr);
        CHECK(second != nullptr);
        CHECK(third != nullptr);
        CHECK(fourth != nullptr);
        if (first != nullptr) {
            CHECK(first->issue_sequence == 1);
            CHECK(first->order_id == OrderId{1});
            CHECK(first->request.quantity.value() == 2);
        }
        if (second != nullptr) {
            CHECK(second->issue_sequence == 2);
            CHECK(second->order_id == OrderId{2});
            CHECK(second->request.side == OrderSide::sell);
        }
        if (third != nullptr) {
            CHECK(third->issue_sequence == 3);
            CHECK(third->order_id == OrderId{1});
        }
        if (fourth != nullptr) {
            CHECK(fourth->issue_sequence == 4);
            CHECK(fourth->order_id == OrderId{3});
            CHECK(fourth->request.side == OrderSide::buy);
        }
    }
    CHECK(driver.buffered_commands().empty());

    CHECK(state.retained_snapshot.has_value());
    if (state.retained_snapshot.has_value()) {
        CHECK(state.retained_snapshot->cash() == 1000.0);
    }
}

void retained_context_expires_but_returned_snapshots_remain_owned() {
    RecordingState state;
    auto account = portfolio();
    {
        StrategyTestDriver driver{std::make_unique<RecordingStrategy>(state)};
        driver.start(account.snapshot());
        driver.bar(bar(), account.snapshot());
        CHECK(state.retained_context.has_value());
        if (state.retained_context.has_value()) {
            CHECK_THROWS_AS(
                state.retained_context->portfolio(), StrategyContextError);
            CHECK_THROWS_AS(
                state.retained_context->position(Symbol{"SPY"}),
                StrategyContextError);
        }
    }
    CHECK(state.retained_snapshot.has_value());
    if (state.retained_snapshot.has_value()) {
        CHECK(state.retained_snapshot->cash() == 1000.0);
    }
    if (state.retained_context.has_value()) {
        CHECK_THROWS_AS(state.retained_context->portfolio(), StrategyContextError);
    }
}

struct ThreadState final {
    bool cross_thread_rejected{false};
};

class CrossThreadStrategy final : public Strategy {
public:
    explicit CrossThreadStrategy(ThreadState& state) : state_(state) {}

    void on_bar(StrategyContext& context, const Bar&) override {
        StrategyContext retained = context;
        std::thread worker{[this, retained] {
            try {
                static_cast<void>(retained.portfolio());
            } catch (const StrategyContextError&) {
                state_.cross_thread_rejected = true;
            }
        }};
        worker.join();
    }

private:
    ThreadState& state_;
};

void context_rejects_cross_thread_access() {
    ThreadState state;
    StrategyTestDriver driver{std::make_unique<CrossThreadStrategy>(state)};
    auto account = portfolio();
    driver.start(account.snapshot());
    driver.bar(bar(), account.snapshot());
    CHECK(state.cross_thread_rejected);
}

struct ReentrantState final {
    StrategyTestDriver* driver{nullptr};
    bool callback_rejected{false};
    bool drain_rejected{false};
};

class ReentrantStrategy final : public Strategy {
public:
    ReentrantStrategy(ReentrantState& state, PortfolioSnapshot snapshot)
        : state_(state), snapshot_(std::move(snapshot)) {}

    void on_start(StrategyContext&) override {
        try {
            state_.driver->bar(::bar(), snapshot_);
        } catch (const StrategyLifecycleError&) {
            state_.callback_rejected = true;
        }
        try {
            static_cast<void>(state_.driver->take_commands());
        } catch (const StrategyLifecycleError&) {
            state_.drain_rejected = true;
        }
    }

    void on_bar(StrategyContext&, const Bar&) override {}

private:
    ReentrantState& state_;
    PortfolioSnapshot snapshot_;
};

void callbacks_and_command_draining_are_not_reentrant() {
    auto account = portfolio();
    ReentrantState state;
    StrategyTestDriver driver{
        std::make_unique<ReentrantStrategy>(state, account.snapshot())};
    state.driver = &driver;
    driver.start(account.snapshot());
    CHECK(state.callback_rejected);
    CHECK(state.drain_rejected);
    CHECK(!driver.failed());
}

void invalid_lifecycle_transitions_are_rejected() {
    auto account = portfolio();
    StrategyTestDriver driver{std::make_unique<NoopStrategy>()};
    CHECK_THROWS_AS(driver.bar(bar(), account.snapshot()), StrategyLifecycleError);
    CHECK_THROWS_AS(driver.fill(fill(), account.snapshot()), StrategyLifecycleError);
    CHECK_THROWS_AS(driver.end(account.snapshot()), StrategyLifecycleError);
    driver.start(account.snapshot());
    CHECK_THROWS_AS(driver.start(account.snapshot()), StrategyLifecycleError);
    driver.end(account.snapshot());
    CHECK_THROWS_AS(driver.bar(bar(), account.snapshot()), StrategyLifecycleError);
    CHECK_THROWS_AS(driver.fill(fill(), account.snapshot()), StrategyLifecycleError);
    CHECK_THROWS_AS(driver.end(account.snapshot()), StrategyLifecycleError);
    CHECK_THROWS_AS(
        StrategyTestDriver{std::unique_ptr<Strategy>{}}, std::invalid_argument);
}

struct FailureState final {
    std::optional<StrategyContext> retained_context;
};

class ThrowingStrategy final : public Strategy {
public:
    explicit ThrowingStrategy(FailureState& state) : state_(state) {}

    void on_bar(StrategyContext& context, const Bar&) override {
        state_.retained_context = context;
        throw std::runtime_error("strategy callback failed");
    }

private:
    FailureState& state_;
};

void callback_exceptions_fail_the_driver_and_invalidate_context() {
    auto account = portfolio();
    FailureState state;
    StrategyTestDriver driver{std::make_unique<ThrowingStrategy>(state)};
    driver.start(account.snapshot());
    CHECK_THROWS_AS(driver.bar(bar(), account.snapshot()), std::runtime_error);
    CHECK(driver.failed());
    CHECK(!driver.ended());
    CHECK_THROWS_AS(driver.buffered_commands(), StrategyLifecycleError);
    CHECK_THROWS_AS(driver.end(account.snapshot()), StrategyLifecycleError);
    CHECK(state.retained_context.has_value());
    if (state.retained_context.has_value()) {
        CHECK_THROWS_AS(state.retained_context->portfolio(), StrategyContextError);
    }
}

struct ValidationState final {
    bool invalid_rejected{false};
    std::optional<OrderId> valid_id;
};

class ValidationStrategy final : public Strategy {
public:
    explicit ValidationStrategy(ValidationState& state) : state_(state) {}

    void on_start(StrategyContext& context) override {
        auto invalid = market_order(OrderSide::buy, 1);
        invalid.limit_price = PriceGrid::from_tick_size(0.01).canonicalize(100.0);
        try {
            static_cast<void>(context.submit_order(std::move(invalid)));
        } catch (const InvalidOrderRequest&) {
            state_.invalid_rejected = true;
        }
        state_.valid_id =
            context.submit_order(market_order(OrderSide::buy, 1)).order_id();
    }

    void on_bar(StrategyContext&, const Bar&) override {}

private:
    ValidationState& state_;
};

void invalid_intent_is_not_buffered_and_does_not_consume_an_order_id() {
    auto account = portfolio();
    ValidationState state;
    StrategyTestDriver driver{std::make_unique<ValidationStrategy>(state)};
    driver.start(account.snapshot());
    CHECK(state.invalid_rejected);
    CHECK(state.valid_id == std::optional{OrderId{1}});
    CHECK(driver.buffered_commands().size() == 1);
}

}  // namespace

int main() {
    lifecycle_and_buffered_commands_are_deterministic();
    retained_context_expires_but_returned_snapshots_remain_owned();
    context_rejects_cross_thread_access();
    callbacks_and_command_draining_are_not_reentrant();
    invalid_lifecycle_transitions_are_rejected();
    callback_exceptions_fail_the_driver_and_invalidate_context();
    invalid_intent_is_not_buffered_and_does_not_consume_an_order_id();

    if (failures != 0) {
        std::cerr << failures << " strategy test(s) failed\n";
        return EXIT_FAILURE;
    }
    return EXIT_SUCCESS;
}
