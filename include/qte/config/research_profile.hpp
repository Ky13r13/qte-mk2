#pragma once

#include "qte/core/currency.hpp"
#include "qte/market_data/instrument.hpp"

namespace qte::config {

// Configuration for the initial single-valuation-currency research profile.
class ResearchProfile final {
public:
    ResearchProfile();
    explicit ResearchProfile(core::Currency valuation_currency);

    [[nodiscard]] const core::Currency& valuation_currency() const noexcept {
        return valuation_currency_;
    }
    [[nodiscard]] bool supports(const market_data::InstrumentSpec& instrument) const noexcept;
    void require_supported(const market_data::InstrumentSpec& instrument) const;

private:
    core::Currency valuation_currency_;
};

}  // namespace qte::config
