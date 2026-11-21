# lab4 research protocol (registered 2026-11-21, before any lab4 result)

Historical simulation only. No credentials, no orders, no demo/live trading.

## Why a new lab

Earlier work (Engine 3.0, chronological shared-portfolio research) tested
~16 long/cash hypotheses on BTC/ETH only, capped at 20% exposure. Nothing beat
the rebalanced passive comparator robustly. Two structural reasons:

1. **One price path.** BTC and ETH are ~0.8 correlated; two assets give very
   little independent evidence, so every gate is noisy.
2. **Directional long-only timing on BTC rarely beats buy-and-hold on return.**
   Its documented benefit is drawdown reduction, so "beat BH return" was the
   wrong success bar for trend rules.

lab4 therefore (a) widens the universe to a point-in-time 2019 top-cap list
including dead coins, (b) adds a market-neutral funding-carry hypothesis,
(c) judges directional rules on risk-adjusted terms, and (d) separates
development and holdout data in time.

## Data

- Daily Binance USDT spot candles, public archive `data.binance.vision`.
  Universe: `lab4.data.UNIVERSE_2019` (top-20 by market cap on 2019-01-01,
  minus stablecoins and coins without Binance USDT history). An asset is
  tradable from its 120th available day. Interrupted candles are dropped,
  never patched. Assets that stop trading (EOS 2025-05, WAVES 2024-06) are
  sold at their last close.
- BTCUSDT/ETHUSDT USD-M perpetual daily candles and 8-hourly funding rates.

## Periods

- **Development:** 2018-01-01 – 2022-12-31. All design iteration happens here.
- **Holdout:** 2023-01-01 – last complete day. Evaluated once per frozen
  family. Caveat: BTC/ETH 2023–2026 prices were already seen in earlier
  research, so for BTC/ETH-only rules the holdout is *not pristine*. The
  universe and carry rules are new, but the analyst has general knowledge of
  2023–2026 crypto history. A real forward test on new data is still required.

## Execution model

Signals use only data up to the close of day t; trades fill at the open of
day t+1. Cost per side: 10 bps fee + 5 bps slippage (base); stress = 2x.
Carry trades pay costs on both legs. Long-only spot, no leverage, gross
exposure <= 100%. Carry uses 0.5 of capital as spot and 0.5 as perp margin
(1x short), so carry return per unit of capital = 0.5 x (spot - perp + funding).

## Registered family (fixed before results)

| id | idea |
|---|---|
| `xs_mom_top5` | Weekly: top 5 assets by 28-day return, equal weight, only when BTC close > SMA100, else cash |
| `xs_mom_top5_vt` | Same, scaled to 40% annualised portfolio vol target (gross <= 100%) |
| `tsmom_universe_vt` | Every asset with positive 20/60/120d momentum vote majority and close > SMA100; inverse-vol weights, 40% vol target |
| `btceth_trend_vt` | BTC/ETH: close > SMA50 and SMA50 > SMA200, inverse-vol, 40% vol target |
| `funding_carry` | BTC/ETH long spot + short perp while trailing 7-day mean funding > 5% annualised, else cash |
| `carry_trend_blend` | 50% `funding_carry` + 50% `btceth_trend_vt` |

Benchmarks: BTC buy-and-hold, BTC/ETH 50/50 monthly rebalanced, equal-weight
universe monthly rebalanced.

Parameter neighbourhoods (lookbacks x0.5/x2, top 3/7, thresholds 0%/10%) are
reported on development data to show stability; the registered value is not
replaced by the best neighbour.

## "Winning strategy" gates (final, frozen before the holdout run)

Amendment 1 (2026-11-21, before ANY lab4 result): the user set the goal
"significantly outperform the S&P 500". Benchmark = SPY dividend-adjusted
closes (Yahoo), cross-checked against FRED SP500 (daily corr 0.998). The
primary gates use base costs (15 bps/side, ~1.5x Binance VIP0 taker+BNB);
2x costs must still be profitable. All must pass in development AND holdout:

1. CAGR >= SPY CAGR + 5 percentage points (same calendar period).
2. Sharpe >= SPY Sharpe (crypto: 365-day, SPY: 252-day annualisation).
3. Max drawdown <= 40%.
4. >= 70% of rolling 182-day windows (30-day step) positive.
5. No calendar year (partial years included) worse than -10%.
6. CAGR > 0 at 2x costs.
7. Holdout only: stationary block-bootstrap (30-day mean blocks, 2000 draws)
   5th percentile of annualised mean return > 0.

Periods: development 2018-04-01 – 2022-12-31 (BTC SMA200 warm-up from
2017-08-17 data); holdout 2023-01-01 – 2026-10-31 (last month with complete
spot, perp and funding archives).

Passing = *historical candidate* only, eligible for a paper/demo forward test.
It is never a guarantee of future profit.

## Development iteration log (holdout untouched throughout)

- Registered family (v1): xs_mom_top5, xs_mom_top5_vt, tsmom_universe_vt,
  btceth_trend_vt, funding_carry, carry_trend_blend, regime_switch (fixed
  economic regime mapping), xs_ridge_rank (weekly pooled ridge ranker),
  intraday vbo_btc, vbo_btceth, hourly_rsi2_btc, and regime_learned (mapping
  regime -> strategy learned on development only, frozen for holdout).
- Dev finding: no strategy reaches 70% positive 182-day windows, mainly
  because long-only rules sit in cash in bear markets. Intraday rules lose to
  costs (hourly RSI2 -22.5%/yr; VBO negative at 2x costs).
- v2 (added after dev results): btceth_trend_ls_vt (1x perp shorts in
  down-trends), allweather_trend_carry, core_blend. Shorts made dev results
  worse (CAGR 29.6% vs 33.5%, MDD 41% vs 33%).
- v3 (added after v2 dev results): trend_carry_overlay (long-only trend,
  idle capital in carry), core_blend_lo (50% xs_mom_top5_vt + 50% overlay).
- Fix before freeze: btceth_trend_ls_vt checked short availability with the
  period-t return series (needs t+1 data); now uses the funding observed at t.
- 18 strategies were tried in total. The best holdout result is therefore
  optimistically biased; all results are reported, and a pass is a candidate
  for a forward test, not proof.

## Known biases

- The universe uses 2019-01-01 market caps, so the first 9 development
  months (2018) contain mild survivorship bias.
- BTC/ETH 2023–2026 prices were seen in earlier research; the analyst knows
  the broad crypto history of the holdout years.
- Idle cash earns 0% (no lending/T-bill yield) — conservative.
- Carry uses 50% capital efficiency; portfolio margin could double carry
  returns but adds venue risk. Funding archives give 8-hourly settlements.
- Daily-close drawdowns understate intraday drawdowns.

## Regime map

Each day gets a BTC trend regime (bull: close > SMA200 and SMA50 > SMA200;
bear: both below; else transition) and a volatility regime (30-day realised
vol above/below its trailing 365-day median). Annualised return per regime is
reported for every strategy, so it is visible *where* each idea works.
