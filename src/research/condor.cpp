#include "qte/research/condor.hpp"
#include "qte/research/european.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <map>
#include <stdexcept>

namespace qte::research {
namespace {
constexpr double tick = 0.05;
constexpr double fee = 1.25;
constexpr double multiplier = 100.0;
constexpr double trading_year = 390.0 * 252;
constexpr int end = 960;

struct Leg {
    bool short_leg;
    bool call;
    double strike;
};

double rounded(double value, double scale) {
    return std::nearbyint(value * scale) / scale;
}

double modeled_fill(const Leg& leg, double spot, int minutes, double iv, double spread, bool opening) {
    const double mid = european(spot, leg.strike, iv, minutes / trading_year, leg.call).price;
    const bool sell = leg.short_leg == opening;
    return sell ? std::max(0.0, std::floor(rounded((mid - spread) / tick, 1e9)) * tick)
                : std::max(tick, std::ceil(rounded((mid + spread) / tick, 1e9)) * tick);
}
}

std::optional<CondorTrade> condor_day(std::span<const CondorBar> bars, double ratio, double vix,
                                      const CondorSpec& spec) {
    if (!(ratio > 0 && vix > 0 && spec.distance > 0 && spec.wing > 0
          && spec.iv_multiple > 0 && spec.half_spread >= 0 && spec.entry >= 570 && spec.entry <= 955)) {
        throw std::invalid_argument("Invalid condor parameters");
    }
    std::map<int, CondorBar> by_minute;
    for (const auto& bar : bars) {
        by_minute.insert_or_assign(bar.minute, bar);
    }
    const auto entry = by_minute.find(spec.entry);
    const auto final = by_minute.find(955);
    if (entry == by_minute.end() || final == by_minute.end()) {
        return std::nullopt;
    }
    const double iv = vix / 100 * spec.iv_multiple;
    const double spot = entry->second.open * ratio;
    const int minutes = end - spec.entry;
    const double em = spot * iv * std::sqrt(minutes / trading_year);
    const double put = 5 * std::floor((spot - spec.distance * em) / 5);
    const double call = 5 * std::ceil((spot + spec.distance * em) / 5);
    std::vector<Leg> legs{{true, false, put}, {false, false, put - spec.wing}};
    std::vector<double> shorts{put};
    if (!spec.put_spread) {
        legs.push_back({true, true, call});
        legs.push_back({false, true, call + spec.wing});
        shorts.push_back(call);
    }
    double credit = 0;
    for (const auto& leg : legs) {
        const double price = modeled_fill(leg, spot, minutes, iv, spec.half_spread, true);
        credit += leg.short_leg ? price : -price;
    }
    const double fees = fee * legs.size();
    CondorTrade result{rounded(credit, 100), std::nullopt, 0.0, shorts, rounded(spot, 100),
                       rounded((spec.wing - credit) * multiplier + fees, 100)};
    if (spec.stop) {
        for (auto bar = entry; bar != final; ++bar) {
            const double px = bar->second.close * ratio;
            if (px >= put && (spec.put_spread || px <= call)) {
                continue;
            }
            double cost = 0;
            for (const auto& leg : legs) {
                const double price = modeled_fill(leg, px, end - bar->first - 5, iv, spec.half_spread, false);
                cost += leg.short_leg ? price : -price;
            }
            result.pnl = rounded((credit - cost) * multiplier - fees - fees, 100);
            return result;
        }
    }
    const double settle = final->second.close * ratio;
    double payoff = 0;
    for (const auto& leg : legs) {
        const double value = european(settle, leg.strike, iv, 0, leg.call).price;
        payoff += leg.short_leg ? value : -value;
    }
    result.settlement = rounded(settle, 100);
    result.pnl = rounded((credit - payoff) * multiplier - fees, 100);
    return result;
}
}
