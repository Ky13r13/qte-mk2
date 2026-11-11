#include "qte/risk/futures_margin.hpp"

#include <chrono>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <limits>
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
using qte::core::ShareAmount;
using qte::market_data::InstrumentSpec;
using qte::market_data::Symbol;
using qte::market_data::Timestamp;
using qte::orders::Fill;
using qte::orders::OrderRecord;
using qte::orders::OrderRequest;
using qte::orders::OrderSide;
using qte::orders::OrderType;
using qte::portfolio::Portfolio;
using qte::portfolio::ValuationMark;
using qte::risk::FuturesMarginEvaluator;
using qte::risk::FuturesAdmissionCode;
using qte::risk::FuturesMarginCalculationError;
using qte::risk::FuturesMarginRate;
using qte::risk::FuturesMarginSchedule;
using qte::risk::InvalidFuturesMarginInput;
using qte::risk::PendingFuturesOrder;

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

[[nodiscard]] InstrumentSpec future(
    const char* symbol = "NQ", const std::int64_t multiplier = 20,
    const Currency currency = Currency::usd()) {
    return InstrumentSpec::future(
        Symbol{symbol}, currency, PriceGrid::from_tick_size(0.25), multiplier,
        Timestamp{1000ns}, Timestamp{2000ns});
}

[[nodiscard]] FuturesMarginRate rate(
    const char* symbol = "NQ", const std::int64_t multiplier = 20,
    const double initial = 1000.0, const double maintenance = 800.0,
    const double fee = 2.5, const Currency currency = Currency::usd()) {
    return FuturesMarginRate{
        Symbol{symbol}, currency, multiplier, initial, maintenance, fee};
}

[[nodiscard]] FuturesMarginSchedule schedule(
    const std::vector<InstrumentSpec>& universe,
    std::vector<FuturesMarginRate> rates) {
    return FuturesMarginSchedule::create(
        Currency::usd(), "declared-margin-source", "vintage-1",
        Timestamp{100ns}, Timestamp{110ns}, Timestamp{1000ns},
        std::move(rates), universe);
}

[[nodiscard]] OrderRecord open_order(
    const std::uint64_t id, const InstrumentSpec& instrument,
    const OrderSide side, const std::int64_t quantity,
    const Timestamp submitted = Timestamp{120ns}) {
    OrderRecord order{
        OrderId{id},
        OrderRequest{instrument.symbol(), side, ShareAmount::from_count(quantity),
                     OrderType::market, std::nullopt, std::nullopt},
        submitted, EventSequence{id}, EventSequence{id}};
    order.open(instrument);
    return order;
}

void fill_and_mark(
    Portfolio& ledger, const InstrumentSpec& instrument,
    const std::int64_t quantity, const double entry, const double mark,
    const std::uint64_t sequence = 1) {
    ledger.apply_fill(Fill::create(
        FillId{sequence}, OrderId{sequence}, instrument.symbol(), OrderSide::buy,
        ShareAmount::from_count(quantity), Timestamp{120ns}, EventSequence{sequence},
        entry, instrument.price_grid().canonicalize(entry), 0.0,
        instrument.contract_multiplier()));
    ledger.mark(instrument.symbol(), ValuationMark::create(
        Timestamp{130ns}, EventSequence{sequence + 1}, mark));
}

void reports_current_pending_fees_and_conservative_collateral() {
    const auto nq = future();
    const std::vector universe{nq};
    Portfolio ledger{10000.0, Currency::usd(), universe};
    fill_and_mark(ledger, nq, 2, 100.0, 110.0);
    auto buy = open_order(10, nq, OrderSide::buy, 3);
    auto sell = open_order(11, nq, OrderSide::sell, 4);
    const std::vector pending{
        PendingFuturesOrder::capture(buy), PendingFuturesOrder::capture(sell)};
    const FuturesMarginEvaluator evaluator{
        schedule(universe, {rate()}), 500.0, 20ns};
    const auto report = evaluator.evaluate(ledger, pending, Timestamp{140ns});
    CHECK_NEAR(report.current_initial, 2000.0);
    CHECK_NEAR(report.current_maintenance, 1600.0);
    CHECK_NEAR(report.projected_initial, 5000.0);
    CHECK_NEAR(report.additional_pending_initial, 3000.0);
    CHECK_NEAR(report.pending_fees, 17.5);
    CHECK_NEAR(report.eligible_collateral, 10000.0);
    CHECK_NEAR(report.available_after_projected, 4482.5);
    CHECK_NEAR(report.maintenance_excess, 7900.0);
    CHECK(!report.initial_breach);
    CHECK(!report.maintenance_breach);

    const std::vector reversed{pending[1], pending[0]};
    const auto reversed_report = evaluator.evaluate(ledger, reversed, Timestamp{140ns});
    CHECK_NEAR(reversed_report.projected_initial, report.projected_initial);
    CHECK_NEAR(reversed_report.pending_fees, report.pending_fees);
}

