#include "qte/risk/risk.hpp"

#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <optional>
#include <span>
#include <stdexcept>
#include <string_view>
#include <utility>
#include <vector>

namespace {

using namespace std::chrono_literals;
using qte::core::Currency;
using qte::core::EventSequence;
using qte::core::FillId;
using qte::core::OrderId;
using qte::core::PriceGrid;
using qte::core::ShareAmount;
using qte::execution::ExecutionCosts;
using qte::execution::FillCandidate;
using qte::execution::MarketOpen;
using qte::execution::OpenOnlyExecutionModel;
using qte::market_data::InstrumentSpec;
using qte::market_data::Symbol;
using qte::market_data::Timestamp;
using qte::orders::Fill;
using qte::orders::OrderRecord;
using qte::orders::OrderRequest;
using qte::orders::OrderSide;
using qte::orders::OrderType;
using qte::orders::TimeInForce;
using qte::portfolio::Portfolio;
using qte::portfolio::ValuationMark;
using qte::risk::InvalidRiskInput;
using qte::risk::PendingOrderSnapshot;
using qte::risk::RiskCalculationError;
using qte::risk::RiskDecision;
using qte::risk::RiskLimits;
using qte::risk::RiskManager;
using qte::risk::RiskRejectionCode;

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

void check_rejected(
    const RiskDecision& decision,
    const RiskRejectionCode expected,
    const int line) {
    check(!decision.approved(), "decision rejected", line);
    check(decision.rejection_code().has_value(), "rejection code exists", line);
    if (decision.rejection_code().has_value()) {
        check(*decision.rejection_code() == expected, "rejection code", line);
    }
    check(!decision.reason().empty(), "rejection reason exists", line);
}

#define CHECK_REJECTED(decision, code) check_rejected((decision), (code), __LINE__)

[[nodiscard]] InstrumentSpec instrument(const char* const symbol = "SPY") {
    return InstrumentSpec{
        Symbol{symbol}, Currency::usd(), PriceGrid::from_tick_size(0.01)};
}

[[nodiscard]] Portfolio portfolio(
    const double cash = 1000.0,
    std::vector<InstrumentSpec> instruments = {instrument()}) {
    return Portfolio{cash, Currency::usd(), std::move(instruments)};
}

[[nodiscard]] OrderRequest request(
    const OrderSide side,
    const std::int64_t quantity,
    const char* const symbol = "SPY") {
    return OrderRequest{
        .symbol = Symbol{symbol},
        .side = side,
        .quantity = ShareAmount::from_count(quantity),
        .type = OrderType::market,
        .limit_price = std::nullopt,
        .stop_price = std::nullopt,
        .time_in_force = TimeInForce::good_til_canceled,
    };
}

[[nodiscard]] OrderRecord open_order(
    const std::uint64_t id,
    const OrderSide side,
    const std::int64_t quantity,
    const char* const symbol = "SPY") {
    OrderRecord order{
        OrderId{id},
        request(side, quantity, symbol),
        Timestamp{100ns},
        EventSequence{id},
        EventSequence{id},
    };
    order.open(instrument(symbol));
    return order;
}

[[nodiscard]] Fill fill(
    const std::uint64_t id,
    const OrderSide side,
    const std::int64_t quantity,
    const double price,
    const double commission = 0.0,
    const char* const symbol = "SPY") {
    return Fill::create(
        FillId{id},
        OrderId{id},
        Symbol{symbol},
        side,
        ShareAmount::from_count(quantity),
        Timestamp{std::chrono::nanoseconds{10 * id}},
        EventSequence{2 * id - 1},
        price,
        PriceGrid::from_tick_size(0.01).canonicalize(price),
        commission);
}

void mark(
    Portfolio& portfolio,
    const char* const symbol,
    const double price,
    const std::uint64_t sequence,
    const std::uint64_t timestamp) {
    portfolio.mark(
        Symbol{symbol},
        ValuationMark::create(
            Timestamp{std::chrono::nanoseconds{timestamp}},
            EventSequence{sequence},
            price));
}

[[nodiscard]] FillCandidate candidate(
    OrderRecord& order,
    const double open_price) {
    const auto result = OpenOnlyExecutionModel{ExecutionCosts::create()}.evaluate(
        order,
        instrument(order.request().symbol.value().c_str()),
        MarketOpen::create(
            order.request().symbol,
            Timestamp{200ns},
            EventSequence{100},
            open_price));
    if (!result.has_value()) {
        throw std::runtime_error("test expected an execution candidate");
    }
    return *result;
}

void limits_are_explicit_validated_and_disableable() {
    const auto defaults = RiskLimits::create();
    CHECK(defaults.max_order_quantity().has_value());
    if (defaults.max_order_quantity().has_value()) {
        CHECK(defaults.max_order_quantity()->value() ==
              std::numeric_limits<std::int64_t>::max());
    }
    CHECK(defaults.max_symbol_allocation() == std::optional{1.0});
    CHECK(defaults.max_gross_leverage() == std::optional{1.0});
    CHECK(!defaults.allow_short());
    CHECK(defaults.cash_floor() == std::optional{0.0});

    const auto disabled = RiskLimits::create(
        std::nullopt, std::nullopt, std::nullopt, true, std::nullopt);
    CHECK(!disabled.max_order_quantity().has_value());
    CHECK(!disabled.max_symbol_allocation().has_value());
    CHECK(!disabled.max_gross_leverage().has_value());
    CHECK(disabled.allow_short());
    CHECK(!disabled.cash_floor().has_value());

    const double nan = std::numeric_limits<double>::quiet_NaN();
    const double infinity = std::numeric_limits<double>::infinity();
    CHECK_THROWS_AS(RiskLimits::create(0), InvalidRiskInput);
    CHECK_THROWS_AS(RiskLimits::create(std::nullopt, -0.1), InvalidRiskInput);
    CHECK_THROWS_AS(
        RiskLimits::create(std::nullopt, nan), InvalidRiskInput);
    CHECK_THROWS_AS(
        RiskLimits::create(std::nullopt, std::nullopt, infinity),
        InvalidRiskInput);
    CHECK_THROWS_AS(
        RiskLimits::create(
            std::nullopt, std::nullopt, std::nullopt, false, nan),
        InvalidRiskInput);
}

void portfolio_risk_snapshots_are_owned_values() {
    auto actual = portfolio();
    mark(actual, "SPY", 100.0, 1, 1);
    const auto snapshot = actual.snapshot();
    mark(actual, "SPY", 200.0, 2, 2);

    CHECK(snapshot.cash() == 1000.0);
    CHECK(snapshot.equity() == std::optional{1000.0});
    CHECK(snapshot.positions().size() == 1);
    if (snapshot.positions().size() == 1) {
        CHECK(snapshot.positions().front().symbol == Symbol{"SPY"});
        CHECK(snapshot.positions().front().mark_price == std::optional{100.0});
    }
}

void submission_requires_valid_inputs_mark_and_positive_equity() {
    RiskManager manager{RiskLimits::create()};
    auto actual = portfolio();
    const auto unmarked = manager.evaluate_submission(
        request(OrderSide::buy, 1), instrument(), actual.snapshot(), {}, 100.0, 0.0);
    CHECK_REJECTED(unmarked, RiskRejectionCode::missing_positive_mark);

    mark(actual, "SPY", 100.0, 1, 1);
    const auto approved = manager.evaluate_submission(
        request(OrderSide::buy, 10), instrument(), actual.snapshot(), {}, 100.0, 0.0);
    CHECK(approved.approved());
    CHECK(!approved.rejection_code().has_value());
    CHECK(approved.reason().empty());

    CHECK_THROWS_AS(
        manager.evaluate_submission(
            request(OrderSide::buy, 1, "QQQ"),
            instrument(),
            actual.snapshot(),
            {},
            100.0,
            0.0),
        InvalidRiskInput);
    CHECK_THROWS_AS(
        manager.evaluate_submission(
            request(OrderSide::buy, 1),
            instrument(),
            actual.snapshot(),
            {},
            0.0,
            0.0),
        InvalidRiskInput);

    auto insolvent = portfolio(100.0);
    insolvent.apply_fill(fill(1, OrderSide::buy, 10, 10.0, 200.0));
    mark(insolvent, "SPY", 10.0, 2, 11);
    const auto rejected = manager.evaluate_submission(
        request(OrderSide::buy, 1),
        instrument(),
        insolvent.snapshot(),
        {},
        10.0,
        0.0);
    CHECK_REJECTED(rejected, RiskRejectionCode::cash_floor);
}

void submission_enforces_order_size_and_long_only() {
    auto actual = portfolio();
    mark(actual, "SPY", 100.0, 1, 1);
    RiskManager sized{RiskLimits::create(10, std::nullopt, std::nullopt, true)};
    CHECK(sized.evaluate_submission(
        request(OrderSide::buy, 10), instrument(), actual.snapshot(), {}, 100.0, 0.0)
              .approved());
    CHECK_REJECTED(
        sized.evaluate_submission(
            request(OrderSide::buy, 11),
            instrument(),
            actual.snapshot(),
            {},
            100.0,
            0.0),
        RiskRejectionCode::max_order_quantity);

    RiskManager long_only{RiskLimits::create(
        std::nullopt, std::nullopt, std::nullopt, false, std::nullopt)};
    CHECK_REJECTED(
        long_only.evaluate_submission(
            request(OrderSide::sell, 1),
            instrument(),
            actual.snapshot(),
            {},
            100.0,
            0.0),
        RiskRejectionCode::short_not_allowed);

    auto positioned = portfolio();
    positioned.apply_fill(fill(1, OrderSide::buy, 10, 100.0));
    mark(positioned, "SPY", 100.0, 2, 11);
    auto first_sell = open_order(2, OrderSide::sell, 8);
    const std::vector pending{
        PendingOrderSnapshot::capture(first_sell, 100.0, 0.0)};
    CHECK_REJECTED(
        long_only.evaluate_submission(
            request(OrderSide::sell, 3),
            instrument(),
            positioned.snapshot(),
            pending,
            100.0,
            0.0),
        RiskRejectionCode::short_not_allowed);
}

void pending_buys_reserve_cash_and_pending_sells_get_no_credit() {
    auto actual = portfolio(1000.0);
    mark(actual, "SPY", 100.0, 1, 1);
    RiskManager manager{RiskLimits::create(
        std::nullopt, std::nullopt, std::nullopt, true, 0.0)};

    auto first_buy = open_order(1, OrderSide::buy, 9);
    std::vector pending_buy{
        PendingOrderSnapshot::capture(first_buy, 100.0, 9.0)};
    CHECK_REJECTED(
        manager.evaluate_submission(
            request(OrderSide::buy, 1),
            instrument(),
            actual.snapshot(),
            pending_buy,
            100.0,
            0.0),
        RiskRejectionCode::cash_floor);

    auto funded = portfolio(100.0);
    funded.apply_fill(fill(1, OrderSide::buy, 10, 10.0));
    mark(funded, "SPY", 10.0, 2, 11);
    auto pending_sell_order = open_order(2, OrderSide::sell, 10);
    std::vector pending_sell{
        PendingOrderSnapshot::capture(pending_sell_order, 10.0, 0.0)};
    CHECK_REJECTED(
        manager.evaluate_submission(
            request(OrderSide::buy, 1),
            instrument(),
            funded.snapshot(),
            pending_sell,
            10.0,
            0.0),
        RiskRejectionCode::cash_floor);
}

void submission_enforces_symbol_allocation_and_total_gross_leverage() {
    auto actual = portfolio(
        1000.0, {instrument("SPY"), instrument("QQQ")});
    mark(actual, "SPY", 100.0, 1, 1);
    mark(actual, "QQQ", 100.0, 2, 2);

    RiskManager symbol_limit{RiskLimits::create(
        std::nullopt, 0.5, std::nullopt, true, std::nullopt)};
    CHECK(symbol_limit.evaluate_submission(
        request(OrderSide::buy, 5), instrument(), actual.snapshot(), {}, 100.0, 0.0)
              .approved());
    CHECK_REJECTED(
        symbol_limit.evaluate_submission(
            request(OrderSide::buy, 6),
            instrument(),
            actual.snapshot(),
            {},
            100.0,
            0.0),
        RiskRejectionCode::max_symbol_allocation);

    auto first = open_order(1, OrderSide::buy, 5, "SPY");
    std::vector pending{
        PendingOrderSnapshot::capture(first, 100.0, 0.0)};
    RiskManager gross_limit{RiskLimits::create(
        std::nullopt, std::nullopt, 0.9, true, std::nullopt)};
    CHECK_REJECTED(
        gross_limit.evaluate_submission(
            request(OrderSide::buy, 5, "QQQ"),
            instrument("QQQ"),
            actual.snapshot(),
            pending,
            100.0,
            0.0),
        RiskRejectionCode::max_gross_leverage);
}

void fill_time_rechecks_actual_gap_costs_and_other_reservations() {
    auto actual = portfolio(2000.0);
    mark(actual, "SPY", 100.0, 1, 1);
    RiskManager manager{RiskLimits::create(
        std::nullopt, std::nullopt, std::nullopt, true, 0.0)};

    auto executing_order = open_order(1, OrderSide::buy, 5);
    auto other_order = open_order(2, OrderSide::buy, 15);
    std::vector pending{
        PendingOrderSnapshot::capture(executing_order, 100.0, 0.0),
        PendingOrderSnapshot::capture(other_order, 100.0, 0.0),
    };
    const auto at_estimate = candidate(executing_order, 100.0);
    CHECK(manager.evaluate_execution(at_estimate, actual.snapshot(), pending).approved());

    const auto gap = candidate(executing_order, 101.0);
    CHECK_REJECTED(
        manager.evaluate_execution(gap, actual.snapshot(), pending),
        RiskRejectionCode::cash_floor);
    CHECK(executing_order.status() == qte::orders::OrderStatus::open);
    CHECK(!executing_order.cancellation_reason().has_value());
    CHECK(actual.fill_count() == 0);
    CHECK(actual.cash() == 2000.0);
}

void fill_time_rechecks_long_only_and_exposure() {
    auto actual = portfolio(1000.0);
    actual.apply_fill(fill(1, OrderSide::buy, 10, 100.0));
    mark(actual, "SPY", 100.0, 2, 11);

    auto sell = open_order(2, OrderSide::sell, 11);
    std::vector sell_pending{
        PendingOrderSnapshot::capture(sell, 100.0, 0.0)};
    const auto sell_candidate = candidate(sell, 100.0);
    RiskManager long_only{RiskLimits::create(
        std::nullopt, std::nullopt, std::nullopt, false, std::nullopt)};
    CHECK_REJECTED(
        long_only.evaluate_execution(
            sell_candidate, actual.snapshot(), sell_pending),
        RiskRejectionCode::short_not_allowed);

    auto empty = portfolio(1000.0);
    mark(empty, "SPY", 100.0, 1, 1);
    auto buy = open_order(1, OrderSide::buy, 6);
    std::vector buy_pending{
        PendingOrderSnapshot::capture(buy, 100.0, 0.0)};
    RiskManager allocation{RiskLimits::create(
        std::nullopt, 0.5, std::nullopt, true, std::nullopt)};
    CHECK_REJECTED(
        allocation.evaluate_execution(candidate(buy, 100.0), empty.snapshot(), buy_pending),
        RiskRejectionCode::max_symbol_allocation);
}

void fill_time_gap_can_invalidate_leverage_after_submission() {
    auto actual = portfolio(1000.0);
    mark(actual, "SPY", 100.0, 1, 1);
    RiskManager manager{RiskLimits::create(
        std::nullopt, std::nullopt, 0.75, true, std::nullopt)};
    CHECK(manager.evaluate_submission(
        request(OrderSide::buy, 5),
        instrument(),
        actual.snapshot(),
        {},
        100.0,
        0.0).approved());

    auto order = open_order(1, OrderSide::buy, 5);
    std::vector pending{
        PendingOrderSnapshot::capture(order, 100.0, 0.0)};
    CHECK_REJECTED(
        manager.evaluate_execution(candidate(order, 200.0), actual.snapshot(), pending),
        RiskRejectionCode::max_gross_leverage);
}

void pure_reductions_are_allowed_through_breached_caps_and_nonpositive_equity() {
    auto breached = portfolio(1000.0);
    breached.apply_fill(fill(1, OrderSide::buy, 10, 100.0));
    mark(breached, "SPY", 200.0, 2, 11);
    RiskManager strict{RiskLimits::create(
        std::nullopt, 0.05, 0.05, false, 0.0)};
    CHECK(strict.evaluate_submission(
        request(OrderSide::sell, 5),
        instrument(),
        breached.snapshot(),
        {},
        200.0,
        0.0).approved());

    auto reduce = open_order(2, OrderSide::sell, 5);
    std::vector reduce_pending{
        PendingOrderSnapshot::capture(reduce, 200.0, 0.0)};
    CHECK(strict.evaluate_execution(
        candidate(reduce, 200.0), breached.snapshot(), reduce_pending).approved());
    const auto* unchanged_position = breached.position(Symbol{"SPY"});
    CHECK(unchanged_position != nullptr);
    if (unchanged_position != nullptr) {
        CHECK(unchanged_position->quantity().value() == 10);
    }

    auto insolvent = portfolio(100.0);
    insolvent.apply_fill(fill(1, OrderSide::sell, 10, 10.0));
    mark(insolvent, "SPY", 30.0, 2, 11);
    CHECK(insolvent.equity().has_value());
    if (insolvent.equity().has_value()) {
        CHECK(*insolvent.equity() == -100.0);
    }
    CHECK(strict.evaluate_submission(
        request(OrderSide::buy, 5),
        instrument(),
        insolvent.snapshot(),
        {},
        30.0,
        0.0).approved());

    auto cover = open_order(2, OrderSide::buy, 5);
    std::vector cover_pending{
        PendingOrderSnapshot::capture(cover, 30.0, 0.0)};
    CHECK(strict.evaluate_execution(
        candidate(cover, 30.0), insolvent.snapshot(), cover_pending).approved());

    const auto increasing = strict.evaluate_submission(
        request(OrderSide::sell, 1),
        instrument(),
        insolvent.snapshot(),
        {},
        30.0,
        0.0);
    CHECK_REJECTED(increasing, RiskRejectionCode::short_not_allowed);

    RiskManager equity_gate{RiskLimits::create(
        std::nullopt, std::nullopt, std::nullopt, true, 0.0)};
    CHECK_REJECTED(
        equity_gate.evaluate_submission(
            request(OrderSide::sell, 1),
            instrument(),
            insolvent.snapshot(),
            {},
            30.0,
            0.0),
        RiskRejectionCode::nonpositive_equity);
}

void pending_snapshot_and_execution_identity_are_strict() {
    auto unopened = open_order(1, OrderSide::buy, 1);
    static_cast<void>(unopened.cancel(qte::orders::OrderCancellationReason::user_requested));
    CHECK_THROWS_AS(
        PendingOrderSnapshot::capture(unopened, 100.0, 0.0),
        InvalidRiskInput);

    auto actual = portfolio();
    mark(actual, "SPY", 100.0, 1, 1);
    auto order = open_order(1, OrderSide::buy, 1);
    const auto fill_candidate = candidate(order, 100.0);
    RiskManager manager{RiskLimits::create()};
    CHECK_THROWS_AS(
        manager.evaluate_execution(fill_candidate, actual.snapshot(), {}),
        InvalidRiskInput);

    const auto reservation = PendingOrderSnapshot::capture(order, 100.0, 0.0);
    const std::vector duplicate_pending{reservation, reservation};
    CHECK_THROWS_AS(
        manager.evaluate_submission(
            request(OrderSide::buy, 1),
            instrument(),
            actual.snapshot(),
            duplicate_pending,
            100.0,
            0.0),
        InvalidRiskInput);
}

void conservative_pending_quantity_overflow_is_explicit() {
    auto actual = portfolio();
    mark(actual, "SPY", 1.0, 1, 1);
    auto first = open_order(
        1, OrderSide::buy, std::numeric_limits<std::int64_t>::max());
    auto second = open_order(2, OrderSide::buy, 1);
    const std::vector pending{
        PendingOrderSnapshot::capture(first, 1.0, 0.0),
        PendingOrderSnapshot::capture(second, 1.0, 0.0),
    };
    RiskManager manager{RiskLimits::create(
        std::nullopt, std::nullopt, std::nullopt, true, std::nullopt)};
    CHECK_THROWS_AS(
        manager.evaluate_submission(
            request(OrderSide::sell, 1),
            instrument(),
            actual.snapshot(),
            pending,
            1.0,
            0.0),
        RiskCalculationError);
    CHECK(qte::risk::to_string(RiskRejectionCode::cash_floor) == "cash_floor");
}

}  // namespace

int main() {
    limits_are_explicit_validated_and_disableable();
    portfolio_risk_snapshots_are_owned_values();
    submission_requires_valid_inputs_mark_and_positive_equity();
    submission_enforces_order_size_and_long_only();
    pending_buys_reserve_cash_and_pending_sells_get_no_credit();
    submission_enforces_symbol_allocation_and_total_gross_leverage();
    fill_time_rechecks_actual_gap_costs_and_other_reservations();
    fill_time_rechecks_long_only_and_exposure();
    fill_time_gap_can_invalidate_leverage_after_submission();
    pure_reductions_are_allowed_through_breached_caps_and_nonpositive_equity();
    pending_snapshot_and_execution_identity_are_strict();
    conservative_pending_quantity_overflow_is_explicit();

    if (failures != 0) {
        std::cerr << failures << " risk test(s) failed\n";
        return EXIT_FAILURE;
    }
    return EXIT_SUCCESS;
}
