#include "qte/portfolio/portfolio.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <optional>
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
using qte::market_data::InstrumentSpec;
using qte::market_data::Symbol;
using qte::market_data::Timestamp;
using qte::orders::Fill;
using qte::orders::OrderSide;
using qte::portfolio::DuplicatePortfolioFill;
using qte::portfolio::InvalidPortfolioInput;
using qte::portfolio::OutOfSequencePortfolioFill;
using qte::portfolio::Portfolio;
using qte::portfolio::PortfolioAccountingError;
using qte::portfolio::ValuationMark;

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

void check_optional_near(
    const std::optional<double>& actual,
    const double expected,
    const std::string_view expression,
    const int line) {
    check(actual.has_value(), expression, line);
    if (actual.has_value()) {
        check(near(*actual, expected), expression, line);
    }
}

#define CHECK_NEAR(actual, expected) check(near((actual), (expected)), #actual, __LINE__)
#define CHECK_OPTIONAL_NEAR(actual, expected) \
    check_optional_near((actual), (expected), #actual, __LINE__)

[[nodiscard]] InstrumentSpec instrument(
    const char* const symbol = "SPY",
    const double tick_size = 0.01,
    const Currency currency = Currency::usd()) {
    return InstrumentSpec{
        Symbol{symbol}, currency, PriceGrid::from_tick_size(tick_size)};
}

[[nodiscard]] Portfolio portfolio(
    const double initial_cash = 1000.0,
    std::vector<InstrumentSpec> instruments = {instrument()}) {
    return Portfolio{initial_cash, Currency::usd(), std::move(instruments)};
}

[[nodiscard]] Fill fill(
    const std::uint64_t fill_id,
    const OrderSide side,
    const std::int64_t quantity,
    const double price,
    const double commission,
    const std::uint64_t event_sequence,
    const std::uint64_t timestamp,
    const char* const symbol = "SPY",
    const std::optional<double> fill_tick = std::nullopt) {
    const auto grid = PriceGrid::from_tick_size(fill_tick.value_or(price));
    return Fill::create(
        FillId{fill_id},
        OrderId{fill_id},
        Symbol{symbol},
        side,
        ShareAmount::from_count(quantity),
        Timestamp{std::chrono::nanoseconds{timestamp}},
        EventSequence{event_sequence},
        price,
        grid.canonicalize(price),
        commission);
}

[[nodiscard]] ValuationMark mark(
    const std::uint64_t event_sequence,
    const double price,
    const std::uint64_t timestamp) {
    return ValuationMark::create(
        Timestamp{std::chrono::nanoseconds{timestamp}},
        EventSequence{event_sequence},
        price);
}

void construction_defines_a_fixed_single_currency_universe() {
    auto actual = portfolio(
        1000.0, {instrument("SPY"), instrument("QQQ", 0.05)});
    CHECK(actual.initial_cash() == 1000.0);
    CHECK(actual.cash() == 1000.0);
    CHECK(actual.valuation_currency() == Currency::usd());
    CHECK(actual.instrument_count() == 2);
    CHECK(actual.fill_count() == 0);
    CHECK(!actual.contains_fill(FillId{1}));
    CHECK(actual.position(Symbol{"SPY"}) != nullptr);
    CHECK(actual.position(Symbol{"QQQ"}) != nullptr);
    CHECK(actual.position(Symbol{"IWM"}) == nullptr);
    CHECK_OPTIONAL_NEAR(actual.market_value(), 0.0);
    CHECK_OPTIONAL_NEAR(actual.unrealized_pnl(), 0.0);
    CHECK_OPTIONAL_NEAR(actual.equity(), 1000.0);
    CHECK_OPTIONAL_NEAR(actual.gross_exposure(), 0.0);
    CHECK_OPTIONAL_NEAR(actual.net_exposure(), 0.0);
    CHECK_OPTIONAL_NEAR(actual.net_pnl(), 0.0);

    CHECK_THROWS_AS(portfolio(-1.0), InvalidPortfolioInput);
    CHECK_THROWS_AS(
        portfolio(std::numeric_limits<double>::quiet_NaN()),
        InvalidPortfolioInput);
    CHECK_THROWS_AS(
        portfolio(std::numeric_limits<double>::infinity()),
        InvalidPortfolioInput);
    CHECK_THROWS_AS(
        portfolio(1000.0, {}),
        InvalidPortfolioInput);
    CHECK_THROWS_AS(
        portfolio(1000.0, {instrument("SPY"), instrument("SPY")}),
        InvalidPortfolioInput);
    CHECK_THROWS_AS(
        portfolio(1000.0, {instrument(
            "BMW", 0.01, Currency::from_code("EUR"))}),
        InvalidPortfolioInput);
}

void unmarked_open_positions_make_aggregate_valuation_unavailable() {
    auto actual = portfolio();
    actual.apply_fill(fill(1, OrderSide::buy, 10, 10.0, 1.0, 1, 100));

    CHECK_NEAR(actual.cash(), 899.0);
    CHECK_NEAR(actual.realized_gross_pnl(), 0.0);
    CHECK_NEAR(actual.commissions_paid(), 1.0);
    CHECK(!actual.market_value().has_value());
    CHECK(!actual.unrealized_pnl().has_value());
    CHECK(!actual.equity().has_value());
    CHECK(!actual.gross_exposure().has_value());
    CHECK(!actual.net_exposure().has_value());
    CHECK(!actual.net_pnl().has_value());

    actual.mark(Symbol{"SPY"}, mark(2, 11.0, 101));
    CHECK_OPTIONAL_NEAR(actual.market_value(), 110.0);
    CHECK_OPTIONAL_NEAR(actual.unrealized_pnl(), 10.0);
    CHECK_OPTIONAL_NEAR(actual.equity(), 1009.0);
    CHECK_OPTIONAL_NEAR(actual.net_pnl(), 9.0);

    auto round_trip = portfolio();
    round_trip.apply_fill(fill(1, OrderSide::buy, 10, 10.0, 1.0, 1, 100));
    CHECK(!round_trip.equity().has_value());
    round_trip.apply_fill(fill(2, OrderSide::sell, 10, 12.0, 1.0, 2, 101));
    CHECK_OPTIONAL_NEAR(round_trip.market_value(), 0.0);
    CHECK_OPTIONAL_NEAR(round_trip.unrealized_pnl(), 0.0);
    CHECK_OPTIONAL_NEAR(round_trip.equity(), 1018.0);
    CHECK_OPTIONAL_NEAR(round_trip.net_pnl(), 18.0);
}

void six_fill_cash_and_equity_fixture_matches_the_adr() {
    struct Row final {
        OrderSide side;
        std::int64_t quantity;
        double price;
        double fee;
        double cash;
        std::int64_t position_quantity;
        double average_price;
        double realized;
        double fees;
        double equity;
    };
    constexpr Row rows[] = {
        {OrderSide::buy, 10, 10.0, 1.0, 899.0, 10, 10.0, 0.0, 1.0, 999.0},
        {OrderSide::buy, 10, 12.0, 1.0, 778.0, 20, 11.0, 0.0, 2.0, 1018.0},
        {OrderSide::sell, 5, 14.0, 1.0, 847.0, 15, 11.0, 15.0, 3.0, 1057.0},
        {OrderSide::sell, 20, 9.0, 2.0, 1025.0, -5, 9.0, -15.0, 5.0, 980.0},
        {OrderSide::buy, 8, 8.0, 1.0, 960.0, 3, 8.0, -10.0, 6.0, 984.0},
        {OrderSide::sell, 3, 10.0, 1.0, 989.0, 0, 0.0, -4.0, 7.0, 989.0},
    };

    auto actual = portfolio();
    std::uint64_t id = 1;
    for (const auto& row : rows) {
        const auto timestamp = id * 100;
        actual.mark(Symbol{"SPY"}, mark(id * 2 - 1, row.price, timestamp));
        actual.apply_fill(fill(
            id,
            row.side,
            row.quantity,
            row.price,
            row.fee,
            id * 2,
            timestamp));

        CHECK_NEAR(actual.cash(), row.cash);
        CHECK_NEAR(actual.realized_gross_pnl(), row.realized);
        CHECK_NEAR(actual.commissions_paid(), row.fees);
        CHECK_OPTIONAL_NEAR(actual.equity(), row.equity);
        CHECK_OPTIONAL_NEAR(actual.net_pnl(), row.equity - 1000.0);

        const auto* current = actual.position(Symbol{"SPY"});
        CHECK(current != nullptr);
        if (current != nullptr) {
            CHECK(current->quantity().value() == row.position_quantity);
            CHECK_NEAR(current->average_entry_price(), row.average_price);
        }
        ++id;
    }

    CHECK(actual.fill_count() == 6);
    CHECK(actual.contains_fill(FillId{1}));
    CHECK(actual.contains_fill(FillId{6}));
    CHECK(!actual.contains_fill(FillId{7}));
    CHECK(actual.fills().size() == 6);
    if (actual.fills().size() == 6) {
        CHECK(actual.fills()[0].id() == FillId{1});
        CHECK(actual.fills()[5].id() == FillId{6});
    }
}

void multiple_symbols_aggregate_long_short_value_and_exposure() {
    auto actual = portfolio(
        1000.0, {instrument("AAA"), instrument("BBB")});
    actual.mark(Symbol{"AAA"}, mark(1, 10.0, 100));
    actual.mark(Symbol{"BBB"}, mark(2, 20.0, 100));
    actual.apply_fill(fill(
        1, OrderSide::buy, 10, 10.0, 1.0, 3, 100, "AAA"));
    actual.apply_fill(fill(
        2, OrderSide::sell, 5, 20.0, 1.0, 4, 100, "BBB"));

    CHECK_NEAR(actual.cash(), 998.0);
    CHECK_OPTIONAL_NEAR(actual.market_value(), 0.0);
    CHECK_OPTIONAL_NEAR(actual.gross_exposure(), 200.0);
    CHECK_OPTIONAL_NEAR(actual.net_exposure(), 0.0);
    CHECK_OPTIONAL_NEAR(actual.equity(), 998.0);
    CHECK_OPTIONAL_NEAR(actual.net_pnl(), -2.0);

    actual.mark(Symbol{"AAA"}, mark(5, 11.0, 200));
    actual.mark(Symbol{"BBB"}, mark(6, 18.0, 200));
    CHECK_OPTIONAL_NEAR(actual.market_value(), 20.0);
    CHECK_OPTIONAL_NEAR(actual.unrealized_pnl(), 20.0);
    CHECK_OPTIONAL_NEAR(actual.gross_exposure(), 200.0);
    CHECK_OPTIONAL_NEAR(actual.net_exposure(), 20.0);
    CHECK_OPTIONAL_NEAR(actual.equity(), 1018.0);
    CHECK_OPTIONAL_NEAR(actual.net_pnl(), 18.0);
}

void duplicate_id_and_event_order_failures_are_atomic() {
    auto actual = portfolio();
    actual.mark(Symbol{"SPY"}, mark(1, 10.0, 100));
    const auto first = fill(1, OrderSide::buy, 2, 10.0, 0.5, 2, 100);
    actual.apply_fill(first);

    CHECK_THROWS_AS(actual.apply_fill(first), DuplicatePortfolioFill);
    CHECK_NEAR(actual.cash(), 979.5);
    CHECK(actual.fill_count() == 1);

    CHECK_THROWS_AS(
        actual.apply_fill(fill(3, OrderSide::buy, 1, 10.0, 0.0, 3, 100)),
        OutOfSequencePortfolioFill);
    CHECK_THROWS_AS(
        actual.apply_fill(fill(2, OrderSide::buy, 1, 10.0, 0.0, 2, 100)),
        InvalidPortfolioInput);
    CHECK_THROWS_AS(
        actual.apply_fill(fill(2, OrderSide::buy, 1, 10.0, 0.0, 3, 99)),
        InvalidPortfolioInput);
    CHECK_THROWS_AS(
        actual.mark(Symbol{"SPY"}, mark(2, 11.0, 101)),
        InvalidPortfolioInput);
    CHECK_THROWS_AS(
        actual.mark(Symbol{"SPY"}, mark(3, 11.0, 99)),
        InvalidPortfolioInput);

    CHECK_NEAR(actual.cash(), 979.5);
    CHECK(actual.fill_count() == 1);
    const auto* current = actual.position(Symbol{"SPY"});
    CHECK(current != nullptr);
    if (current != nullptr) {
        CHECK(current->quantity().value() == 2);
        CHECK_NEAR(current->average_entry_price(), 10.0);
    }

    actual.apply_fill(fill(2, OrderSide::buy, 1, 10.0, 0.25, 3, 100));
    CHECK(actual.fill_count() == 2);
    CHECK_NEAR(actual.cash(), 969.25);
}

void universe_and_tick_validation_are_independent_and_atomic() {
    auto unknown = portfolio();
    CHECK_THROWS_AS(
        unknown.apply_fill(fill(
            1, OrderSide::buy, 1, 10.0, 0.0, 1, 100, "QQQ")),
        InvalidPortfolioInput);
    CHECK(unknown.fill_count() == 0);
    CHECK(unknown.cash() == 1000.0);

    CHECK_THROWS_AS(
        unknown.mark(Symbol{"QQQ"}, mark(1, 10.0, 100)),
        InvalidPortfolioInput);
    CHECK_OPTIONAL_NEAR(unknown.equity(), 1000.0);

    auto off_grid = portfolio(1000.0, {instrument("SPY", 0.05)});
    CHECK_THROWS_AS(
        off_grid.apply_fill(fill(
            1,
            OrderSide::buy,
            1,
            100.02,
            0.0,
            1,
            100,
            "SPY",
            0.03)),
        InvalidPortfolioInput);
    CHECK(off_grid.fill_count() == 0);
    CHECK(off_grid.cash() == 1000.0);
}

void arithmetic_failures_do_not_commit_cash_position_or_fill_history() {
    auto quantity_overflow = portfolio();
    quantity_overflow.apply_fill(fill(
        1,
        OrderSide::buy,
        std::numeric_limits<std::int64_t>::max(),
        1.0,
        0.0,
        1,
        100));
    const double cash_before = quantity_overflow.cash();
    CHECK_THROWS_AS(
        quantity_overflow.apply_fill(fill(
            2, OrderSide::buy, 1, 1.0, 0.0, 2, 100)),
        PortfolioAccountingError);
    CHECK(quantity_overflow.fill_count() == 1);
    CHECK(quantity_overflow.cash() == cash_before);
    const auto* max_position = quantity_overflow.position(Symbol{"SPY"});
    CHECK(max_position != nullptr);
    if (max_position != nullptr) {
        CHECK(max_position->quantity().value() ==
              std::numeric_limits<std::int64_t>::max());
    }

    auto cash_overflow = portfolio(
        std::numeric_limits<double>::max(),
        {instrument("SPY", std::numeric_limits<double>::max())});
    CHECK_THROWS_AS(
        cash_overflow.apply_fill(fill(
            1,
            OrderSide::sell,
            1,
            std::numeric_limits<double>::max(),
            0.0,
            1,
            100)),
        PortfolioAccountingError);
    CHECK(cash_overflow.fill_count() == 0);
    CHECK(cash_overflow.cash() == std::numeric_limits<double>::max());
    const auto* flat = cash_overflow.position(Symbol{"SPY"});
    CHECK(flat != nullptr);
    if (flat != nullptr) {
        CHECK(flat->is_flat());
    }

    CHECK_THROWS_AS(
        quantity_overflow.mark(Symbol{"SPY"}, mark(2, 1.0e300, 101)),
        PortfolioAccountingError);
    const auto* still_unmarked = quantity_overflow.position(Symbol{"SPY"});
    CHECK(still_unmarked != nullptr);
    if (still_unmarked != nullptr) {
        CHECK(!still_unmarked->valuation_mark().has_value());
    }
}

void partial_fill_fees_and_cash_are_conserved() {
    auto actual = portfolio();
    actual.apply_fill(fill(1, OrderSide::buy, 5, 10.0, 0.50, 1, 100));
    actual.apply_fill(fill(2, OrderSide::buy, 5, 10.0, 0.75, 2, 100));
    actual.mark(Symbol{"SPY"}, mark(3, 10.0, 100));

    CHECK_NEAR(actual.cash(), 898.75);
    CHECK_NEAR(actual.commissions_paid(), 1.25);
    CHECK_OPTIONAL_NEAR(actual.market_value(), 100.0);
    CHECK_OPTIONAL_NEAR(actual.equity(), 998.75);
    CHECK_OPTIONAL_NEAR(actual.net_pnl(), -1.25);
}

}  // namespace

int main() {
    construction_defines_a_fixed_single_currency_universe();
    unmarked_open_positions_make_aggregate_valuation_unavailable();
    six_fill_cash_and_equity_fixture_matches_the_adr();
    multiple_symbols_aggregate_long_short_value_and_exposure();
    duplicate_id_and_event_order_failures_are_atomic();
    universe_and_tick_validation_are_independent_and_atomic();
    arithmetic_failures_do_not_commit_cash_position_or_fill_history();
    partial_fill_fees_and_cash_are_conserved();

    if (failures != 0) {
        std::cerr << failures << " test assertion(s) failed\n";
        return EXIT_FAILURE;
    }

    std::cout << "all portfolio tests passed\n";
    return EXIT_SUCCESS;
}