void opposing_orders_receive_no_offset_credit_and_partial_uses_remaining() {
    const auto nq = future();
    const std::vector universe{nq};
    Portfolio ledger{10000.0, Currency::usd(), universe};
    auto buy = open_order(1, nq, OrderSide::buy, 10);
    buy.record_fill_quantity(ShareAmount::from_count(4));
    auto sell = open_order(2, nq, OrderSide::sell, 8);
    const std::vector pending{
        PendingFuturesOrder::capture(buy), PendingFuturesOrder::capture(sell)};
    const FuturesMarginEvaluator evaluator{
        schedule(universe, {rate()}), 0.0, 0ns};
    const auto report = evaluator.evaluate(ledger, pending, Timestamp{120ns});
    CHECK_NEAR(report.projected_initial, 8000.0);
    CHECK_NEAR(report.pending_fees, 35.0);
}

void collateral_includes_losses_but_excludes_unsettled_profits() {
    const auto nq = future();
    const std::vector universe{nq};
    const FuturesMarginEvaluator evaluator{
        schedule(universe, {rate()}), 0.0, 20ns};
    Portfolio profit{10000.0, Currency::usd(), universe};
    fill_and_mark(profit, nq, 2, 100.0, 110.0);
    CHECK_NEAR(evaluator.evaluate(profit, {}, Timestamp{140ns}).eligible_collateral,
               10000.0);
    Portfolio loss{10000.0, Currency::usd(), universe};
    fill_and_mark(loss, nq, 2, 100.0, 90.0);
    CHECK_NEAR(evaluator.evaluate(loss, {}, Timestamp{140ns}).eligible_collateral,
               9600.0);
}

void schedule_and_ledger_qualification_fail_closed() {
    const auto nq = future();
    const auto mnq = future("MNQ", 2);
    const std::vector universe{nq, mnq};
    CHECK_THROWS_AS(schedule(universe, {rate()}), InvalidFuturesMarginInput);
    CHECK_THROWS_AS(
        schedule({nq}, {rate("NQ", 2)}), InvalidFuturesMarginInput);
    CHECK_THROWS_AS(
        schedule({nq}, {rate(), rate()}), InvalidFuturesMarginInput);
    CHECK_THROWS_AS(
        schedule({InstrumentSpec{Symbol{"SPY"}, Currency::usd(),
                                 PriceGrid::from_tick_size(0.01)}},
                 {rate("SPY", 1)}),
        InvalidFuturesMarginInput);

    Portfolio wrong_ledger{10000.0, Currency::usd(), {mnq}};
    const FuturesMarginEvaluator evaluator{
        schedule({nq}, {rate()}), 0.0, 20ns};
    CHECK_THROWS_AS(
        evaluator.evaluate(wrong_ledger, {}, Timestamp{120ns}),
        InvalidFuturesMarginInput);
}

void marks_timing_pending_ids_and_terminal_orders_fail_closed() {
    const auto nq = future();
    const std::vector universe{nq};
    const FuturesMarginEvaluator evaluator{
        schedule(universe, {rate()}), 0.0, 5ns};
    Portfolio unmarked{10000.0, Currency::usd(), universe};
    unmarked.apply_fill(Fill::create(
        FillId{1}, OrderId{1}, Symbol{"NQ"}, OrderSide::buy,
        ShareAmount::from_count(1), Timestamp{120ns}, EventSequence{1}, 100.0,
        nq.price_grid().canonicalize(100.0), 0.0, 20));
    CHECK_THROWS_AS(
        evaluator.evaluate(unmarked, {}, Timestamp{140ns}),
        InvalidFuturesMarginInput);
    unmarked.mark(Symbol{"NQ"}, ValuationMark::create(
        Timestamp{130ns}, EventSequence{2}, 100.0));
    CHECK_THROWS_AS(
        evaluator.evaluate(unmarked, {}, Timestamp{140ns}),
        InvalidFuturesMarginInput);

    auto pending = open_order(3, nq, OrderSide::buy, 1);
    const auto captured = PendingFuturesOrder::capture(pending);
    const std::vector duplicates{captured, captured};
    Portfolio flat{10000.0, Currency::usd(), universe};
    CHECK_THROWS_AS(
        evaluator.evaluate(flat, duplicates, Timestamp{120ns}),
        InvalidFuturesMarginInput);
    auto future_order = open_order(4, nq, OrderSide::buy, 1, Timestamp{150ns});
    const std::vector not_yet{PendingFuturesOrder::capture(future_order)};
    CHECK_THROWS_AS(
        evaluator.evaluate(flat, not_yet, Timestamp{120ns}),
        InvalidFuturesMarginInput);
    static_cast<void>(pending.cancel(qte::orders::OrderCancellationReason::user_requested));
    CHECK_THROWS_AS(PendingFuturesOrder::capture(pending), InvalidFuturesMarginInput);
    CHECK_THROWS_AS(
        evaluator.evaluate(flat, {}, Timestamp{100ns}), InvalidFuturesMarginInput);
    CHECK_THROWS_AS(
        evaluator.evaluate(flat, {}, Timestamp{1000ns}), InvalidFuturesMarginInput);
}

