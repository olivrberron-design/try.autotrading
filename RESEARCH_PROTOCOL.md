# Chronological shared-portfolio research

This research extends the previous per-symbol Engine 3 study with an explicitly
shared cash account. It is historical mathematical simulation, using only public
market data. It cannot access exchange credentials or place orders.

Run the fixed protocol and review every result:

```sh
python3 -m unittest discover -s tests -v
python3 chronological_research.py --download --output data/chronological
python3 publish_chronological.py --input data/chronological/report.json --html data/chronological/report.html
```

The GitHub **Chronological portfolio research** workflow performs the same work on
manual dispatch. Pull requests run only offline tests. The workflow has read-only
repository permission and does not deploy or publish a new default report.

## What is tested

The first batch contains ten specified hypotheses: two simple baselines,
multi-horizon momentum, inverse-volatility trend, relative-strength rotation,
adaptive efficiency trend, RSI2 dips, a daily breakout ensemble,
volatility-scaled momentum, and an online logistic classifier. Exact rules and
parameters live in `autotrading/research_strategies.py` and are copied into each
run's protocol JSON before results are computed. No grid optimizer is used.

The default second batch retains all ten and adds six hypotheses: monthly pooled
ridge return forecasts for 7/21 days, two BTC-regime gates, an online expert/cash
mixture, and a fixed momentum/breakout blend. Their specifications are in
`autotrading/research_models.py`. Ridge uses only matured forward-open labels,
training-window feature scaling, and fixed regularization. These six were added
after reviewing the first batch; the same historical data remain discovery.
The second run's multiple-comparison family includes all 16. Use `--batch 1` to
reproduce the first ten. The model algorithms may update during a walk-forward
test using newly matured past labels; the algorithm choice and update schedule
are frozen, and no future label is available to the model.

BTC and ETH share 5,000 units of their episode's quote currency. Total target
exposure is at most 20% of contemporaneous equity. Decisions use completed daily
candles and execute at the following daily open. Sells fund buys; all cash,
quantities, fees and terminal liquidation values reconcile. There is no shorting,
leverage, interest, funding income or intraday stop model. A 1-percentage-point
rebalance band reduces turnover. Market moves and the band can make actual
exposure differ from the target cap between rebalances.

Per-side base costs are 10 bps fee plus 6 bps adverse execution impact, with
32/64-bps total per-side stress cases and one extra day of execution delay.
Minimum order value (5 quote units) and quantity steps (BTC 0.00001, ETH 0.0001)
are fixed research assumptions, not historical exchange-filter reconstructions.
Daily-close drawdown does not measure intraday drawdown. A reported fill is one
BUY or SELL; fills are not necessarily independent round-trip trades.

## Data and exclusion boundaries

Two episodes are separate accounts. Their percent returns must not be added or
presented as one continuous investment:

- BTCUSDT/ETHUSDT: downloaded from 2018-02-10 through 2022-06-30, then 240 bars
  of warmup and a first eligible signal. Execution starts 2018-10-09.
- BTCUSDC/ETHUSDC: downloaded from 2023-03-25 through 2025-12-31, with the same
  warmup. Execution starts 2023-11-21.

The 2018-02-08 API daily candle has an early close timestamp during an exchange
interruption. We exclude that incomplete episode instead of fabricating a full
candle. Every accepted episode must have contiguous, finite, internally valid
UTC OHLCV candles. Missing days, mixed quote currencies, nonfinite values and
unfinished daily candles fail closed. Data subset and source-file hashes are
recorded. The cached USDC file may contain later dates, but only the permitted
subset reaches strategy code or evaluation.

Previously reserved intervals remain outside evaluation:
2022-07-08 02:00–2022-09-29 02:59:59.999 UTC and
2026-01-27 06:00–2026-10-20 16:59:59.999 UTC. USDC and USDT are different
instruments. They are not spliced, and correlated market histories are not
independent replications. BTC/ETH were chosen in prior work; this study does not
resolve survivorship or universe-selection bias.

## Evaluation and interpretation

Fixed strategies receive full chronological discovery runs, disjoint 180-day
windows, annual attribution, and all cost/latency stresses. Partial tails remain
visible but are excluded from the full-window count. Each window begins with
fresh cash; the full-period equity curve carries positions continuously.

A separate selector trains on 365 past days, freezes its choice for the following
90 days, and repeats. Only a strategy with positive training return, positive
excess over the rebalanced equal-weight comparator, and at least 12 training fills
is eligible. Eligible models rank by training Sharpe. The fallback is cash.
The test account carries holdings and transition costs across folds. The final
partial fold is included. Selection currently supports next-day execution only.
This selector's profitability is a **batch-level prerequisite**, not independent
out-of-sample proof for each fixed strategy.

Circular moving-block bootstrap with 15/30/60-day blocks and shared resampling
indices reports confidence intervals and a centered maximum-statistic correction
for multiple comparisons within the registered batch. These are exploratory
stationarity approximations, not DSR/PBO estimates. They cannot correct the
unknown full history of previous human experiments or guarantee future returns.
Confidence intervals are annualized **mean log returns**, not CAGR forecasts.

The protocol records all candidate gates before running: positive double-cost
returns in both episodes, at least 60% positive full 180-day windows, eight such
windows, positive median excess over the rebalanced comparator, daily-close
drawdown at most 15%, at least 40 fills, positive lower confidence limits,
familywise p at most .05 in each episode/block setting, and a positive batch
walk-forward selector in both episodes. Failed gates remain in the report.

Passing would identify only a provisional historical candidate. Repeated
historical exploration remains discovery, even when individual decisions are
causal. A genuine forward record requires freezing the implementation and
observing newly arriving data. No profit guarantee follows from this research.

## Sources

- [Binance public data](https://github.com/binance/binance-public-data)
- [Binance market-data API](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market)
- [Bailey and López de Prado, selection bias and the Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)

## Additional diagnostics in this change

`selector_research.py` records a third exploratory batch: equal weighting of the
entire nonbaseline family, equal/inverse-risk weights among qualifying past
models, and an ensemble requiring three positive training quarters. These use
the same 365/90-day chronology and carry accounts continuously. Their common
comparison interval starts only after the first training year; it is shorter
than the full discovery interval. They do not replace or relax earlier gates.

`regime_diagnostics.py --input data/chronological --output data/regimes.json`
attributes daily returns to bull/bear/transition regimes based on the previous
completed BTC close and SMA50/200. The compounded conditional returns describe
noncontiguous days; they are not stand-alone tradable strategy returns.

`hourly_robustness.py` uses already cached hourly data to replay each daily
hypothesis at 00:00 and 01:00 UTC. The midnight replay must match the daily
engine when boundary prices agree. It also measures hourly-close drawdown and
double-cost results. The accepted USDT hourly subset is 2021-12-25–2022-06-30:
the Binance source itself has shortened BTC/ETH hourly close timestamps on
2021-12-24 at 04:00 UTC, so that incomplete section is excluded. The USDC replay
covers the same 2023-11-21–2025-12-31 period as the daily study. Hourly-close
observations still do not capture intrahour extremes or order-book fills.

All three diagnostic scripts are read-only with respect to trading accounts.
They write local research artifacts only; no credentials are loaded.
