#include "qte/risk/futures_lifecycle.hpp"

#include <chrono>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <optional>
#include <string_view>
#include <vector>

namespace {

using namespace std::chrono_literals;
using qte::core::Currency;
using qte::core::EventSequence;
using qte::core::FillId;
using qte::core::OrderId;
using qte::core::PriceGrid;
using qte::core::SettlementId;
using qte::core::ShareAmount;
using qte::market_data::InstrumentSpec;
using qte::market_data::Symbol;
using qte::market_data::Timestamp;
using qte::orders::CancelResult;
using qte::orders::Fill;
using qte::orders::OrderCancellationReason;
using qte::orders::OrderRecord;
using qte::orders::OrderRejectionReason;
using qte::orders::OrderRequest;
using qte::orders::OrderSide;
using qte::orders::OrderStatus;
using qte::orders::OrderType;
using qte::portfolio::Portfolio;
using qte::portfolio::ValuationMark;
using qte::portfolio::VariationSettlement;
using qte::risk::FuturesAdmissionCode;
using qte::risk::FuturesFillOutcome;
using qte::risk::FuturesLifecycleError;
using qte::risk::FuturesMarginEvaluator;
using qte::risk::FuturesMarginRate;
using qte::risk::FuturesMarginSchedule;
using qte::risk::FuturesOrderLifecycle;
using qte::risk::InvalidFuturesMarginInput;

int failures = 0;

void check(const bool condition, const std::string_view expression, const int line) {
    if (!condition) {
        std::cerr << "line " << line << ": check failed: " << expression << '\n';
        ++failures;
    }
}

template <typename Exception, typename Function>
void check_throws(Function&& function, const std::string_view expression, const int line) {
    try {
        function();
        std::cerr << "line " << line << ": expected exception from: " << expression << '\n';
        ++failures;
    } catch (const Exception&) {
        return;
    } catch (...) {
        std::cerr << "line " << line << ": wrong exception from: " << expression << '\n';
        ++failures;
    }
}

#define CHECK(expression) check((expression), #expression, __LINE__)
#define CHECK_THROWS_AS(expression, exception_type) \
    check_throws<exception_type>([&] { static_cast<void>(expression); }, #expression, __LINE__)
#define CHECK_NEAR(actual, expected) \
    check(std::abs((actual) - (expected)) < 1e-9, #actual, __LINE__)

// Artificial contract and rates; not current exchange or broker values.
[[nodiscard]] InstrumentSpec future(const char* symbol = "NQ") {
    return InstrumentSpec::future(
        Symbol{symbol}, Currency::usd(), PriceGrid::from_tick_size(0.25), 20,
        Timestamp{1000ns}, Timestamp{2000ns});
}

[[nodiscard]] FuturesMarginEvaluator evaluator(const InstrumentSpec& instrument) {
    return FuturesMarginEvaluator{
        FuturesMarginSchedule::create(
            Currency::usd(), "declared-margin-source", "vintage-1",
            Timestamp{100ns}, Timestamp{110ns}, Timestamp{1000ns},
            {FuturesMarginRate{instrument.symbol(), Currency::usd(), 20, 1000.0, 800.0, 2.5}},
            {instrument}),
        0.0, 50ns};
}

[[nodiscard]] FuturesOrderLifecycle lifecycle(const InstrumentSpec& instrument, double cash) {
    return FuturesOrderLifecycle{
        Portfolio{cash, Currency::usd(), {instrument}}, evaluator(instrument)};
}

[[nodiscard]] OrderRecord candidate(
    const std::uint64_t id, const InstrumentSpec& instrument, const OrderSide side,
    const std::int64_t quantity, const Timestamp submitted, const std::uint64_t sequence) {
    return OrderRecord{
        OrderId{id},
        OrderRequest{instrument.symbol(), side, ShareAmount::from_count(quantity),
                     OrderType::market, std::nullopt, std::nullopt},
        submitted, EventSequence{sequence}, EventSequence{sequence}};
}

[[nodiscard]] Fill fill(
    const std::uint64_t id, const std::uint64_t order, const InstrumentSpec& instrument,
    const OrderSide side, const std::int64_t quantity, const Timestamp at,
    const std::uint64_t sequence, const double price, const double commission) {
    return Fill::create(
        FillId{id}, OrderId{order}, instrument.symbol(), side,
        ShareAmount::from_count(quantity), at, EventSequence{sequence}, price,
        instrument.price_grid().canonicalize(price), commission,
        instrument.contract_multiplier());
}

void mark(FuturesOrderLifecycle& owner, const InstrumentSpec& instrument,
          const Timestamp at, const std::uint64_t sequence, const double price) {
    owner.mark(instrument.symbol(), ValuationMark::create(at, EventSequence{sequence}, price));
}

void submission_opens_rejects_and_reserves_from_one_frontier() {
    const auto nq = future();
    auto owner = lifecycle(nq, 2600.0);
    mark(owner, nq, Timestamp{120ns}, 1, 100.0);

    const auto first = owner.submit(
        candidate(1, nq, OrderSide::buy, 2, Timestamp{125ns}, 2), Timestamp{125ns});
    CHECK(first.code == FuturesAdmissionCode::approved);
    CHECK(owner.order(OrderId{1})->status() == OrderStatus::open);
    CHECK(owner.pending().size() == 1);

    // The second order is evaluated against the first's reservation without
    // the caller assembling pending state: 3000 + 7.5 exceeds 2600.
    const auto second = owner.submit(
        candidate(2, nq, OrderSide::buy, 1, Timestamp{126ns}, 3), Timestamp{126ns});
    CHECK(second.code == FuturesAdmissionCode::initial_margin);
    const auto* rejected = owner.order(OrderId{2});
    CHECK(rejected->status() == OrderStatus::rejected);
    CHECK(rejected->rejection_reason() == OrderRejectionReason::risk);
    CHECK(owner.submission_code(OrderId{2}) == FuturesAdmissionCode::initial_margin);
    CHECK(owner.pending().size() == 1);
    CHECK(owner.order_count() == 2);

    const auto report = owner.report(Timestamp{130ns});
    CHECK_NEAR(report.projected_initial, 2000.0);
    CHECK_NEAR(report.pending_fees, 5.0);
    CHECK_NEAR(report.available_after_projected, 595.0);

    auto duplicate = candidate(1, nq, OrderSide::sell, 1, Timestamp{127ns}, 4);
    CHECK_THROWS_AS(owner.submit(std::move(duplicate), Timestamp{127ns}), FuturesLifecycleError);
    CHECK(duplicate.status() == OrderStatus::new_order);
    auto early = candidate(3, nq, OrderSide::buy, 1, Timestamp{200ns}, 5);
    CHECK_THROWS_AS(owner.submit(std::move(early), Timestamp{130ns}), InvalidFuturesMarginInput);
    CHECK(early.status() == OrderStatus::new_order);
    CHECK(owner.order_count() == 2);
    CHECK(owner.order(OrderId{3}) == nullptr);
    CHECK(!owner.submission_code(OrderId{3}).has_value());

    const auto stale = owner.submit(
        candidate(4, nq, OrderSide::buy, 1, Timestamp{300ns}, 6), Timestamp{300ns});
    CHECK(stale.code == FuturesAdmissionCode::missing_fresh_mark);
    CHECK(!stale.projected.has_value());
    CHECK(owner.order(OrderId{4})->status() == OrderStatus::rejected);
    CHECK(owner.pending().size() == 1);
    CHECK(owner.ledger().fill_count() == 0);
}

void fills_commit_atomically_and_redelivery_is_idempotent() {
    const auto nq = future();
    auto owner = lifecycle(nq, 2600.0);
    mark(owner, nq, Timestamp{120ns}, 1, 100.0);
    static_cast<void>(owner.submit(
        candidate(1, nq, OrderSide::buy, 2, Timestamp{125ns}, 2), Timestamp{125ns}));
    static_cast<void>(owner.submit(
        candidate(2, nq, OrderSide::buy, 1, Timestamp{126ns}, 3), Timestamp{126ns}));

    const auto first = fill(1, 1, nq, OrderSide::buy, 1, Timestamp{140ns}, 5, 100.0, 3.0);
    const auto applied = owner.apply_fill(first);
    CHECK(applied.outcome == FuturesFillOutcome::applied);
    CHECK(applied.admission.has_value() &&
          applied.admission->code == FuturesAdmissionCode::approved);
    CHECK(owner.ledger().fill_count() == 1);
    CHECK_NEAR(owner.ledger().cash(), 2597.0);
    CHECK(owner.order(OrderId{1})->status() == OrderStatus::partially_filled);
    CHECK(owner.pending().size() == 1);
    CHECK(owner.pending().front().remaining_quantity().value() == 1);
    CHECK_NEAR(owner.report(Timestamp{140ns}).pending_fees, 2.5);

    const auto redelivered = owner.apply_fill(first);
    CHECK(redelivered.outcome == FuturesFillOutcome::already_applied);
    CHECK(!redelivered.admission.has_value());
    CHECK(owner.ledger().fill_count() == 1);
    CHECK_NEAR(owner.ledger().cash(), 2597.0);
    CHECK(owner.order(OrderId{1})->remaining_quantity().value() == 1);

    CHECK_THROWS_AS(owner.apply_fill(
        fill(1, 1, nq, OrderSide::buy, 1, Timestamp{140ns}, 5, 100.0, 4.0)),
        FuturesLifecycleError);
    CHECK_THROWS_AS(owner.apply_fill(
        fill(2, 99, nq, OrderSide::buy, 1, Timestamp{145ns}, 6, 100.0, 2.5)),
        FuturesLifecycleError);
    CHECK_THROWS_AS(owner.apply_fill(
        fill(2, 2, nq, OrderSide::buy, 1, Timestamp{145ns}, 6, 100.0, 2.5)),
        FuturesLifecycleError);
    CHECK_THROWS_AS(owner.apply_fill(
        fill(3, 1, nq, OrderSide::buy, 1, Timestamp{145ns}, 6, 100.0, 2.5)),
        qte::portfolio::OutOfSequencePortfolioFill);

    const auto stale = owner.apply_fill(
        fill(2, 1, nq, OrderSide::buy, 1, Timestamp{200ns}, 6, 100.0, 2.5));
    CHECK(stale.outcome == FuturesFillOutcome::denied);
    CHECK(stale.admission->code == FuturesAdmissionCode::missing_fresh_mark);
    CHECK(owner.ledger().fill_count() == 1);
    CHECK_NEAR(owner.ledger().cash(), 2597.0);
    CHECK(owner.order(OrderId{1})->remaining_quantity().value() == 1);

    mark(owner, nq, Timestamp{150ns}, 6, 101.0);
    const auto finish = owner.apply_fill(
        fill(2, 1, nq, OrderSide::buy, 1, Timestamp{160ns}, 7, 101.0, 2.5));
    CHECK(finish.outcome == FuturesFillOutcome::applied);
    CHECK(owner.order(OrderId{1})->status() == OrderStatus::filled);
    CHECK(owner.pending().empty());
    CHECK_NEAR(owner.report(Timestamp{160ns}).pending_fees, 0.0);
    CHECK_NEAR(owner.ledger().cash(), 2594.5);
    CHECK(owner.ledger().position(nq.symbol())->quantity().value() == 2);
    CHECK(owner.cancel(OrderId{1}, OrderCancellationReason::user_requested) ==
          CancelResult::already_terminal);
}

void acknowledged_cancel_releases_reservation() {
    const auto nq = future();
    auto owner = lifecycle(nq, 2100.0);
    mark(owner, nq, Timestamp{120ns}, 1, 100.0);
    CHECK(owner.submit(candidate(1, nq, OrderSide::buy, 2, Timestamp{125ns}, 2),
                       Timestamp{125ns}).code == FuturesAdmissionCode::approved);
    CHECK(owner.submit(candidate(2, nq, OrderSide::buy, 2, Timestamp{126ns}, 3),
                       Timestamp{126ns}).code == FuturesAdmissionCode::initial_margin);

    CHECK(owner.cancel(OrderId{1}, OrderCancellationReason::user_requested) ==
          CancelResult::canceled);
    CHECK(owner.pending().empty());
    CHECK_NEAR(owner.report(Timestamp{130ns}).projected_initial, 0.0);
    CHECK(owner.cancel(OrderId{1}, OrderCancellationReason::user_requested) ==
          CancelResult::already_terminal);
    CHECK(owner.cancel(OrderId{2}, OrderCancellationReason::user_requested) ==
          CancelResult::already_terminal);
    CHECK_THROWS_AS(owner.cancel(OrderId{9}, OrderCancellationReason::user_requested),
                    FuturesLifecycleError);

    CHECK(owner.submit(candidate(3, nq, OrderSide::buy, 2, Timestamp{130ns}, 4),
                       Timestamp{130ns}).code == FuturesAdmissionCode::approved);
    CHECK_THROWS_AS(owner.apply_fill(
        fill(1, 1, nq, OrderSide::buy, 1, Timestamp{140ns}, 5, 100.0, 2.5)),
        FuturesLifecycleError);
    CHECK(owner.ledger().fill_count() == 0);
}

void original_reduction_code_survives_account_recovery() {
    const auto nq = future();
    auto owner = lifecycle(nq, 2010.0);
    mark(owner, nq, Timestamp{120ns}, 1, 100.0);
    CHECK(owner.submit(candidate(1, nq, OrderSide::buy, 2, Timestamp{125ns}, 2),
                       Timestamp{125ns}).code == FuturesAdmissionCode::approved);
    CHECK(owner.apply_fill(fill(1, 1, nq, OrderSide::buy, 2, Timestamp{130ns}, 3, 100.0, 6.0))
              .outcome == FuturesFillOutcome::applied);
    CHECK_NEAR(owner.ledger().cash(), 2004.0);

    // Closing both contracts would reserve 2000 initial plus 5 in fees against
    // 2004 collateral, so only the nonreversing reduction exception admits it.
    CHECK(owner.submit(candidate(2, nq, OrderSide::sell, 2, Timestamp{135ns}, 4),
                       Timestamp{135ns}).code == FuturesAdmissionCode::reduction_only);

    // Variation settlement at 150 moves 2 * 20 * 50 = 2000 into cash. The
    // account recovers, so an opening buy is admitted normally.
    CHECK(owner.apply_settlement(VariationSettlement::create(
              SettlementId{1}, nq.symbol(), Timestamp{140ns}, Timestamp{140ns},
              Timestamp{140ns}, EventSequence{5}, 150.0, 20, "declared-settlement", "v1")) ==
          qte::portfolio::SettlementApplyResult::applied);
    mark(owner, nq, Timestamp{140ns}, 6, 150.0);
    CHECK_NEAR(owner.ledger().cash(), 4004.0);
    CHECK(owner.submit(candidate(3, nq, OrderSide::buy, 1, Timestamp{145ns}, 7),
                       Timestamp{145ns}).code == FuturesAdmissionCode::approved);

    // The owner retained reduction_only for order 2. With an opposing opening
    // reservation outstanding, its fill no longer qualifies as nonreversing.
    const auto closing = fill(2, 2, nq, OrderSide::sell, 1, Timestamp{150ns}, 8, 150.0, 2.5);
    const auto conflict = owner.apply_fill(closing);
    CHECK(conflict.outcome == FuturesFillOutcome::denied);
    CHECK(conflict.admission->code == FuturesAdmissionCode::reduction_conflict);
    CHECK(owner.ledger().fill_count() == 1);
    CHECK(owner.order(OrderId{2})->remaining_quantity().value() == 2);

    CHECK(owner.cancel(OrderId{3}, OrderCancellationReason::user_requested) ==
          CancelResult::canceled);
    CHECK(owner.apply_fill(closing).outcome == FuturesFillOutcome::applied);
    CHECK(owner.order(OrderId{2})->status() == OrderStatus::partially_filled);
    CHECK(owner.ledger().position(nq.symbol())->quantity().value() == 1);

    // Futures cash moves by C*m*(P-B) - F on reduction; B is 150 after settlement.
    const auto& ledger = owner.ledger();
    CHECK_NEAR(ledger.cash(), 4001.5);
    CHECK_NEAR(ledger.realized_gross_pnl(), 1000.0);
    CHECK_NEAR(ledger.commissions_paid(), 8.5);
    CHECK_NEAR(*ledger.equity() - ledger.initial_cash(),
               ledger.realized_gross_pnl() + *ledger.unrealized_pnl() -
                   ledger.commissions_paid());
}

void construction_requires_matching_universe_and_no_unowned_fills() {
    const auto nq = future();
    const auto es = future("ES");
    CHECK_THROWS_AS((FuturesOrderLifecycle{Portfolio{1000.0, Currency::usd(), {nq}},
                                           evaluator(es)}),
                    InvalidFuturesMarginInput);
    CHECK_THROWS_AS((FuturesOrderLifecycle{Portfolio{1000.0, Currency::usd(), {nq, es}},
                                           evaluator(nq)}),
                    InvalidFuturesMarginInput);

    Portfolio used{5000.0, Currency::usd(), {nq}};
    used.apply_fill(fill(1, 1, nq, OrderSide::buy, 1, Timestamp{120ns}, 1, 100.0, 0.0));
    CHECK_THROWS_AS((FuturesOrderLifecycle{std::move(used), evaluator(nq)}),
                    InvalidFuturesMarginInput);
}

}  // namespace

int main() {
    submission_opens_rejects_and_reserves_from_one_frontier();
    fills_commit_atomically_and_redelivery_is_idempotent();
    acknowledged_cancel_releases_reservation();
    original_reduction_code_survives_account_recovery();
    construction_requires_matching_universe_and_no_unowned_fills();
    if (failures != 0) {
        std::cerr << failures << " test assertion(s) failed\n";
        return EXIT_FAILURE;
    }
    std::cout << "all futures lifecycle tests passed\n";
    return EXIT_SUCCESS;
}
