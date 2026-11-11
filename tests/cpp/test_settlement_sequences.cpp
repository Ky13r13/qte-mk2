#include "qte/portfolio/portfolio.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <stdexcept>

namespace {

using namespace qte;

void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

void same(double actual, double expected) {
    require(std::isfinite(actual) && std::isfinite(expected), "nonfinite comparison");
    require(std::abs(actual - expected) <= 1e-9 + 1e-12 * std::max(std::abs(actual), std::abs(expected)),
            "settlement path changed economic result");
}

double known(std::optional<double> value) {
    require(value.has_value(), "expected an available valuation");
    return *value;
}

market_data::Timestamp time(std::uint64_t value) {
    return market_data::Timestamp{std::chrono::nanoseconds{static_cast<std::int64_t>(value)}};
}

const portfolio::Position& position(const portfolio::Portfolio& ledger) {
    const auto* found = ledger.position(market_data::Symbol{"NQ-fixture"});
    require(found != nullptr, "missing fixture position");
    return *found;
}

void same_economic_state(const portfolio::Portfolio& settled, const portfolio::Portfolio& baseline) {
    const auto& left = position(settled);
    const auto& right = position(baseline);
    require(left.quantity() == right.quantity(), "settlement changed quantity");
    same(left.average_entry_price(), right.average_entry_price());
    same(settled.realized_gross_pnl(), baseline.realized_gross_pnl());
    same(settled.commissions_paid(), baseline.commissions_paid());
    same(known(settled.equity()), known(baseline.equity()));
    same(known(settled.net_pnl()), known(baseline.net_pnl()));
    same(known(settled.unrealized_pnl()), known(baseline.unrealized_pnl()));
    same(known(settled.gross_exposure()), known(baseline.gross_exposure()));
    same(known(settled.net_exposure()), known(baseline.net_exposure()));
    same(known(settled.equity()) - settled.initial_cash(), known(settled.net_pnl()));
    require(settled.fill_count() == baseline.fill_count(), "settlement manufactured fill");
    require(settled.closed_trades().size() == baseline.closed_trades().size(), "settlement manufactured episode");
    for (std::size_t index = 0; index < settled.closed_trades().size(); ++index) {
        same(settled.closed_trades()[index].realized_gross_pnl(), baseline.closed_trades()[index].realized_gross_pnl());
        same(settled.closed_trades()[index].allocated_commissions(), baseline.closed_trades()[index].allocated_commissions());
    }
}

// Every path executes identical fills/marks twice: once with daily variation,
// once with none. Cash differs while open, but economic outcomes must not.
void deterministic_paths_conserve_economic_results() {
    const auto grid = core::PriceGrid::from_tick_size(.25);
    const auto instrument = market_data::InstrumentSpec::future(
        market_data::Symbol{"NQ-fixture"}, core::Currency::usd(), grid, 20,
        time(999999), time(1000000));
    for (std::uint64_t seed = 1; seed <= 32; ++seed) {
        std::uint64_t state = seed;
        const auto next = [&state]() {
            state = state * 6364136223846793005ULL + 1442695040888963407ULL;
            return state;
        };
        portfolio::Portfolio settled{100000, core::Currency::usd(), {instrument}};
        portfolio::Portfolio baseline{100000, core::Currency::usd(), {instrument}};
        std::uint64_t sequence = 0;
        std::uint64_t fill_id = 0;
        std::uint64_t settlement_id = 0;
        for (int step = 0; step <= 100; ++step) {
            const auto target = step == 100 ? 0 : static_cast<std::int64_t>(next() % 11) - 5;
            const auto delta = target - position(settled).quantity().value();
            const double price = 80 + .25 * static_cast<double>(next() % 240);
            if (delta != 0) {
                ++sequence;
                ++fill_id;
                const auto fact = orders::Fill::create(
                    core::FillId{fill_id}, core::OrderId{fill_id}, instrument.symbol(),
                    delta > 0 ? orders::OrderSide::buy : orders::OrderSide::sell,
                    core::ShareAmount::from_count(delta > 0 ? delta : -delta),
                    time(sequence), core::EventSequence{sequence}, price,
                    grid.canonicalize(price), .25 * static_cast<double>(1 + next() % 4), 20);
                settled.apply_fill(fact);
                baseline.apply_fill(fact);
            }
            ++sequence;
            const auto mark = portfolio::ValuationMark::create(time(sequence), core::EventSequence{sequence}, price);
            settled.mark(instrument.symbol(), mark);
            baseline.mark(instrument.symbol(), mark);
            same_economic_state(settled, baseline);
            const double old_cash = settled.cash();
            const auto& old_position = position(settled);
            const double settlement_price = 85.125 + .25 * static_cast<double>(next() % 200);
            const double expected_transfer = static_cast<double>(old_position.quantity().value()) * 20 *
                (settlement_price - known(old_position.settlement_basis_price()));
            ++sequence;
            ++settlement_id;
            const auto fact = portfolio::VariationSettlement::create(
                core::SettlementId{settlement_id}, instrument.symbol(), time(sequence),
                time(sequence), time(sequence), core::EventSequence{sequence}, settlement_price,
                20, "synthetic-path", "v1");
            require(settled.apply_settlement(fact) == portfolio::SettlementApplyResult::applied,
                    "fresh settlement not applied");
            same(settled.cash(), old_cash + expected_transfer);
            same_economic_state(settled, baseline);
            require(settled.apply_settlement(fact) == portfolio::SettlementApplyResult::already_applied,
                    "retry not idempotent");
            same_economic_state(settled, baseline);
        }
        require(position(settled).is_flat(), "final path not flat");
        same(settled.cash(), baseline.cash());
        same(known(position(settled).settlement_basis_price()), 0);
        require(settled.settlement_count() == 101, "missing settlement journal entries");
    }
}

}  // namespace

int main() {
    try {
        deterministic_paths_conserve_economic_results();
        std::cout << "32 settlement paths passed\n";
        return EXIT_SUCCESS;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return EXIT_FAILURE;
    }
}
