#include "qte/analytics/performance.hpp"
#include "qte/engine/backtest.hpp"

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <chrono>
#include <map>
#include <limits>
#include <memory>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace py = pybind11;

namespace {

using namespace qte;

[[nodiscard]] std::int64_t nanos(const market_data::Timestamp value) {
    return value.time_since_epoch().count();
}

[[nodiscard]] market_data::Timestamp timestamp(const std::int64_t value) {
    return market_data::Timestamp{std::chrono::nanoseconds{value}};
}

class PythonStrategy final : public strategy::Strategy,
                             public py::trampoline_self_life_support {
public:
    using strategy::Strategy::Strategy;

    void on_start(strategy::StrategyContext& context) override {
        py::gil_scoped_acquire acquire;
        PYBIND11_OVERRIDE(void, strategy::Strategy, on_start, context);
    }

    void on_bar(
        strategy::StrategyContext& context,
        const market_data::Bar& bar) override {
        py::gil_scoped_acquire acquire;
        PYBIND11_OVERRIDE_PURE(void, strategy::Strategy, on_bar, context, bar);
    }

    void on_fill(
        strategy::StrategyContext& context,
        const orders::Fill& fill) override {
        py::gil_scoped_acquire acquire;
        PYBIND11_OVERRIDE(void, strategy::Strategy, on_fill, context, fill);
    }

    void on_end(strategy::StrategyContext& context) override {
        py::gil_scoped_acquire acquire;
        PYBIND11_OVERRIDE(void, strategy::Strategy, on_end, context);
    }
};

[[nodiscard]] orders::OrderRequest order_request(
    std::string symbol,
    const orders::OrderSide side,
    const std::int64_t quantity,
    const orders::OrderType type,
    const std::optional<double> limit,
    const std::optional<double> stop,
    const double tick_size) {
    const auto grid = core::PriceGrid::from_tick_size(tick_size);
    auto request = orders::OrderRequest{
        .symbol = market_data::Symbol{std::move(symbol)},
        .side = side,
        .quantity = core::ShareAmount::from_count(quantity),
        .type = type,
        .limit_price = limit.has_value()
            ? std::optional{grid.canonicalize(*limit)} : std::nullopt,
        .stop_price = stop.has_value()
            ? std::optional{grid.canonicalize(*stop)} : std::nullopt,
        .time_in_force = orders::TimeInForce::good_til_canceled,
    };
    orders::require_valid(request);
    return request;
}

[[nodiscard]] market_data::ValidatedDataset make_dataset(
    std::vector<market_data::Bar> bars,
    const std::int64_t interval_ns,
    std::string source_id,
    const double tick_size,
    std::string currency,
    std::string volume_unit) {
    std::map<std::string, std::vector<market_data::Bar>> grouped;
    for (auto& bar : bars) {
        grouped[bar.symbol.value()].push_back(std::move(bar));
    }
    std::vector<market_data::InstrumentSpec> instruments;
    std::vector<market_data::BarStream> streams;
    const auto quote = core::Currency::from_code(std::move(currency));
    const auto grid = core::PriceGrid::from_tick_size(tick_size);
    for (auto& [symbol, stream] : grouped) {
        instruments.emplace_back(market_data::Symbol{symbol}, quote, grid);
        streams.push_back(market_data::BarStream{
            market_data::Symbol{symbol}, std::move(stream)});
    }
    return market_data::preflight(market_data::DatasetInput{
        market_data::DatasetMetadata::create(
            std::chrono::nanoseconds{interval_ns}, quote,
            market_data::PriceAdjustmentMode::unadjusted,
            market_data::CorporateActionCoverage::action_free,
            std::move(volume_unit), std::move(source_id)),
        std::move(instruments), std::move(streams)});
}

}  // namespace

