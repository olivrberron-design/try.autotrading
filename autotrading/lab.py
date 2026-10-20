"""Reproducible, random-window strategy research. Historical signals only; NO orders.

IMPORTANT: Sample windows are correlated when they overlap. Regime labels use
ONLY hourly candles available before each window's first trade.
A chronological holdout is reserved and never sampled during discovery.
"""
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timezone
import random
from statistics import mean, median, pstdev

from .backtest import compute_signals, simulate
from .indicators import ema
from .rules import STRATEGIES

BAR_MS = 15 * 60 * 1000
DAY_BARS = 96
SAMPLE_DAYS = (5, 7, 10, 14, 21, 30, 45, 60, 90)
DISCOVERY_FRACTION = 0.75


def ms_utc(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def regime_at(window_start_ms, hour_closes_ms, hour_ema50, hour_ema200):
    """Classify using only CLOSED hourly candles at the window start."""
    j = bisect_right(hour_closes_ms, window_start_ms - 1) - 1
    if j < 199 or hour_ema200[j] is None:
        return "unknown"
    ratio = hour_ema50[j] / hour_ema200[j] - 1
    if ratio > 0.003:
        return "bull"
    if ratio < -0.003:
        return "bear"
    return "sideways"


def sample_windows(first, boundary, count, seed, lengths=(5, 7, 10, 14, 21, 30, 45, 60, 90), bars_per_day=DAY_BARS):
    """Reproducible, RANDOM and DISJOINT discovery windows.

    Random durations plus random gaps spread samples across the entire
    discovery period. We adapt allowed window length to the data budget:
    24 independent 30-day windows simply do NOT fit within ~274 days.
    The resulting windows are disjoint but the market is still autocorrelated.
    """
    if bars_per_day <= 0:
        raise ValueError("bars_per_day must be positive")
    if not 1 <= count <= 100:
        raise ValueError("Choose 1..100 sample windows")
    if boundary <= first or not lengths or min(lengths) < 3:
        raise ValueError("Invalid discovery range or window lengths")
    days_available = (boundary - first) // bars_per_day
    # Reserve 20% of the discovery span as gaps, to avoid adjacent clones.
    max_days = int(0.8 * days_available / count)
    options = [int(d) for d in lengths if d <= max_days]
    if not options:
        raise ValueError("Not enough historical data for this many DISJOINT windows. Choose fewer windows or more days.")
    rng = random.Random(seed)
    durations = [rng.choice(options) * bars_per_day for _ in range(count)]
    rng.shuffle(durations)
    slack = boundary - first - sum(durations)
    if slack < 0:
        raise ValueError("Overallocated historical sample windows")
    # Stars and bars: sample a random composition of slack across count+1 gaps.
    # Uses at most count random integers, without allocating slack-sized arrays.
    cuts = sorted(rng.sample(range(slack + count), count))
    gaps = []
    last_cut = -1
    for cut in cuts:
        gaps.append(cut - last_cut - 1)
        last_cut = cut
    gaps.append(slack + count - 1 - last_cut)
    cursor = first + gaps[0]
    windows = []
    for i, width in enumerate(durations):
        windows.append((cursor, cursor + width))
        cursor += width + gaps[i + 1]
    if cursor != boundary or len(windows) != count:
        raise RuntimeError("Random-window allocation invariant failed")
    return windows


def overlapped_fraction(windows):
    """Fraction of *sampled bars* covered more than once (not independence)."""
    events = []
    for a, b in windows:
        events.extend(((a, 1), (b, -1)))
    events.sort()
    multiple = 0
    covered = 0
    last = None
    active = 0
    for at, change in events:
        if last is not None and at > last:
            if active:
                covered += at - last
            if active > 1:
                multiple += at - last
        active += change
        last = at
    return round(100 * multiple / covered, 2) if covered else 0.0


def _aggregate(rows):
    returns = [r["return_pct"] for r in rows]
    excess = [r["excess_vs_bh20_pct"] for r in rows]
    dd = [r["max_drawdown_pct"] for r in rows]
    trades = sum(r["closed_trades"] for r in rows)
    # Samples are disjoint, though prices and outcomes remain serially correlated.
    return {
        "windows": len(rows),
        "completed_trades_across_windows": trades,
        "mean_return_pct": round(mean(returns), 4) if rows else None,
        "median_return_pct": round(median(returns), 4) if rows else None,
        "median_excess_vs_bh20_pct": round(median(excess), 4) if rows else None,
        "beat_bh20_windows_pct": round(
            100 * sum(v > 0 for v in excess) / len(rows), 2) if rows else None,
        "worst_window_return_pct": round(min(returns), 4) if rows else None,
        "max_window_drawdown_pct": round(max(dd), 4) if rows else None,
        "zero_trade_windows": sum(r["closed_trades"] == 0 for r in rows),
    }


def research(symbol, fast, hourly, *, trading_start_ms, n_windows=24,
             seed=42, capital=5000.0, allocation=0.2,
             fee_bps=10.0, slippage_bps=5.0, validate=False):
    """Run random discovery windows, score rules, optionally evaluate holdout.

    IMPORTANT: Reports never use holdout to select a rule. If validate=True,
    only the discovery-selected rule is evaluated on the holdout ONCE;
    user must not iteratively re-fit against displayed holdout performance.
    """
    first = bisect_left([c.open_time_ms for c in fast], trading_start_ms)
    if first < 200:
        raise ValueError("At least 200 warmup candles required")
    boundary = first + int((len(fast) - first) * DISCOVERY_FRACTION)
    if len(fast) - boundary < 30 * DAY_BARS:
        raise ValueError("Need >=30 days of untouched holdout")
    windows = sample_windows(first, boundary, n_windows, seed)
    signals = compute_signals(fast, hourly)
    h_ms = [c.close_time_ms for c in hourly]
    h_prices = [c.close for c in hourly]
    h50, h200 = ema(h_prices, 50), ema(h_prices, 200)

    samples = []
    per_strategy = defaultdict(list)
    by_regime = defaultdict(lambda: defaultdict(list))
    for index, (a, b) in enumerate(windows, 1):
        regime = regime_at(fast[a].open_time_ms, h_ms, h50, h200)
        row = {
            "window": index,
            "start_utc": ms_utc(fast[a].open_time_ms),
            "end_utc": ms_utc(fast[b - 1].close_time_ms),
            "duration_days": round((b - a) / DAY_BARS, 2),
            "start_regime_1h_ema": regime,
            "results": {},
        }
        for name in STRATEGIES:
            stats = simulate(fast, signals[name], a, b, capital, allocation,
                             fee_bps, slippage_bps)
            compact = {k: v for k, v in stats.items() if k not in (
                "trades", "signal_counts")}
            compact["excess_vs_bh20_pct"] = round(
                stats["return_pct"] - stats["buy_hold_equal_allocation_pct"], 4)
            row["results"][name] = compact
            per_strategy[name].append(compact)
            by_regime[name][regime].append(compact)
        samples.append(row)

    summary = {}
    for name in STRATEGIES:
        agg = _aggregate(per_strategy[name])
        breakdown = {label: _aggregate(rows)
                     for label, rows in by_regime[name].items()}
        # Conservatively disqualify no/too-few-trade or mostly inactive rules.
        # This is a heuristic screening score, not evidence of significance.
        eligible = (agg["completed_trades_across_windows"] >= max(10, n_windows // 2)
                    and agg["zero_trade_windows"] <= n_windows // 2)
        agg["screen_eligible"] = eligible
        agg["by_start_regime"] = breakdown
        summary[name] = agg

    eligible_names = [name for name in STRATEGIES if summary[name]["screen_eligible"]]
    ranked = sorted(eligible_names, key=lambda n: (
        summary[n]["median_excess_vs_bh20_pct"],
        summary[n]["beat_bh20_windows_pct"],
        -summary[n]["max_window_drawdown_pct"],
    ), reverse=True)
    selected = ranked[0] if ranked else None
    # A "promising" candidate needs positive median outperformance AND a
    # strict majority of random windows beating passive equal-allocation.
    promising = bool(selected and summary[selected]["median_excess_vs_bh20_pct"] > 0
                     and summary[selected]["median_return_pct"] > 0
                     and summary[selected]["beat_bh20_windows_pct"] > 50)

    report = {
        "symbol": symbol,
        "experiment": "random nonoverlapping discovery windows / optional reserved chronology",
        "source": "Binance public spot historical klines",
        "seed": seed,
        "window_count": n_windows,
        "discovery_start_utc": ms_utc(fast[first].open_time_ms),
        "discovery_end_utc": ms_utc(fast[boundary - 1].close_time_ms),
        "sealed_holdout_start_utc": ms_utc(fast[boundary].open_time_ms),
        "sealed_holdout_end_utc": ms_utc(fast[-1].close_time_ms),
        "overlap_pct_of_unique_discovery_bars": overlapped_fraction(windows),
        "independent_windows_by_time": True,
        "untouched_holdout_warning": "Recent 2026 data were exposed in earlier 180-day tests; historical holdout is NOT genuinely pristine. Use later forward demo trading for new out-of-sample evidence.",
        "cost_assumptions": {
            "capital_usdc": capital, "position_fraction": allocation,
            "fee_bps_each_side": fee_bps, "slippage_bps_each_side": slippage_bps,
        },
        "regime_definition": "1h EMA50 vs EMA200 at window START; +/-0.3% threshold, no future bars",
        "ranked_eligible": ranked,
        "selected_by_discovery_only": selected,
        "passes_basic_discovery_screen": promising,
        "strategies": summary,
        "sampled_windows": samples,
        "holdout": None,
        "cautions": [
            "Windows do not overlap, but returns may still be serially correlated and samples are NOT fully independent.",
            "Regime label uses only data at window start; a month-long window can change regime internally.",
            "A good random-window score can still be overfit after testing many strategies/seeds.",
            "Prior experiments already exposed part of this recent historical holdout; only FUTURE forward testing can restore pristine unseen data.",
            "This tests one pair at a time, not a combined multi-asset portfolio.",
            "All fills are estimated next-candle open with fixed fee/slippage; no exchange execution guarantees."
        ],
    }
    if validate:
        report["holdout"] = {
            "validation_performed": True,
            "selected_by_discovery_only": selected,
            "note": "Holdout is now EXPOSED; do not reuse it for strategy modifications.",
        }
        if selected:
            holdout_stats = simulate(
                fast, signals[selected], boundary, len(fast),
                capital, allocation, fee_bps, slippage_bps
            )
            report["holdout"]["result"] = {k: v for k, v in holdout_stats.items()
                                            if k != "trades"}
            report["holdout"]["closed_trades"] = holdout_stats["trades"]
    return report
