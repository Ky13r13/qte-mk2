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
using qte::market_data::InstrumentKind;
using qte::market_data::OptionRight;
using qte::market_data::OptionSettlement;
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
    const std::optional<double> fill_tick = std::nullopt,
    const std::int64_t multiplier = 1) {
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
        commission,
        multiplier);
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
    const auto* owned_instrument = actual.instrument(Symbol{"QQQ"});
    CHECK(owned_instrument != nullptr);
    if (owned_instrument != nullptr) {
        CHECK(owned_instrument->symbol() == Symbol{"QQQ"});
        CHECK(owned_instrument->quote_currency() == Currency::usd());
        CHECK_NEAR(owned_instrument->price_grid().canonicalize(10.05).value(), 10.05);
        CHECK_THROWS_AS(owned_instrument->price_grid().canonicalize(10.01), std::invalid_argument);
    }
    CHECK(actual.instrument(Symbol{"IWM"}) == nullptr);
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

void derivative_universe_and_cashflows_are_explicit() {
    const auto grid = PriceGrid::from_tick_size(0.25);
    const auto last = Timestamp{200ns};
    const auto expiry = Timestamp{300ns};
    const auto future = InstrumentSpec::future(
        Symbol{"NQ"}, Currency::usd(), grid, 20, last, expiry);
    const auto option_expiry = Timestamp{250ns};
    const auto option = InstrumentSpec::european_futures_option(
        Symbol{"NQC"}, Currency::usd(), grid, 20, last, option_expiry, Symbol{"NQ"},
        OptionRight::call, grid.canonicalize(100.0),
        OptionSettlement::futures_delivery);

    auto futures = portfolio(10000.0, {future});
    futures.apply_fill(fill(
        1, OrderSide::buy, 2, 100.0, 2.0, 1, 100, "NQ", std::nullopt, 20));
    CHECK_NEAR(futures.cash(), 9998.0);
    futures.mark(Symbol{"NQ"}, mark(2, 110.0, 101));
    CHECK_OPTIONAL_NEAR(futures.market_value(), 400.0);
    CHECK_OPTIONAL_NEAR(futures.net_exposure(), 4400.0);
    CHECK_OPTIONAL_NEAR(futures.gross_exposure(), 4400.0);
    futures.apply_fill(fill(
        2, OrderSide::sell, 3, 115.0, 3.0, 3, 102, "NQ", std::nullopt, 20));
    CHECK_NEAR(futures.cash(), 10595.0);
    CHECK_NEAR(futures.realized_gross_pnl(), 600.0);
    CHECK_OPTIONAL_NEAR(futures.market_value(), 100.0);
    CHECK_OPTIONAL_NEAR(futures.net_exposure(), -2200.0);
    CHECK_OPTIONAL_NEAR(futures.equity(), 10695.0);
    CHECK(futures.closed_trades().size() == 1);
    if (futures.closed_trades().size() == 1) {
        CHECK_NEAR(futures.closed_trades()[0].allocated_commissions(), 4.0);
    }
    const auto* reversed_trade = futures.open_trade(Symbol{"NQ"});
    CHECK(reversed_trade != nullptr);
    if (reversed_trade != nullptr) {
        CHECK_NEAR(reversed_trade->allocated_commissions(), 1.0);
    }
    const auto cash_before_rejection = futures.cash();
    CHECK_THROWS_AS(futures.apply_fill(fill(
        3, OrderSide::buy, 1, 115.0, 0.0, 4, 103, "NQ")),
        InvalidPortfolioInput);
    CHECK(futures.fill_count() == 2);
    CHECK_NEAR(futures.cash(), cash_before_rejection);
    const auto* rejected_position = futures.position(Symbol{"NQ"});
    CHECK(rejected_position != nullptr);
    if (rejected_position != nullptr) {
        CHECK(rejected_position->quantity().value() == -1);
    }

    auto options = portfolio(10000.0, {future, option});
    options.apply_fill(fill(
        1, OrderSide::buy, 1, 10.0, 1.0, 1, 100, "NQC", std::nullopt, 20));
    options.mark(Symbol{"NQC"}, ValuationMark::option_value(
        Timestamp{101ns}, EventSequence{2}, 0.0));
    CHECK_NEAR(options.cash(), 9799.0);
    CHECK_OPTIONAL_NEAR(options.equity(), 9799.0);
    const auto snapshot = options.snapshot();
    CHECK(snapshot.positions().size() == 2);
    if (snapshot.positions().size() == 2) {
        CHECK(snapshot.positions()[1].instrument_kind ==
              InstrumentKind::futures_option);
    }

    const auto wrong_multiplier = InstrumentSpec::european_futures_option(
        Symbol{"BAD"}, Currency::usd(), grid, 2, last, option_expiry, Symbol{"NQ"},
        OptionRight::put, grid.canonicalize(100.0), OptionSettlement::cash);
    CHECK_THROWS_AS(portfolio(10000.0, {future, wrong_multiplier}),
                    InvalidPortfolioInput);
    CHECK_THROWS_AS(portfolio(10000.0, {option}), InvalidPortfolioInput);

    auto overflow = portfolio(10000.0, {future});
    overflow.apply_fill(fill(
        1, OrderSide::buy, std::numeric_limits<std::int64_t>::max(), 1.0, 0.0,
        1, 100, "NQ", std::nullopt, 20));
    CHECK_THROWS_AS(
        overflow.mark(Symbol{"NQ"}, mark(2, 1.0e300, 101)),
        PortfolioAccountingError);
    const auto* overflow_position = overflow.position(Symbol{"NQ"});
    CHECK(overflow_position != nullptr);
    if (overflow_position != nullptr) {
        CHECK(!overflow_position->valuation_mark().has_value());
    }
}

