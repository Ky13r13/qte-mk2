#pragma once

#include "qte/core/currency.hpp"
#include "qte/market_data/instrument.hpp"

#include <chrono>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace qte::market_data {

enum class PriceAdjustmentMode {
    unknown,
    unadjusted,
    adjusted,
};

enum class CorporateActionCoverage {
    unknown,
    action_free,
    contains_actions,
};

class DatasetMetadata final {
public:
    [[nodiscard]] static DatasetMetadata create(
        std::chrono::nanoseconds bar_interval,
        core::Currency valuation_currency,
        PriceAdjustmentMode adjustment_mode,
        CorporateActionCoverage corporate_actions,
        std::string volume_unit,
        std::string source_id);

    [[nodiscard]] std::chrono::nanoseconds bar_interval() const noexcept {
        return bar_interval_;
    }
    [[nodiscard]] const core::Currency& valuation_currency() const noexcept {
        return valuation_currency_;
    }
    [[nodiscard]] PriceAdjustmentMode adjustment_mode() const noexcept {
        return adjustment_mode_;
    }
    [[nodiscard]] CorporateActionCoverage corporate_actions() const noexcept {
        return corporate_actions_;
    }
    [[nodiscard]] const std::string& volume_unit() const noexcept {
        return volume_unit_;
    }
    [[nodiscard]] const std::string& source_id() const noexcept { return source_id_; }

private:
    DatasetMetadata(
        std::chrono::nanoseconds bar_interval,
        core::Currency valuation_currency,
        PriceAdjustmentMode adjustment_mode,
        CorporateActionCoverage corporate_actions,
        std::string volume_unit,
        std::string source_id);

    std::chrono::nanoseconds bar_interval_;
    core::Currency valuation_currency_;
    PriceAdjustmentMode adjustment_mode_;
    CorporateActionCoverage corporate_actions_;
    std::string volume_unit_;
    std::string source_id_;
};

struct BarStream final {
    Symbol symbol;
    std::vector<Bar> bars;
};

struct DatasetInput final {
    DatasetMetadata metadata;
    std::vector<InstrumentSpec> instruments;
    std::vector<BarStream> streams;
};

struct DataGap final {
    Symbol symbol;
    Timestamp previous_end;
    Timestamp next_start;
};

enum class DatasetValidationCode {
    unsupported_adjustment_mode,
    unsupported_corporate_actions,
    duplicate_instrument,
    invalid_symbol,
    currency_mismatch,
    duplicate_stream,
    unknown_stream_symbol,
    missing_stream,
    empty_stream,
    bar_symbol_mismatch,
    invalid_bar,
    nonpositive_tradable_price,
    interval_mismatch,
    duplicate_bar,
    out_of_order_bar,
    overlapping_bars,
};

struct DatasetValidationError final {
    DatasetValidationCode code;
    std::string message;
};

class InvalidDataset final : public std::invalid_argument {
public:
    explicit InvalidDataset(std::vector<DatasetValidationError> errors);

    [[nodiscard]] const std::vector<DatasetValidationError>& errors() const noexcept {
        return errors_;
    }

private:
    std::vector<DatasetValidationError> errors_;
};

class ValidatedDataset final {
public:
    [[nodiscard]] const DatasetMetadata& metadata() const noexcept { return metadata_; }
    [[nodiscard]] const std::vector<InstrumentSpec>& instruments() const noexcept {
        return instruments_;
    }
    [[nodiscard]] const std::vector<BarStream>& streams() const noexcept {
        return streams_;
    }
    [[nodiscard]] const std::vector<DataGap>& gaps() const noexcept { return gaps_; }
    [[nodiscard]] std::size_t bar_count() const noexcept { return bar_count_; }

private:
    friend ValidatedDataset preflight(DatasetInput input);
    ValidatedDataset(
        DatasetMetadata metadata,
        std::vector<InstrumentSpec> instruments,
        std::vector<BarStream> streams,
        std::vector<DataGap> gaps,
        std::size_t bar_count);

    DatasetMetadata metadata_;
    std::vector<InstrumentSpec> instruments_;
    std::vector<BarStream> streams_;
    std::vector<DataGap> gaps_;
    std::size_t bar_count_;
};

[[nodiscard]] ValidatedDataset preflight(DatasetInput input);
[[nodiscard]] std::string canonical_hash(const ValidatedDataset& dataset);
[[nodiscard]] std::string_view to_string(DatasetValidationCode code) noexcept;

}  // namespace qte::market_data
