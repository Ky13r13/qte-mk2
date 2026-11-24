#include "qte/research/sweep.hpp"

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstddef>
#include <deque>
#include <exception>
#include <limits>
#include <mutex>
#include <string>
#include <thread>

namespace qte::research {
namespace {

constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();

bool finite_positive(double v) { return std::isfinite(v) && v > 0.0; }

bool side_allowed(Sides sides, int side) {
    return sides == Sides::both || (side > 0 ? sides == Sides::long_only : sides == Sides::short_only);
}

struct Position {
    int side{0};
    std::size_t entry_idx{0};
    double entry_price{0.0};
    double stop{kNaN};
    double target{kNaN};
    std::int64_t signal_ns{0};
};

class Runner {
public:
    Runner(const BarArrays& bars, const std::vector<SessionContext>& sessions,
           const FuturesCosts& costs, const StrategySpec& spec)
        : bars_(bars), sessions_(sessions), costs_(costs), spec_(spec),
          slip_(costs.slippage_ticks * costs.tick_size) {}

    RunResult run() {
        result_.session_pnl.assign(sessions_.size(), 0.0);
        const std::size_t n = bars_.start_ns.size();
        std::size_t b = 0;
        while (b < n) {
            std::size_t e = b;
            while (e + 1 < n && bars_.session[e + 1] == bars_.session[b]) ++e;
            run_session(b, e);
            b = e + 1;
        }
        finish_metrics();
        return std::move(result_);
    }

private:
    // ---- per-session simulation -------------------------------------------------
    void run_session(std::size_t b, std::size_t e) {
        const auto sid = static_cast<std::size_t>(bars_.session[b]);
        const SessionContext& ctx = sessions_[sid];
        const auto flatten = static_cast<std::size_t>(spec_.flatten_bars_before_close);
        if (e - b < flatten) {
            record_slot_volumes(b, e);
            return;
        }
        const std::size_t last_allowed = e - flatten;
        const bool in_range = ctx.regime >= 0 && ctx.regime < 32;
        auto has = [&](std::uint32_t mask) { return in_range && ((mask >> ctx.regime) & 1u) != 0u; };
        const bool vix_ok = (spec_.min_prior_vix <= 0.0 || ctx.prior_vix >= spec_.min_prior_vix) &&
                            (spec_.max_prior_vix <= 0.0 || ctx.prior_vix <= spec_.max_prior_vix);
        const bool regime_ok = has(spec_.allowed_regimes) && vix_ok;
        const bool long_ok = has(spec_.allowed_regimes_long), short_ok = has(spec_.allowed_regimes_short);
        const double m = ctx.expected_move;

        if (spec_.family == Family::sigma_reversal) compute_bands(b, e);
        bool range_valid = false;
        double range_hi = 0.0, range_lo = 0.0;
        if (spec_.family == Family::opening_range_breakout) {
            const auto rb = static_cast<std::size_t>(spec_.range_bars);
            if (e - b + 1 > rb &&
                bars_.start_ns[b + rb - 1] - bars_.start_ns[b] ==
                    static_cast<std::int64_t>(rb - 1) * bars_.interval_ns) {
                range_hi = *std::max_element(bars_.high.begin() + static_cast<std::ptrdiff_t>(b),
                                             bars_.high.begin() + static_cast<std::ptrdiff_t>(b + rb));
                range_lo = *std::min_element(bars_.low.begin() + static_cast<std::ptrdiff_t>(b),
                                             bars_.low.begin() + static_cast<std::ptrdiff_t>(b + rb));
                range_valid = spec_.max_range_sigma <= 0.0 || range_hi - range_lo < spec_.max_range_sigma * m;
            }
        }

        Position pos;
        int trades_today = 0;
        bool stress = false;
        for (std::size_t i = b; i <= e; ++i) {
            if (pos.side != 0 && i >= pos.entry_idx) manage(pos, i, last_allowed, sid);

            const double c = bars_.close[i];
            stress = stress || c <= ctx.anchor - 2.5 * m || (c <= ctx.anchor - 2.0 * m && ctx.prior_vix >= 25.0);

            if (pos.side != 0 || !regime_ok || trades_today >= spec_.max_trades_per_session ||
                i + 1 > last_allowed) {
                continue;
            }
            int side = 0;
            if (spec_.family == Family::sigma_reversal) {
                side = sigma_signal(b, i, ctx);
                if (side > 0 && spec_.stress_latch && stress) side = 0;
            } else if (range_valid) {
                side = orb_signal(b, i, range_hi, range_lo);
            }
            if (side == 0 || !side_allowed(spec_.sides, side) || !(side > 0 ? long_ok : short_ok)) continue;

            pos.side = side;
            pos.entry_idx = i + 1;
            pos.entry_price = bars_.open[i + 1] + side * slip_;
            pos.signal_ns = bars_.start_ns[i] + bars_.interval_ns;
            pos.stop = spec_.stop_sigma > 0.0 ? pos.entry_price - side * spec_.stop_sigma * m : kNaN;
            if (spec_.family == Family::opening_range_breakout && spec_.stop_at_range_mid) {
                pos.stop = 0.5 * (range_hi + range_lo);
            }
            pos.target = spec_.target_sigma > 0.0 ? pos.entry_price + side * spec_.target_sigma * m : kNaN;
            ++trades_today;
        }
        record_slot_volumes(b, e);
    }

