#pragma once

#include "qte/core/currency.hpp"
#include "qte/core/price.hpp"
#include "qte/market_data/bar.hpp"

#include <cstdint>
#include <utility>

namespace qte::market_data {

// Metadata for the first supported cash-equity research profile.
class InstrumentSpec final {
public:
    InstrumentSpec(
        Symbol symbol,
        core::Currency quote_currency,
        core::PriceGrid price_grid)
        : symbol_(std::move(symbol)),
          quote_currency_(std::move(quote_currency)),
          price_grid_(price_grid) {}

    [[nodiscard]] const Symbol& symbol() const noexcept { return symbol_; }
    [[nodiscard]] const core::Currency& quote_currency() const noexcept {
        return quote_currency_;
    }
    [[nodiscard]] const core::PriceGrid& price_grid() const noexcept { return price_grid_; }
    [[nodiscard]] static constexpr std::int64_t contract_multiplier() noexcept { return 1; }

private:
    Symbol symbol_;
    core::Currency quote_currency_;
    core::PriceGrid price_grid_;
};

}  // namespace qte::market_data
