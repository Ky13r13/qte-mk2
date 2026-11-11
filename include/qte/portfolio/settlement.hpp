#pragma once

#include "qte/core/identifier.hpp"
#include "qte/market_data/bar.hpp"

#include <cstdint>
#include <string>
#include <utility>

namespace qte::portfolio {

// An explicit cash-settlement fact, not a valuation mark or an inferred daily
// event. Adapters supply source identity and availability; Portfolio owns replay.
class VariationSettlement final {
public:
    VariationSettlement(const VariationSettlement&) = default;
    VariationSettlement(VariationSettlement&&) noexcept = default;
    VariationSettlement& operator=(const VariationSettlement&) = delete;
    VariationSettlement& operator=(VariationSettlement&&) = delete;

    [[nodiscard]] static VariationSettlement create(
        core::SettlementId id,
        market_data::Symbol symbol,
        market_data::Timestamp reference_at,
        market_data::Timestamp available_at,
        market_data::Timestamp effective_at,
        core::EventSequence sequence,
        double price,
        std::int64_t contract_multiplier,
        std::string source_id,
        std::string vintage_id);

    [[nodiscard]] core::SettlementId id() const noexcept { return id_; }
    [[nodiscard]] const market_data::Symbol& symbol() const noexcept { return symbol_; }
    [[nodiscard]] market_data::Timestamp reference_at() const noexcept { return reference_at_; }
    [[nodiscard]] market_data::Timestamp available_at() const noexcept { return available_at_; }
    [[nodiscard]] market_data::Timestamp effective_at() const noexcept { return effective_at_; }
    [[nodiscard]] core::EventSequence sequence() const noexcept { return sequence_; }
    [[nodiscard]] double price() const noexcept { return price_; }
    [[nodiscard]] std::int64_t contract_multiplier() const noexcept { return contract_multiplier_; }
    [[nodiscard]] const std::string& source_id() const noexcept { return source_id_; }
    [[nodiscard]] const std::string& vintage_id() const noexcept { return vintage_id_; }

    friend bool operator==(const VariationSettlement&, const VariationSettlement&) = default;

private:
    VariationSettlement(
        core::SettlementId id, market_data::Symbol symbol,
        market_data::Timestamp reference_at, market_data::Timestamp available_at,
        market_data::Timestamp effective_at, core::EventSequence sequence,
        double price, std::int64_t contract_multiplier,
        std::string source_id, std::string vintage_id)
        : id_(id), symbol_(std::move(symbol)), reference_at_(reference_at),
          available_at_(available_at), effective_at_(effective_at), sequence_(sequence),
          price_(price), contract_multiplier_(contract_multiplier),
          source_id_(std::move(source_id)), vintage_id_(std::move(vintage_id)) {}

    core::SettlementId id_;
    market_data::Symbol symbol_;
    market_data::Timestamp reference_at_;
    market_data::Timestamp available_at_;
    market_data::Timestamp effective_at_;
    core::EventSequence sequence_;
    double price_;
    std::int64_t contract_multiplier_;
    std::string source_id_;
    std::string vintage_id_;
};

}  // namespace qte::portfolio