    void manage(Position& pos, std::size_t i, std::size_t last_allowed, std::size_t sid) {
        const int s = pos.side;
        const double o = bars_.open[i], h = bars_.high[i], l = bars_.low[i];
        double price = kNaN;
        ExitReason reason = ExitReason::time;
        const bool has_stop = !std::isnan(pos.stop), has_target = !std::isnan(pos.target);
        if (has_stop && (s > 0 ? o <= pos.stop : o >= pos.stop)) {
            price = o, reason = ExitReason::stop;  // gapped through the stop
        } else if (has_target && (s > 0 ? o >= pos.target : o <= pos.target) && i > pos.entry_idx) {
            price = o, reason = ExitReason::target;
        } else if (has_stop && (s > 0 ? l <= pos.stop : h >= pos.stop)) {
            price = pos.stop, reason = ExitReason::stop;  // stop first if both touched
        } else if (has_target && (s > 0 ? h >= pos.target : l <= pos.target)) {
            price = pos.target, reason = ExitReason::target;
        } else if (spec_.hold_bars > 0 &&
                   i + 1 >= pos.entry_idx + static_cast<std::size_t>(spec_.hold_bars)) {
            price = bars_.close[i], reason = ExitReason::time;
        } else if (i >= last_allowed) {
            price = bars_.close[i], reason = ExitReason::session_end;
        }
        if (std::isnan(price)) return;
        const double exit_price = price - s * slip_;
        Trade t;
        t.session = static_cast<std::int32_t>(sid);
        t.signal_ns = pos.signal_ns;
        t.entry_ns = bars_.start_ns[pos.entry_idx];
        t.exit_ns = bars_.start_ns[i] + bars_.interval_ns;
        t.side = s;
        t.entry_price = pos.entry_price;
        t.exit_price = exit_price;
        t.pnl = s * (exit_price - pos.entry_price) * costs_.point_value - 2.0 * costs_.commission_per_side;
        t.reason = reason;
        result_.session_pnl[sid] += t.pnl;
        result_.trades.push_back(t);
        pos = Position{};
    }

    // ---- sigma reversal ---------------------------------------------------------
    void compute_bands(std::size_t b, std::size_t e) {
        const auto w = static_cast<std::size_t>(spec_.band_window);
        lower_.assign(e - b + 1, kNaN);
        upper_.assign(e - b + 1, kNaN);
        for (std::size_t i = b; i <= e; ++i) {
            if (i - b + 1 < w) continue;
            const std::size_t first = i + 1 - w;
            if (bars_.start_ns[i] - bars_.start_ns[first] != static_cast<std::int64_t>(w - 1) * bars_.interval_ns) {
                continue;
            }
            double sum = 0.0;
            for (std::size_t k = first; k <= i; ++k) sum += bars_.close[k];
            const double mean = sum / static_cast<double>(w);
            double ss = 0.0;
            for (std::size_t k = first; k <= i; ++k) ss += (bars_.close[k] - mean) * (bars_.close[k] - mean);
            const double sd = std::sqrt(ss / static_cast<double>(w));
            lower_[i - b] = mean - spec_.band_width * sd;
            upper_[i - b] = mean + spec_.band_width * sd;
        }
    }

