#pragma once

#include "qte/core/currency.hpp"
#include "qte/market_data/instrument.hpp"
#include "qte/portfolio/position.hpp"
#include "qte/portfolio/trade.hpp"

#include <cstddef>
#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace qte::portfolio {

class InvalidPortfolioInput final : public std::invalid_argument {
public:
    using std::invalid_argument::invalid_argument;
};

class PortfolioAccountingError final : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

class DuplicatePortfolioFill final : public std::logic_error {
public:
    using std::logic_error::logic_error;
};

class OutOfSequencePortfolioFill final : public std::logic_error {
public:
    using std::logic_error::logic_error;
};

// The first single-currency portfolio ledger. Its instrument universe is fixed
// at construction so fill and mark validation never depends on provider data.
class Portfolio final {
public:
    Portfolio(
        double initial_cash,
        core::Currency valuation_currency,
        std::vector<market_data::InstrumentSpec> instruments);

    Portfolio(const Portfolio&) = delete;
    Portfolio& operator=(const Portfolio&) = delete;
    Portfolio(Portfolio&&) noexcept = default;
    Portfolio& operator=(Portfolio&&) noexcept = default;

    [[nodiscard]] double initial_cash() const noexcept { return initial_cash_; }
    [[nodiscard]] double cash() const noexcept { return cash_sum_; }
    [[nodiscard]] const core::Currency& valuation_currency() const noexcept {
        return valuation_currency_;
    }
    [[nodiscard]] std::size_t instrument_count() const noexcept {
        return positions_.size();
    }
    [[nodiscard]] std::size_t fill_count() const noexcept { return fills_.size(); }
    [[nodiscard]] bool contains_fill(core::FillId id) const noexcept;
    [[nodiscard]] const std::vector<orders::Fill>& fills() const noexcept {
        return fills_;
    }
    [[nodiscard]] const std::vector<TradeEpisode>& closed_trades() const noexcept {
        return closed_trades_;
    }
    [[nodiscard]] const TradeEpisode* open_trade(
        const market_data::Symbol& symbol) const noexcept;

    [[nodiscard]] const Position* position(const market_data::Symbol& symbol) const noexcept;
    [[nodiscard]] double realized_gross_pnl() const;
    [[nodiscard]] double commissions_paid() const;
    [[nodiscard]] std::optional<double> market_value() const;
    [[nodiscard]] std::optional<double> unrealized_pnl() const;
    [[nodiscard]] std::optional<double> equity() const;
    [[nodiscard]] std::optional<double> gross_exposure() const;
    [[nodiscard]] std::optional<double> net_exposure() const;
    [[nodiscard]] std::optional<double> net_pnl() const;

    void apply_fill(const orders::Fill& fill);
    void mark(const market_data::Symbol& symbol, ValuationMark mark);

private:
    using InstrumentMap = std::map<std::string, market_data::InstrumentSpec>;
    using PositionMap = std::map<std::string, Position>;

    struct Aggregates final {
        double realized_gross_pnl;
        double commissions_paid;
        std::optional<double> market_value;
        std::optional<double> unrealized_pnl;
        std::optional<double> gross_exposure;
    };

    struct TradeUpdate final {
        std::optional<TradeEpisode> open_trade;
        std::optional<TradeEpisode> closed_trade;
    };

    static void add_compensated(
        double value,
        double& sum,
        double& compensation);
    [[nodiscard]] Aggregates aggregates(
        const std::string* replacement_key = nullptr,
        const Position* replacement = nullptr) const;
    [[nodiscard]] TradeUpdate stage_trade_update(
        const std::string& key,
        const orders::Fill& fill,
        const PositionTransition& transition) const;
    void require_event_order(
        market_data::Timestamp effective_at,
        core::EventSequence sequence) const;
    void require_valid_state(
        double cash,
        const std::string* replacement_key = nullptr,
        const Position* replacement = nullptr) const;
    void record_event(
        market_data::Timestamp effective_at,
        core::EventSequence sequence) noexcept;

    double initial_cash_;
    double cash_sum_;
    double cash_compensation_{0.0};
    core::Currency valuation_currency_;
    InstrumentMap instruments_;
    PositionMap positions_;
    std::map<std::string, std::optional<TradeEpisode>> open_trades_;
    std::vector<orders::Fill> fills_;
    std::vector<TradeEpisode> closed_trades_;
    std::optional<market_data::Timestamp> last_event_at_;
    std::optional<core::EventSequence> last_event_sequence_;
};

}  // namespace qte::portfolio
