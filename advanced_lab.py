#!/usr/bin/env python3
"""Engine 3.0 discovery ONLY. Final 25 percent reserved, never evaluated."""
from dataclasses import asdict
from datetime import datetime, timezone
import argparse
import json
import math
import os
from pathlib import Path
from autotrading.advanced_strategies import advanced_signals, STRATEGIES_V3
from autotrading.engine3 import ExecutionConfig, simulate_v3
from autotrading.history import load_history, public_get, current_spot_filters
from autotrading.lab import sample_windows

DAY_MS = 86_400_000
HOUR_MS = 3_600_000
BARS_DAY = 24
WARMUP_DAYS = 240
DEST = Path(__file__).resolve().parent / "data" / "advanced"


def utc(ms):
    return datetime.fromtimestamp(ms/1000, timezone.utc).isoformat()


def lite(stats):
    return {k: v for k, v in stats.items() if k not in ("trades", "equity_curve_daily")}


def run_research(symbol, hours, h4, days, cfg, trading_start_ms,
                 requested_windows=12, seed=42):
    first = next((i for i, x in enumerate(hours)
                  if x.open_time_ms >= trading_start_ms), None)
    if first is None or first < 200:
        raise ValueError("Insufficient completed historical warmup")
    boundary = first + int(0.75 * (len(hours) - first))
    if boundary - first < 180 * BARS_DAY:
        raise ValueError("Need >=180 days of discovery data")
    signals = advanced_signals(hours, h4, days)
    names = tuple(STRATEGIES_V3)
    chronological = {}
    for name in names:
        stats = simulate_v3(hours, signals[name], first, boundary, cfg)
        chronological[name] = {
            "stats": lite(stats), "equity_curve_daily": stats["equity_curve_daily"],
            "trades": stats["trades"],
        }

    # Chronological nested selection: 120d TRAIN, NEXT 30d TEST, rolling 30d.
    folds = []
    train_bars, test_bars = 120 * BARS_DAY, 30 * BARS_DAY
    start = first
    while start + train_bars + test_bars <= boundary:
        train_stop, test_stop = start + train_bars, start + train_bars + test_bars
        training = {
            name: lite(simulate_v3(hours, signals[name], start, train_stop, cfg))
            for name in names
        }
        eligible = [
            name for name in names if
            training[name]["return_pct"] > 0 and
            training[name]["excess_vs_bh20_pct"] > 0 and
            training[name]["closed_trades"] >= 2
        ]
        selected = max(eligible, key=lambda n: (
            training[n]["excess_vs_bh20_pct"]
            - 0.5 * training[n]["max_drawdown_pct"]
        )) if eligible else "cash"
        if selected == "cash":
            testing = {"return_pct": 0.0, "closed_trades": 0,
                       "max_drawdown_pct": 0.0, "cash_benchmark_pct": 0.0,
                       "excess_vs_bh20_pct": None}
        else:
            testing = lite(simulate_v3(hours, signals[selected],
                                       train_stop, test_stop, cfg))
        folds.append({
            "fold": len(folds) + 1,
            "training_start_utc": utc(hours[start].open_time_ms),
            "training_end_utc": utc(hours[train_stop-1].close_time_ms),
            "test_start_utc": utc(hours[train_stop].open_time_ms),
            "test_end_utc": utc(hours[test_stop-1].close_time_ms),
            "train_results": training,
            "selected_using_train_only": selected,
            "oos_result": testing,
        })
        start += test_bars
    compounded = math.prod(1 + f["oos_result"]["return_pct"]/100 for f in folds)
    wf = {
        "train_days": 120, "test_days": 30, "step_days": 30,
        "folds": folds, "oos_months_observed": len(folds),
        "selected_oos_compound_return_pct": round(100*(compounded-1), 4),
        "warning": "Capital resets each fold. Only TRAIN selects its next OOS strategy. Months nonoverlapping, but serially correlated.",
    }

    # 30/45/60d stress windows (NOT 5-day windows for 55d Turtle).
    count = min(requested_windows, 8 if boundary-first >= 400*BARS_DAY else 4)
    windows = sample_windows(first, boundary, count, seed, lengths=(30, 45, 60))
    stress = []
    for n, (a, b) in enumerate(windows, 1):
        stress.append({
            "window": n,
            "start_utc": utc(hours[a].open_time_ms),
            "end_utc": utc(hours[b-1].close_time_ms),
            "duration_days": (b-a)/BARS_DAY,
            "results": {name: lite(simulate_v3(hours, signals[name], a, b, cfg))
                        for name in names},
        })
    return {
        "symbol": symbol, "engine": "3.0-research-only",
        "strategy_definitions": STRATEGIES_V3, "config": asdict(cfg),
        "discovery_start_utc": utc(hours[first].open_time_ms),
        "discovery_end_utc": utc(hours[boundary-1].close_time_ms),
        "reserved_holdout_start_utc": utc(hours[boundary].open_time_ms),
        "reserved_holdout_end_utc": utc(hours[-1].close_time_ms),
        "reserved_holdout_evaluated": False,
        "random_window_count_requested": requested_windows,
        "random_window_count_used": count,
        "stress_windows": stress, "continuous_discovery": chronological,
        "walk_forward": wf,
        "cautions": [
            "Research-only: no futures, shorts, leverage, pyramiding or real orders.",
            "BTC and ETH have SEPARATE funded simulation accounts, NOT a shared portfolio.",
            "Historical order-book fill queue unavailable; fixed adverse spread/slippage modeled.",
            "Current Binance exchangeInfo filters may differ from historical symbol filters.",
            "All 1h/4h/1d features use completed candles only. Hourly OHLC stop-first.",
            "Intrabar stops/take-profit are ambiguous; the adverse stop always takes priority.",
            "Prior research already viewed some dates in reserved 2026 history.",
            "Historical results do NOT demonstrate future profitable trading.",
        ],
    }


