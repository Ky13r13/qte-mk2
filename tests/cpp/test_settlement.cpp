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
#include <string>
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
using qte::core::SettlementId;
using qte::core::ShareAmount;
using qte::market_data::InstrumentSpec;
using qte::market_data::InstrumentKind;
using qte::market_data::OptionRight;
using qte::market_data::OptionSettlement;
using qte::market_data::Symbol;
using qte::market_data::Timestamp;
using qte::orders::Fill;
using qte::orders::OrderSide;
using qte::portfolio::ConflictingPortfolioSettlement;
using qte::portfolio::InvalidPortfolioInput;
using qte::portfolio::OutOfSequencePortfolioSettlement;
using qte::portfolio::Portfolio;
using qte::portfolio::PortfolioAccountingError;
using qte::portfolio::SettlementApplyResult;
using qte::portfolio::VariationSettlement;
using qte::portfolio::ValuationMark;

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

[[nodiscard]] bool near(const double left, const double right) {
    return std::abs(left - right) <= 1.0e-9 + 1.0e-12 * std::max(std::abs(left), std::abs(right));
}

#define CHECK_NEAR(actual, expected) CHECK(near((actual), (expected)))

void check_optional_near(const std::optional<double>& actual, const double expected,
                         const std::string_view expression, const int line) {
    check(actual.has_value(), expression, line);
    if (actual.has_value()) check(near(*actual, expected), expression, line);
}

