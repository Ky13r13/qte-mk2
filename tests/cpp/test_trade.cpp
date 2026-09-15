#include "qte/portfolio/portfolio.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <optional>
#include <string_view>
#include <utility>
#include <vector>

namespace {

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
using qte::orders::OrderSide;
using qte::portfolio::DuplicatePortfolioFill;
using qte::portfolio::Portfolio;
using qte::portfolio::PositionDirection;
using qte::portfolio::TradeOutcome;

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

[[nodiscard]] bool near(const double left, const double right) {
    return std::abs(left - right) <=
        1.0e-9 + 1.0e-12 * std::max(std::abs(left), std::abs(right));
}

#define CHECK_NEAR(actual, expected) check(near((actual), (expected)), #actual, __LINE__)

[[nodiscard]] InstrumentSpec instrument(const char* const symbol = "SPY") {
    return InstrumentSpec{
        Symbol{symbol}, Currency::usd(), PriceGrid::from_tick_size(0.01)};
}

[[nodiscard]] Portfolio portfolio(
    std::vector<InstrumentSpec> instruments = {instrument()}) {
    return Portfolio{1000.0, Currency::usd(), std::move(instruments)};
}

[[nodiscard]] Fill fill(
    const std::uint64_t id,
    const OrderSide side,
    const std::int64_t quantity,
    const double price,
    const double commission = 0.0,
    const char* const symbol = "SPY") {
    const auto grid = PriceGrid::from_tick_size(0.01);
    return Fill::create(
        FillId{id},
        OrderId{id},
        Symbol{symbol},
        side,
        ShareAmount::from_count(quantity),
        Timestamp{std::chrono::nanoseconds{id}},
        EventSequence{id},
        price,
        grid.canonicalize(price),
        commission);
}

void open_episodes_are_separate_from_closed_trade_results() {
    auto actual = portfolio();
    actual.apply_fill(fill(1, OrderSide::buy, 10, 10.0, 1.0));

    CHECK(actual.closed_trades().empty());
    CHECK(actual.open_trade(Symbol{"QQQ"}) == nullptr);
    const auto* open = actual.open_trade(Symbol{"SPY"});
    CHECK(open != nullptr);
    if (open != nullptr) {
        CHECK(open->symbol() == Symbol{"SPY"});
        CHECK(open->direction() == PositionDirection::long_position);
        CHECK(open->opening_fill_id() == FillId{1});
        CHECK(open->opened_at() == Timestamp{std::chrono::nanoseconds{1}});
        CHECK(open->opening_sequence() == EventSequence{1});
        CHECK(!open->closing_fill_id().has_value());
        CHECK(!open->closed_at().has_value());
        CHECK(!open->closing_sequence().has_value());
        CHECK(open->opened_quantity() == 10);
        CHECK(open->closed_quantity() == 0);
        CHECK(open->remaining_quantity() == 10);
        CHECK_NEAR(open->realized_gross_pnl(), 0.0);
        CHECK_NEAR(open->allocated_commissions(), 1.0);
        CHECK_NEAR(open->net_realized_pnl(), -1.0);
        CHECK(!open->is_closed());
        CHECK(!open->outcome().has_value());
    }
}

void scaling_and_reductions_remain_one_flat_to_flat_episode() {
    auto actual = portfolio();
    actual.apply_fill(fill(1, OrderSide::buy, 10, 10.0, 1.0));
    actual.apply_fill(fill(2, OrderSide::buy, 5, 12.0, 0.5));
    actual.apply_fill(fill(3, OrderSide::sell, 5, 14.0, 0.5));

    CHECK(actual.closed_trades().empty());
    const auto* reduced = actual.open_trade(Symbol{"SPY"});
    CHECK(reduced != nullptr);
    if (reduced != nullptr) {
        CHECK(reduced->opened_quantity() == 15);
        CHECK(reduced->closed_quantity() == 5);
        CHECK(reduced->remaining_quantity() == 10);
        CHECK_NEAR(reduced->realized_gross_pnl(), 50.0 / 3.0);
        CHECK_NEAR(reduced->allocated_commissions(), 2.0);
    }

    actual.apply_fill(fill(4, OrderSide::sell, 10, 13.0, 1.0));
    CHECK(actual.open_trade(Symbol{"SPY"}) == nullptr);
    CHECK(actual.closed_trades().size() == 1);
    if (actual.closed_trades().size() == 1) {
        const auto& closed = actual.closed_trades()[0];
        CHECK(closed.direction() == PositionDirection::long_position);
        CHECK(closed.opening_fill_id() == FillId{1});
        CHECK(closed.closing_fill_id() == FillId{4});
        CHECK(closed.opened_quantity() == 15);
        CHECK(closed.closed_quantity() == 15);
        CHECK(closed.remaining_quantity() == 0);
        CHECK_NEAR(closed.realized_gross_pnl(), 40.0);
        CHECK_NEAR(closed.allocated_commissions(), 3.0);
        CHECK_NEAR(closed.net_realized_pnl(), 37.0);
        CHECK(closed.is_closed());
        CHECK(closed.outcome() == TradeOutcome::winning);
    }
}

void reversals_close_one_episode_and_open_the_opposite_direction() {
    auto actual = portfolio();
    actual.apply_fill(fill(1, OrderSide::buy, 10, 10.0, 1.0));
    actual.apply_fill(fill(2, OrderSide::sell, 15, 12.0, 3.0));

    CHECK(actual.closed_trades().size() == 1);
    if (actual.closed_trades().size() == 1) {
        const auto& long_trade = actual.closed_trades()[0];
        CHECK(long_trade.direction() == PositionDirection::long_position);
        CHECK(long_trade.closing_fill_id() == FillId{2});
        CHECK_NEAR(long_trade.realized_gross_pnl(), 20.0);
        CHECK_NEAR(long_trade.allocated_commissions(), 3.0);
        CHECK_NEAR(long_trade.net_realized_pnl(), 17.0);
    }

    const auto* short_trade = actual.open_trade(Symbol{"SPY"});
    CHECK(short_trade != nullptr);
    if (short_trade != nullptr) {
        CHECK(short_trade->direction() == PositionDirection::short_position);
        CHECK(short_trade->opening_fill_id() == FillId{2});
        CHECK(short_trade->opened_quantity() == 5);
        CHECK(short_trade->remaining_quantity() == 5);
        CHECK_NEAR(short_trade->allocated_commissions(), 1.0);
    }

    actual.apply_fill(fill(3, OrderSide::buy, 5, 11.0, 1.0));
    CHECK(actual.open_trade(Symbol{"SPY"}) == nullptr);
    CHECK(actual.closed_trades().size() == 2);
    if (actual.closed_trades().size() == 2) {
        const auto& closed_short = actual.closed_trades()[1];
        CHECK(closed_short.direction() == PositionDirection::short_position);
        CHECK(closed_short.opening_fill_id() == FillId{2});
        CHECK(closed_short.closing_fill_id() == FillId{3});
        CHECK_NEAR(closed_short.realized_gross_pnl(), 5.0);
        CHECK_NEAR(closed_short.allocated_commissions(), 2.0);
        CHECK_NEAR(closed_short.net_realized_pnl(), 3.0);
    }

    CHECK_NEAR(actual.realized_gross_pnl(), 25.0);
    CHECK_NEAR(actual.commissions_paid(), 5.0);
    CHECK(actual.net_pnl().has_value());
    if (actual.net_pnl().has_value()) {
        CHECK_NEAR(*actual.net_pnl(), 20.0);
    }
}

void reversal_fee_remainder_is_conserved_exactly() {
    auto actual = portfolio();
    actual.apply_fill(fill(1, OrderSide::buy, 2, 10.0));
    actual.apply_fill(fill(2, OrderSide::sell, 3, 11.0, 1.0));

    CHECK(actual.closed_trades().size() == 1);
    const auto* open = actual.open_trade(Symbol{"SPY"});
    CHECK(open != nullptr);
    if (actual.closed_trades().size() == 1 && open != nullptr) {
        const double closing_fee = actual.closed_trades()[0].allocated_commissions();
        const double opening_fee = open->allocated_commissions();
        CHECK(closing_fee + opening_fee == 1.0);
        CHECK_NEAR(closing_fee, 2.0 / 3.0);
        CHECK_NEAR(opening_fee, 1.0 / 3.0);
    }
}

void short_to_long_reversal_uses_the_same_fee_allocation_contract() {
    auto actual = portfolio();
    actual.apply_fill(fill(1, OrderSide::sell, 4, 10.0, 0.4));
    actual.apply_fill(fill(2, OrderSide::buy, 6, 8.0, 1.2));

    CHECK(actual.closed_trades().size() == 1);
    const auto* open = actual.open_trade(Symbol{"SPY"});
    CHECK(open != nullptr);
    if (actual.closed_trades().size() == 1 && open != nullptr) {
        const auto& closed_short = actual.closed_trades()[0];
        CHECK(closed_short.direction() == PositionDirection::short_position);
        CHECK(closed_short.opened_quantity() == 4);
        CHECK(closed_short.closed_quantity() == 4);
        CHECK_NEAR(closed_short.realized_gross_pnl(), 8.0);
        CHECK_NEAR(closed_short.allocated_commissions(), 1.2);
        CHECK_NEAR(closed_short.net_realized_pnl(), 6.8);

        CHECK(open->direction() == PositionDirection::long_position);
        CHECK(open->opening_fill_id() == FillId{2});
        CHECK(open->opened_quantity() == 2);
        CHECK(open->remaining_quantity() == 2);
        CHECK_NEAR(open->allocated_commissions(), 0.4);
        CHECK_NEAR(
            closed_short.allocated_commissions() + open->allocated_commissions(),
            1.6);
    }
}

void outcomes_use_closed_episode_net_pnl_and_exact_breakeven() {
    auto actual = portfolio(
        {instrument("WIN"), instrument("LOSS"), instrument("EVEN")});
    actual.apply_fill(fill(1, OrderSide::sell, 1, 10.0, 0.0, "WIN"));
    actual.apply_fill(fill(2, OrderSide::buy, 1, 9.0, 0.0, "WIN"));
    actual.apply_fill(fill(3, OrderSide::buy, 1, 10.0, 0.0, "LOSS"));
    actual.apply_fill(fill(4, OrderSide::sell, 1, 9.0, 0.0, "LOSS"));
    actual.apply_fill(fill(5, OrderSide::buy, 1, 10.0, 1.0, "EVEN"));
    actual.apply_fill(fill(6, OrderSide::sell, 1, 12.0, 1.0, "EVEN"));

    CHECK(actual.closed_trades().size() == 3);
    if (actual.closed_trades().size() == 3) {
        CHECK(actual.closed_trades()[0].outcome() == TradeOutcome::winning);
        CHECK(actual.closed_trades()[1].outcome() == TradeOutcome::losing);
        CHECK(actual.closed_trades()[2].outcome() == TradeOutcome::breakeven);
        CHECK(actual.closed_trades()[2].net_realized_pnl() == 0.0);
    }
}

void closed_trade_order_follows_global_closing_fill_order() {
    auto actual = portfolio({instrument("AAA"), instrument("BBB")});
    actual.apply_fill(fill(1, OrderSide::buy, 1, 10.0, 0.0, "AAA"));
    actual.apply_fill(fill(2, OrderSide::buy, 1, 20.0, 0.0, "BBB"));
    actual.apply_fill(fill(3, OrderSide::sell, 1, 21.0, 0.0, "BBB"));
    actual.apply_fill(fill(4, OrderSide::sell, 1, 11.0, 0.0, "AAA"));

    CHECK(actual.closed_trades().size() == 2);
    if (actual.closed_trades().size() == 2) {
        CHECK(actual.closed_trades()[0].symbol() == Symbol{"BBB"});
        CHECK(actual.closed_trades()[0].closing_fill_id() == FillId{3});
        CHECK(actual.closed_trades()[1].symbol() == Symbol{"AAA"});
        CHECK(actual.closed_trades()[1].closing_fill_id() == FillId{4});
    }
}

void rejected_fills_cannot_duplicate_closed_or_open_trade_state() {
    auto actual = portfolio();
    actual.apply_fill(fill(1, OrderSide::buy, 1, 10.0));
    const auto closing = fill(2, OrderSide::sell, 1, 11.0);
    actual.apply_fill(closing);
    CHECK(actual.closed_trades().size() == 1);

    CHECK_THROWS_AS(actual.apply_fill(closing), DuplicatePortfolioFill);
    CHECK(actual.closed_trades().size() == 1);
    CHECK(actual.open_trade(Symbol{"SPY"}) == nullptr);
    CHECK(actual.fill_count() == 2);
}

}  // namespace

int main() {
    open_episodes_are_separate_from_closed_trade_results();
    scaling_and_reductions_remain_one_flat_to_flat_episode();
    reversals_close_one_episode_and_open_the_opposite_direction();
    reversal_fee_remainder_is_conserved_exactly();
    short_to_long_reversal_uses_the_same_fee_allocation_contract();
    outcomes_use_closed_episode_net_pnl_and_exact_breakeven();
    closed_trade_order_follows_global_closing_fill_order();
    rejected_fills_cannot_duplicate_closed_or_open_trade_state();

    if (failures != 0) {
        std::cerr << failures << " test assertion(s) failed\n";
        return EXIT_FAILURE;
    }

    std::cout << "all trade episode tests passed\n";
    return EXIT_SUCCESS;
}
