#pragma once

#include "qte/market_data/dataset.hpp"

#include <cstddef>
#include <map>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace qte::market_data {

struct OpeningObservation final {
    Symbol symbol;
    Timestamp timestamp;
    double price;
};

class TimeSlice final {
public:
    [[nodiscard]] Timestamp timestamp() const noexcept { return timestamp_; }
    [[nodiscard]] const std::vector<Bar>& completed_bars() const noexcept {
        return completed_bars_;
    }
    [[nodiscard]] const std::vector<OpeningObservation>& openings() const noexcept {
        return openings_;
    }

private:
    friend class DeterministicSchedule;
    explicit TimeSlice(Timestamp timestamp) : timestamp_(timestamp) {}

    Timestamp timestamp_;
    std::vector<Bar> completed_bars_;
    std::vector<OpeningObservation> openings_;
};

class DeterministicSchedule final {
public:
    explicit DeterministicSchedule(const ValidatedDataset& dataset);

    [[nodiscard]] const std::vector<TimeSlice>& slices() const noexcept {
        return slices_;
    }

private:
    std::vector<TimeSlice> slices_;
};

class BarHistorySnapshot final {
public:
    BarHistorySnapshot() = default;

    [[nodiscard]] std::vector<Bar> bars(const Symbol& symbol) const;
    [[nodiscard]] std::size_t size(const Symbol& symbol) const noexcept;

private:
    friend class BoundedBarHistory;
    explicit BarHistorySnapshot(std::map<std::string, std::vector<Bar>> histories)
        : histories_(std::move(histories)) {}

    std::map<std::string, std::vector<Bar>> histories_;
};

class BoundedBarHistory final {
public:
    BoundedBarHistory(
        const std::vector<InstrumentSpec>& instruments,
        std::size_t capacity_per_symbol);

    void publish(const std::vector<Bar>& completed_batch);
    [[nodiscard]] BarHistorySnapshot snapshot() const;
    [[nodiscard]] std::size_t capacity_per_symbol() const noexcept {
        return capacity_per_symbol_;
    }

private:
    std::size_t capacity_per_symbol_;
    std::map<std::string, std::vector<Bar>> histories_;
};

}  // namespace qte::market_data
