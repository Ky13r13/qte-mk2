#include "qte/research/condor.hpp"

#include <cmath>
#include <iostream>
#include <vector>

int main() {
    using namespace qte::research;
    const CondorSpec settle{1.0, 25, false, false, 1.0, .05, 600};
    std::vector<CondorBar> within{{600, 7000, 7000}, {955, 7000, 7000}};
    auto winner = condor_day(within, 1.0, 20, settle);
    if (!winner || winner->settlement != 7000 || std::abs(winner->pnl - (winner->credit * 100 - 5)) > .001) {
        std::cerr << "Unexpected zero-payoff settlement\n";
        return 1;
    }
    within.back().close = 7500;
    auto loser = condor_day(within, 1.0, 20, settle);
    if (!loser || loser->settlement != 7500 || loser->pnl != -loser->max_settlement_loss) {
        std::cerr << "Wing failed to cap settlement loss\n";
        return 1;
    }
    CondorSpec stop = settle;
    stop.stop = true;
    within.insert(within.begin() + 1, {605, 7000, 7500});
    auto stopped = condor_day(within, 1.0, 20, stop);
    if (!stopped || stopped->settlement || stopped->pnl >= 0
        || stopped->pnl < -stopped->max_settlement_loss - 4 * (.05 + .05) * 100 - 5) {
        std::cerr << "Unexpected stopped loss or exit\n";
        return 1;
    }
}
