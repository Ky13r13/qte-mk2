# Gamma Lab

Open **Gamma** in the local GUI (`/gamma`). Inputs recalculate the surface after
a short debounce. This is a **manual-input model calculator, not live market
data**. The default numbers are synthetic; opening the page never connects to
a broker, loads an options chain or submits orders.

The surface plots per-option gamma against futures price and remaining calendar
days, holding strike and annualized implied volatility fixed. The selected-expiry
slice shows the same calculation at the entered time to expiry. Inspect the
surface for point values and expand **Calculation details** to compare analytic
gamma with numerical price curvature. Signed position gamma includes the
entered contract multiplier and quantity; it is not dealer gamma.

The default **3D surface** uses a local triangulated mesh: horizontal axes are
futures price and log calendar days, and height is linear per-option gamma.
Drag to rotate, or focus the chart and use arrow keys; **Home** or **Reset view**
restores the initial camera. **Heatmap** shows the same samples in 2D. Both views
retain the keyboard-accessible **Inspect surface values** selectors. Camera
changes do not recalculate or change the model. Height auto-scales to the
displayed maximum, shown explicitly in the legend; a zero-gamma grid is flat.
The mesh interpolates the bounded samples, not an observed market surface.
Rendering uses orthographic projection and far-to-near triangle drawing, with
no chart library, external assets or new dependencies.

## Model and units

Black-76 European, premium-paid options on a positive futures price, with a flat
continuously compounded rate and constant lognormal implied volatility:

```text
T = calendar days / 365
sigma = volatility_pct / 100
r = rate_pct / 100
d1 = [ln(F / K) + 0.5 sigma² T] / (sigma sqrt(T))
d2 = d1 - sigma sqrt(T)
D = exp(-r T)
call = D [F N(d1) - K N(d2)]
put  = D [K N(-d2) - F N(-d1)]
gamma = D phi(d1) / [F sigma sqrt(T)]
position gamma = quantity × multiplier × gamma
numerical gamma = [price(F+h) - 2 price(F) + price(F-h)] / h²
```

Price is in index points; delta is points per underlying point; gamma is per
underlying point. With a USD-per-point multiplier, position gamma is USD/point².
`N` and `phi` denote the standard-normal CDF and density. Long calls and long
puts have the same gamma at otherwise equal inputs; negative quantity changes
position gamma's sign. The finite-difference check has truncation/roundoff error,
especially in the tails, and is a diagnostic rather than an independent market
validation. Its step and absolute discrepancy are reported explicitly.

The implementation uses discounted prices/Greeks. OpenGamma documents the
[undiscounted Black formulas and the multiplicative numeraire convention](https://strata.opengamma.io/apidocs/com/opengamma/strata/pricer/impl/option/BlackFormulaRepository.html).
This convention is distinct from futures-style option margining and does not
model early exercise or actual exchange settlement rules.

## Boundaries

- The bounded surface is 41 futures prices × 25 log-spaced expiry values; it
  starts at one hour and extends to at least 30 days. It is a sampled model
  surface, not an observed strike/expiry implied-volatility surface.
- No volatility smile, skew, term structure, cashflows, transaction costs,
  hedge PnL, probabilities or dealer-positioning estimates are inferred.
- VX measures a different underlying's volatility context. It is not inserted
  automatically as Nasdaq option IV. See [VX scenarios](strategies/vix-expected-moves.md).
- Inputs/results are transient. Editing invalidates the previous result;
  stale responses cannot replace newer calculations. There is no polling feed
  or synthetic stream dressed up as live quotes.
- To support market-live calculations next, QTE needs an entitled options-chain
  source with contract identity, timestamp/availability, quotes or qualified IV,
  expiry/exercise convention and provenance. Dealer exposure additionally needs
  defensible signed inventory assumptions; open interest alone is insufficient.

The pure Python calculator is separate from C++ execution/accounting. The
authenticated, same-origin `POST /api/v1/gamma/surface` accepts only the fixed
bounded schema, writes no artifacts, and is also available on the limited
Windows GUI path. Native Windows/browser acceptance remains separate from
Linux verification.
