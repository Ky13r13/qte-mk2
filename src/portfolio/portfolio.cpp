#include "qte/portfolio/portfolio.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <utility>

namespace qte::portfolio {
namespace {

[[nodiscard]] bool within_accounting_tolerance(
    const double left,
    const double right) noexcept {
    return std::abs(left - right) <=
        1.0e-9 + 1.0e-12 * std::max(std::abs(left), std::abs(right));
}

}  // namespace

Portfolio::Portfolio(
    const double initial_cash,
    core::Currency valuation_currency,
    std::vector<market_data::InstrumentSpec> instruments)
    : initial_cash_(initial_cash),
      cash_sum_(initial_cash),
      valuation_currency_(std::move(valuation_currency)) {
    if (!std::isfinite(initial_cash) || initial_cash < 0.0) {
        throw InvalidPortfolioInput("initial cash must be finite and non-negative");
    }
    if (instruments.empty()) {
        throw InvalidPortfolioInput("portfolio requires at least one instrument");
    }

    for (auto& instrument : instruments) {
        if (instrument.quote_currency() != valuation_currency_) {
            throw InvalidPortfolioInput(
                "instrument quote currency does not match portfolio valuation currency");
        }
        const std::string key = instrument.symbol().value();
        if (instruments_.contains(key)) {
            throw InvalidPortfolioInput("portfolio instrument symbols must be unique");
        }
        positions_.emplace(key, Position{instrument});
        open_trades_.emplace(key, std::nullopt);
        instruments_.emplace(key, std::move(instrument));
    }
    for (const auto& [key, instrument] : instruments_) {
        static_cast<void>(key);
        if (instrument.kind() != market_data::InstrumentKind::futures_option) continue;
        const auto underlying = instruments_.find(instrument.underlying()->value());
        if (underlying == instruments_.end() ||
            underlying->second.kind() != market_data::InstrumentKind::future ||
            underlying->second.quote_currency() != instrument.quote_currency() ||
            underlying->second.contract_multiplier() != instrument.contract_multiplier() ||
            *instrument.expiry() > *underlying->second.expiry() ||
            *instrument.last_trade() > *underlying->second.last_trade()) {
            throw InvalidPortfolioInput(
                "futures option underlying metadata is incompatible with portfolio universe");
        }
    }
}

bool Portfolio::contains_fill(const core::FillId id) const noexcept {
    return id.value() <= fills_.size();
}

bool Portfolio::contains_settlement(const core::SettlementId id) const noexcept {
    return id.value() <= settlements_.size();
}

const Position* Portfolio::position(const market_data::Symbol& symbol) const noexcept {
    const auto found = positions_.find(symbol.value());
    return found == positions_.end() ? nullptr : &found->second;
}

const market_data::InstrumentSpec* Portfolio::instrument(
    const market_data::Symbol& symbol) const noexcept {
    const auto found = instruments_.find(symbol.value());
    return found == instruments_.end() ? nullptr : &found->second;
}

PortfolioSnapshot Portfolio::snapshot() const {
    std::vector<PositionSnapshot> snapshots;
    snapshots.reserve(positions_.size());
    for (const auto& [key, position] : positions_) {
        static_cast<void>(key);
        snapshots.push_back(PositionSnapshot{
            position.symbol(),
            position.quantity(),
            position.valuation_mark().has_value()
                ? std::optional{position.valuation_mark()->price()}
                : std::nullopt,
            position.valuation_mark().has_value()
                ? std::optional{position.valuation_mark()->effective_at()}
                : std::nullopt,
            position.valuation_mark().has_value()
                ? std::optional{position.valuation_mark()->sequence()}
                : std::nullopt,
            position.kind(),
        });
    }
    return PortfolioSnapshot{cash_sum_, equity(), std::move(snapshots)};
}

const TradeEpisode* Portfolio::open_trade(
    const market_data::Symbol& symbol) const noexcept {
    const auto found = open_trades_.find(symbol.value());
    if (found == open_trades_.end() || !found->second.has_value()) {
        return nullptr;
    }
    return &*found->second;
}

double Portfolio::realized_gross_pnl() const {
    return aggregates().realized_gross_pnl;
}

double Portfolio::commissions_paid() const {
    return aggregates().commissions_paid;
}

std::optional<double> Portfolio::market_value() const {
    return aggregates().market_value;
}

std::optional<double> Portfolio::unrealized_pnl() const {
    return aggregates().unrealized_pnl;
}

std::optional<double> Portfolio::equity() const {
    const auto total_market_value = market_value();
    if (!total_market_value.has_value()) {
        return std::nullopt;
    }
    return cash_sum_ + *total_market_value;
}

std::optional<double> Portfolio::gross_exposure() const {
    return aggregates().gross_exposure;
}

std::optional<double> Portfolio::net_exposure() const {
    return aggregates().net_exposure;
}

std::optional<double> Portfolio::net_pnl() const {
    const auto totals = aggregates();
    if (!totals.unrealized_pnl.has_value()) {
        return std::nullopt;
    }
    return totals.realized_gross_pnl + *totals.unrealized_pnl -
           totals.commissions_paid;
}

void Portfolio::apply_fill(const orders::Fill& fill) {
    const auto committed = static_cast<std::uint64_t>(fills_.size());
    if (fill.id().value() <= committed) {
        throw DuplicatePortfolioFill("fill ID has already been applied to portfolio");
    }
    if (committed == std::numeric_limits<std::uint64_t>::max() ||
        fill.id().value() != committed + 1) {
        throw OutOfSequencePortfolioFill(
            "fill ID is not the next portfolio ledger sequence");
    }
    require_event_order(fill.effective_at(), fill.effective_sequence());

    const std::string key = fill.symbol().value();
    const auto instrument = instruments_.find(key);
    const auto current_position = positions_.find(key);
    if (instrument == instruments_.end() || current_position == positions_.end()) {
        throw InvalidPortfolioInput("fill symbol is outside the portfolio universe");
    }
    if (fill.contract_multiplier() != instrument->second.contract_multiplier()) {
        throw InvalidPortfolioInput(
            "fill contract multiplier does not match instrument metadata");
    }
    try {
        static_cast<void>(instrument->second.price_grid().canonicalize(
            fill.executed_price().value()));
    } catch (const std::invalid_argument&) {
        throw InvalidPortfolioInput(
            "fill execution price is not valid on the instrument tick grid");
    } catch (const std::out_of_range&) {
        throw InvalidPortfolioInput(
            "fill execution price is not valid on the instrument tick grid");
    }

    Position prospective_position = current_position->second;
    PositionTransition transition = [&] {
        try {
            return prospective_position.apply_fill(fill);
        } catch (const PositionAccountingError& error) {
            throw PortfolioAccountingError(error.what());
        } catch (const std::overflow_error& error) {
            throw PortfolioAccountingError(error.what());
        }
    }();
    auto trade_update = stage_trade_update(key, fill, transition);

    double prospective_cash = cash_sum_;
    double prospective_cash_compensation = cash_compensation_;
    if (instrument->second.kind() == market_data::InstrumentKind::future) {
        add_compensated(
            transition.realized_cash_pnl(), prospective_cash,
            prospective_cash_compensation);
    } else {
        const double signed_notional = fill.side() == orders::OrderSide::buy
                                           ? -fill.gross_notional()
                                           : fill.gross_notional();
        add_compensated(
            signed_notional, prospective_cash, prospective_cash_compensation);
    }
    add_compensated(
        -fill.commission(), prospective_cash, prospective_cash_compensation);

    require_valid_state(prospective_cash, &key, &prospective_position);

    fills_.push_back(fill);
    try {
        if (trade_update.closed_trade.has_value()) {
            closed_trades_.push_back(*trade_update.closed_trade);
        }
    } catch (...) {
        fills_.pop_back();
        throw;
    }
    current_position->second = std::move(prospective_position);
    open_trades_.at(key) = std::move(trade_update.open_trade);
    cash_sum_ = prospective_cash;
    cash_compensation_ = prospective_cash_compensation;
    record_event(fill.effective_at(), fill.effective_sequence());
}

SettlementApplyResult Portfolio::apply_settlement(const VariationSettlement& settlement) {
    const auto committed = static_cast<std::uint64_t>(settlements_.size());
    // Identity is checked before clocks so a retry remains harmless even after
    // fills, marks and later settlements. An altered retry is never a correction.
    if (settlement.id().value() <= committed) {
        if (settlements_[static_cast<std::size_t>(settlement.id().value() - 1)].event == settlement) {
            return SettlementApplyResult::already_applied;
        }
        throw ConflictingPortfolioSettlement("settlement ID was committed with different content");
    }
    if (committed == std::numeric_limits<std::uint64_t>::max() ||
        settlement.id().value() != committed + 1) {
        throw OutOfSequencePortfolioSettlement("settlement ID is not the next ledger sequence");
    }
    require_event_order(settlement.effective_at(), settlement.sequence());
    const auto& key = settlement.symbol().value();
    const auto instrument = instruments_.find(key);
    const auto current = positions_.find(key);
    if (instrument == instruments_.end() || current == positions_.end() ||
        instrument->second.kind() != market_data::InstrumentKind::future) {
        throw InvalidPortfolioInput("variation settlement requires a known futures instrument");
    }
    if (settlement.contract_multiplier() != instrument->second.contract_multiplier()) {
        throw InvalidPortfolioInput("settlement multiplier does not match instrument metadata");
    }
    if (settlement.reference_at() >= *instrument->second.expiry()) {
        throw InvalidPortfolioInput("expiry/final settlement is not supported");
    }
    // This increment uses current inventory, not a reconstructed cutoff ledger.
    // All earlier processed fills exactly at reference_at are explicitly included.
    const auto latest_fill = std::find_if(fills_.rbegin(), fills_.rend(), [&](const auto& fill) {
        return fill.symbol() == settlement.symbol();
    });
    if (latest_fill != fills_.rend() && latest_fill->effective_at() > settlement.reference_at()) {
        throw InvalidPortfolioInput("late settlement would require historical inventory reconstruction");
    }
    const auto latest_settlement = std::find_if(
        settlements_.rbegin(), settlements_.rend(), [&](const auto& record) {
            return record.event.symbol() == settlement.symbol();
        });
    if (latest_settlement != settlements_.rend() &&
        settlement.reference_at() <= latest_settlement->event.reference_at()) {
        throw ConflictingPortfolioSettlement("settlement reference must increase for each symbol; corrections unsupported");
    }

    Position prospective = current->second;
    const double previous_basis = *prospective.settlement_basis_price();
    double cash_delta;
    try {
        cash_delta = prospective.settle_variation(settlement.price());
    } catch (const PositionAccountingError& error) {
        throw PortfolioAccountingError(error.what());
    }
    double prospective_cash = cash_sum_;
    double prospective_compensation = cash_compensation_;
    add_compensated(cash_delta, prospective_cash, prospective_compensation);
    require_valid_state(prospective_cash, &key, &prospective);

    // Only potentially throwing publication comes before noexcept state commits.
    // Latest fill/reference are derived from journals, avoiding a second cache
    // that could diverge on an allocation or invariant failure.
    settlements_.push_back(SettlementRecord{
        settlement, cash_delta, prospective.quantity(), previous_basis,
        *prospective.settlement_basis_price()});
    current->second = std::move(prospective);
    cash_sum_ = prospective_cash;
    cash_compensation_ = prospective_compensation;
    record_event(settlement.effective_at(), settlement.sequence());
    return SettlementApplyResult::applied;
}

void Portfolio::mark(const market_data::Symbol& symbol, ValuationMark mark) {
    require_event_order(mark.effective_at(), mark.sequence());
    const std::string key = symbol.value();
    const auto current_position = positions_.find(key);
    if (current_position == positions_.end()) {
        throw InvalidPortfolioInput("mark symbol is outside the portfolio universe");
    }

    Position prospective_position = current_position->second;
    try {
        prospective_position.mark(mark);
    } catch (const PositionAccountingError& error) {
        throw PortfolioAccountingError(error.what());
    }
    require_valid_state(cash_sum_, &key, &prospective_position);

    current_position->second = std::move(prospective_position);
    record_event(mark.effective_at(), mark.sequence());
}

void Portfolio::add_compensated(
    const double value,
    double& sum,
    double& compensation) {
    const double adjusted = value - compensation;
    const double next_sum = sum + adjusted;
    const double next_compensation = (next_sum - sum) - adjusted;
    if (!std::isfinite(adjusted) || !std::isfinite(next_sum) ||
        !std::isfinite(next_compensation)) {
        throw PortfolioAccountingError("portfolio cumulative total overflow");
    }
    sum = next_sum;
    compensation = next_compensation;
}

Portfolio::Aggregates Portfolio::aggregates(
    const std::string* const replacement_key,
    const Position* const replacement) const {
    double realized = 0.0;
    double realized_compensation = 0.0;
    double commissions = 0.0;
    double commission_compensation = 0.0;
    double market_value_sum = 0.0;
    double market_value_compensation = 0.0;
    double unrealized_sum = 0.0;
    double unrealized_compensation = 0.0;
    double gross_exposure_sum = 0.0;
    double gross_exposure_compensation = 0.0;
    double net_exposure_sum = 0.0;
    double net_exposure_compensation = 0.0;
    bool valuation_available = true;

    for (const auto& [key, stored_position] : positions_) {
        const Position& current = replacement_key != nullptr && replacement != nullptr &&
                                          key == *replacement_key
                                      ? *replacement
                                      : stored_position;
        add_compensated(
            current.realized_gross_pnl(), realized, realized_compensation);
        add_compensated(
            current.commissions_paid(), commissions, commission_compensation);

        const auto current_market_value = current.market_value();
        const auto current_unrealized = current.unrealized_pnl();
        const auto current_gross_exposure = current.gross_exposure();
        const auto current_net_exposure = current.net_exposure();
        if (!current_market_value.has_value() || !current_unrealized.has_value()) {
            valuation_available = false;
            continue;
        }
        add_compensated(
            *current_market_value,
            market_value_sum,
            market_value_compensation);
        add_compensated(
            *current_unrealized,
            unrealized_sum,
            unrealized_compensation);
        if (!current_gross_exposure.has_value() || !current_net_exposure.has_value()) {
            valuation_available = false;
            continue;
        }
        add_compensated(*current_gross_exposure, gross_exposure_sum,
                        gross_exposure_compensation);
        add_compensated(*current_net_exposure, net_exposure_sum,
                        net_exposure_compensation);
    }

    return Aggregates{
        realized,
        commissions,
        valuation_available ? std::optional{market_value_sum} : std::nullopt,
        valuation_available ? std::optional{unrealized_sum} : std::nullopt,
        valuation_available ? std::optional{gross_exposure_sum} : std::nullopt,
        valuation_available ? std::optional{net_exposure_sum} : std::nullopt,
    };
}

Portfolio::TradeUpdate Portfolio::stage_trade_update(
    const std::string& key,
    const orders::Fill& fill,
    const PositionTransition& transition) const {
    const auto found = open_trades_.find(key);
    if (found == open_trades_.end()) {
        throw PortfolioAccountingError("trade state is missing for portfolio symbol");
    }

    TradeUpdate update{found->second, std::nullopt};
    const auto previous = transition.previous_quantity();
    const auto current = transition.new_quantity();
    const auto opened = transition.opened_quantity().magnitude();
    const auto closed = transition.closed_quantity().magnitude();

    const auto direction_for = [](const core::ShareQuantity quantity) {
        return quantity.value() > 0 ? PositionDirection::long_position
                                    : PositionDirection::short_position;
    };

    if (previous.is_zero()) {
        if (update.open_trade.has_value() || current.is_zero() || opened == 0) {
            throw PortfolioAccountingError("invalid flat-to-open trade transition");
        }
        update.open_trade = TradeEpisode::start(
            fill.symbol(), direction_for(current), fill, opened, fill.commission());
        return update;
    }

    if (!update.open_trade.has_value() ||
        update.open_trade->direction() != direction_for(previous) ||
        update.open_trade->remaining_quantity() != previous.magnitude()) {
        throw PortfolioAccountingError("open trade does not match position state");
    }

    if (transition.is_reversal()) {
        if (closed != previous.magnitude() || opened != current.magnitude()) {
            throw PortfolioAccountingError("invalid reversal trade transition");
        }
        const double closing_fraction =
            static_cast<double>(closed) /
            static_cast<double>(fill.quantity().value());
        const double closing_commission = fill.commission() * closing_fraction;
        const double opening_commission = fill.commission() - closing_commission;

        update.open_trade->add_closed(
            closed, transition.realized_gross_pnl(), closing_commission);
        update.open_trade->close(fill);
        update.closed_trade = *update.open_trade;
        update.open_trade = TradeEpisode::start(
            fill.symbol(), direction_for(current), fill, opened, opening_commission);
        return update;
    }

    if (closed == 0 && opened > 0) {
        update.open_trade->add_opened(opened, fill.commission());
        return update;
    }
    if (closed > 0 && opened == 0) {
        update.open_trade->add_closed(
            closed, transition.realized_gross_pnl(), fill.commission());
        if (current.is_zero()) {
            update.open_trade->close(fill);
            update.closed_trade = *update.open_trade;
            update.open_trade.reset();
        }
        return update;
    }

    throw PortfolioAccountingError("unsupported trade transition shape");
}

void Portfolio::require_event_order(
    const market_data::Timestamp effective_at,
    const core::EventSequence sequence) const {
    if (last_event_at_.has_value() && effective_at < *last_event_at_) {
        throw InvalidPortfolioInput("portfolio event timestamp moved backwards");
    }
    if (last_event_sequence_.has_value() && sequence <= *last_event_sequence_) {
        throw InvalidPortfolioInput("portfolio event sequence must increase");
    }
}

void Portfolio::require_valid_state(
    const double cash,
    const std::string* const replacement_key,
    const Position* const replacement) const {
    if (!std::isfinite(cash)) {
        throw PortfolioAccountingError("portfolio cash is not finite");
    }
    const auto totals = aggregates(replacement_key, replacement);
    if (!totals.market_value.has_value() ||
        !totals.unrealized_pnl.has_value()) {
        return;
    }

    const double equity = cash + *totals.market_value;
    const double net_pnl = totals.realized_gross_pnl +
        *totals.unrealized_pnl - totals.commissions_paid;
    const double equity_change = equity - initial_cash_;
    if (!std::isfinite(equity) || !std::isfinite(net_pnl) ||
        !std::isfinite(equity_change)) {
        throw PortfolioAccountingError("portfolio valuation overflow");
    }
    if (!within_accounting_tolerance(equity_change, net_pnl)) {
        throw PortfolioAccountingError("portfolio equity identity failed");
    }
}

void Portfolio::record_event(
    const market_data::Timestamp effective_at,
    const core::EventSequence sequence) noexcept {
    last_event_at_ = effective_at;
    last_event_sequence_ = sequence;
}

}  // namespace qte::portfolio