    int sigma_signal(std::size_t b, std::size_t i, const SessionContext& ctx) const {
        if (i == b) return 0;
        const std::size_t j = i - b;
        if (std::isnan(lower_[j]) || std::isnan(lower_[j - 1])) return 0;
        const double m = ctx.expected_move, c = bars_.close[i], o = bars_.open[i], pc = bars_.close[i - 1];
        const auto lookback = static_cast<std::size_t>(spec_.touch_lookback);
        const std::size_t from = i + 1 >= b + lookback ? i + 1 - lookback : b;
        if (spec_.sides != Sides::short_only) {
            const double level = ctx.anchor - spec_.bound_sigma * m;
            double lo = bars_.low[from];
            for (std::size_t k = from; k <= i; ++k) lo = std::min(lo, bars_.low[k]);
            if (pc < lower_[j - 1] && c >= lower_[j] && c > o && lo <= level &&
                std::fabs(c - level) <= spec_.touch_tolerance_sigma * m) {
                return 1;
            }
        }
        if (spec_.sides != Sides::long_only) {
            const double level = ctx.anchor + spec_.bound_sigma * m;
            double hi = bars_.high[from];
            for (std::size_t k = from; k <= i; ++k) hi = std::max(hi, bars_.high[k]);
            if (pc > upper_[j - 1] && c <= upper_[j] && c < o && hi >= level &&
                std::fabs(c - level) <= spec_.touch_tolerance_sigma * m) {
                return -1;
            }
        }
        return 0;
    }

    // ---- opening-range breakout -------------------------------------------------
    int orb_signal(std::size_t b, std::size_t i, double hi, double lo) const {
        const std::size_t idx = i - b;
        if (idx < static_cast<std::size_t>(spec_.range_bars) ||
            idx > static_cast<std::size_t>(spec_.last_entry_bar)) {
            return 0;
        }
        const double c = bars_.close[i];
        const int side = c > hi ? 1 : (c < lo ? -1 : 0);
        if (side == 0 || spec_.volume_ratio <= 0.0) return side;
        if (idx >= slot_history_.size()) return 0;
        const auto& hist = slot_history_[idx];
        if (hist.size() < static_cast<std::size_t>(spec_.volume_lookback)) return 0;
        double sum = 0.0;
        for (double v : hist) sum += v;
        const double baseline = sum / static_cast<double>(hist.size());
        return baseline > 0.0 && bars_.volume[i] / baseline > spec_.volume_ratio ? side : 0;
    }

    void record_slot_volumes(std::size_t b, std::size_t e) {
        if (spec_.family != Family::opening_range_breakout || spec_.volume_ratio <= 0.0) return;
        const auto cap = static_cast<std::size_t>(spec_.volume_lookback);
        for (std::size_t i = b; i <= e; ++i) {
            const std::size_t idx = i - b;
            if (idx >= slot_history_.size()) slot_history_.resize(idx + 1);
            auto& hist = slot_history_[idx];
            hist.push_back(bars_.volume[i]);
            if (hist.size() > cap) hist.pop_front();
        }
    }

    // ---- metrics ----------------------------------------------------------------
    void finish_metrics() {
        double wins = 0.0, losses = 0.0;
        std::int64_t winners = 0;
        for (const auto& t : result_.trades) {
            result_.net_pnl += t.pnl;
            if (t.pnl > 0) wins += t.pnl, ++winners;
            else losses -= t.pnl;
        }
        result_.trade_count = static_cast<std::int64_t>(result_.trades.size());
        result_.win_rate = result_.trade_count ? static_cast<double>(winners) / static_cast<double>(result_.trade_count) : 0.0;
        result_.profit_factor = losses > 0.0 ? wins / losses : 0.0;
        double peak = 0.0, equity = 0.0;
        for (double p : result_.session_pnl) {
            equity += p;
            peak = std::max(peak, equity);
            result_.max_drawdown = std::max(result_.max_drawdown, peak - equity);
        }
        const std::size_t n = result_.session_pnl.size();
        if (n > 1) {
            double mean = 0.0;
            for (double p : result_.session_pnl) mean += p;
            mean /= static_cast<double>(n);
            double ss = 0.0;
            for (double p : result_.session_pnl) ss += (p - mean) * (p - mean);
            const double sd = std::sqrt(ss / static_cast<double>(n - 1));
            result_.sharpe = sd > 0.0 ? mean / sd * std::sqrt(252.0) : 0.0;
        }
    }

