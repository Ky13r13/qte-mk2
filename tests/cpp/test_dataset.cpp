#include "qte/market_data/dataset.hpp"

#include <chrono>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string_view>
#include <utility>
#include <vector>

namespace {

using namespace std::chrono_literals;
using qte::core::Currency;
using qte::core::PriceGrid;
using qte::market_data::Bar;
using qte::market_data::BarObservation;
using qte::market_data::BarStream;
using qte::market_data::CorporateActionCoverage;
using qte::market_data::DatasetInput;
using qte::market_data::DatasetMetadata;
using qte::market_data::DatasetTiming;
using qte::market_data::DatasetValidationCode;
using qte::market_data::InstrumentSpec;
using qte::market_data::InvalidDataset;
using qte::market_data::PriceAdjustmentMode;
using qte::market_data::Symbol;
using qte::market_data::Timestamp;

int failures = 0;

void check(const bool condition, const std::string_view expression, const int line) {
    if (!condition) {
        std::cerr << "line " << line << ": check failed: " << expression << '\n';
        ++failures;
    }
}

template <typename Exception, typename Function>
void check_throws(Function&& function, const std::string_view expression, const int line) {
    try {
        function();
        std::cerr << "line " << line << ": expected exception from: "
                  << expression << '\n';
        ++failures;
    } catch (const Exception&) {
    } catch (...) {
        std::cerr << "line " << line << ": wrong exception from: "
                  << expression << '\n';
        ++failures;
    }
}

#define CHECK(expression) check((expression), #expression, __LINE__)
#define CHECK_THROWS_AS(expression, exception_type) \
    check_throws<exception_type>([&] { static_cast<void>(expression); }, #expression, __LINE__)

[[nodiscard]] DatasetMetadata metadata(
    const PriceAdjustmentMode adjustment = PriceAdjustmentMode::unadjusted,
    const CorporateActionCoverage actions = CorporateActionCoverage::action_free,
    const Currency currency = Currency::usd()) {
    return DatasetMetadata::create(
        1h, currency, adjustment, actions, "shares", "synthetic-fixture");
}

[[nodiscard]] InstrumentSpec instrument(
    const char* const symbol,
    const Currency currency = Currency::usd()) {
    return InstrumentSpec{
        Symbol{symbol}, currency, PriceGrid::from_tick_size(0.01)};
}

[[nodiscard]] Bar bar(
    const char* const symbol,
    const std::int64_t start_hour,
    const double open = 100.0,
    const std::int64_t duration_hours = 1) {
    return Bar{
        Symbol{symbol},
        Timestamp{std::chrono::hours{start_hour}},
        Timestamp{std::chrono::hours{start_hour + duration_hours}},
        open,
        open + 2.0,
        open - 2.0,
        open + 1.0,
        1000.0,
    };
}

[[nodiscard]] DatasetInput valid_input() {
    return DatasetInput{
        metadata(),
        {instrument("SPY"), instrument("QQQ")},
        {
            BarStream{Symbol{"SPY"}, {bar("SPY", 0), bar("SPY", 1)}},
            BarStream{Symbol{"QQQ"}, {bar("QQQ", 0), bar("QQQ", 2)}},
        },
    };
}

[[nodiscard]] DatasetInput explicit_input(
    const std::string& timing_source = "declared-session-schedule") {
    auto input = valid_input();
    input.metadata = DatasetMetadata::create_explicit(
        Currency::usd(), PriceAdjustmentMode::unadjusted,
        CorporateActionCoverage::action_free, "shares", "synthetic-fixture",
        timing_source);
    for (auto& stream : input.streams) {
        for (const auto& item : stream.bars) {
            stream.availability_times.push_back(item.end_time + 1h);
        }
    }
    return input;
}

[[nodiscard]] bool contains(
    const InvalidDataset& error,
    const DatasetValidationCode code) {
    for (const auto& item : error.errors()) {
        if (item.code == code) {
            return true;
        }
    }
    return false;
}

template <typename Mutator>
void check_invalid(Mutator&& mutate, const DatasetValidationCode expected, const int line) {
    auto input = valid_input();
    mutate(input);
    try {
        static_cast<void>(qte::market_data::preflight(std::move(input)));
        check(false, "dataset preflight rejection", line);
    } catch (const InvalidDataset& error) {
        check(contains(error, expected), "expected dataset validation code", line);
    } catch (...) {
        check(false, "dataset preflight exception type", line);
    }
}

#define CHECK_INVALID(mutator, code) check_invalid((mutator), (code), __LINE__)

void metadata_boundary_is_explicit() {
    CHECK_THROWS_AS(
        DatasetMetadata::create(
            0ns,
            Currency::usd(),
            PriceAdjustmentMode::unadjusted,
            CorporateActionCoverage::action_free,
            "shares",
            "source"),
        std::invalid_argument);
    CHECK_THROWS_AS(
        DatasetMetadata::create_explicit(
            Currency::usd(), PriceAdjustmentMode::unadjusted,
            CorporateActionCoverage::action_free, "shares", "source", " \t"),
        std::invalid_argument);
    CHECK_THROWS_AS(
        DatasetMetadata::create_explicit(
            Currency::usd(), PriceAdjustmentMode::unadjusted,
            CorporateActionCoverage::action_free, "shares", "source",
            std::string(257, 'x')),
        std::invalid_argument);
    const auto explicit_metadata = DatasetMetadata::create_explicit(
        Currency::usd(), PriceAdjustmentMode::unadjusted,
        CorporateActionCoverage::action_free, "shares", "source", "schedule-v1");
    CHECK(explicit_metadata.timing_policy() == DatasetTiming::explicit_availability_v1);
    CHECK(explicit_metadata.timing_source_id() == "schedule-v1");
    CHECK_THROWS_AS(explicit_metadata.bar_interval(), std::logic_error);
    CHECK(qte::market_data::to_string(DatasetTiming::fixed_interval_v1) ==
          "fixed_interval_v1");
    CHECK_THROWS_AS(
        DatasetMetadata::create(
            1h,
            Currency::usd(),
            PriceAdjustmentMode::unadjusted,
            CorporateActionCoverage::action_free,
            "",
            "source"),
        std::invalid_argument);

    CHECK_INVALID(
        [](DatasetInput& input) {
            input.metadata = metadata(PriceAdjustmentMode::unknown);
        },
        DatasetValidationCode::unsupported_adjustment_mode);
    CHECK_INVALID(
        [](DatasetInput& input) {
            input.metadata = metadata(
                PriceAdjustmentMode::unadjusted,
                CorporateActionCoverage::contains_actions);
        },
        DatasetValidationCode::unsupported_corporate_actions);
}

void explicit_availability_contract_is_strict_and_owns_observations() {
    std::vector<BarObservation> observations{
        BarObservation{bar("SPY", 0), Timestamp{90min}},
        BarObservation{bar("SPY", 1), Timestamp{150min}},
    };
    auto stream = BarStream::from_observations(Symbol{"SPY"}, observations);
    CHECK(stream.bars.size() == 2);
    CHECK(stream.availability_times.size() == 2);
    if (stream.bars.size() == 2 && stream.availability_times.size() == 2) {
        observations.at(0).bar.open = 999.0;
        observations.at(0).available_at = Timestamp{10h};
        CHECK(stream.bars.at(0).open == 100.0);
        CHECK(stream.publication_time(0) == Timestamp{90min});
    }

    auto variable = explicit_input();
    variable.streams.at(0).bars.at(1).end_time = Timestamp{3h};
    variable.streams.at(0).availability_times.at(1) = Timestamp{4h};
    const auto accepted = qte::market_data::preflight(std::move(variable));
    CHECK(accepted.metadata().timing_policy() == DatasetTiming::explicit_availability_v1);

    auto fixed_variable = valid_input();
    fixed_variable.streams.at(0).bars.at(1).end_time = Timestamp{3h};
    try {
        static_cast<void>(qte::market_data::preflight(std::move(fixed_variable)));
        CHECK(false);
    } catch (const InvalidDataset& error) {
        CHECK(contains(error, DatasetValidationCode::interval_mismatch));
    }

    CHECK_INVALID(
        [](DatasetInput& input) {
            input.streams.at(0).availability_times.push_back(Timestamp{4h});
        },
        DatasetValidationCode::availability_count_mismatch);
    check_invalid(
        [](DatasetInput& input) {
            input.metadata = DatasetMetadata::create_explicit(
                Currency::usd(), PriceAdjustmentMode::unadjusted,
                CorporateActionCoverage::action_free, "shares", "source", "timing");
        },
        DatasetValidationCode::availability_count_mismatch,
        __LINE__);

    auto early = explicit_input();
    early.streams.at(0).availability_times.at(0) = Timestamp{30min};
    try {
        static_cast<void>(qte::market_data::preflight(std::move(early)));
        CHECK(false);
    } catch (const InvalidDataset& error) {
        CHECK(contains(error, DatasetValidationCode::publication_before_bar_end));
    }
    auto equal_release = explicit_input();
    equal_release.streams.at(0).availability_times.at(1) =
        equal_release.streams.at(0).availability_times.at(0);
    try {
        static_cast<void>(qte::market_data::preflight(std::move(equal_release)));
        CHECK(false);
    } catch (const InvalidDataset& error) {
        CHECK(contains(error, DatasetValidationCode::publication_out_of_order));
    }
    auto reversed_release = explicit_input();
    reversed_release.streams.at(0).availability_times = {Timestamp{3h}, Timestamp{2h}};
    try {
        static_cast<void>(qte::market_data::preflight(std::move(reversed_release)));
        CHECK(false);
    } catch (const InvalidDataset& error) {
        CHECK(contains(error, DatasetValidationCode::publication_out_of_order));
    }
}

void hashes_preserve_legacy_and_commit_explicit_timing() {
    const auto legacy = qte::market_data::preflight(valid_input());
    CHECK(qte::market_data::canonical_hash(legacy) == "77a0773a2cff7dd7");
    CHECK(qte::market_data::canonical_hash_algorithm(legacy) == "fnv1a64-v1");

    auto first_input = explicit_input();
    auto reordered_input = first_input;
    std::swap(reordered_input.streams.at(0), reordered_input.streams.at(1));
    const auto first = qte::market_data::preflight(std::move(first_input));
    const auto reordered = qte::market_data::preflight(std::move(reordered_input));
    CHECK(qte::market_data::canonical_hash(first) ==
          qte::market_data::canonical_hash(reordered));
    CHECK(qte::market_data::canonical_hash_algorithm(first) == "fnv1a64-timed-v1");

    auto delayed_input = explicit_input();
    delayed_input.streams.at(0).availability_times.at(0) += 1ns;
    const auto delayed = qte::market_data::preflight(std::move(delayed_input));
    CHECK(qte::market_data::canonical_hash(first) !=
          qte::market_data::canonical_hash(delayed));
    const auto changed_source = qte::market_data::preflight(explicit_input("other-schedule"));
    CHECK(qte::market_data::canonical_hash(first) !=
          qte::market_data::canonical_hash(changed_source));
}

void timestamp_extremes_do_not_overflow_validation() {
    auto legacy = valid_input();
    legacy.streams.at(0).bars.at(0).start_time =
        Timestamp{std::chrono::nanoseconds::min()};
    legacy.streams.at(0).bars.at(0).end_time =
        Timestamp{std::chrono::nanoseconds::max()};
    try {
        static_cast<void>(qte::market_data::preflight(std::move(legacy)));
        CHECK(false);
    } catch (const InvalidDataset& error) {
        CHECK(contains(error, DatasetValidationCode::interval_mismatch));
    }

    auto explicit_data = explicit_input();
    auto& extreme = explicit_data.streams.at(0).bars.at(1);
    extreme.start_time = Timestamp{std::chrono::nanoseconds::max() - 2ns};
    extreme.end_time = Timestamp{std::chrono::nanoseconds::max() - 1ns};
    explicit_data.streams.at(0).availability_times.at(1) =
        Timestamp{std::chrono::nanoseconds::max()};
    explicit_data.streams.at(0).availability_times.at(0) = Timestamp{2h};
    const auto accepted = qte::market_data::preflight(std::move(explicit_data));
    CHECK(accepted.bar_count() == 4);
}

void valid_dataset_is_owned_canonical_and_records_gaps() {
    auto input = valid_input();
    std::swap(input.streams[0], input.streams[1]);
    std::swap(input.instruments[0], input.instruments[1]);
    const auto dataset = qte::market_data::preflight(std::move(input));

    CHECK(dataset.bar_count() == 4);
    CHECK(dataset.streams().size() == 2);
    if (dataset.streams().size() == 2) {
        CHECK(dataset.streams()[0].symbol == Symbol{"QQQ"});
        CHECK(dataset.streams()[1].symbol == Symbol{"SPY"});
        CHECK(dataset.streams()[0].bars.size() == 2);
        if (dataset.streams()[0].bars.size() == 2) {
            CHECK(dataset.streams()[0].bars[0].start_time == Timestamp{0h});
            CHECK(dataset.streams()[0].bars[1].start_time == Timestamp{2h});
        }
    }
    CHECK(dataset.gaps().size() == 1);
    if (dataset.gaps().size() == 1) {
        CHECK(dataset.gaps().front().symbol == Symbol{"QQQ"});
        CHECK(dataset.gaps().front().previous_end == Timestamp{1h});
        CHECK(dataset.gaps().front().next_start == Timestamp{2h});
    }
}

void universe_and_stream_contract_is_strict() {
    CHECK_INVALID(
        [](DatasetInput& input) { input.instruments.push_back(instrument("SPY")); },
        DatasetValidationCode::duplicate_instrument);
    CHECK_INVALID(
        [](DatasetInput& input) {
            input.instruments[0] = instrument("BAD SYMBOL");
        },
        DatasetValidationCode::invalid_symbol);
    CHECK_INVALID(
        [](DatasetInput& input) {
            input.instruments[0] = instrument(
                "SPY", Currency::from_code("EUR"));
        },
        DatasetValidationCode::currency_mismatch);
    CHECK_INVALID(
        [](DatasetInput& input) { input.streams.push_back(input.streams.front()); },
        DatasetValidationCode::duplicate_stream);
    CHECK_INVALID(
        [](DatasetInput& input) {
            input.streams.push_back(
                BarStream{Symbol{"IWM"}, {bar("IWM", 0)}});
        },
        DatasetValidationCode::unknown_stream_symbol);
    CHECK_INVALID(
        [](DatasetInput& input) { input.streams.pop_back(); },
        DatasetValidationCode::missing_stream);
    CHECK_INVALID(
        [](DatasetInput& input) { input.streams.front().bars.clear(); },
        DatasetValidationCode::empty_stream);
    CHECK_INVALID(
        [](DatasetInput& input) {
            input.streams.front().bars.front().symbol = Symbol{"QQQ"};
        },
        DatasetValidationCode::bar_symbol_mismatch);
}

void bar_boundaries_are_rejected_without_reordering_or_repair() {
    CHECK_INVALID(
        [](DatasetInput& input) {
            input.streams.front().bars.front().open = 0.0;
            input.streams.front().bars.front().low = 0.0;
        },
        DatasetValidationCode::nonpositive_tradable_price);
    CHECK_INVALID(
        [](DatasetInput& input) {
            input.streams.front().bars.front() = bar("SPY", 0, 100.0, 2);
        },
        DatasetValidationCode::interval_mismatch);
    CHECK_INVALID(
        [](DatasetInput& input) {
            input.streams.front().bars[1] = input.streams.front().bars[0];
        },
        DatasetValidationCode::duplicate_bar);
    check_invalid(
        [](DatasetInput& input) {
            input.streams.front().bars = {
                bar("SPY", 1), bar("SPY", 0)};
        },
        DatasetValidationCode::out_of_order_bar,
        __LINE__);
    check_invalid(
        [](DatasetInput& input) {
            auto overlapping = bar("SPY", 0);
            overlapping.start_time = Timestamp{30min};
            overlapping.end_time = Timestamp{90min};
            input.streams.front().bars = {bar("SPY", 0), overlapping};
        },
        DatasetValidationCode::overlapping_bars,
        __LINE__);
    CHECK_INVALID(
        [](DatasetInput& input) {
            input.streams.front().bars.front().high = 99.0;
        },
        DatasetValidationCode::invalid_bar);
}

}  // namespace

int main() {
    metadata_boundary_is_explicit();
    valid_dataset_is_owned_canonical_and_records_gaps();
    universe_and_stream_contract_is_strict();
    bar_boundaries_are_rejected_without_reordering_or_repair();
    explicit_availability_contract_is_strict_and_owns_observations();
    hashes_preserve_legacy_and_commit_explicit_timing();
    timestamp_extremes_do_not_overflow_validation();

    if (failures != 0) {
        std::cerr << failures << " dataset test(s) failed\n";
        return EXIT_FAILURE;
    }
    return EXIT_SUCCESS;
}
