#include "qte/research/sweep.hpp"

#include <cmath>
#include <cstdlib>
#include <iostream>
#include <string_view>
#include <vector>

namespace {

using namespace qte::research;

int failures = 0;

bool check(bool condition, std::string_view expression, int line) {
    if (!condition) {
        std::cerr << "line " << line << ": check failed: " << expression << '\n';
        ++failures;
    }
    return condition;
}

#define CHECK(expr) check((expr), #expr, __LINE__)

bool near(double a, double b) { return std::fabs(a - b) < 1e-9; }

constexpr std::int64_t kMin = 60'000'000'000LL;

struct Builder {
    BarArrays bars;
    std::vector<SessionContext> sessions;
    Builder() { bars.interval_ns = kMin; }
    std::int32_t session(double anchor, double move, double vix = 20.0, std::int32_t regime = 8) {
        sessions.push_back({anchor, move, vix, regime});
        return static_cast<std::int32_t>(sessions.size() - 1);
    }
    void bar(std::int32_t s, std::int64_t minute, double o, double h, double l, double c, double v = 100.0) {
        bars.start_ns.push_back((static_cast<std::int64_t>(s) * 1000 + minute) * kMin);
        bars.open.push_back(o);
        bars.high.push_back(h);
        bars.low.push_back(l);
        bars.close.push_back(c);
        bars.volume.push_back(v);
        bars.session.push_back(s);
    }
    void flat(std::int32_t s, std::int64_t from, std::int64_t to, double p, double v = 100.0) {
        for (auto m = from; m < to; ++m) bar(s, m, p, p + 0.5, p - 0.5, p, v);
    }
};

FuturesCosts es() { return FuturesCosts{50.0, 0.25, 2.0, 1.0}; }

StrategySpec orb() {
    StrategySpec s;
    s.family = Family::opening_range_breakout;
    s.range_bars = 3;
    s.max_range_sigma = 0.0;
    s.last_entry_bar = 100;
    return s;
}

void orb_entry_next_open_costs_and_session_flatten() {
    Builder b;
    auto s = b.session(100, 10);
    b.bar(s, 0, 100, 101, 99, 100);
    b.bar(s, 1, 100, 101, 99, 100);
    b.bar(s, 2, 100, 101, 99, 100);
    b.bar(s, 3, 100, 102, 100, 101.5);  // close above range high 101: signal at its right edge
    b.bar(s, 4, 102, 103, 101.5, 102.5);
    b.bar(s, 5, 103, 104, 102.5, 104);
    const auto r = run(b.bars, b.sessions, es(), orb());
    CHECK(r.trade_count == 1);
    const auto& t = r.trades.at(0);
    CHECK(t.side == 1);
    CHECK(t.signal_ns == 4 * kMin);
    CHECK(t.entry_ns == 4 * kMin);
    CHECK(near(t.entry_price, 102.25));  // next open + one tick adverse
    CHECK(t.reason == ExitReason::session_end);
    CHECK(near(t.exit_price, 103.75));   // last close - one tick adverse
    CHECK(near(t.pnl, (103.75 - 102.25) * 50 - 4.0));
    CHECK(near(r.session_pnl.at(0), t.pnl));
}

void stop_is_assumed_before_target_and_gaps_fill_at_open() {
    Builder b;
    auto s = b.session(100, 10);
    b.flat(s, 0, 3, 100);
    b.bar(s, 3, 100, 101, 100, 100.8);   // breakout above 100.5
    b.bar(s, 4, 101, 120, 80, 101);      // touches both stop and target
    b.bar(s, 5, 101, 102, 100, 101);
    auto spec = orb();
    spec.stop_sigma = 0.5;    // 5 points
    spec.target_sigma = 1.0;  // 10 points
    auto r = run(b.bars, b.sessions, es(), spec);
    CHECK(r.trades.at(0).reason == ExitReason::stop);
    CHECK(near(r.trades.at(0).exit_price, 101.25 - 5 - 0.25));

    Builder g;
    auto s2 = g.session(100, 10);
    g.flat(s2, 0, 3, 100);
    g.bar(s2, 3, 100, 101, 100, 100.8);
    g.bar(s2, 4, 101, 102, 100.5, 101);
    g.bar(s2, 5, 90, 91, 89, 90);  // opens below the stop at 96.25
    r = run(g.bars, g.sessions, es(), spec);
    CHECK(r.trades.at(0).reason == ExitReason::stop);
    CHECK(near(r.trades.at(0).exit_price, 90 - 0.25));
}

void hold_bars_and_regime_mask_and_sides() {
    Builder b;
    auto s = b.session(100, 10, 20, 3);
    b.flat(s, 0, 3, 100);
    b.bar(s, 3, 100, 101, 100, 100.8);
    b.flat(s, 4, 10, 101);
    auto spec = orb();
    spec.hold_bars = 2;
    auto r = run(b.bars, b.sessions, es(), spec);
    CHECK(r.trades.at(0).reason == ExitReason::time);
    CHECK(r.trades.at(0).exit_ns == 6 * kMin);  // closes of entry bar (4) and bar 5
    spec.allowed_regimes = ~(1u << 3);
    CHECK(run(b.bars, b.sessions, es(), spec).trade_count == 0);
    spec.allowed_regimes = 0xFFFFFFFFu;
    spec.sides = Sides::short_only;
    CHECK(run(b.bars, b.sessions, es(), spec).trade_count == 0);
}

void side_specific_regimes_and_vix_gate() {
    Builder b;
    auto up = b.session(100, 10, 14.0, 5);    // low-vol regime 5, breakout up
    b.flat(up, 0, 3, 100);
    b.bar(up, 3, 100, 101, 100, 100.8);
    b.flat(up, 4, 6, 101);
    auto down = b.session(100, 10, 30.0, 5);  // same regime, high prior VIX, breakout down
    b.flat(down, 0, 3, 100);
    b.bar(down, 3, 100, 100, 99, 99.2);
    b.flat(down, 4, 6, 99);
    auto spec = orb();
    CHECK(run(b.bars, b.sessions, es(), spec).trade_count == 2);
    spec.allowed_regimes_short = ~(1u << 5);  // shorts barred in regime 5, longs unaffected
    auto r = run(b.bars, b.sessions, es(), spec);
    CHECK(r.trade_count == 1 && r.trades.at(0).side == 1);
    spec = orb();
    spec.min_prior_vix = 20.0;  // only the VIX 30 session qualifies
    r = run(b.bars, b.sessions, es(), spec);
    CHECK(r.trade_count == 1 && r.trades.at(0).session == 1);
    spec.max_prior_vix = 10.0;  // max below min is invalid
    bool threw = false;
    try { (void)run(b.bars, b.sessions, es(), spec); } catch (const InvalidSweepInput&) { threw = true; }
    CHECK(threw);
}

void no_entry_on_last_bar_or_after_flatten_cutoff() {
    Builder b;
    auto s = b.session(100, 10);
    b.flat(s, 0, 3, 100);
    b.bar(s, 3, 100, 101, 100, 100.8);  // last bar: no next bar to enter on
    CHECK(run(b.bars, b.sessions, es(), orb()).trade_count == 0);
    b.bar(s, 4, 101, 101, 100.5, 101);
    auto spec = orb();
    spec.flatten_bars_before_close = 1;  // bar 4 is beyond the allowed window
    CHECK(run(b.bars, b.sessions, es(), spec).trade_count == 0);
}

void volume_filter_uses_prior_sessions_only() {
    Builder b;
    auto spec = orb();
    spec.volume_ratio = 1.5;
    spec.volume_lookback = 2;
    for (int d = 0; d < 3; ++d) {
        auto s = b.session(100, 10);
        b.flat(s, 0, 3, 100);
        const double v = d == 2 ? 400.0 : 100.0;  // only today's breakout bar is heavy
        b.bar(s, 3, 100, 101, 100, 100.8, v);
        b.flat(s, 4, 6, 101);
    }
    const auto r = run(b.bars, b.sessions, es(), spec);
    CHECK(r.trade_count == 1);
    CHECK(r.trades.at(0).session == 2);  // sessions 0-1 lack a full baseline / heavy volume
}

void sigma_reversal_long_and_stress_latch() {
    Builder b;
    auto s = b.session(100, 4);
    for (int m = 0; m < 19; ++m) b.bar(s, m, 99, 101, 99, 100);
    b.bar(s, 19, 93, 95, 93, 94);     // drops below the band, low touches 96 (= 100 - 1*4)
    b.bar(s, 20, 96, 98, 96, 97);     // positive candle back inside the band, within 1 move of level
    b.flat(s, 21, 30, 98);
    StrategySpec spec;
    spec.family = Family::sigma_reversal;
    spec.sides = Sides::long_only;
    spec.hold_bars = 6;
    spec.max_trades_per_session = 5;
    spec.touch_tolerance_sigma = 0.25;
    auto r = run(b.bars, b.sessions, es(), spec);
    CHECK(r.trade_count == 1);
    CHECK(r.trades.at(0).signal_ns == 21 * kMin);
    CHECK(r.trades.at(0).side == 1);
    // With a deep enough prior-day move the stress latch vetoes the long.
    b.sessions[0].expected_move = 2.0;  // 94 <= 100 - 2.5*2: latch trips; level 98 touched
    spec.bound_sigma = 1.0;
    spec.touch_tolerance_sigma = 0.6;
    CHECK(run(b.bars, b.sessions, es(), spec).trade_count == 0);
    spec.stress_latch = false;
    CHECK(run(b.bars, b.sessions, es(), spec).trade_count == 1);
}

void sweep_matches_run_and_is_thread_independent() {
    Builder b;
    for (int d = 0; d < 20; ++d) {
        auto s = b.session(100, 10);
        double p = 100;
        for (int m = 0; m < 60; ++m) {
            const double step = ((d * 7 + m * 13) % 11 - 5) * 0.4;
            const double o = p;
            p = std::max(50.0, p + step);
            b.bar(s, m, o, std::max(o, p) + 0.3, std::min(o, p) - 0.3, p, 100 + (m * d) % 50);
        }
    }
    std::vector<StrategySpec> specs;
    for (int rb : {2, 3, 5}) {
        for (double stop : {0.0, 0.3}) {
            auto s = orb();
            s.range_bars = rb;
            s.stop_sigma = stop;
            s.sides = Sides::both;
            specs.push_back(s);
            StrategySpec r;
            r.family = Family::sigma_reversal;
            r.band_window = 5 + rb;
            r.stop_sigma = stop;
            specs.push_back(r);
        }
    }
    const auto one = sweep(b.bars, b.sessions, es(), specs, 1);
    const auto many = sweep(b.bars, b.sessions, es(), specs, 8);
    CHECK(one.size() == specs.size());
    bool any_trades = false;
    for (std::size_t k = 0; k < specs.size(); ++k) {
        const auto direct = run(b.bars, b.sessions, es(), specs[k]);
        CHECK(one[k].trade_count == direct.trade_count && many[k].trade_count == direct.trade_count);
        CHECK(one[k].net_pnl == direct.net_pnl && many[k].net_pnl == direct.net_pnl);
        CHECK(one[k].session_pnl == many[k].session_pnl);
        any_trades = any_trades || direct.trade_count > 0;
    }
    CHECK(any_trades);
}

void validation_rejects_bad_inputs() {
    Builder b;
    auto s = b.session(100, 10);
    b.flat(s, 0, 5, 100);
    auto bad = b.bars;
    bad.high[2] = 50;
    bool threw = false;
    try { (void)run(bad, b.sessions, es(), orb()); } catch (const InvalidSweepInput&) { threw = true; }
    CHECK(threw);
    threw = false;
    auto spec = orb();
    spec.range_bars = 0;
    try { (void)run(b.bars, b.sessions, es(), spec); } catch (const InvalidSweepInput&) { threw = true; }
    CHECK(threw);
    threw = false;
    auto unordered = b.bars;
    unordered.start_ns[3] = unordered.start_ns[2];
    try { (void)run(unordered, b.sessions, es(), orb()); } catch (const InvalidSweepInput&) { threw = true; }
    CHECK(threw);
}

}  // namespace

int main() {
    orb_entry_next_open_costs_and_session_flatten();
    stop_is_assumed_before_target_and_gaps_fill_at_open();
    hold_bars_and_regime_mask_and_sides();
    no_entry_on_last_bar_or_after_flatten_cutoff();
    side_specific_regimes_and_vix_gate();
    volume_filter_uses_prior_sessions_only();
    sigma_reversal_long_and_stress_latch();
    sweep_matches_run_and_is_thread_independent();
    validation_rejects_bad_inputs();
    if (failures) {
        std::cerr << failures << " failure(s)\n";
        return EXIT_FAILURE;
    }
    std::cout << "sweep tests passed\n";
    return EXIT_SUCCESS;
}