def main():
    ap = argparse.ArgumentParser(description="Engine3: historical research, zero orders")
    ap.add_argument("--days", type=int, choices=(365, 540, 730), default=365)
    ap.add_argument("--symbol", choices=("both", "BTCUSDC", "ETHUSDC"), default="both")
    ap.add_argument("--windows", type=int, choices=(4, 8, 12, 24, 36), default=12)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--fee-bps", type=float, default=10.)
    ap.add_argument("--slippage-bps", type=float, default=5.)
    ap.add_argument("--spread-bps", type=float, default=2.)
    ap.add_argument("--risk-fraction", type=float, default=0.005)
    ap.add_argument("--latency-bars", type=int, choices=(1, 2, 3), default=1)
    args = ap.parse_args()
    print("ENGINE 3.0 | DISCOVERY ONLY | PUBLIC DATA | NO KEYS / NO ORDERS", flush=True)
    server_ms = int(public_get("/api/v3/time")["serverTime"])
    end_ms = server_ms // HOUR_MS * HOUR_MS - 1
    trade_start = end_ms + 1 - args.days * DAY_MS
    download_start = trade_start - WARMUP_DAYS * DAY_MS
    result = {
        "schema_version": 3, "type": "PUBLIC_HISTORICAL_RESEARCH_ONLY",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_sha": os.environ.get("GITHUB_SHA", ""),
        "seed": args.seed, "study_days": args.days, "markets": {},
    }
    for symbol in ("BTCUSDC", "ETHUSDC") if args.symbol == "both" else (args.symbol,):
        print(f"{symbol}: downloading complete 1h, 4h, 1d data", flush=True)
        hours = load_history(symbol, "1h", download_start, end_ms)
        four = load_history(symbol, "4h", download_start, end_ms)
        days = load_history(symbol, "1d", download_start, end_ms)
        filters = current_spot_filters(symbol)
        cfg = ExecutionConfig(
            fee_bps=args.fee_bps, slippage_bps=args.slippage_bps,
            spread_bps=args.spread_bps, risk_fraction=args.risk_fraction,
            latency_bars=args.latency_bars, min_qty=filters["min_qty"],
            step_size=filters["step_size"], min_notional=filters["min_notional"],
        )
        market = run_research(symbol, hours, four, days, cfg, trade_start,
                              requested_windows=args.windows, seed=args.seed)
        market["filter_source"] = filters["filter_source"]
        result["markets"][symbol] = market
        for name, block in market["continuous_discovery"].items():
            s = block["stats"]
            print(f" {symbol} {name}: {s['return_pct']:+.2f}% net "
                  f"DD {s['max_drawdown_pct']:.2f}%, "
                  f"{s['closed_trades']} trades", flush=True)
    DEST.mkdir(parents=True, exist_ok=True)
    target = DEST / "latest.json"
    target.write_text(json.dumps(result, separators=(",", ":"), ensure_ascii=False),
                      encoding="utf-8")
    lines = [
        "# Engine 3.0: causal multi-timeframe backtest",
        "**No orders. Final 25% RESERVED, NOT evaluated.**",
        f"Historical research on {args.days}d, symbol {args.symbol}, seed {args.seed}.",
        "",
    ]
    for symbol, market in result["markets"].items():
        lines += [f"## {symbol}", "",
                  "| Strategy | Discovery net % | Max DD % | Trades | Sharpe |",
                  "|---|---:|---:|---:|---:|"]
        for name, block in market["continuous_discovery"].items():
            s = block["stats"]
            lines.append(f"| {name} | {s['return_pct']:+.2f} | {s['max_drawdown_pct']:.2f} "
                         f"| {s['closed_trades']} | {s['sharpe_annualized_365d']} |")
        wf = market["walk_forward"]
        lines += ["", f"Walk-forward folds: {len(wf['folds'])}; "
                  f"selected OOS compound {wf['selected_oos_compound_return_pct']:+.2f}%.",
                  f"Nonoverlapping 30-60d stress windows: {market['random_window_count_used']}.",
                  ""]
    summary = "\n".join(lines)
    (DEST / "SUMMARY.md").write_text(summary, encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(summary)
    print("Wrote advanced report:", target, flush=True)


if __name__ == "__main__":
    main()
