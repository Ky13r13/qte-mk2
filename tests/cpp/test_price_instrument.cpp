#include "qte/config/research_profile.hpp"
#include "qte/core/currency.hpp"
#include "qte/core/price.hpp"
#include "qte/market_data/instrument.hpp"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string_view>

namespace {

using qte::config::ResearchProfile;
using qte::core::Currency;
using qte::core::PriceGrid;
using qte::market_data::InstrumentSpec;
using qte::market_data::Symbol;

int failures = 0;

void check(const bool condition, const std::string_view expression, const int line) {
    if (!condition) {
        std::cerr << "line " << line << ": check failed: " << expression << '\n';
        ++failures;
    }
}

void check_close(
    const double actual,
    const double expected,
    const std::string_view expression,
    const int line) {
    const double tolerance = 1e-12 * std::max({1.0, std::abs(actual), std::abs(expected)});
    check(std::abs(actual - expected) <= tolerance, expression, line);
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
#define CHECK_CLOSE(actual, expected) check_close((actual), (expected), #actual, __LINE__)
#define CHECK_THROWS_AS(expression, exception_type) \
    check_throws<exception_type>([&] { static_cast<void>(expression); }, #expression, __LINE__)

void currency_codes_are_explicit() {
    CHECK(Currency::usd().code() == "USD");
    CHECK(Currency::from_code("EUR").code() == "EUR");
    CHECK_THROWS_AS(Currency::from_code(""), std::invalid_argument);
    CHECK_THROWS_AS(Currency::from_code("US"), std::invalid_argument);
    CHECK_THROWS_AS(Currency::from_code("USDD"), std::invalid_argument);
    CHECK_THROWS_AS(Currency::from_code("usd"), std::invalid_argument);
    CHECK_THROWS_AS(Currency::from_code("U$D"), std::invalid_argument);
}

void tick_size_must_be_positive_and_finite() {
    CHECK_CLOSE(PriceGrid::from_tick_size(0.01).tick_size(), 0.01);
    CHECK_THROWS_AS(PriceGrid::from_tick_size(0.0), std::invalid_argument);
    CHECK_THROWS_AS(PriceGrid::from_tick_size(-0.01), std::invalid_argument);
    CHECK_THROWS_AS(
        PriceGrid::from_tick_size(std::numeric_limits<double>::quiet_NaN()),
        std::invalid_argument);
    CHECK_THROWS_AS(
        PriceGrid::from_tick_size(std::numeric_limits<double>::infinity()),
        std::invalid_argument);
}

void order_prices_are_canonicalized_with_a_small_tolerance() {
    const auto grid = PriceGrid::from_tick_size(0.01);
    constexpr double accepted_offset = 0.5e-10;
    constexpr double rejected_offset = 2.0e-10;

    CHECK_CLOSE(grid.canonicalize(10.00).value(), 10.00);
    CHECK_CLOSE(grid.canonicalize(10.00 + accepted_offset).value(), 10.00);
    CHECK_CLOSE(grid.canonicalize(10.00 - accepted_offset).value(), 10.00);
    CHECK_THROWS_AS(grid.canonicalize(10.00 + rejected_offset), std::invalid_argument);
    CHECK_THROWS_AS(grid.canonicalize(10.005), std::invalid_argument);
}

void invalid_order_prices_are_rejected() {
    const auto grid = PriceGrid::from_tick_size(1.0);

    CHECK_THROWS_AS(grid.canonicalize(0.0), std::invalid_argument);
    CHECK_THROWS_AS(grid.canonicalize(-1.0), std::invalid_argument);
    CHECK_THROWS_AS(
        grid.canonicalize(std::numeric_limits<double>::quiet_NaN()),
        std::invalid_argument);
    CHECK_THROWS_AS(
        grid.canonicalize(std::numeric_limits<double>::infinity()),
        std::invalid_argument);
    CHECK(grid.canonicalize(9'007'199'254'740'991.0).value() ==
          9'007'199'254'740'991.0);
    CHECK_THROWS_AS(
        grid.canonicalize(9'007'199'254'740'992.0),
        std::out_of_range);
}

void synthesized_prices_round_adversely() {
    const auto grid = PriceGrid::from_tick_size(0.05);

    CHECK_CLOSE(grid.round_up(10.01).value(), 10.05);
    CHECK_CLOSE(grid.round_down(10.01).value(), 10.00);
    CHECK_CLOSE(grid.round_up(10.00 + 2e-10).value(), 10.00);
    CHECK_CLOSE(grid.round_down(10.00 - 2e-10).value(), 10.00);
    CHECK_THROWS_AS(grid.round_down(0.01), std::invalid_argument);
}

void instruments_are_bound_to_one_valuation_currency() {
    const InstrumentSpec spy{
        Symbol{"SPY"},
        Currency::usd(),
        PriceGrid::from_tick_size(0.01),
    };
    const InstrumentSpec dax{
        Symbol{"DAX"},
        Currency::from_code("EUR"),
        PriceGrid::from_tick_size(0.01),
    };
    const ResearchProfile default_profile;
    const ResearchProfile euro_profile{Currency::from_code("EUR")};

    CHECK(spy.symbol().value() == "SPY");
    CHECK(spy.quote_currency() == Currency::usd());
    CHECK(spy.contract_multiplier() == 1);
    CHECK(default_profile.valuation_currency() == Currency::usd());
    CHECK(default_profile.supports(spy));
    CHECK(!default_profile.supports(dax));
    CHECK(euro_profile.supports(dax));
    default_profile.require_supported(spy);
    CHECK_THROWS_AS(default_profile.require_supported(dax), std::invalid_argument);
}

}  // namespace

int main() {
    currency_codes_are_explicit();
    tick_size_must_be_positive_and_finite();
    order_prices_are_canonicalized_with_a_small_tolerance();
    invalid_order_prices_are_rejected();
    synthesized_prices_round_adversely();
    instruments_are_bound_to_one_valuation_currency();

    if (failures != 0) {
        std::cerr << failures << " test assertion(s) failed\n";
        return EXIT_FAILURE;
    }

    std::cout << "all price and instrument tests passed\n";
    return EXIT_SUCCESS;
}
