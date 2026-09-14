#include "qte/core/identifier.hpp"
#include "qte/core/quantity.hpp"

#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string_view>
#include <type_traits>

namespace {

using qte::core::EventSequence;
using qte::core::EventSequenceGenerator;
using qte::core::FillId;
using qte::core::FillIdGenerator;
using qte::core::OrderId;
using qte::core::OrderIdGenerator;
using qte::core::SequentialIdGenerator;
using qte::core::ShareAmount;
using qte::core::ShareQuantity;

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

void signed_quantities_are_exact() {
    static_assert(std::numeric_limits<double>::is_iec559);
    static_assert(std::numeric_limits<double>::radix == 2);
    static_assert(std::numeric_limits<double>::digits == 53);

    const auto zero = ShareQuantity::zero();
    const auto long_position = ShareQuantity::from_raw(17);
    const auto short_position = ShareQuantity::from_raw(-17);

    CHECK(zero.is_zero());
    CHECK(zero.value() == 0);
    CHECK(long_position.value() == 17);
    CHECK(long_position.magnitude() == 17U);
    CHECK(short_position.value() == -17);
    CHECK(short_position.magnitude() == 17U);
    CHECK(short_position.negated() == long_position);
}

void invalid_signed_quantities_are_rejected() {
    CHECK_THROWS_AS(
        ShareQuantity::from_raw(std::numeric_limits<std::int64_t>::min()),
        std::out_of_range);
    CHECK_THROWS_AS(ShareQuantity::from_numeric(1.5), std::invalid_argument);
    CHECK_THROWS_AS(
        ShareQuantity::from_numeric(std::numeric_limits<double>::quiet_NaN()),
        std::invalid_argument);
    CHECK_THROWS_AS(
        ShareQuantity::from_numeric(std::numeric_limits<double>::infinity()),
        std::invalid_argument);
    CHECK_THROWS_AS(
        ShareQuantity::from_numeric(9'223'372'036'854'775'808.0),
        std::out_of_range);
    CHECK_THROWS_AS(
        ShareQuantity::from_numeric(-9'223'372'036'854'775'808.0),
        std::out_of_range);
}

void integral_numeric_quantities_are_accepted() {
    const auto largest_exact_double_below_limit =
        std::nextafter(9'223'372'036'854'775'808.0, 0.0);

    CHECK(ShareQuantity::from_numeric(0.0) == ShareQuantity::zero());
    CHECK(ShareQuantity::from_numeric(42.0).value() == 42);
    CHECK(ShareQuantity::from_numeric(-42.0).value() == -42);
    CHECK(ShareQuantity::from_numeric(largest_exact_double_below_limit).value() > 0);
}

void positive_amounts_enforce_the_request_boundary() {
    CHECK(ShareAmount::from_count(5).value() == 5);
    CHECK(ShareAmount::from_numeric(5.0).as_quantity().value() == 5);
    CHECK_THROWS_AS(ShareAmount::from_count(0), std::invalid_argument);
    CHECK_THROWS_AS(ShareAmount::from_count(-1), std::invalid_argument);
    CHECK_THROWS_AS(ShareAmount::from_numeric(2.5), std::invalid_argument);
}

void quantity_arithmetic_checks_both_boundaries() {
    constexpr auto maximum = std::numeric_limits<std::int64_t>::max();
    const auto max_quantity = ShareQuantity::from_raw(maximum);
    const auto min_quantity = ShareQuantity::from_raw(-maximum);
    const auto one = ShareQuantity::from_raw(1);

    CHECK(ShareQuantity::from_raw(-5).checked_add(ShareQuantity::from_raw(12)).value() == 7);
    CHECK(ShareQuantity::from_raw(5).checked_subtract(ShareQuantity::from_raw(12)).value() == -7);
    CHECK(max_quantity.checked_add(ShareQuantity::zero()) == max_quantity);
    CHECK(min_quantity.checked_subtract(ShareQuantity::zero()) == min_quantity);
    CHECK_THROWS_AS(max_quantity.checked_add(one), std::overflow_error);
    CHECK_THROWS_AS(min_quantity.checked_add(one.negated()), std::overflow_error);
    CHECK_THROWS_AS(max_quantity.checked_subtract(one.negated()), std::overflow_error);
    CHECK_THROWS_AS(min_quantity.checked_subtract(one), std::overflow_error);
}

void identifier_types_are_distinct_and_nonzero() {
    static_assert(!std::is_same_v<OrderId, FillId>);
    static_assert(!std::is_same_v<OrderId, EventSequence>);

    CHECK(OrderId{7}.value() == 7U);
    CHECK(FillId{7}.value() == 7U);
    CHECK(EventSequence{7}.value() == 7U);
    CHECK_THROWS_AS(OrderId{0}, std::invalid_argument);
    CHECK_THROWS_AS(FillId{0}, std::invalid_argument);
    CHECK_THROWS_AS(EventSequence{0}, std::invalid_argument);
}

void independent_generators_are_deterministic() {
    OrderIdGenerator orders;
    FillIdGenerator fills;
    EventSequenceGenerator events;
    OrderIdGenerator second_run_orders;

    CHECK(orders.next().value() == 1U);
    CHECK(orders.next().value() == 2U);
    CHECK(fills.next().value() == 1U);
    CHECK(events.next().value() == 1U);
    CHECK(second_run_orders.next().value() == 1U);
}

void generator_exhaustion_is_checked() {
    SequentialIdGenerator<OrderId, 2> ids;

    CHECK(!ids.exhausted());
    CHECK(ids.next().value() == 1U);
    CHECK(ids.next().value() == 2U);
    CHECK(ids.exhausted());
    CHECK_THROWS_AS(ids.next(), std::overflow_error);
    CHECK(ids.exhausted());
}

}  // namespace

int main() {
    signed_quantities_are_exact();
    invalid_signed_quantities_are_rejected();
    integral_numeric_quantities_are_accepted();
    positive_amounts_enforce_the_request_boundary();
    quantity_arithmetic_checks_both_boundaries();
    identifier_types_are_distinct_and_nonzero();
    independent_generators_are_deterministic();
    generator_exhaustion_is_checked();

    if (failures != 0) {
        std::cerr << failures << " test assertion(s) failed\n";
        return EXIT_FAILURE;
    }

    std::cout << "all domain type tests passed\n";
    return EXIT_SUCCESS;
}
