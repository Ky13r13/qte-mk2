#pragma once

#include "qte/core/currency.hpp"
#include "qte/core/price.hpp"
#include "qte/market_data/bar.hpp"

#include <cstdint>
#include <optional>
#include <stdexcept>
#include <utility>

namespace qte::market_data {

enum class InstrumentKind { cash_equity, future, futures_option };
enum class OptionRight { call, put };
enum class OptionSettlement { futures_delivery, cash };

class InstrumentSpec final {
public:
    InstrumentSpec(
        Symbol symbol,
        core::Currency quote_currency,
        core::PriceGrid price_grid)
        : symbol_(std::move(symbol)),
          quote_currency_(std::move(quote_currency)), price_grid_(price_grid) {}

    [[nodiscard]] static InstrumentSpec future(
        Symbol symbol, core::Currency quote_currency, core::PriceGrid price_grid,
        std::int64_t contract_multiplier, Timestamp last_trade, Timestamp expiry) {
        return InstrumentSpec{
            std::move(symbol), std::move(quote_currency), price_grid,
            InstrumentKind::future, contract_multiplier, last_trade, expiry, std::nullopt,
            std::nullopt, std::nullopt, std::nullopt};
    }

    [[nodiscard]] static InstrumentSpec european_futures_option(
        Symbol symbol, core::Currency quote_currency, core::PriceGrid price_grid,
        std::int64_t contract_multiplier, Timestamp last_trade, Timestamp expiry, Symbol underlying,
        OptionRight right, core::TickPrice strike, OptionSettlement settlement) {
        return InstrumentSpec{
            std::move(symbol), std::move(quote_currency), price_grid,
            InstrumentKind::futures_option, contract_multiplier, last_trade, expiry,
            std::move(underlying), right, strike, settlement};
    }

    [[nodiscard]] const Symbol& symbol() const noexcept { return symbol_; }
    [[nodiscard]] const core::Currency& quote_currency() const noexcept {
        return quote_currency_;
    }
    [[nodiscard]] const core::PriceGrid& price_grid() const noexcept { return price_grid_; }
    [[nodiscard]] InstrumentKind kind() const noexcept { return kind_; }
    [[nodiscard]] std::int64_t contract_multiplier() const noexcept { return contract_multiplier_; }
    [[nodiscard]] const std::optional<Timestamp>& last_trade() const noexcept { return last_trade_; }
    [[nodiscard]] const std::optional<Timestamp>& expiry() const noexcept { return expiry_; }
    [[nodiscard]] const std::optional<Symbol>& underlying() const noexcept { return underlying_; }
    [[nodiscard]] const std::optional<OptionRight>& option_right() const noexcept { return option_right_; }
    [[nodiscard]] const std::optional<core::TickPrice>& strike() const noexcept { return strike_; }
    [[nodiscard]] const std::optional<OptionSettlement>& option_settlement() const noexcept {
        return option_settlement_;
    }

private:
    InstrumentSpec(
        Symbol symbol, core::Currency quote_currency, core::PriceGrid price_grid,
        InstrumentKind kind, std::int64_t contract_multiplier,
        std::optional<Timestamp> last_trade, std::optional<Timestamp> expiry,
        std::optional<Symbol> underlying,
        std::optional<OptionRight> right, std::optional<core::TickPrice> strike,
        std::optional<OptionSettlement> settlement)
        : symbol_(std::move(symbol)), quote_currency_(std::move(quote_currency)),
          price_grid_(price_grid), kind_(kind),
          contract_multiplier_(contract_multiplier), last_trade_(last_trade), expiry_(expiry),
          underlying_(std::move(underlying)), option_right_(right), strike_(strike),
          option_settlement_(settlement) {
        if (contract_multiplier_ <= 0) {
            throw std::invalid_argument("contract multiplier must be positive");
        }
        if (last_trade_.has_value() && expiry_.has_value() && *last_trade_ > *expiry_) {
            throw std::invalid_argument("last trade cannot follow expiry");
        }
        if (kind_ == InstrumentKind::futures_option &&
            (!strike_.has_value() || strike_->value() <= 0.0)) {
            throw std::invalid_argument("option strike must be positive");
        }
        if (kind_ == InstrumentKind::futures_option) {
            if (!underlying_.has_value() || *underlying_ == symbol_) {
                throw std::invalid_argument("option underlying must be a different symbol");
            }
            if (*option_right_ != OptionRight::call && *option_right_ != OptionRight::put) {
                throw std::invalid_argument("option right is unsupported");
            }
            if (*option_settlement_ != OptionSettlement::futures_delivery &&
                *option_settlement_ != OptionSettlement::cash) {
                throw std::invalid_argument("option settlement is unsupported");
            }
        }
    }

    Symbol symbol_;
    core::Currency quote_currency_;
    core::PriceGrid price_grid_;
    InstrumentKind kind_{InstrumentKind::cash_equity};
    std::int64_t contract_multiplier_{1};
    std::optional<Timestamp> last_trade_;
    std::optional<Timestamp> expiry_;
    std::optional<Symbol> underlying_;
    std::optional<OptionRight> option_right_;
    std::optional<core::TickPrice> strike_;
    std::optional<OptionSettlement> option_settlement_;
};

}  // namespace qte::market_data