void option_and_micro_future_combined_equity_identity_is_preserved() {
    const auto grid = PriceGrid::from_tick_size(0.25);
    const auto last = Timestamp{200ns};
    const auto nq = InstrumentSpec::future(
        Symbol{"NQ"}, Currency::usd(), grid, 20, last, Timestamp{300ns});
    const auto mnq = InstrumentSpec::future(
        Symbol{"MNQ"}, Currency::usd(), grid, 2, last, Timestamp{300ns});
    const auto option = InstrumentSpec::european_futures_option(
        Symbol{"NQC"}, Currency::usd(), grid, 20, last, Timestamp{250ns},
        Symbol{"NQ"}, OptionRight::call, grid.canonicalize(100.0),
        OptionSettlement::futures_delivery);
    auto actual = portfolio(10000.0, {nq, mnq, option});
    actual.apply_fill(fill(
        1, OrderSide::buy, 1, 10.0, 1.0, 1, 100, "NQC", std::nullopt, 20));
    actual.apply_fill(fill(
        2, OrderSide::buy, 5, 100.0, 2.0, 2, 100, "MNQ", std::nullopt, 2));
    actual.mark(Symbol{"NQC"}, ValuationMark::option_value(
        Timestamp{101ns}, EventSequence{3}, 12.0));
    actual.mark(Symbol{"MNQ"}, mark(4, 110.0, 101));
    CHECK_OPTIONAL_NEAR(actual.equity(), 10137.0);
    CHECK_OPTIONAL_NEAR(actual.net_pnl(), 137.0);
    CHECK_OPTIONAL_NEAR(actual.net_exposure(), 1340.0);
    CHECK_OPTIONAL_NEAR(actual.gross_exposure(), 1340.0);
    actual.apply_fill(fill(
        3, OrderSide::sell, 1, 15.0, 1.0, 5, 102, "NQC", std::nullopt, 20));
    CHECK_OPTIONAL_NEAR(actual.equity(), 10196.0);
    CHECK_OPTIONAL_NEAR(actual.net_pnl(), 196.0);
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
    derivative_universe_and_cashflows_are_explicit();
    option_and_micro_future_combined_equity_identity_is_preserved();
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
