#include "qte/market_data/schedule.hpp"

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace {

using namespace std::chrono_literals;
using qte::core::Currency;
using qte::core::PriceGrid;
using qte::market_data::Bar;
using qte::market_data::BarStream;
using qte::market_data::BoundedBarHistory;
using qte::market_data::CorporateActionCoverage;
using qte::market_data::DatasetInput;
using qte::market_data::DatasetMetadata;
using qte::market_data::DeterministicSchedule;
using qte::market_data::InstrumentSpec;
using qte::market_data::PriceAdjustmentMode;
using qte::market_data::Symbol;
using qte::market_data::Timestamp;
using qte::market_data::ValidatedDataset;

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

[[nodiscard]] InstrumentSpec instrument(const char* const symbol) {
    return InstrumentSpec{
        Symbol{symbol}, Currency::usd(), PriceGrid::from_tick_size(0.01)};
}

[[nodiscard]] Bar bar(
    const char* const symbol,
    const std::int64_t start,
    const double open = 100.0,
    const double close = 101.0) {
    return Bar{
        Symbol{symbol},
        Timestamp{std::chrono::hours{start}},
        Timestamp{std::chrono::hours{start + 1}},
        open,
        std::max(open, close) + 1.0,
        std::min(open, close) - 1.0,
        close,
        1000.0,
    };
}

[[nodiscard]] ValidatedDataset dataset(const bool reverse_streams = false) {
    DatasetInput input{
        DatasetMetadata::create(
            1h,
            Currency::usd(),
            PriceAdjustmentMode::unadjusted,
            CorporateActionCoverage::action_free,
            "shares",
            "schedule-fixture"),
        {instrument("SPY"), instrument("QQQ")},
        {
            BarStream{Symbol{"SPY"}, {bar("SPY", 0), bar("SPY", 1)}},
            BarStream{Symbol{"QQQ"}, {bar("QQQ", 0), bar("QQQ", 2)}},
        },
    };
    if (reverse_streams) {
        std::swap(input.streams[0], input.streams[1]);
    }
    return qte::market_data::preflight(std::move(input));
}

[[nodiscard]] std::vector<std::string> trace(const DeterministicSchedule& schedule) {
    std::vector<std::string> result;
    for (const auto& slice : schedule.slices()) {
        const auto hour =
            std::chrono::duration_cast<std::chrono::hours>(
                slice.timestamp().time_since_epoch()).count();
        for (const auto& completed : slice.completed_bars()) {
            result.push_back(
                std::to_string(hour) + ":close:" + completed.symbol.value());
        }
        for (const auto& opening : slice.openings()) {
            result.push_back(
                std::to_string(hour) + ":open:" + opening.symbol.value());
        }
    }
    return result;
}

void schedule_merges_by_time_phase_and_symbol_deterministically() {
    const DeterministicSchedule first{dataset(false)};
    const DeterministicSchedule second{dataset(true)};
    CHECK(trace(first) == trace(second));

    const std::vector<std::string> expected{
        "0:open:QQQ",
        "0:open:SPY",
        "1:close:QQQ",
        "1:close:SPY",
        "1:open:SPY",
        "2:close:SPY",
        "2:open:QQQ",
        "3:close:QQQ",
    };
    CHECK(trace(first) == expected);
}

void opening_observations_do_not_expose_future_bar_fields() {
    auto baseline_input = dataset();
    auto changed_input = DatasetInput{
        baseline_input.metadata(),
        baseline_input.instruments(),
        baseline_input.streams(),
    };
    changed_input.streams[1].bars[1].high = 500.0;
    changed_input.streams[1].bars[1].close = 400.0;
    changed_input.streams[1].bars[1].volume = 999999.0;
    const DeterministicSchedule baseline{baseline_input};
    const DeterministicSchedule changed{
        qte::market_data::preflight(std::move(changed_input))};

    CHECK(baseline.slices().size() == changed.slices().size());
    if (baseline.slices().size() == changed.slices().size()) {
        for (std::size_t index = 0; index < baseline.slices().size(); ++index) {
            const auto& left = baseline.slices()[index].openings();
            const auto& right = changed.slices()[index].openings();
            CHECK(left.size() == right.size());
            if (left.size() == right.size()) {
                for (std::size_t item = 0; item < left.size(); ++item) {
                    CHECK(left[item].symbol == right[item].symbol);
                    CHECK(left[item].timestamp == right[item].timestamp);
                    CHECK(left[item].price == right[item].price);
                }
            }
        }
    }
}

void bounded_history_publishes_completed_batches_and_preserves_gaps() {
    const auto source = dataset();
    BoundedBarHistory history{source.instruments(), 1};
    CHECK(history.snapshot().size(Symbol{"SPY"}) == 0);
    CHECK_THROWS_AS(
        BoundedBarHistory(source.instruments(), 0), std::invalid_argument);

    const DeterministicSchedule schedule{source};
    for (const auto& slice : schedule.slices()) {
        history.publish(slice.completed_bars());
        const auto snapshot = history.snapshot();
        for (const auto& completed : slice.completed_bars()) {
            const auto visible = snapshot.bars(completed.symbol);
            CHECK(!visible.empty());
            if (!visible.empty()) {
                CHECK(visible.back().end_time <= slice.timestamp());
            }
        }
    }
    CHECK(history.snapshot().size(Symbol{"SPY"}) == 1);
    CHECK(history.snapshot().size(Symbol{"QQQ"}) == 1);
    const auto qqq = history.snapshot().bars(Symbol{"QQQ"});
    CHECK(qqq.size() == 1);
    if (qqq.size() == 1) {
        CHECK(qqq.front().start_time == Timestamp{2h});
    }
}

}  // namespace

int main() {
    schedule_merges_by_time_phase_and_symbol_deterministically();
    opening_observations_do_not_expose_future_bar_fields();
    bounded_history_publishes_completed_batches_and_preserves_gaps();

    if (failures != 0) {
        std::cerr << failures << " schedule test(s) failed\n";
        return EXIT_FAILURE;
    }
    return EXIT_SUCCESS;
}
