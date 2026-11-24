#pragma once

namespace qte::research {
struct EuropeanOption {
    double price;
    double gamma;
};

// Undiscounted European Black-76. Time and volatility use the same annual clock.
[[nodiscard]] EuropeanOption european(double forward, double strike, double volatility,
                                      double years, bool call);
}
