#pragma once

// Research sweep kernel: fast, deterministic, single-contract futures screening.
//
// This is a *screening* simulator for ranking many parameter sets. It is not
// the accounting authority: the portfolio ledger and BacktestEngine remain the
// single source of truth for positions and cash. Candidates that survive
// screening are re-run through the full engine before any promotion.
//
// Timing (no look-ahead):
// - A signal is evaluated on a bar's completed values, i.e. at its right edge.
// - Entry fills at the *next* bar's open in the same session, adverse slippage applied.
// - Stops/targets are checked against each held bar's high/low starting with the
//   entry bar. If both are touched inside one bar the stop is assumed first. A bar
//   that opens beyond the stop fills at its open.
// - Positions are flattened at the close of the last allowed bar of the session.
// - Session context (anchor, expected move, prior VIX, regime) is prior-day data.

#include <cstdint>
#include <stdexcept>
#include <vector>

namespace qte::research {

struct BarArrays final {
    std::int64_t interval_ns{0};
    std::vector<std::int64_t> start_ns;
    std::vector<double> open, high, low, close, volume;
    std::vector<std::int32_t> session;  // index into the session context vector, nondecreasing
};

struct SessionContext final {
    double anchor{0.0};         // prior regular-session close of the traded series
    double expected_move{0.0};  // anchor * prior VIX / 100 / sqrt(252)
    double prior_vix{0.0};
    std::int32_t regime{0};     // index into qte.regime_study.REGIMES
};

struct FuturesCosts final {
    double point_value{50.0};         // ES 50, MES 5
    double tick_size{0.25};
    double commission_per_side{2.0};  // per contract, USD
    double slippage_ticks{1.0};       // adverse, per fill
};

enum class Family : std::int32_t { sigma_reversal = 0, opening_range_breakout = 1 };
enum class Sides : std::int32_t { long_only = 0, short_only = 1, both = 2 };

struct StrategySpec final {
    Family family{Family::sigma_reversal};
    Sides sides{Sides::both};
    std::uint32_t allowed_regimes{0xFFFFFFFFu};        // bit r allows regime index r (all entries)
    std::uint32_t allowed_regimes_long{0xFFFFFFFFu};   // additionally required for long entries
    std::uint32_t allowed_regimes_short{0xFFFFFFFFu};  // additionally required for short entries
    double min_prior_vix{0.0};  // volatility gate on the prior-day VIX close; 0 disables
    double max_prior_vix{0.0};

    // Sigma reversal (Bollinger re-entry near a prior-close +/- k expected-move level).
    double bound_sigma{1.0};
    double touch_tolerance_sigma{0.25};
    std::int32_t band_window{20};
    double band_width{2.0};
    std::int32_t touch_lookback{5};
    bool stress_latch{true};  // veto longs after a 2.5-move decline (or 2 moves with prior VIX >= 25)

    // Opening-range breakout.
    std::int32_t range_bars{6};
    double max_range_sigma{0.6};  // range width must be below this many expected moves
    double volume_ratio{0.0};     // 0 disables; else bar volume / mean of same slot in prior sessions
    std::int32_t volume_lookback{20};
    std::int32_t last_entry_bar{48};  // last bar index within the session allowed to signal
    bool stop_at_range_mid{false};

    // Exits (all families). Zero disables.
    std::int32_t hold_bars{0};  // exit at the close of the hold_bars-th bar, counting the entry bar
    double stop_sigma{0.0};
    double target_sigma{0.0};
    std::int32_t max_trades_per_session{1};
    std::int32_t flatten_bars_before_close{0};
};

enum class ExitReason : std::int32_t { time = 0, stop = 1, target = 2, session_end = 3 };

struct Trade final {
    std::int32_t session{0};
    std::int64_t signal_ns{0};  // right edge of the signal bar (decision time)
    std::int64_t entry_ns{0};
    std::int64_t exit_ns{0};
    std::int32_t side{0};
    double entry_price{0.0};
    double exit_price{0.0};
    double pnl{0.0};  // USD, one contract, after commissions
    ExitReason reason{ExitReason::time};
};

struct RunResult final {
    std::vector<Trade> trades;
    std::vector<double> session_pnl;  // one entry per session context, zero when flat all day
    std::int64_t trade_count{0};
    double net_pnl{0.0};
    double win_rate{0.0};
    double profit_factor{0.0};  // gross wins / gross losses; 0 when no losses and no wins
    double max_drawdown{0.0};   // on cumulative session P&L, USD, nonnegative
    double sharpe{0.0};         // mean / stdev of session P&L * sqrt(252); 0 if undefined
};

class InvalidSweepInput final : public std::invalid_argument {
public:
    using std::invalid_argument::invalid_argument;
};

void validate(const BarArrays& bars, const std::vector<SessionContext>& sessions,
              const FuturesCosts& costs);
void validate(const StrategySpec& spec);

[[nodiscard]] RunResult run(const BarArrays& bars, const std::vector<SessionContext>& sessions,
                            const FuturesCosts& costs, const StrategySpec& spec);

// Evaluate many specs over the same data. Results are in spec order and are
// identical to calling run() for each spec, independent of thread count.
[[nodiscard]] std::vector<RunResult> sweep(const BarArrays& bars,
                                           const std::vector<SessionContext>& sessions,
                                           const FuturesCosts& costs,
                                           const std::vector<StrategySpec>& specs,
                                           unsigned threads = 0);

}  // namespace qte::research