void retrospective_flat_ledgers_and_quantity_overflow_fail_closed() {
    const auto nq = future();
    const std::vector universe{nq};
    const FuturesMarginEvaluator evaluator{
        schedule(universe, {rate()}), 0.0, 20ns};

    Portfolio round_trip{10000.0, Currency::usd(), universe};
    round_trip.apply_fill(Fill::create(
        FillId{1}, OrderId{1}, nq.symbol(), OrderSide::buy,
        ShareAmount::from_count(1), Timestamp{130ns}, EventSequence{1}, 100.0,
        nq.price_grid().canonicalize(100.0), 0.0, nq.contract_multiplier()));
    round_trip.apply_fill(Fill::create(
        FillId{2}, OrderId{2}, nq.symbol(), OrderSide::sell,
        ShareAmount::from_count(1), Timestamp{140ns}, EventSequence{2}, 100.0,
        nq.price_grid().canonicalize(100.0), 0.0, nq.contract_multiplier()));
    CHECK_THROWS_AS(
        evaluator.evaluate(round_trip, {}, Timestamp{135ns}),
        InvalidFuturesMarginInput);

    Portfolio flat{10000.0, Currency::usd(), universe};
    auto maximum = open_order(
        20, nq, OrderSide::buy, std::numeric_limits<std::int64_t>::max());
    auto one_more = open_order(21, nq, OrderSide::buy, 1);
    const std::vector excessive{
        PendingFuturesOrder::capture(maximum),
        PendingFuturesOrder::capture(one_more)};
    CHECK_THROWS_AS(
        evaluator.evaluate(flat, excessive, Timestamp{120ns}),
        FuturesMarginCalculationError);
}

void submission_admission_is_bounded_and_nonmutating() {
    const auto nq = future();
    const std::vector universe{nq};
    const FuturesMarginEvaluator evaluator{
        schedule(universe, {rate()}), 0.0, 20ns};
    Portfolio ledger{2500.0, Currency::usd(), universe};
    ledger.mark(nq.symbol(), ValuationMark::create(
        Timestamp{130ns}, EventSequence{1}, 100.0));
    OrderRecord candidate{
        OrderId{40},
        OrderRequest{nq.symbol(), OrderSide::buy, ShareAmount::from_count(2),
                     OrderType::market, std::nullopt, std::nullopt},
        Timestamp{135ns}, EventSequence{2}, EventSequence{2}};
    const auto before = ledger.snapshot();
    const auto accepted = evaluator.evaluate_submission(
        candidate, ledger, {}, Timestamp{140ns});
    CHECK(accepted.code == FuturesAdmissionCode::approved);
    CHECK(accepted.approved());
    CHECK(accepted.projected.has_value());
    CHECK(candidate.status() == qte::orders::OrderStatus::new_order);
    CHECK_NEAR(ledger.cash(), before.cash());

    OrderRecord too_large{
        OrderId{41},
        OrderRequest{nq.symbol(), OrderSide::buy, ShareAmount::from_count(3),
                     OrderType::market, std::nullopt, std::nullopt},
        Timestamp{135ns}, EventSequence{3}, EventSequence{3}};
    const auto rejected = evaluator.evaluate_submission(
        too_large, ledger, {}, Timestamp{140ns});
    CHECK(rejected.code == FuturesAdmissionCode::initial_margin);
    CHECK(!rejected.approved());

    Portfolio unmarked{2500.0, Currency::usd(), universe};
    const auto no_mark = evaluator.evaluate_submission(
        candidate, unmarked, {}, Timestamp{140ns});
    CHECK(no_mark.code == FuturesAdmissionCode::missing_fresh_mark);
    CHECK(!no_mark.projected.has_value());
    CHECK(evaluator.evaluate_submission(
              candidate, unmarked, {}, Timestamp{1000ns}).code ==
          FuturesAdmissionCode::missing_fresh_mark);
    Portfolio marked_but_expired{2500.0, Currency::usd(), universe};
    marked_but_expired.mark(nq.symbol(), ValuationMark::create(
        Timestamp{990ns}, EventSequence{1}, 100.0));
    CHECK_THROWS_AS(
        evaluator.evaluate_submission(
            candidate, marked_but_expired, {}, Timestamp{1000ns}),
        InvalidFuturesMarginInput);
}

