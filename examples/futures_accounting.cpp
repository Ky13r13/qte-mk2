// Synthetic direct-ledger workflow, not exchange margin rates or a backtest.
#include "qte/risk/futures_margin.hpp"

#include <chrono>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <vector>

namespace {
using namespace qte;
using namespace std::chrono_literals;

market_data::Timestamp at(std::int64_t value) {
    return market_data::Timestamp{std::chrono::nanoseconds{value}};
}

orders::Fill fill(std::uint64_t id, orders::OrderSide side, double price,
                  std::uint64_t sequence, const market_data::InstrumentSpec& instrument) {
    return orders::Fill::create(
        core::FillId{id}, core::OrderId{id}, instrument.symbol(), side,
        core::ShareAmount::from_count(1), at(static_cast<std::int64_t>(sequence)),
        core::EventSequence{sequence}, price, instrument.price_grid().canonicalize(price),
        2.5, instrument.contract_multiplier());
}

orders::OrderRecord pending(std::uint64_t id, orders::OrderSide side,
                            const market_data::InstrumentSpec& instrument) {
    orders::OrderRecord order{
        core::OrderId{id},
        {instrument.symbol(), side, core::ShareAmount::from_count(2), orders::OrderType::market,
         std::nullopt, std::nullopt},
        at(3), core::EventSequence{3}, core::EventSequence{3}};
    // Artificial accepted-order facts for the budget illustration, not a broker.
    order.open(instrument);
    return order;
}

void show(const char* phase, const portfolio::Portfolio& ledger,
          const risk::FuturesMarginEvaluator& evaluator,
          std::span<const risk::PendingFuturesOrder> pending_orders,
          market_data::Timestamp now) {
    const auto report = evaluator.evaluate(ledger, pending_orders, now);
    const auto equity = ledger.equity();
    if (!equity.has_value()) throw std::runtime_error("demo equity unexpectedly unavailable");
    std::cout << phase << " | cash=" << ledger.cash() << " equity=" << *equity
              << " initial=" << report.current_initial
              << " pending_margin=" << report.additional_pending_initial
              << " pending_fees=" << report.pending_fees
              << " available=" << report.available_after_projected << '\n';
}

void admission_example(const market_data::InstrumentSpec& instrument,
                       const risk::FuturesMarginSchedule& schedule) {
    portfolio::Portfolio ledger{10000, core::Currency::usd(), {instrument}};
    ledger.mark(instrument.symbol(), portfolio::ValuationMark::create(at(1), core::EventSequence{1}, 100));
    const risk::FuturesMarginEvaluator evaluator{schedule, 0, 100ns};
    const auto request = [&](std::uint64_t id, std::int64_t quantity) {
        return orders::OrderRecord{core::OrderId{id},
            {instrument.symbol(), orders::OrderSide::buy, core::ShareAmount::from_count(quantity),
             orders::OrderType::market, std::nullopt, std::nullopt},
            at(2), core::EventSequence{id + 1}, core::EventSequence{id + 1}};
    };
    auto first = request(1, 3);
    const auto accepted = evaluator.evaluate_submission(first, ledger, {}, at(2));
    if (!accepted.approved()) throw std::runtime_error("funded submission unexpectedly rejected");
    first.open(instrument);
    std::cout << "submission: buy 3 accepted\n";

    const std::vector reserved{risk::PendingFuturesOrder::capture(first)};
    auto second = request(2, 2);
    const auto denied = evaluator.evaluate_submission(second, ledger, reserved, at(2));
    if (denied.code != risk::FuturesAdmissionCode::initial_margin) {
        throw std::runtime_error("over-budget submission was not rejected for initial margin");
    }
    second.reject(orders::OrderRejectionReason::risk);
    std::cout << "submission: buy 2 rejected while buy 3 remains pending\n";
    // Only an acknowledged lifecycle transition releases the pending reserve.
    static_cast<void>(first.cancel(orders::OrderCancellationReason::user_requested));
    auto retry = request(3, 2);
    const auto after_cancel = evaluator.evaluate_submission(retry, ledger, {}, at(2));
    if (!after_cancel.approved()) throw std::runtime_error("cancellation did not release the budget");
    retry.open(instrument);
    std::cout << "submission: fresh buy 2 accepted after cancellation\n";
    static_cast<void>(retry.cancel(orders::OrderCancellationReason::user_requested));
    if (ledger.cash() != 10000 || ledger.fill_count() != 0) {
        throw std::runtime_error("submission-only demo mutated portfolio accounting");
    }
    std::cout << "submission decisions created no fills; cash remains 10000.00\n";
}
}  // namespace

int main() {
    try {
        const auto instrument = market_data::InstrumentSpec::future(
            market_data::Symbol{"NQ-demo"}, core::Currency::usd(),
            core::PriceGrid::from_tick_size(.25), 20, at(1000000), at(2000000));
        portfolio::Portfolio ledger{10000, core::Currency::usd(), {instrument}};
        const auto schedule = risk::FuturesMarginSchedule::create(
            core::Currency::usd(), "synthetic-example-not-broker-rates", "v1",
            at(0), at(0), at(1000000),
            {{instrument.symbol(), core::Currency::usd(), 20, 2000, 1500, 2.5}}, {instrument});
        const risk::FuturesMarginEvaluator evaluator{schedule, 0, 100ns};
        std::cout << std::fixed << std::setprecision(2)
                  << "SYNTHETIC accounting + fixed-margin example; no strategy, broker or live orders\n";
        ledger.apply_fill(fill(1, orders::OrderSide::buy, 100, 1, instrument));
        ledger.mark(instrument.symbol(), portfolio::ValuationMark::create(at(2), core::EventSequence{2}, 105));
        show("open", ledger, evaluator, {}, at(2));

        auto buy = pending(10, orders::OrderSide::buy, instrument);
        auto sell = pending(11, orders::OrderSide::sell, instrument);
        const std::vector<risk::PendingFuturesOrder> reservations{
            risk::PendingFuturesOrder::capture(buy), risk::PendingFuturesOrder::capture(sell)};
        show("opposing pending orders (no netting)", ledger, evaluator, reservations, at(4));

        const auto settlement = portfolio::VariationSettlement::create(
            core::SettlementId{1}, instrument.symbol(), at(4), at(5), at(5),
            core::EventSequence{5}, 110, 20, "synthetic-settlement", "v1");
        ledger.apply_settlement(settlement);
        show("settled; old mark unchanged", ledger, evaluator, reservations, at(5));
        if (ledger.apply_settlement(settlement) != portfolio::SettlementApplyResult::already_applied) {
            throw std::runtime_error("duplicate settlement was not ignored");
        }

        static_cast<void>(buy.cancel(orders::OrderCancellationReason::user_requested));
        static_cast<void>(sell.cancel(orders::OrderCancellationReason::user_requested));
        // Accepted cancellations remove reservations; a request alone would not.
        ledger.apply_fill(fill(2, orders::OrderSide::sell, 110, 6, instrument));
        show("closed; pending orders canceled", ledger, evaluator, {}, at(6));
        std::cout << "economic gross PnL=" << ledger.realized_gross_pnl()
                  << " fees=" << ledger.commissions_paid() << '\n';
        admission_example(instrument, schedule);
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "futures example failed: " << error.what() << '\n';
        return 1;
    }
}
