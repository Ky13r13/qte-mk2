#include "qte/market_data/dataset.hpp"

#include <algorithm>
#include <bit>
#include <cctype>
#include <cstdint>
#include <iomanip>
#include <limits>
#include <map>
#include <sstream>
#include <utility>

namespace qte::market_data {
namespace {

[[nodiscard]] bool valid_core_symbol(const std::string& value) {
    return std::none_of(value.begin(), value.end(), [](const unsigned char character) {
        return std::isspace(character) != 0 || std::iscntrl(character) != 0;
    });
}

[[nodiscard]] std::string validation_message(
    const std::vector<DatasetValidationError>& errors) {
    std::ostringstream message;
    message << "invalid dataset";
    bool first = true;
    for (const auto& error : errors) {
        message << (first ? ": " : "; ") << error.message;
        first = false;
    }
    return message.str();
}

void add_error(
    std::vector<DatasetValidationError>& errors,
    const DatasetValidationCode code,
    std::string message) {
    errors.push_back(DatasetValidationError{code, std::move(message)});
}

class StableHasher final {
public:
    void add_byte(const std::uint8_t value) noexcept {
        value_ ^= value;
        value_ *= 1099511628211ULL;
    }

    void add_uint64(const std::uint64_t value) noexcept {
        for (unsigned int shift = 0; shift < 64; shift += 8) {
            add_byte(static_cast<std::uint8_t>((value >> shift) & 0xffU));
        }
    }

    void add_string(const std::string& value) noexcept {
        add_uint64(value.size());
        for (const unsigned char character : value) {
            add_byte(character);
        }
    }

    void add_double(const double value) noexcept {
        add_uint64(std::bit_cast<std::uint64_t>(value));
    }

