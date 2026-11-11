#include "qte/config/research_profile.hpp"

#include <stdexcept>
#include <utility>

namespace qte::config {

ResearchProfile::ResearchProfile() : valuation_currency_(core::Currency::usd()) {}

ResearchProfile::ResearchProfile(core::Currency valuation_currency)
    : valuation_currency_(std::move(valuation_currency)) {}

bool ResearchProfile::supports(const market_data::InstrumentSpec& instrument) const noexcept {
    return instrument.kind() == market_data::InstrumentKind::cash_equity &&
        instrument.quote_currency() == valuation_currency_;
}

void ResearchProfile::require_supported(
    const market_data::InstrumentSpec& instrument) const {
    if (!supports(instrument)) {
        throw std::invalid_argument(
            "research profile requires cash equities in the valuation currency; "
            "derivative replay is not enabled");
    }
}

}  // namespace qte::config
