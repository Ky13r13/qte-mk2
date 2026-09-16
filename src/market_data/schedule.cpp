#include "qte/market_data/schedule.hpp"

#include <algorithm>
#include <map>
#include <string>
#include <utility>

namespace qte::market_data {

DeterministicSchedule::DeterministicSchedule(const ValidatedDataset& dataset) {
    std::map<Timestamp, TimeSlice> by_time;
    for (const auto& stream : dataset.streams()) {
        for (const auto& bar : stream.bars) {
            auto opening = by_time.emplace(
                bar.start_time, TimeSlice{bar.start_time}).first;
            opening->second.openings_.push_back(
                OpeningObservation{bar.symbol, bar.start_time, bar.open});
            auto closing = by_time.emplace(
                bar.end_time, TimeSlice{bar.end_time}).first;
            closing->second.completed_bars_.push_back(bar);
        }
    }

    slices_.reserve(by_time.size());
    for (auto& [timestamp, slice] : by_time) {
        static_cast<void>(timestamp);
        const auto bar_symbol_order = [](const Bar& left, const Bar& right) {
            return left.symbol.value() < right.symbol.value();
        };
        const auto opening_symbol_order = [](
            const OpeningObservation& left,
            const OpeningObservation& right) {
            return left.symbol.value() < right.symbol.value();
        };
        std::sort(
            slice.completed_bars_.begin(),
            slice.completed_bars_.end(),
            bar_symbol_order);
        std::sort(
            slice.openings_.begin(),
            slice.openings_.end(),
            opening_symbol_order);
        slices_.push_back(std::move(slice));
    }
}

std::vector<Bar> BarHistorySnapshot::bars(const Symbol& symbol) const {
    const auto found = histories_.find(symbol.value());
    return found == histories_.end() ? std::vector<Bar>{} : found->second;
}

std::size_t BarHistorySnapshot::size(const Symbol& symbol) const noexcept {
    const auto found = histories_.find(symbol.value());
    return found == histories_.end() ? 0 : found->second.size();
}

BoundedBarHistory::BoundedBarHistory(
    const std::vector<InstrumentSpec>& instruments,
    const std::size_t capacity_per_symbol)
    : capacity_per_symbol_(capacity_per_symbol) {
    if (capacity_per_symbol == 0) {
        throw std::invalid_argument("bar history capacity must be positive");
    }
    for (const auto& instrument : instruments) {
        if (!histories_.emplace(instrument.symbol().value(), std::vector<Bar>{}).second) {
            throw std::invalid_argument("bar history instruments must be unique");
        }
    }
    if (histories_.empty()) {
        throw std::invalid_argument("bar history requires at least one instrument");
    }
}

void BoundedBarHistory::publish(const std::vector<Bar>& completed_batch) {
    std::map<std::string, std::optional<Timestamp>> prospective_ends;
    for (const auto& [symbol, history] : histories_) {
        prospective_ends.emplace(
            symbol,
            history.empty() ? std::nullopt
                            : std::optional{history.back().end_time});
    }
    for (const auto& bar : completed_batch) {
        require_valid(bar);
        const auto found = histories_.find(bar.symbol.value());
        if (found == histories_.end()) {
            throw std::invalid_argument("completed bar symbol is outside history universe");
        }
        auto& previous_end = prospective_ends.at(bar.symbol.value());
        if (previous_end.has_value() && bar.end_time <= *previous_end) {
            throw std::invalid_argument(
                "completed bar history must advance strictly per symbol");
        }
        previous_end = bar.end_time;
    }

    for (const auto& bar : completed_batch) {
        auto& history = histories_.at(bar.symbol.value());
        history.push_back(bar);
        if (history.size() > capacity_per_symbol_) {
            history.erase(history.begin());
        }
    }
}

BarHistorySnapshot BoundedBarHistory::snapshot() const {
    return BarHistorySnapshot{histories_};
}

}  // namespace qte::market_data