#define CHECK_OPTIONAL_NEAR(actual, expected) \
    check_optional_near((actual), (expected), #actual, __LINE__)

[[nodiscard]] InstrumentSpec future(const char* symbol = "NQ", const std::int64_t multiplier = 20) {
    const auto grid = PriceGrid::from_tick_size(0.25);
    return InstrumentSpec::future(Symbol{symbol}, Currency::usd(), grid, multiplier, Timestamp{900ns}, Timestamp{1000ns});
}

[[nodiscard]] Portfolio portfolio(std::vector<InstrumentSpec> instruments = {future()}) {
    return Portfolio{10000.0, Currency::usd(), std::move(instruments)};
}

[[nodiscard]] Fill fill(
    const std::uint64_t id, const OrderSide side, const std::int64_t quantity,
    const double price, const std::uint64_t sequence, const std::uint64_t at,
    const char* symbol = "NQ", const std::int64_t multiplier = 20) {
    const auto grid = PriceGrid::from_tick_size(0.25);
    return Fill::create(FillId{id}, OrderId{id}, Symbol{symbol}, side, ShareAmount::from_count(quantity),
                        Timestamp{std::chrono::nanoseconds{at}}, EventSequence{sequence}, price,
                        grid.canonicalize(price), 0.0, multiplier);
}

[[nodiscard]] VariationSettlement settlement(
    const std::uint64_t id, const double price, const std::uint64_t sequence,
    const std::uint64_t reference, const std::uint64_t effective,
    const char* symbol = "NQ", const std::int64_t multiplier = 20,
    std::string source = "synthetic", std::string vintage = "v1") {
    return VariationSettlement::create(
        SettlementId{id}, Symbol{symbol}, Timestamp{std::chrono::nanoseconds{reference}},
        Timestamp{std::chrono::nanoseconds{reference}}, Timestamp{std::chrono::nanoseconds{effective}},
        EventSequence{sequence}, price, multiplier, std::move(source), std::move(vintage));
}

[[nodiscard]] ValuationMark mark(const std::uint64_t sequence, const double price,
                                 const std::uint64_t at) {
    return ValuationMark::create(Timestamp{std::chrono::nanoseconds{at}}, EventSequence{sequence}, price);
}

void long_short_and_idempotent_variation_cashflow_fixture() {
    auto actual = portfolio();
    actual.apply_fill(fill(1, OrderSide::buy, 2, 100.0, 1, 100));
    const auto first = settlement(1, 110.0, 2, 100, 101);
    CHECK(actual.apply_settlement(first) == SettlementApplyResult::applied);
    CHECK_NEAR(actual.cash(), 10400.0);
    const auto* open = actual.position(Symbol{"NQ"});
    CHECK(open != nullptr);
    if (open != nullptr) CHECK_OPTIONAL_NEAR(open->settlement_basis_price(), 110.0);
    CHECK(actual.settlement_count() == 1 && actual.contains_settlement(SettlementId{1}));
    if (actual.settlement_count() == 1) {
        const auto& record = actual.settlements()[0];
        CHECK_NEAR(record.cash_delta, 400.0); CHECK(record.quantity.value() == 2);
        CHECK_NEAR(record.previous_basis, 100.0); CHECK_NEAR(record.new_basis, 110.0);
    }
    CHECK(actual.apply_settlement(first) == SettlementApplyResult::already_applied);
    CHECK_NEAR(actual.cash(), 10400.0);
    actual.apply_fill(fill(2, OrderSide::sell, 2, 110.0, 3, 102));
    CHECK_NEAR(actual.cash(), 10400.0);  // closing cash uses settlement basis, not entry basis
    CHECK_NEAR(actual.realized_gross_pnl(), 400.0);  // economic realized PnL still uses entry basis
    actual.apply_fill(fill(3, OrderSide::sell, 2, 100.0, 4, 103));
    actual.apply_settlement(settlement(2, 90.0, 5, 103, 104));
    CHECK_NEAR(actual.cash(), 10800.0);
    const auto* short_position = actual.position(Symbol{"NQ"});
    CHECK(short_position != nullptr);
    if (short_position != nullptr) CHECK_OPTIONAL_NEAR(short_position->settlement_basis_price(), 90.0);
}

void add_reduce_reverse_and_flat_reset_both_future_bases() {
    auto actual = portfolio();
    actual.apply_fill(fill(1, OrderSide::buy, 2, 100.0, 1, 100));
    actual.apply_settlement(settlement(1, 110.0, 2, 100, 101));
    actual.apply_fill(fill(2, OrderSide::buy, 2, 130.0, 3, 102));
    const auto* added = actual.position(Symbol{"NQ"});
    CHECK(added != nullptr);
    if (added != nullptr) {
        CHECK_NEAR(added->average_entry_price(), 115.0);
        CHECK_OPTIONAL_NEAR(added->settlement_basis_price(), 120.0);
    }
    actual.apply_fill(fill(3, OrderSide::sell, 3, 120.0, 4, 103));
    const auto* reduced = actual.position(Symbol{"NQ"});
    CHECK(reduced != nullptr);
    if (reduced != nullptr) CHECK_OPTIONAL_NEAR(reduced->settlement_basis_price(), 120.0);
    actual.apply_fill(fill(4, OrderSide::sell, 2, 125.0, 5, 104));
    const auto* reversed = actual.position(Symbol{"NQ"});
    CHECK(reversed != nullptr);
    if (reversed != nullptr) {
        CHECK(reversed->quantity().value() == -1);
        CHECK_NEAR(reversed->average_entry_price(), 125.0);
        CHECK_OPTIONAL_NEAR(reversed->settlement_basis_price(), 125.0);
    }
    actual.apply_fill(fill(5, OrderSide::buy, 1, 125.0, 6, 105));
    const auto* flat = actual.position(Symbol{"NQ"});
    CHECK(flat != nullptr);
    if (flat != nullptr) {
        CHECK(flat->is_flat()); CHECK_NEAR(flat->average_entry_price(), 0.0);
        CHECK_OPTIONAL_NEAR(flat->settlement_basis_price(), 0.0);
    }
}

void settlement_identity_order_and_failures_are_atomic() {
    auto actual = portfolio();
    actual.apply_fill(fill(1, OrderSide::buy, 1, 100.0, 1, 100));
    const auto committed = settlement(1, 101.0, 2, 100, 101);
    actual.apply_settlement(committed);
    const auto cash = actual.cash();
    CHECK_THROWS_AS(actual.apply_settlement(settlement(1, 102.0, 2, 100, 101)), ConflictingPortfolioSettlement);
    CHECK_THROWS_AS(actual.apply_settlement(settlement(3, 102.0, 3, 101, 102)), OutOfSequencePortfolioSettlement);
    CHECK_THROWS_AS(actual.apply_settlement(settlement(2, 102.0, 3, 100, 102)), ConflictingPortfolioSettlement);
    CHECK_THROWS_AS(actual.apply_settlement(settlement(2, 102.0, 2, 101, 102)), InvalidPortfolioInput);
    CHECK_THROWS_AS(actual.apply_settlement(settlement(2, 102.0, 3, 99, 102)), InvalidPortfolioInput);
    CHECK_NEAR(actual.cash(), cash); CHECK(actual.settlement_count() == 1);
}

void validation_preserves_marks_episodes_and_mixed_derivative_state() {
    const auto grid = PriceGrid::from_tick_size(0.25);
    const auto option = InstrumentSpec::european_futures_option(
        Symbol{"NQC"}, Currency::usd(), grid, 20, Timestamp{900ns}, Timestamp{950ns}, Symbol{"NQ"},
        OptionRight::call, grid.canonicalize(100.0), OptionSettlement::futures_delivery);
    auto actual = portfolio({future(), option});
    actual.apply_fill(fill(1, OrderSide::buy, 1, 100.0, 1, 100));
    actual.apply_fill(fill(2, OrderSide::buy, 1, 10.0, 2, 100, "NQC", 20));
    const auto realized = actual.realized_gross_pnl();
    const auto episodes = actual.closed_trades().size();
    const auto marks = actual.position(Symbol{"NQ"});
    CHECK(marks != nullptr);
    actual.apply_settlement(settlement(1, 110.0, 3, 100, 101));
    CHECK_NEAR(actual.realized_gross_pnl(), realized);
    CHECK(actual.closed_trades().size() == episodes);
    CHECK_THROWS_AS(actual.apply_settlement(settlement(2, 111.0, 4, 101, 102, "NQC", 20)), InvalidPortfolioInput);
    CHECK_THROWS_AS(actual.apply_settlement(settlement(2, 111.0, 4, 101, 102, "NQ", 2)), InvalidPortfolioInput);
    CHECK_THROWS_AS(actual.apply_settlement(settlement(2, 111.0, 4, 1000, 1001)), InvalidPortfolioInput);
    CHECK(actual.settlement_count() == 1);
    const auto* future_position = actual.position(Symbol{"NQ"});
    CHECK(future_position != nullptr);
    if (future_position != nullptr) CHECK_OPTIONAL_NEAR(future_position->settlement_basis_price(), 110.0);
    const auto* option_position = actual.position(Symbol{"NQC"});
    CHECK(option_position != nullptr);
    if (option_position != nullptr) CHECK(!option_position->settlement_basis_price().has_value());
}

void dto_validation_price_grid_and_overflow_are_explicit() {
    CHECK_THROWS_AS(settlement(1, 0.0, 1, 1, 1), std::invalid_argument);
    CHECK_THROWS_AS(settlement(1, std::numeric_limits<double>::infinity(), 1, 1, 1), std::invalid_argument);
    CHECK_THROWS_AS(settlement(1, 1.0, 1, 2, 1), std::invalid_argument);
    CHECK_THROWS_AS(settlement(1, 1.0, 1, 1, 1, "NQ", 0), std::invalid_argument);
    CHECK_THROWS_AS(settlement(1, 1.0, 1, 1, 1, "NQ", 20, " ", "v"), std::invalid_argument);
    CHECK_THROWS_AS(settlement(1, 1.0, 1, 1, 1, "NQ", 20, std::string(1025, 's'), "v"), std::invalid_argument);
    auto actual = portfolio({future("NQ", 20)});
    actual.apply_fill(fill(1, OrderSide::buy, std::numeric_limits<std::int64_t>::max(), 1.0, 1, 100));
    const auto cash = actual.cash();
    CHECK_THROWS_AS(actual.apply_settlement(settlement(1, 1.0e300, 2, 100, 101)), PortfolioAccountingError);
    CHECK_NEAR(actual.cash(), cash); CHECK(actual.settlement_count() == 0);
}

void marks_and_economic_inventory_are_not_rewritten_by_settlement() {
    const auto grid = PriceGrid::from_tick_size(0.25);
    const auto option = InstrumentSpec::european_futures_option(
        Symbol{"NQC"}, Currency::usd(), grid, 20, Timestamp{900ns}, Timestamp{950ns}, Symbol{"NQ"},
        OptionRight::call, grid.canonicalize(100.0), OptionSettlement::futures_delivery);
    auto actual = portfolio({future(), option});
    actual.apply_fill(fill(1, OrderSide::buy, 1, 100.0, 1, 100));
    actual.apply_fill(fill(2, OrderSide::buy, 1, 10.0, 2, 100, "NQC", 20));
    actual.mark(Symbol{"NQ"}, mark(3, 103.0, 101));
    actual.mark(Symbol{"NQC"}, ValuationMark::option_value(Timestamp{101ns}, EventSequence{4}, 12.0));
    const auto equity_before = actual.equity();
    const auto pnl_before = actual.net_pnl();
    const auto gross_before = actual.gross_exposure();
    const auto net_before = actual.net_exposure();
    const auto realized_before = actual.realized_gross_pnl();
    const auto fees_before = actual.commissions_paid();
    const auto episodes_before = actual.closed_trades().size();
    actual.apply_settlement(settlement(1, 110.0, 5, 100, 102));
    const auto* future_position = actual.position(Symbol{"NQ"});
    const auto* option_position = actual.position(Symbol{"NQC"});
    CHECK(future_position != nullptr && option_position != nullptr);
    if (future_position != nullptr) {
        CHECK(future_position->valuation_mark().has_value());
        if (future_position->valuation_mark().has_value()) CHECK_NEAR(future_position->valuation_mark()->price(), 103.0);
        CHECK_NEAR(future_position->average_entry_price(), 100.0);
    }
    if (option_position != nullptr && option_position->valuation_mark().has_value()) {
        CHECK_NEAR(option_position->valuation_mark()->price(), 12.0);
    }
    CHECK_NEAR(actual.realized_gross_pnl(), realized_before);
    CHECK_NEAR(actual.commissions_paid(), fees_before);
    CHECK(actual.closed_trades().size() == episodes_before);
    CHECK_OPTIONAL_NEAR(gross_before, 2300.0);
    CHECK_OPTIONAL_NEAR(net_before, 2300.0);
    CHECK_OPTIONAL_NEAR(equity_before, 10100.0);
    CHECK_OPTIONAL_NEAR(pnl_before, 100.0);
    CHECK_OPTIONAL_NEAR(actual.gross_exposure(), 2300.0);
    CHECK_OPTIONAL_NEAR(actual.net_exposure(), 2300.0);
    CHECK_OPTIONAL_NEAR(actual.equity(), 10100.0);
    CHECK_OPTIONAL_NEAR(actual.net_pnl(), 100.0);

    auto unmarked = portfolio();
    unmarked.apply_fill(fill(1, OrderSide::buy, 1, 100.0, 1, 100));
    unmarked.apply_settlement(settlement(1, 110.0, 2, 100, 101));
    CHECK(!unmarked.market_value().has_value());
    CHECK(!unmarked.equity().has_value());
}

void flat_boundaries_retries_conflicts_and_causality_are_explicit() {
    auto flat = portfolio();
    CHECK(flat.apply_settlement(settlement(1, 100.1, 1, 0, 1)) == SettlementApplyResult::applied);
    CHECK_NEAR(flat.cash(), 10000.0);
    CHECK(flat.settlement_count() == 1);
    if (flat.settlement_count() == 1) {
        CHECK_NEAR(flat.settlements()[0].cash_delta, 0.0);
        CHECK(flat.settlements()[0].quantity.value() == 0);
        CHECK_NEAR(flat.settlements()[0].previous_basis, 0.0);
        CHECK_NEAR(flat.settlements()[0].new_basis, 0.0);
    }

    auto actual = portfolio();
    actual.apply_fill(fill(1, OrderSide::buy, 1, 100.0, 1, 100));
    const auto one = settlement(1, 101.0, 2, 100, 101);
    const auto two = settlement(2, 102.0, 3, 101, 102);
    actual.apply_settlement(one); actual.apply_settlement(two);
    CHECK(actual.apply_settlement(one) == SettlementApplyResult::already_applied);
    actual.apply_fill(fill(2, OrderSide::sell, 1, 102.0, 4, 103));
    CHECK(actual.fill_count() == 2);  // retry did not advance global clocks

    const std::vector<VariationSettlement> conflicts{
        settlement(1, 103.0, 2, 100, 101), settlement(1, 101.0, 9, 100, 101),
        settlement(1, 101.0, 2, 99, 101), settlement(1, 101.0, 2, 100, 102),
        settlement(1, 101.0, 2, 100, 101, "NQ", 20, "other", "v1"),
        settlement(1, 101.0, 2, 100, 101, "NQ", 20, "synthetic", "v2"),
        settlement(1, 101.0, 2, 100, 101, "OTHER")};
    for (const auto& conflict : conflicts) CHECK_THROWS_AS(actual.apply_settlement(conflict), ConflictingPortfolioSettlement);

    auto cutoff = portfolio();
    cutoff.apply_fill(fill(1, OrderSide::buy, 1, 100.0, 1, 100));
    CHECK(cutoff.apply_settlement(settlement(1, 101.0, 2, 100, 100)) == SettlementApplyResult::applied);
    cutoff.apply_fill(fill(2, OrderSide::buy, 1, 101.0, 3, 101));
    const auto cash = cutoff.cash();
    CHECK_THROWS_AS(cutoff.apply_settlement(settlement(2, 102.0, 4, 100, 102)), InvalidPortfolioInput);
    CHECK_NEAR(cutoff.cash(), cash); CHECK(cutoff.settlement_count() == 1);
}

void settlement_domain_boundaries_and_failed_attempt_rollback_are_tested() {
    CHECK_THROWS_AS(VariationSettlement::create(SettlementId{1}, Symbol{"NQ"}, Timestamp{2ns}, Timestamp{1ns},
                                                 Timestamp{2ns}, EventSequence{1}, 1.0, 20, "s", "v"), std::invalid_argument);
    CHECK_THROWS_AS(VariationSettlement::create(SettlementId{1}, Symbol{"NQ"}, Timestamp{1ns}, Timestamp{2ns},
                                                 Timestamp{1ns}, EventSequence{1}, 1.0, 20, "s", "v"), std::invalid_argument);
    auto actual = portfolio();
    actual.apply_fill(fill(1, OrderSide::buy, 1, 100.0, 1, 100));
    CHECK(actual.apply_settlement(settlement(1, 100.1, 2, 100, 101)) == SettlementApplyResult::applied);
    auto expiry = portfolio();
    CHECK(expiry.apply_settlement(settlement(1, 100.0, 1, 999, 999)) == SettlementApplyResult::applied);
    auto expired = portfolio();
    CHECK_THROWS_AS(expired.apply_settlement(settlement(1, 100.0, 1, 1000, 1000)), InvalidPortfolioInput);
    auto unknown = portfolio();
    CHECK_THROWS_AS(unknown.apply_settlement(settlement(1, 100.0, 1, 0, 1, "QQQ")), InvalidPortfolioInput);
    auto equity = Portfolio{10000.0, Currency::usd(), {InstrumentSpec{Symbol{"SPY"}, Currency::usd(), PriceGrid::from_tick_size(.01)}}};
    CHECK_THROWS_AS(equity.apply_settlement(settlement(1, 100.0, 1, 0, 1, "SPY")), InvalidPortfolioInput);

    auto overflow = portfolio();
    overflow.apply_fill(fill(1, OrderSide::buy, std::numeric_limits<std::int64_t>::max(), 1.0, 1, 100));
    CHECK_THROWS_AS(overflow.apply_settlement(settlement(1, 1.0e300, 2, 100, 101)), PortfolioAccountingError);
    CHECK(overflow.apply_settlement(settlement(1, 1.0, 2, 100, 101)) == SettlementApplyResult::applied);
}

}  // namespace

int main() {
    long_short_and_idempotent_variation_cashflow_fixture();
    add_reduce_reverse_and_flat_reset_both_future_bases();
    settlement_identity_order_and_failures_are_atomic();
    validation_preserves_marks_episodes_and_mixed_derivative_state();
    dto_validation_price_grid_and_overflow_are_explicit();
    marks_and_economic_inventory_are_not_rewritten_by_settlement();
    flat_boundaries_retries_conflicts_and_causality_are_explicit();
    settlement_domain_boundaries_and_failed_attempt_rollback_are_tested();
    if (failures != 0) {
        std::cerr << failures << " test assertion(s) failed\n";
        return EXIT_FAILURE;
    }
    std::cout << "all settlement tests passed\n";
    return EXIT_SUCCESS;
}