    [[nodiscard]] std::string hex() const {
        std::ostringstream output;
        output << std::hex << std::setfill('0') << std::setw(16) << value_;
        return output.str();
    }

private:
    std::uint64_t value_{14695981039346656037ULL};
};

}  // namespace

DatasetMetadata DatasetMetadata::create(
    const std::chrono::nanoseconds bar_interval,
    core::Currency valuation_currency,
    const PriceAdjustmentMode adjustment_mode,
    const CorporateActionCoverage corporate_actions,
    std::string volume_unit,
    std::string source_id) {
    if (bar_interval <= std::chrono::nanoseconds::zero()) {
        throw std::invalid_argument("dataset bar interval must be positive");
    }
    if (volume_unit.empty()) {
        throw std::invalid_argument("dataset volume unit must not be empty");
    }
    if (source_id.empty()) {
        throw std::invalid_argument("dataset source ID must not be empty");
    }
    return DatasetMetadata{
        bar_interval,
        std::move(valuation_currency),
        adjustment_mode,
        corporate_actions,
        std::move(volume_unit),
        std::move(source_id),
        DatasetTiming::fixed_interval_v1,
        {},
    };
}

DatasetMetadata DatasetMetadata::create_explicit(
    core::Currency valuation_currency,
    const PriceAdjustmentMode adjustment_mode,
    const CorporateActionCoverage corporate_actions,
    std::string volume_unit,
    std::string source_id,
    std::string timing_source_id) {
    if (volume_unit.empty()) {
        throw std::invalid_argument("dataset volume unit must not be empty");
    }
    if (source_id.empty()) {
        throw std::invalid_argument("dataset source ID must not be empty");
    }
    const bool printable = std::all_of(
        timing_source_id.begin(), timing_source_id.end(), [](const unsigned char value) {
            return std::isprint(value) != 0;
        });
    const bool only_whitespace = std::all_of(
        timing_source_id.begin(), timing_source_id.end(), [](const unsigned char value) {
            return std::isspace(value) != 0;
        });
    if (timing_source_id.empty() || timing_source_id.size() > 256 || !printable ||
        only_whitespace) {
        throw std::invalid_argument(
            "dataset timing source ID must be printable, nonblank, and at most 256 bytes");
    }
    return DatasetMetadata{
        std::chrono::nanoseconds::zero(),
        std::move(valuation_currency),
        adjustment_mode,
        corporate_actions,
        std::move(volume_unit),
        std::move(source_id),
        DatasetTiming::explicit_availability_v1,
        std::move(timing_source_id),
    };
}

std::chrono::nanoseconds DatasetMetadata::bar_interval() const {
    if (timing_policy_ != DatasetTiming::fixed_interval_v1) {
        throw std::logic_error("explicit-availability datasets have no fixed bar interval");
    }
    return bar_interval_;
}

DatasetMetadata::DatasetMetadata(
    const std::chrono::nanoseconds bar_interval,
    core::Currency valuation_currency,
    const PriceAdjustmentMode adjustment_mode,
    const CorporateActionCoverage corporate_actions,
    std::string volume_unit,
    std::string source_id,
    const DatasetTiming timing_policy,
    std::string timing_source_id)
    : bar_interval_(bar_interval),
      valuation_currency_(std::move(valuation_currency)),
      adjustment_mode_(adjustment_mode),
      corporate_actions_(corporate_actions),
      volume_unit_(std::move(volume_unit)),
      source_id_(std::move(source_id)),
      timing_policy_(timing_policy),
      timing_source_id_(std::move(timing_source_id)) {}

BarStream BarStream::from_observations(
    Symbol symbol,
    std::vector<BarObservation> observations) {
    BarStream result{std::move(symbol), {}, {}};
    result.bars.reserve(observations.size());
    result.availability_times.reserve(observations.size());
    for (auto& observation : observations) {
        result.bars.push_back(std::move(observation.bar));
        result.availability_times.push_back(observation.available_at);
    }
    return result;
}

Timestamp BarStream::publication_time(const std::size_t index) const {
    return availability_times.empty() ? bars.at(index).end_time
                                      : availability_times.at(index);
}

InvalidDataset::InvalidDataset(std::vector<DatasetValidationError> errors)
    : std::invalid_argument(validation_message(errors)), errors_(std::move(errors)) {}

ValidatedDataset::ValidatedDataset(
    DatasetMetadata metadata,
    std::vector<InstrumentSpec> instruments,
    std::vector<BarStream> streams,
    std::vector<DataGap> gaps,
    const std::size_t bar_count)
    : metadata_(std::move(metadata)),
      instruments_(std::move(instruments)),
      streams_(std::move(streams)),
      gaps_(std::move(gaps)),
      bar_count_(bar_count) {}

ValidatedDataset preflight(DatasetInput input) {
    std::vector<DatasetValidationError> errors;
    if (input.metadata.adjustment_mode() != PriceAdjustmentMode::unadjusted) {
        add_error(
            errors,
            DatasetValidationCode::unsupported_adjustment_mode,
            "initial profile requires explicitly unadjusted prices");
    }
    if (input.metadata.corporate_actions() !=
        CorporateActionCoverage::action_free) {
        add_error(
            errors,
            DatasetValidationCode::unsupported_corporate_actions,
            "initial profile requires an explicitly action-free interval");
    }

    std::map<std::string, std::size_t> instrument_indices;
    for (std::size_t index = 0; index < input.instruments.size(); ++index) {
        const auto& instrument = input.instruments[index];
        const auto& symbol = instrument.symbol().value();
        if (!valid_core_symbol(symbol)) {
            add_error(
                errors,
                DatasetValidationCode::invalid_symbol,
                "instrument symbol contains whitespace or control characters: " + symbol);
        }
        if (!instrument_indices.emplace(symbol, index).second) {
            add_error(
                errors,
                DatasetValidationCode::duplicate_instrument,
                "duplicate instrument: " + symbol);
        }
        if (instrument.quote_currency() != input.metadata.valuation_currency()) {
            add_error(
                errors,
                DatasetValidationCode::currency_mismatch,
                "instrument currency differs from dataset valuation currency: " + symbol);
        }
    }

    std::map<std::string, std::size_t> stream_indices;
    std::vector<DataGap> gaps;
    std::size_t bar_count = 0;
    for (std::size_t stream_index = 0;
         stream_index < input.streams.size();
         ++stream_index) {
        const auto& stream = input.streams[stream_index];
        const auto& symbol = stream.symbol.value();
        if (!stream_indices.emplace(symbol, stream_index).second) {
            add_error(
                errors,
                DatasetValidationCode::duplicate_stream,
                "duplicate stream: " + symbol);
        }
        if (!instrument_indices.contains(symbol)) {
            add_error(
                errors,
                DatasetValidationCode::unknown_stream_symbol,
                "stream symbol is outside the instrument universe: " + symbol);
        }
        if (stream.bars.empty()) {
            add_error(
                errors,
                DatasetValidationCode::empty_stream,
                "stream contains no bars: " + symbol);
        }
        const bool explicit_timing = input.metadata.timing_policy() ==
                                     DatasetTiming::explicit_availability_v1;
        if ((!explicit_timing && !stream.availability_times.empty()) ||
            (explicit_timing && stream.availability_times.size() != stream.bars.size())) {
            add_error(
                errors,
                DatasetValidationCode::availability_count_mismatch,
                "availability cardinality differs from timing policy: " + symbol);
        }

        const Bar* previous = nullptr;
        Timestamp previous_publication{};
        bool has_previous_publication = false;
        for (std::size_t bar_index = 0; bar_index < stream.bars.size(); ++bar_index) {
            const auto& bar = stream.bars[bar_index];
            ++bar_count;
            if (bar.symbol != stream.symbol) {
                add_error(
                    errors,
                    DatasetValidationCode::bar_symbol_mismatch,
                    "bar symbol differs from its stream: " + symbol);
            }
            if (!validate(bar).empty()) {
                add_error(
                    errors,
                    DatasetValidationCode::invalid_bar,
                    "bar fails canonical validation: " + symbol);
            }
            if (bar.open <= 0.0 || bar.high <= 0.0 || bar.low <= 0.0 ||
                bar.close <= 0.0) {
                add_error(
                    errors,
                    DatasetValidationCode::nonpositive_tradable_price,
                    "tradable OHLC prices must be strictly positive: " + symbol);
            }
            if (!explicit_timing) {
                const auto start = bar.start_time.time_since_epoch().count();
                const auto end = bar.end_time.time_since_epoch().count();
                const auto interval = input.metadata.bar_interval().count();
                if (start > std::numeric_limits<std::int64_t>::max() - interval ||
                    start + interval != end) {
                    add_error(
                        errors,
                        DatasetValidationCode::interval_mismatch,
                        "bar duration differs from dataset interval: " + symbol);
                }
            }
            if (explicit_timing && bar_index < stream.availability_times.size()) {
                const auto publication = stream.availability_times[bar_index];
                if (publication < bar.end_time) {
                    add_error(
                        errors,
                        DatasetValidationCode::publication_before_bar_end,
                        "bar publication precedes its end: " + symbol);
                }
                if (has_previous_publication && publication <= previous_publication) {
                    add_error(
                        errors,
                        DatasetValidationCode::publication_out_of_order,
                        "bar publications are not strictly increasing: " + symbol);
                }
                previous_publication = publication;
                has_previous_publication = true;
            }

            if (previous != nullptr) {
                if (bar.start_time == previous->start_time &&
                    bar.end_time == previous->end_time) {
                    add_error(
                        errors,
                        DatasetValidationCode::duplicate_bar,
                        "duplicate bar interval: " + symbol);
                } else if (bar.start_time <= previous->start_time) {
                    add_error(
                        errors,
                        DatasetValidationCode::out_of_order_bar,
                        "bar stream is not strictly ordered: " + symbol);
                } else if (bar.start_time < previous->end_time) {
                    add_error(
                        errors,
                        DatasetValidationCode::overlapping_bars,
                        "bar intervals overlap: " + symbol);
                } else if (bar.start_time > previous->end_time) {
                    gaps.push_back(DataGap{
                        stream.symbol, previous->end_time, bar.start_time});
                }
            }
            previous = &bar;
        }
    }

    for (const auto& [symbol, unused] : instrument_indices) {
        static_cast<void>(unused);
        if (!stream_indices.contains(symbol)) {
            add_error(
                errors,
                DatasetValidationCode::missing_stream,
                "instrument has no bar stream: " + symbol);
        }
    }
    if (input.instruments.empty()) {
        add_error(
            errors,
            DatasetValidationCode::missing_stream,
            "dataset instrument universe must not be empty");
    }

    if (!errors.empty()) {
        throw InvalidDataset(std::move(errors));
    }

    const auto by_symbol = [](const auto& left, const auto& right) {
        return left.symbol().value() < right.symbol().value();
    };
    std::sort(input.instruments.begin(), input.instruments.end(), by_symbol);
    std::sort(
        input.streams.begin(), input.streams.end(),
        [](const BarStream& left, const BarStream& right) {
            return left.symbol.value() < right.symbol.value();
        });
    std::sort(gaps.begin(), gaps.end(), [](const DataGap& left, const DataGap& right) {
        if (left.previous_end != right.previous_end) {
            return left.previous_end < right.previous_end;
        }
        return left.symbol.value() < right.symbol.value();
    });
    return ValidatedDataset{
        std::move(input.metadata),
        std::move(input.instruments),
        std::move(input.streams),
        std::move(gaps),
        bar_count,
    };
}

std::string canonical_hash(const ValidatedDataset& dataset) {
    StableHasher hash;
    const bool explicit_timing = dataset.metadata().timing_policy() ==
                                 DatasetTiming::explicit_availability_v1;
    if (explicit_timing) {
        hash.add_string("fnv1a64-timed-v1");
    }
    hash.add_uint64(kBarSchemaVersion);
    if (explicit_timing) {
        hash.add_uint64(static_cast<std::uint64_t>(dataset.metadata().timing_policy()));
        hash.add_string(dataset.metadata().timing_source_id());
    } else {
        hash.add_uint64(static_cast<std::uint64_t>(
            dataset.metadata().bar_interval().count()));
    }
    hash.add_string(dataset.metadata().valuation_currency().code());
    hash.add_uint64(static_cast<std::uint64_t>(dataset.metadata().adjustment_mode()));
    hash.add_uint64(static_cast<std::uint64_t>(dataset.metadata().corporate_actions()));
    hash.add_string(dataset.metadata().volume_unit());
    hash.add_string(dataset.metadata().source_id());
    for (const auto& instrument : dataset.instruments()) {
        hash.add_string(instrument.symbol().value());
        hash.add_string(instrument.quote_currency().code());
        hash.add_double(instrument.price_grid().tick_size());
    }
    for (const auto& stream : dataset.streams()) {
        hash.add_string(stream.symbol.value());
        hash.add_uint64(stream.bars.size());
        for (std::size_t index = 0; index < stream.bars.size(); ++index) {
            const auto& bar = stream.bars[index];
            hash.add_uint64(static_cast<std::uint64_t>(
                bar.start_time.time_since_epoch().count()));
            hash.add_uint64(static_cast<std::uint64_t>(
                bar.end_time.time_since_epoch().count()));
            hash.add_double(bar.open);
            hash.add_double(bar.high);
            hash.add_double(bar.low);
            hash.add_double(bar.close);
            hash.add_double(bar.volume);
            if (explicit_timing) {
                hash.add_uint64(static_cast<std::uint64_t>(
                    stream.availability_times.at(index).time_since_epoch().count()));
            }
        }
    }
    return hash.hex();
}

std::string_view canonical_hash_algorithm(const ValidatedDataset& dataset) noexcept {
    return dataset.metadata().timing_policy() == DatasetTiming::fixed_interval_v1
               ? "fnv1a64-v1"
               : "fnv1a64-timed-v1";
}

std::string_view to_string(const DatasetTiming timing) noexcept {
    switch (timing) {
        case DatasetTiming::fixed_interval_v1: return "fixed_interval_v1";
        case DatasetTiming::explicit_availability_v1: return "explicit_availability_v1";
    }
    return "unknown";
}

std::string_view to_string(const DatasetValidationCode code) noexcept {
    switch (code) {
        case DatasetValidationCode::unsupported_adjustment_mode: return "unsupported_adjustment_mode";
        case DatasetValidationCode::unsupported_corporate_actions: return "unsupported_corporate_actions";
        case DatasetValidationCode::duplicate_instrument: return "duplicate_instrument";
        case DatasetValidationCode::invalid_symbol: return "invalid_symbol";
        case DatasetValidationCode::currency_mismatch: return "currency_mismatch";
        case DatasetValidationCode::duplicate_stream: return "duplicate_stream";
        case DatasetValidationCode::unknown_stream_symbol: return "unknown_stream_symbol";
        case DatasetValidationCode::missing_stream: return "missing_stream";
        case DatasetValidationCode::empty_stream: return "empty_stream";
        case DatasetValidationCode::bar_symbol_mismatch: return "bar_symbol_mismatch";
        case DatasetValidationCode::invalid_bar: return "invalid_bar";
        case DatasetValidationCode::nonpositive_tradable_price: return "nonpositive_tradable_price";
        case DatasetValidationCode::interval_mismatch: return "interval_mismatch";
        case DatasetValidationCode::duplicate_bar: return "duplicate_bar";
        case DatasetValidationCode::out_of_order_bar: return "out_of_order_bar";
        case DatasetValidationCode::overlapping_bars: return "overlapping_bars";
        case DatasetValidationCode::availability_count_mismatch: return "availability_count_mismatch";
        case DatasetValidationCode::publication_before_bar_end: return "publication_before_bar_end";
        case DatasetValidationCode::publication_out_of_order: return "publication_out_of_order";
    }
    return "unknown";
}

}  // namespace qte::market_data