PYBIND11_MODULE(_core, module) {
    module.doc() = "QTE deterministic C++ research engine";

    py::register_exception<market_data::InvalidBar>(module, "InvalidBar", PyExc_ValueError);
    py::register_exception<market_data::InvalidDataset>(module, "InvalidDataset", PyExc_ValueError);
    py::register_exception<strategy::StrategyContextError>(module, "StrategyContextError", PyExc_RuntimeError);
    py::register_exception<strategy::StrategyLifecycleError>(module, "StrategyLifecycleError", PyExc_RuntimeError);

    py::enum_<orders::OrderSide>(module, "OrderSide")
        .value("BUY", orders::OrderSide::buy)
        .value("SELL", orders::OrderSide::sell);
    py::enum_<orders::OrderStatus>(module, "OrderStatus")
        .value("NEW", orders::OrderStatus::new_order)
        .value("OPEN", orders::OrderStatus::open)
        .value("PARTIALLY_FILLED", orders::OrderStatus::partially_filled)
        .value("FILLED", orders::OrderStatus::filled)
        .value("CANCELED", orders::OrderStatus::canceled)
        .value("REJECTED", orders::OrderStatus::rejected);

    py::class_<market_data::Bar>(module, "Bar")
        .def(py::init([](
            std::string symbol, std::int64_t start_ns, std::int64_t end_ns,
            double open, double high, double low, double close, double volume) {
            auto value = market_data::Bar{
                market_data::Symbol{std::move(symbol)}, timestamp(start_ns),
                timestamp(end_ns), open, high, low, close, volume};
            market_data::require_valid(value);
            return value;
        }))
        .def_property_readonly("symbol", [](const market_data::Bar& value) {
            return value.symbol.value();
        })
        .def_property_readonly("start_ns", [](const market_data::Bar& value) {
            return nanos(value.start_time);
        })
        .def_property_readonly("end_ns", [](const market_data::Bar& value) {
            return nanos(value.end_time);
        })
        .def_readonly("open", &market_data::Bar::open)
        .def_readonly("high", &market_data::Bar::high)
        .def_readonly("low", &market_data::Bar::low)
        .def_readonly("close", &market_data::Bar::close)
        .def_readonly("volume", &market_data::Bar::volume);

    py::class_<market_data::ValidatedDataset>(module, "Dataset")
        .def_static(
            "from_bars", &make_dataset, py::arg("bars"), py::arg("interval_ns"),
            py::arg("source_id"), py::arg("tick_size") = 0.01,
            py::arg("currency") = "USD", py::arg("volume_unit") = "shares")
        .def_property_readonly("bar_count", &market_data::ValidatedDataset::bar_count)
        .def_property_readonly("hash", [](const market_data::ValidatedDataset& value) {
            return market_data::canonical_hash(value);
        })
        .def_property_readonly("gap_count", [](const market_data::ValidatedDataset& value) {
            return value.gaps().size();
        });

    py::class_<execution::ExecutionCosts>(module, "ExecutionCosts")
        .def(py::init([](double commission, double spread, double slippage) {
            return execution::ExecutionCosts::create(commission, spread, slippage);
        }), py::arg("commission_bps") = 0.0, py::arg("spread_bps") = 0.0,
            py::arg("slippage_bps") = 0.0)
        .def_property_readonly("commission_bps", &execution::ExecutionCosts::commission_bps)
        .def_property_readonly("spread_bps", &execution::ExecutionCosts::spread_bps)
        .def_property_readonly("slippage_bps", &execution::ExecutionCosts::slippage_bps);

    py::class_<risk::RiskLimits>(module, "RiskLimits")
        .def(py::init([](
            std::optional<std::int64_t> order_quantity,
            std::optional<double> symbol_allocation,
            std::optional<double> leverage,
            bool allow_short,
            std::optional<double> cash_floor) {
            return risk::RiskLimits::create(
                order_quantity, symbol_allocation, leverage, allow_short, cash_floor);
        }), py::arg("max_order_quantity") = std::numeric_limits<std::int64_t>::max(),
            py::arg("max_symbol_allocation") = 1.0,
            py::arg("max_gross_leverage") = 1.0,
            py::arg("allow_short") = false, py::arg("cash_floor") = 0.0);

    py::class_<engine::BacktestConfig>(module, "BacktestConfig")
        .def(py::init([](
            double initial_cash, execution::ExecutionCosts costs,
            risk::RiskLimits limits, std::size_t history_capacity,
            std::uint64_t seed, std::string build_identity) {
            return engine::BacktestConfig::create(
                initial_cash, costs, limits, history_capacity, seed,
                std::move(build_identity));
        }), py::arg("initial_cash"), py::arg("execution_costs") = execution::ExecutionCosts::create(),
            py::arg("risk_limits") = risk::RiskLimits::create(),
            py::arg("history_capacity") = 256, py::arg("random_seed") = 0,
            py::arg("build_identity") = "unversioned-source");

    py::class_<orders::OrderRequest>(module, "OrderRequest");
    module.def("market_order", [](std::string symbol, orders::OrderSide side, std::int64_t quantity) {
        return order_request(std::move(symbol), side, quantity, orders::OrderType::market,
                             std::nullopt, std::nullopt, 0.01);
    });
    module.def("limit_order", [](std::string symbol, orders::OrderSide side,
                                  std::int64_t quantity, double limit, double tick_size) {
        return order_request(std::move(symbol), side, quantity, orders::OrderType::limit,
                             limit, std::nullopt, tick_size);
    }, py::arg("symbol"), py::arg("side"), py::arg("quantity"), py::arg("limit_price"),
       py::arg("tick_size") = 0.01);
    module.def("stop_order", [](std::string symbol, orders::OrderSide side,
                                 std::int64_t quantity, double stop, double tick_size) {
        return order_request(std::move(symbol), side, quantity, orders::OrderType::stop,
                             std::nullopt, stop, tick_size);
    }, py::arg("symbol"), py::arg("side"), py::arg("quantity"), py::arg("stop_price"),
       py::arg("tick_size") = 0.01);
    module.def("stop_limit_order", [](std::string symbol, orders::OrderSide side,
                                       std::int64_t quantity, double stop, double limit,
                                       double tick_size) {
        return order_request(std::move(symbol), side, quantity, orders::OrderType::stop_limit,
                             limit, stop, tick_size);
    }, py::arg("symbol"), py::arg("side"), py::arg("quantity"), py::arg("stop_price"),
       py::arg("limit_price"), py::arg("tick_size") = 0.01);

    py::class_<strategy::SubmissionReceipt>(module, "SubmissionReceipt")
        .def_property_readonly("order_id", [](const strategy::SubmissionReceipt& value) {
            return value.order_id().value();
        });
    py::class_<strategy::CancellationReceipt>(module, "CancellationReceipt")
        .def_property_readonly("order_id", [](const strategy::CancellationReceipt& value) {
            return value.order_id().value();
        });
    py::class_<portfolio::PositionSnapshot>(module, "PositionSnapshot")
        .def_property_readonly("symbol", [](const portfolio::PositionSnapshot& value) { return value.symbol.value(); })
        .def_property_readonly("quantity", [](const portfolio::PositionSnapshot& value) { return value.quantity.value(); })
        .def_readonly("mark_price", &portfolio::PositionSnapshot::mark_price)
        .def_property_readonly("mark_ns", [](const portfolio::PositionSnapshot& value) -> std::optional<std::int64_t> {
            return value.mark_timestamp.has_value()
                ? std::optional{nanos(*value.mark_timestamp)} : std::nullopt;
        });
    py::class_<portfolio::PortfolioSnapshot>(module, "PortfolioSnapshot")
        .def_property_readonly("cash", &portfolio::PortfolioSnapshot::cash)
        .def_property_readonly("equity", &portfolio::PortfolioSnapshot::equity)
        .def_property_readonly("positions", [](const portfolio::PortfolioSnapshot& value) { return value.positions(); });

    py::class_<strategy::StrategyContext>(module, "StrategyContext")
        .def_property_readonly("portfolio", &strategy::StrategyContext::portfolio)
        .def("position", [](const strategy::StrategyContext& context, const std::string& symbol) {
            return context.position(market_data::Symbol{symbol});
        })
        .def("history", [](const strategy::StrategyContext& context, const std::string& symbol) {
            return context.history(market_data::Symbol{symbol});
        })
        .def("submit_order", &strategy::StrategyContext::submit_order)
        .def("cancel_order", [](strategy::StrategyContext& context, std::uint64_t id) {
            return context.cancel_order(core::OrderId{id});
        });

    py::class_<orders::Fill>(module, "Fill")
        .def_property_readonly("id", [](const orders::Fill& value) { return value.id().value(); })
        .def_property_readonly("order_id", [](const orders::Fill& value) { return value.order_id().value(); })
        .def_property_readonly("symbol", [](const orders::Fill& value) { return value.symbol().value(); })
        .def_property_readonly("side", &orders::Fill::side)
        .def_property_readonly("quantity", [](const orders::Fill& value) { return value.quantity().value(); })
        .def_property_readonly("effective_ns", [](const orders::Fill& value) { return nanos(value.effective_at()); })
        .def_property_readonly("executed_price", [](const orders::Fill& value) { return value.executed_price().value(); })
        .def_property_readonly("gross_notional", &orders::Fill::gross_notional)
        .def_property_readonly("commission", &orders::Fill::commission);

    py::class_<strategy::Strategy, PythonStrategy, py::smart_holder>(module, "Strategy")
        .def(py::init<>())
        .def("on_start", &strategy::Strategy::on_start)
        .def("on_bar", &strategy::Strategy::on_bar)
        .def("on_fill", &strategy::Strategy::on_fill)
        .def("on_end", &strategy::Strategy::on_end);

    py::class_<engine::EquityPoint>(module, "EquityPoint")
        .def_property_readonly("timestamp_ns", [](const engine::EquityPoint& value) { return nanos(value.timestamp); })
        .def_readonly("equity", &engine::EquityPoint::equity)
        .def_readonly("gross_exposure", &engine::EquityPoint::gross_exposure);
    py::class_<engine::OrderSnapshot>(module, "OrderSnapshot")
        .def_property_readonly("id", [](const engine::OrderSnapshot& value) { return value.id.value(); })
        .def_property_readonly("symbol", [](const engine::OrderSnapshot& value) { return value.request.symbol.value(); })
        .def_readonly("status", &engine::OrderSnapshot::status)
        .def_readonly("detail", &engine::OrderSnapshot::detail);
    py::class_<portfolio::TradeEpisode>(module, "TradeEpisode")
        .def_property_readonly("symbol", [](const portfolio::TradeEpisode& value) { return value.symbol().value(); })
        .def_property_readonly("net_realized_pnl", &portfolio::TradeEpisode::net_realized_pnl)
        .def_property_readonly("is_closed", &portfolio::TradeEpisode::is_closed);
    py::class_<engine::RunManifest>(module, "RunManifest")
        .def_readonly("dataset_hash", &engine::RunManifest::dataset_hash)
        .def_readonly("dataset_hash_algorithm", &engine::RunManifest::dataset_hash_algorithm)
        .def_readonly("source_id", &engine::RunManifest::source_id)
        .def_readonly("execution_model", &engine::RunManifest::execution_model)
        .def_readonly("random_seed", &engine::RunManifest::random_seed)
        .def_readonly("build_identity", &engine::RunManifest::build_identity)
        .def_readonly("normalized_config", &engine::RunManifest::normalized_config);
    py::class_<engine::BacktestResults>(module, "BacktestResults")
        .def_property_readonly("equity_curve", [](const engine::BacktestResults& value) { return value.equity_curve(); })
        .def_property_readonly("orders", [](const engine::BacktestResults& value) { return value.orders(); })
        .def_property_readonly("fills", [](const engine::BacktestResults& value) { return value.fills(); })
        .def_property_readonly("trades", [](const engine::BacktestResults& value) { return value.trades(); })
        .def_property_readonly("open_trades", [](const engine::BacktestResults& value) { return value.open_trades(); })
        .def_property_readonly("positions", [](const engine::BacktestResults& value) { return value.positions(); })
        .def_property_readonly("manifest", &engine::BacktestResults::manifest,
                               py::return_value_policy::copy);

    py::class_<analytics::Metric>(module, "Metric")
        .def_property_readonly("value", &analytics::Metric::value)
        .def_property_readonly("undefined_reason", &analytics::Metric::undefined_reason);
    py::class_<analytics::AnnualizationConfig>(module, "AnnualizationConfig")
        .def(py::init<double, double>(), py::arg("periods_per_year"),
             py::arg("annual_risk_free_rate") = 0.0)
        .def_readonly("periods_per_year", &analytics::AnnualizationConfig::periods_per_year)
        .def_readonly("annual_risk_free_rate", &analytics::AnnualizationConfig::annual_risk_free_rate);
    py::class_<analytics::PerformanceReport>(module, "PerformanceReport")
        .def_readonly("returns", &analytics::PerformanceReport::returns)
        .def_readonly("total_return", &analytics::PerformanceReport::total_return)
        .def_readonly("maximum_drawdown", &analytics::PerformanceReport::maximum_drawdown)
        .def_readonly("trade_count", &analytics::PerformanceReport::trade_count)
        .def_readonly("win_rate", &analytics::PerformanceReport::win_rate)
        .def_readonly("profit_factor", &analytics::PerformanceReport::profit_factor)
        .def_readonly("expectancy", &analytics::PerformanceReport::expectancy)
        .def_readonly("turnover", &analytics::PerformanceReport::turnover)
        .def_readonly("average_gross_exposure", &analytics::PerformanceReport::average_gross_exposure)
        .def_readonly("annualized_return", &analytics::PerformanceReport::annualized_return)
        .def_readonly("annualized_volatility", &analytics::PerformanceReport::annualized_volatility)
        .def_readonly("sharpe_ratio", &analytics::PerformanceReport::sharpe_ratio)
        .def_readonly("sortino_ratio", &analytics::PerformanceReport::sortino_ratio)
        .def_readonly("calmar_ratio", &analytics::PerformanceReport::calmar_ratio);
    module.def("analyze", &analytics::analyze, py::arg("results"),
               py::arg("annualization") = std::nullopt);

    py::class_<engine::BacktestEngine>(module, "BacktestEngine")
        .def(py::init<engine::BacktestConfig>())
        .def("run", [](const engine::BacktestEngine& engine,
                        const market_data::ValidatedDataset& dataset,
                        std::shared_ptr<strategy::Strategy> strategy) {
            py::gil_scoped_release release;
            return engine.run(dataset, std::move(strategy));
        }, py::arg("data"), py::arg("strategy"));
}
