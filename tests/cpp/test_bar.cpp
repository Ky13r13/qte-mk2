#include "qte/market_data/bar.hpp"

#include <array>
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string_view>
#include <vector>

namespace {

using namespace std::chrono_literals;
using qte::market_data::Bar;
using qte::market_data::BarValidationCode;
using qte::market_data::Symbol;
using qte::market_data::Timestamp;

int failures = 0;

[[nodiscard]] bool check(
    const bool condition,
    const std::string_view expression,
    const int line) {
    if (!condition) {
        std::cerr << "line " << line << ": check failed: " << expression << '\n';
        ++failures;
    }
    return condition;
}

#define CHECK(expression) static_cast<void>(check((expression), #expression, __LINE__))
#define REQUIRE(expression)                     \
    do {                                        \
        if (!check((expression), #expression, __LINE__)) { \
            return;                             \
        }                                       \
    } while (false)

[[nodiscard]] Bar valid_bar() {
    return Bar{
        .symbol = Symbol{"SPY"},
        .start_time = Timestamp{1'000'000'000ns},
        .end_time = Timestamp{61'000'000'000ns},
        .open = 100.0,
        .high = 102.0,
        .low = 99.0,
        .close = 101.0,
        .volume = 1'000.0,
    };
}

[[nodiscard]] bool has_code(
    const std::vector<qte::market_data::BarValidationError>& errors,
    const BarValidationCode code) {
    for (const auto& error : errors) {
        if (error.code == code) {
            return true;
        }
    }
    return false;
}

void valid_bar_has_no_errors() {
    CHECK(qte::market_data::validate(valid_bar()).empty());
}

void rejects_empty_symbol() {
    bool threw = false;
    try {
        [[maybe_unused]] const Symbol symbol{""};
    } catch (const std::invalid_argument&) {
        threw = true;
    }
    CHECK(threw);
}

void rejects_non_positive_interval() {
    auto bar = valid_bar();
    bar.end_time = bar.start_time;

    const auto errors = qte::market_data::validate(bar);
    REQUIRE(errors.size() == 1);
    CHECK(errors.front().code == BarValidationCode::invalid_interval);
}

void rejects_reversed_interval() {
    auto bar = valid_bar();
    bar.end_time = bar.start_time - 1ns;

    const auto errors = qte::market_data::validate(bar);
    REQUIRE(errors.size() == 1);
    CHECK(errors.front().code == BarValidationCode::invalid_interval);
}

void rejects_inconsistent_ohlc() {
    auto bar = valid_bar();
    bar.high = 100.5;
    bar.low = 101.5;

    const auto errors = qte::market_data::validate(bar);
    REQUIRE(errors.size() == 1);
    CHECK(errors.front().code == BarValidationCode::inconsistent_ohlc);
}

void rejects_nan_and_infinity_for_every_numeric_field() {
    using NumericField = double Bar::*;
    constexpr std::array<NumericField, 5> fields{
        &Bar::open,
        &Bar::high,
        &Bar::low,
        &Bar::close,
        &Bar::volume,
    };
    constexpr std::array invalid_values{
        std::numeric_limits<double>::quiet_NaN(),
        std::numeric_limits<double>::infinity(),
    };

    for (const auto field : fields) {
        for (const double invalid_value : invalid_values) {
            auto bar = valid_bar();
            bar.*field = invalid_value;

            const auto errors = qte::market_data::validate(bar);
            REQUIRE(errors.size() == 1);
            const auto expected_code = field == &Bar::volume
                                           ? BarValidationCode::non_finite_volume
                                           : BarValidationCode::non_finite_price;
            CHECK(errors.front().code == expected_code);
        }
    }
}

void accepts_zero_numeric_values() {
    auto bar = valid_bar();
    bar.open = 0.0;
    bar.high = 0.0;
    bar.low = 0.0;
    bar.close = 0.0;
    bar.volume = 0.0;

    CHECK(qte::market_data::validate(bar).empty());
}

void rejects_each_ohlc_consistency_violation() {
    auto high_below_open = valid_bar();
    high_below_open.open = high_below_open.high + 1.0;
    CHECK(has_code(
        qte::market_data::validate(high_below_open),
        BarValidationCode::inconsistent_ohlc));

    auto high_below_close = valid_bar();
    high_below_close.close = high_below_close.high + 1.0;
    CHECK(has_code(
        qte::market_data::validate(high_below_close),
        BarValidationCode::inconsistent_ohlc));

    auto high_below_low = valid_bar();
    high_below_low.low = high_below_low.high + 1.0;
    CHECK(has_code(
        qte::market_data::validate(high_below_low),
        BarValidationCode::inconsistent_ohlc));

    auto low_above_open = valid_bar();
    low_above_open.low = low_above_open.open + 1.0;
    CHECK(has_code(
        qte::market_data::validate(low_above_open),
        BarValidationCode::inconsistent_ohlc));

    auto low_above_close = valid_bar();
    low_above_close.low = low_above_close.close + 1.0;
    CHECK(has_code(
        qte::market_data::validate(low_above_close),
        BarValidationCode::inconsistent_ohlc));
}

void rejects_negative_volume() {
    auto bar = valid_bar();
    bar.volume = -1.0;

    const auto errors = qte::market_data::validate(bar);
    REQUIRE(errors.size() == 1);
    CHECK(errors.front().code == BarValidationCode::negative_volume);
}

void accepts_valid_bar_with_strict_validation() {
    bool threw = false;
    try {
        qte::market_data::require_valid(valid_bar());
    } catch (...) {
        threw = true;
    }
    CHECK(!threw);
}

void require_valid_preserves_structured_errors() {
    auto bar = valid_bar();
    bar.close = -1.0;

    bool threw = false;
    try {
        qte::market_data::require_valid(bar);
    } catch (const qte::market_data::InvalidBar& error) {
        threw = true;
        REQUIRE(!error.errors().empty());
        CHECK(error.errors().front().code == BarValidationCode::negative_price);
    }
    CHECK(threw);
}

}  // namespace

int main() {
    valid_bar_has_no_errors();
    rejects_empty_symbol();
    rejects_non_positive_interval();
    rejects_reversed_interval();
    rejects_inconsistent_ohlc();
    rejects_nan_and_infinity_for_every_numeric_field();
    accepts_zero_numeric_values();
    rejects_each_ohlc_consistency_violation();
    rejects_negative_volume();
    accepts_valid_bar_with_strict_validation();
    require_valid_preserves_structured_errors();

    if (failures != 0) {
        std::cerr << failures << " test assertion(s) failed\n";
        return EXIT_FAILURE;
    }

    std::cout << "all bar tests passed\n";
    return EXIT_SUCCESS;
}
