#include "qte/portfolio/settlement.hpp"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <stdexcept>

namespace qte::portfolio {
namespace {

void require_identity(const std::string& value) {
    if (value.empty() || value.size() > 1024 ||
        std::all_of(value.begin(), value.end(), [](unsigned char character) {
            return std::isspace(character) != 0;
        })) {
        throw std::invalid_argument("settlement source/vintage must be nonblank and at most 1024 bytes");
    }
}

}  // namespace

VariationSettlement VariationSettlement::create(
    const core::SettlementId id,
    market_data::Symbol symbol,
    const market_data::Timestamp reference_at,
    const market_data::Timestamp available_at,
    const market_data::Timestamp effective_at,
    const core::EventSequence sequence,
    const double price,
    const std::int64_t contract_multiplier,
    std::string source_id,
    std::string vintage_id) {
    if (!std::isfinite(price) || price <= 0.0) {
        throw std::invalid_argument("settlement price must be finite and positive");
    }
    if (contract_multiplier <= 0) {
        throw std::invalid_argument("settlement contract multiplier must be positive");
    }
    if (reference_at > available_at || available_at > effective_at) {
        throw std::invalid_argument("settlement requires reference <= availability <= effective time");
    }
    require_identity(source_id);
    require_identity(vintage_id);
    return VariationSettlement{
        id, std::move(symbol), reference_at, available_at, effective_at, sequence,
        price, contract_multiplier, std::move(source_id), std::move(vintage_id)};
}

}  // namespace qte::portfolio