    const BarArrays& bars_;
    const std::vector<SessionContext>& sessions_;
    const FuturesCosts& costs_;
    const StrategySpec& spec_;
    const double slip_;
    RunResult result_;
    std::vector<double> lower_, upper_;
    std::vector<std::deque<double>> slot_history_;
};

}  // namespace

void validate(const BarArrays& bars, const std::vector<SessionContext>& sessions, const FuturesCosts& costs) {
    const std::size_t n = bars.start_ns.size();
    if (bars.interval_ns <= 0) throw InvalidSweepInput("interval_ns must be positive");
    if (bars.open.size() != n || bars.high.size() != n || bars.low.size() != n || bars.close.size() != n ||
        bars.volume.size() != n || bars.session.size() != n) {
        throw InvalidSweepInput("bar arrays must have equal lengths");
    }
    for (std::size_t i = 0; i < n; ++i) {
        if (i && bars.start_ns[i] <= bars.start_ns[i - 1]) throw InvalidSweepInput("bar starts must strictly increase");
        if (i && bars.session[i] < bars.session[i - 1]) throw InvalidSweepInput("session indices must be nondecreasing");
        if (bars.session[i] < 0 || static_cast<std::size_t>(bars.session[i]) >= sessions.size()) {
            throw InvalidSweepInput("bar session index outside session context");
        }
        const double o = bars.open[i], h = bars.high[i], l = bars.low[i], c = bars.close[i];
        if (!finite_positive(o) || !finite_positive(h) || !finite_positive(l) || !finite_positive(c) ||
            l > std::min(o, c) || std::max(o, c) > h) {
            throw InvalidSweepInput("invalid OHLC at bar " + std::to_string(i));
        }
        if (!std::isfinite(bars.volume[i]) || bars.volume[i] < 0.0) throw InvalidSweepInput("invalid volume");
    }
    for (const auto& s : sessions) {
        if (!finite_positive(s.anchor) || !finite_positive(s.expected_move) || !std::isfinite(s.prior_vix)) {
            throw InvalidSweepInput("session anchor and expected move must be finite and positive");
        }
    }
    if (!finite_positive(costs.point_value) || !finite_positive(costs.tick_size) ||
        !std::isfinite(costs.commission_per_side) || costs.commission_per_side < 0.0 ||
        !std::isfinite(costs.slippage_ticks) || costs.slippage_ticks < 0.0) {
        throw InvalidSweepInput("invalid futures costs");
    }
}

void validate(const StrategySpec& s) {
    auto fin = [](double v) { return std::isfinite(v) && v >= 0.0; };
    if (static_cast<int>(s.family) < 0 || static_cast<int>(s.family) > 1) throw InvalidSweepInput("unknown family");
    if (static_cast<int>(s.sides) < 0 || static_cast<int>(s.sides) > 2) throw InvalidSweepInput("unknown sides");
    if (!finite_positive(s.bound_sigma) || !fin(s.touch_tolerance_sigma) || s.band_window < 2 ||
        !finite_positive(s.band_width) || s.touch_lookback < 1) {
        throw InvalidSweepInput("invalid sigma reversal parameters");
    }
    if (s.range_bars < 1 || !fin(s.max_range_sigma) || !fin(s.volume_ratio) || s.volume_lookback < 1 ||
        s.last_entry_bar < 0) {
        throw InvalidSweepInput("invalid opening-range parameters");
    }
    if (!fin(s.min_prior_vix) || !fin(s.max_prior_vix) ||
        (s.max_prior_vix > 0.0 && s.max_prior_vix < s.min_prior_vix)) {
        throw InvalidSweepInput("invalid prior VIX gate");
    }
    if (s.hold_bars < 0 || !fin(s.stop_sigma) || !fin(s.target_sigma) || s.max_trades_per_session < 1 ||
        s.flatten_bars_before_close < 0) {
        throw InvalidSweepInput("invalid exit parameters");
    }
}

RunResult run(const BarArrays& bars, const std::vector<SessionContext>& sessions, const FuturesCosts& costs,
              const StrategySpec& spec) {
    validate(bars, sessions, costs);
    validate(spec);
    return Runner(bars, sessions, costs, spec).run();
}

std::vector<RunResult> sweep(const BarArrays& bars, const std::vector<SessionContext>& sessions,
                             const FuturesCosts& costs, const std::vector<StrategySpec>& specs, unsigned threads) {
    validate(bars, sessions, costs);
    for (const auto& s : specs) validate(s);
    std::vector<RunResult> results(specs.size());
    if (specs.empty()) return results;
    if (threads == 0) threads = std::max(1u, std::thread::hardware_concurrency());
    threads = std::min<unsigned>(threads, static_cast<unsigned>(specs.size()));
    std::atomic<std::size_t> next{0};
    std::exception_ptr failure;
    std::mutex failure_mutex;
    auto worker = [&] {
        for (std::size_t k = next++; k < specs.size(); k = next++) {
            try {
                results[k] = Runner(bars, sessions, costs, specs[k]).run();
            } catch (...) {
                std::lock_guard lock(failure_mutex);
                if (!failure) failure = std::current_exception();
            }
        }
    };
    std::vector<std::thread> pool;
    pool.reserve(threads);
    for (unsigned t = 0; t < threads; ++t) pool.emplace_back(worker);
    for (auto& t : pool) t.join();
    if (failure) std::rethrow_exception(failure);
    return results;
}

}  // namespace qte::research