void submission_reduction_exception_is_nonreversing() {
    const auto nq = future();
    const std::vector universe{nq};
    const FuturesMarginEvaluator evaluator{
        schedule(universe, {rate("NQ", 20, 1000.0, 800.0, 300.0)}), 0.0, 20ns};
    Portfolio ledger{1000.0, Currency::usd(), universe};
    fill_and_mark(ledger, nq, 2, 100.0, 100.0);
    OrderRecord close{
        OrderId{50},
        OrderRequest{nq.symbol(), OrderSide::sell, ShareAmount::from_count(1),
                     OrderType::market, std::nullopt, std::nullopt},
        Timestamp{135ns}, EventSequence{3}, EventSequence{3}};
    const auto reduction = evaluator.evaluate_submission(
        close, ledger, {}, Timestamp{140ns});
    CHECK(reduction.code == FuturesAdmissionCode::reduction_only);
    CHECK(reduction.approved());

    auto reserved_close = open_order(51, nq, OrderSide::sell, 2, Timestamp{135ns});
    const std::vector competing{PendingFuturesOrder::capture(reserved_close)};
    const auto oversold = evaluator.evaluate_submission(
        close, ledger, competing, Timestamp{140ns});
    CHECK(!oversold.approved());

    const std::vector duplicate{PendingFuturesOrder::capture(
        open_order(50, nq, OrderSide::sell, 1, Timestamp{135ns}))};
    CHECK_THROWS_AS(
        evaluator.evaluate_submission(close, ledger, duplicate, Timestamp{140ns}),
        InvalidFuturesMarginInput);
}

