#include "qte/config/research_profile.hpp"
#include "qte/market_data/dataset.hpp"
#include "qte/risk/risk.hpp"

#include <chrono>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string_view>

namespace {
using namespace qte;
using namespace std::chrono_literals;

int failures = 0;
void check(bool condition, std::string_view expression, int line) {
    if (!condition) {
        std::cerr << "line " << line << ": " << expression << '\n';
        ++failures;
    }
}
#define CHECK(expression) check((expression), #expression, __LINE__)
template <typename Exception, typename Function>
void check_throws(Function&& fn, int line) {
    try {
        fn();
        check(false, "expected rejection", line);
    } catch (const Exception&) {
    } catch (...) {
        check(false, "wrong exception type", line);
    }
}
#define CHECK_THROWS(expression, exception) \
    check_throws<exception>([&] { static_cast<void>(expression); }, __LINE__)

market_data::InstrumentSpec future() {
    return market_data::InstrumentSpec::future(
        market_data::Symbol{"NQZ-test"}, core::Currency::usd(),
        core::PriceGrid::from_tick_size(0.25), 20,
        market_data::Timestamp{1000ns}, market_data::Timestamp{1100ns});
}

market_data::InstrumentSpec option() {
    return market_data::InstrumentSpec::european_futures_option(
        market_data::Symbol{"NQ-call-test"}, core::Currency::usd(),
        core::PriceGrid::from_tick_size(0.25), 20,
        market_data::Timestamp{500ns}, market_data::Timestamp{600ns},
        future().symbol(), market_data::OptionRight::call,
        core::PriceGrid::from_tick_size(5.0).canonicalize(20000.0),
        market_data::OptionSettlement::futures_delivery);
}

orders::OrderRequest request(const market_data::Symbol& symbol) {
    return {
        .symbol = symbol,
        .side = orders::OrderSide::buy,
        .quantity = core::ShareAmount::from_count(1),
        .type = orders::OrderType::market,
        .limit_price = std::nullopt,
        .stop_price = std::nullopt,
        .time_in_force = orders::TimeInForce::good_til_canceled,
    };
}

void derivative_replay_and_execution_are_blocked() {
    const config::ResearchProfile profile;
    const execution::OpenOnlyExecutionModel execution{execution::ExecutionCosts::create()};
    for (const auto& contract : {future(), option()}) {
        CHECK(!profile.supports(contract));
        CHECK_THROWS(profile.require_supported(contract), std::invalid_argument);
        const market_data::Bar bar{
            contract.symbol(), market_data::Timestamp{0ns}, market_data::Timestamp{10ns},
            100.0, 101.0, 99.0, 100.0, 1.0};
        const market_data::DatasetInput input{
            market_data::DatasetMetadata::create(
                10ns, core::Currency::usd(), market_data::PriceAdjustmentMode::unadjusted,
                market_data::CorporateActionCoverage::action_free, "contracts", "synthetic"),
            {contract}, {{contract.symbol(), {bar}}}};
        try {
            static_cast<void>(market_data::preflight(input));
            CHECK(false);
        } catch (const market_data::InvalidDataset& error) {
            bool rejected_kind = false;
            for (const auto& detail : error.errors()) {
                rejected_kind |= detail.code ==
                    market_data::DatasetValidationCode::unsupported_instrument;
            }
            CHECK(rejected_kind);
        }
        orders::OrderRecord order{
            core::OrderId{1}, request(contract.symbol()), market_data::Timestamp{1ns},
            core::EventSequence{1}, core::EventSequence{1}};
        order.open(contract);
        CHECK_THROWS(execution.estimate(
            orders::OrderSide::buy, core::ShareAmount::from_count(1), contract, 100.0),
            execution::InvalidExecutionInput);
        CHECK_THROWS(execution.evaluate(order, contract, execution::MarketOpen::create(
            contract.symbol(), market_data::Timestamp{2ns}, core::EventSequence{2}, 100.0)),
            execution::InvalidExecutionInput);
        CHECK(order.status() == orders::OrderStatus::open);
        CHECK(order.remaining_quantity().value() == 1);
        CHECK(!order.stop_triggered());
    }
}

void equity_risk_rejects_even_flat_mixed_portfolios() {
    const market_data::InstrumentSpec equity{
        market_data::Symbol{"SPY"}, core::Currency::usd(), core::PriceGrid::from_tick_size(0.01)};
    portfolio::Portfolio mixed{1000000.0, core::Currency::usd(), {equity, future(), option()}};
    mixed.mark(equity.symbol(), portfolio::ValuationMark::create(
        market_data::Timestamp{1ns}, core::EventSequence{1}, 100.0));
    const risk::RiskManager risk{risk::RiskLimits::create(
        std::nullopt, std::nullopt, std::nullopt, true, std::nullopt)};
    portfolio::Portfolio equity_only{1000000.0, core::Currency::usd(), {equity}};
    for (const auto& contract : {future(), option()}) {
        const auto result = risk.evaluate_submission(
            request(contract.symbol()), contract, equity_only.snapshot(), {}, 100.0, 0.0);
        CHECK(result.rejection_code() == risk::RiskRejectionCode::unsupported_instrument);
    }
    for (const auto& contract : {equity, future(), option()}) {
        const auto result = risk.evaluate_submission(
            request(contract.symbol()), contract, mixed.snapshot(), {}, 100.0, 0.0);
        CHECK(!result.approved());
        CHECK(result.rejection_code() == risk::RiskRejectionCode::unsupported_instrument);
    }
    // A candidate created from valid equity metadata must not bypass the mixed-ledger gate.
    orders::OrderRecord order{
        core::OrderId{1}, request(equity.symbol()), market_data::Timestamp{1ns},
        core::EventSequence{1}, core::EventSequence{1}};
    order.open(equity);
    const auto candidate = execution::OpenOnlyExecutionModel{execution::ExecutionCosts::create()}
        .evaluate(order, equity, execution::MarketOpen::create(
            equity.symbol(), market_data::Timestamp{2ns}, core::EventSequence{2}, 100.0));
    CHECK(candidate.has_value());
    if (candidate) {
        const auto result = risk.evaluate_execution(*candidate, mixed.snapshot(), {});
        CHECK(!result.approved());
        CHECK(result.rejection_code() == risk::RiskRejectionCode::unsupported_instrument);
    }
    CHECK(risk::to_string(risk::RiskRejectionCode::unsupported_instrument) == "unsupported_instrument");
    CHECK(mixed.fill_count() == 0);
    CHECK(mixed.cash() == 1000000.0);
}
}  // namespace

int main() {
    derivative_replay_and_execution_are_blocked();
    equity_risk_rejects_even_flat_mixed_portfolios();
    return failures == 0 ? EXIT_SUCCESS : EXIT_FAILURE;
}
