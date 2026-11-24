#pragma once

#include <optional>
#include <span>
#include <vector>

namespace qte::research {
struct CondorBar {
    int minute;
    double open;
    double close;
};
struct CondorSpec {
    double distance;
    double wing;
    bool put_spread;
    bool stop;
    double iv_multiple;
    double half_spread;
    int entry;
};
struct CondorTrade {
    double credit;
    std::optional<double> settlement;
    double pnl;
    std::vector<double> shorts;
    double entry_spot;
    double max_settlement_loss;
};

[[nodiscard]] std::optional<CondorTrade> condor_day(std::span<const CondorBar> bars, double ratio,
                                                   double vix, const CondorSpec& spec);
}