void submission_mark_lifecycle_and_candidate_validation() {
    const auto nq = future();
    const std::vector universe{nq};
    const FuturesMarginEvaluator evaluator{
        schedule(universe, {rate()}), 0.0, 20ns};
    Portfolio held{1000.0, Currency::usd(), universe};
    held.apply_fill(Fill::create(
        FillId{1}, OrderId{60}, nq.symbol(), OrderSide::buy,
        ShareAmount::from_count(1), Timestamp{110ns}, EventSequence{1}, 100.0,
        nq.price_grid().canonicalize(100.0), 0.0, nq.contract_multiplier()));
    held.mark(nq.symbol(), ValuationMark::create(
        Timestamp{120ns}, EventSequence{2}, 100.0));
    OrderRecord close{
        OrderId{61},
        OrderRequest{nq.symbol(), OrderSide::sell, ShareAmount::from_count(1),
                     OrderType::market, std::nullopt, std::nullopt},
        Timestamp{130ns}, EventSequence{3}, EventSequence{3}};
    CHECK(evaluator.evaluate_submission(close, held, {}, Timestamp{140ns}).code ==
          FuturesAdmissionCode::reduction_only);
    CHECK(evaluator.evaluate_submission(close, held, {}, Timestamp{141ns}).code ==
          FuturesAdmissionCode::missing_fresh_mark);

    const auto near_contract = InstrumentSpec::future(
        Symbol{"ES"}, Currency::usd(), PriceGrid::from_tick_size(0.25), 50,
        Timestamp{200ns}, Timestamp{300ns});
    const std::vector near_universe{near_contract};
    const FuturesMarginEvaluator near_evaluator{
        schedule(near_universe, {rate("ES", 50)}), 0.0, 20ns};
    Portfolio near_ledger{10000.0, Currency::usd(), near_universe};
    near_ledger.mark(near_contract.symbol(), ValuationMark::create(
        Timestamp{180ns}, EventSequence{1}, 100.0));
    OrderRecord at_cutoff{
        OrderId{62},
        OrderRequest{near_contract.symbol(), OrderSide::buy,
                     ShareAmount::from_count(1), OrderType::market,
                     std::nullopt, std::nullopt},
        Timestamp{190ns}, EventSequence{2}, EventSequence{2}};
    CHECK(near_evaluator.evaluate_submission(
              at_cutoff, near_ledger, {}, Timestamp{200ns}).code ==
          FuturesAdmissionCode::contract_closed);

    OrderRecord future_dated{
        OrderId{63}, at_cutoff.request(), Timestamp{201ns},
        EventSequence{3}, EventSequence{3}};
    CHECK_THROWS_AS(
        near_evaluator.evaluate_submission(
            future_dated, near_ledger, {}, Timestamp{200ns}),
        InvalidFuturesMarginInput);
    auto already_open = open_order(64, near_contract, OrderSide::buy, 1, Timestamp{190ns});
    CHECK_THROWS_AS(
        near_evaluator.evaluate_submission(
            already_open, near_ledger, {}, Timestamp{195ns}),
        InvalidFuturesMarginInput);

    OrderRecord unknown{
        OrderId{65},
        OrderRequest{Symbol{"UNKNOWN"}, OrderSide::buy, ShareAmount::from_count(1),
                     OrderType::market, std::nullopt, std::nullopt},
        Timestamp{190ns}, EventSequence{4}, EventSequence{4}};
    CHECK_THROWS_AS(
        near_evaluator.evaluate_submission(unknown, near_ledger, {}, Timestamp{195ns}),
        InvalidFuturesMarginInput);
    const auto alien_grid = PriceGrid::from_tick_size(0.10);
    OrderRecord off_grid{
        OrderId{66},
        OrderRequest{near_contract.symbol(), OrderSide::buy, ShareAmount::from_count(1),
                     OrderType::limit, alien_grid.canonicalize(100.10), std::nullopt},
        Timestamp{190ns}, EventSequence{5}, EventSequence{5}};
    CHECK_THROWS_AS(
        near_evaluator.evaluate_submission(off_grid, near_ledger, {}, Timestamp{195ns}),
        qte::orders::InvalidOrderRequest);
}

void submission_margin_codes_and_short_reductions() {
    const auto nq = future();
    const std::vector universe{nq};
    const FuturesMarginEvaluator evaluator{
        schedule(universe, {rate()}), 0.0, 20ns};
    Portfolio initial_only{1500.0, Currency::usd(), universe};
    initial_only.mark(nq.symbol(), ValuationMark::create(
        Timestamp{130ns}, EventSequence{1}, 100.0));
    OrderRecord two{
        OrderId{70},
        OrderRequest{nq.symbol(), OrderSide::buy, ShareAmount::from_count(2),
                     OrderType::market, std::nullopt, std::nullopt},
        Timestamp{135ns}, EventSequence{2}, EventSequence{2}};
    CHECK(evaluator.evaluate_submission(two, initial_only, {}, Timestamp{140ns}).code ==
          FuturesAdmissionCode::initial_margin);

    Portfolio maintenance{700.0, Currency::usd(), universe};
    fill_and_mark(maintenance, nq, 1, 100.0, 100.0);
    OrderRecord opening{
        OrderId{71},
        OrderRequest{nq.symbol(), OrderSide::buy, ShareAmount::from_count(1),
                     OrderType::market, std::nullopt, std::nullopt},
        Timestamp{135ns}, EventSequence{3}, EventSequence{3}};
    CHECK(evaluator.evaluate_submission(
              opening, maintenance, {}, Timestamp{140ns}).code ==
          FuturesAdmissionCode::maintenance_margin);

    Portfolio short_ledger{500.0, Currency::usd(), universe};
    short_ledger.apply_fill(Fill::create(
        FillId{1}, OrderId{72}, nq.symbol(), OrderSide::sell,
        ShareAmount::from_count(2), Timestamp{120ns}, EventSequence{1}, 100.0,
        nq.price_grid().canonicalize(100.0), 0.0, nq.contract_multiplier()));
    short_ledger.mark(nq.symbol(), ValuationMark::create(
        Timestamp{130ns}, EventSequence{2}, 100.0));
    OrderRecord buy_to_close{
        OrderId{73},
        OrderRequest{nq.symbol(), OrderSide::buy, ShareAmount::from_count(1),
                     OrderType::market, std::nullopt, std::nullopt},
        Timestamp{135ns}, EventSequence{3}, EventSequence{3}};
    CHECK(evaluator.evaluate_submission(
              buy_to_close, short_ledger, {}, Timestamp{140ns}).code ==
          FuturesAdmissionCode::reduction_only);
    auto opening_sell = open_order(74, nq, OrderSide::sell, 1, Timestamp{135ns});
    const std::vector opening_pending{PendingFuturesOrder::capture(opening_sell)};
    CHECK(!evaluator.evaluate_submission(
               buy_to_close, short_ledger, opening_pending, Timestamp{140ns}).approved());
}

void submission_reservations_release_exactly_on_cancel() {
    const auto nq = future();
    const std::vector universe{nq};
    const FuturesMarginEvaluator evaluator{
        schedule(universe, {rate()}), 0.0, 20ns};
    Portfolio ledger{2500.0, Currency::usd(), universe};
    ledger.mark(nq.symbol(), ValuationMark::create(
        Timestamp{130ns}, EventSequence{1}, 100.0));
    OrderRecord candidate{
        OrderId{80},
        OrderRequest{nq.symbol(), OrderSide::buy, ShareAmount::from_count(2),
                     OrderType::market, std::nullopt, std::nullopt},
        Timestamp{135ns}, EventSequence{2}, EventSequence{2}};
    auto reservation = open_order(81, nq, OrderSide::buy, 1, Timestamp{135ns});
    std::vector pending{PendingFuturesOrder::capture(reservation)};
    CHECK(!evaluator.evaluate_submission(
               candidate, ledger, pending, Timestamp{140ns}).approved());
    CHECK(reservation.cancel(qte::orders::OrderCancellationReason::user_requested) ==
          qte::orders::CancelResult::canceled);
    pending.clear();
    CHECK(evaluator.evaluate_submission(
              candidate, ledger, pending, Timestamp{140ns}).approved());
    CHECK(ledger.last_event_at() == std::optional{Timestamp{130ns}});
    CHECK(ledger.fill_count() == 0);
    const auto* flat_position = ledger.position(nq.symbol());
    CHECK(flat_position != nullptr);
    if (flat_position != nullptr) {
        CHECK(flat_position->quantity().is_zero());
    }

    Portfolio held{500.0, Currency::usd(), universe};
    fill_and_mark(held, nq, 2, 100.0, 100.0);
    OrderRecord final_close{
        OrderId{82},
        OrderRequest{nq.symbol(), OrderSide::sell, ShareAmount::from_count(1),
                     OrderType::market, std::nullopt, std::nullopt},
        Timestamp{135ns}, EventSequence{3}, EventSequence{3}};
    auto one_close = open_order(83, nq, OrderSide::sell, 1, Timestamp{135ns});
    const std::vector exact{PendingFuturesOrder::capture(one_close)};
    CHECK(evaluator.evaluate_submission(
              final_close, held, exact, Timestamp{140ns}).code ==
          FuturesAdmissionCode::reduction_only);
    auto two_close = open_order(84, nq, OrderSide::sell, 2, Timestamp{135ns});
    const std::vector overshoot{PendingFuturesOrder::capture(two_close)};
    CHECK(!evaluator.evaluate_submission(
               final_close, held, overshoot, Timestamp{140ns}).approved());
    CHECK(held.last_event_at() == std::optional{Timestamp{130ns}});
    CHECK(held.fill_count() == 1);
    const auto* held_position = held.position(nq.symbol());
    CHECK(held_position != nullptr);
    if (held_position != nullptr) {
        CHECK(held_position->quantity().value() == 2);
    }
}

}  // namespace

int main() {
    reports_current_pending_fees_and_conservative_collateral();
    opposing_orders_receive_no_offset_credit_and_partial_uses_remaining();
    collateral_includes_losses_but_excludes_unsettled_profits();
    schedule_and_ledger_qualification_fail_closed();
    marks_timing_pending_ids_and_terminal_orders_fail_closed();
    retrospective_flat_ledgers_and_quantity_overflow_fail_closed();
    submission_admission_is_bounded_and_nonmutating();
    submission_reduction_exception_is_nonreversing();
    submission_mark_lifecycle_and_candidate_validation();
    submission_margin_codes_and_short_reductions();
    submission_reservations_release_exactly_on_cancel();
    if (failures != 0) {
        std::cerr << failures << " test assertion(s) failed\n";
        return EXIT_FAILURE;
    }
    std::cout << "all futures margin tests passed\n";
    return EXIT_SUCCESS;
}
