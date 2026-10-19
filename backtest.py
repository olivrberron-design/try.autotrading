#!/usr/bin/env python3
"""Historical spot backtest, READ ONLY. Never interacts with demo account/orders."""
import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from autotrading.history import load_history, public_get, time_range
from autotrading.backtest import backtest


OUTDIR = Path(__file__).resolve().parent / "data" / "backtests"


def fmt(value):
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def show(symbol, result):
    print("\n" + "=" * 77)
    print(symbol, "| Selected using TRAINING performance only:",
          result["selected_by_training_only"])
    print(f"{'STRATEGY':<19} {'PERIOD':<10} {'RETURN %':>10} {'DD %':>9} {'TRADES':>8} {'WIN %':>9} {'BH20 %':>9}")
    for name, pair in result["strategies"].items():
        for period in ("training", "holdout"):
            v = pair[period]
            print(f"{name:<19} {period:<10} {fmt(v['return_pct']):>10} "
                  f"{fmt(v['max_drawdown_pct']):>9} {v['closed_trades']:>8} "
                  f"{fmt(v['win_rate_pct']):>9} {fmt(v['buy_hold_equal_allocation_pct']):>9}")
    print("BH20 = fee/slippage-adjusted buy-and-hold with the same 20% allocation (default).")
    for name, pair in result["strategies"].items():
        for period in ("training", "holdout"):
            v = pair[period]
            if not v["closed_trades"]:
                print(f"NOTICE: {name} {period} has NO completed trades; profit factor and win rate undefined.")
            if v["open_position_at_end"]:
                print(f"NOTICE: {name} {period} ends holding an open position; net liquidation estimated in return.")
    print("Holdout is for evaluation only. Do NOT re-optimize based on these scores.")


def write_results(symbol, result, start_ms, end_ms):
    OUTDIR.mkdir(parents=True, exist_ok=True)
    prefix = f"{symbol}-{start_ms}-{end_ms}"
    report = OUTDIR / (prefix + "-report.json")
    with report.open("w", encoding="utf-8") as fp:
        json.dump({
            "symbol": symbol, "source": "https://data-api.binance.vision",
            "retrieved_utc": datetime.now(timezone.utc).isoformat(),
            "backtest": result,
        }, fp, indent=2)
    for name, periods in result["strategies"].items():
        for period, stats in periods.items():
            path = OUTDIR / f"{prefix}-{name}-{period}-trades.csv"
            with path.open("w", newline="", encoding="utf-8") as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=[
                    "entry_utc", "exit_utc", "entry_price", "exit_price",
                    "quantity", "pnl_usdc", "pnl_pct_on_allocated"
                ])
                writer.writeheader()
                writer.writerows(stats["trades"])
    print("Saved report:", report)


def main():
    ap = argparse.ArgumentParser(description="Historical backtest ONLY (public data; no trades)")
    ap.add_argument("--days", type=int, choices=(30, 60, 90, 180, 365),
                    default=180, help="Historical trading days (default 180) + 16-day warmup")
    ap.add_argument("--symbol", choices=("BTCUSDC", "ETHUSDC", "both"), default="both")
    ap.add_argument("--capital", type=float, default=5000.0)
    ap.add_argument("--allocation", type=float, default=0.2,
                    help="Fraction of available cash invested per entry (default 0.2)")
    ap.add_argument("--fee-bps", type=float, default=10.0,
                    help="Fee per side in basis points (default 10 = 0.10%%)")
    ap.add_argument("--slippage-bps", type=float, default=5.0,
                    help="Adverse fill per side in basis points (default 5 = 0.05%%)")
    ap.add_argument("--train-fraction", type=float, default=0.7)
    ap.add_argument("--refresh", action="store_true", help="Re-download historical bars")
    args = ap.parse_args()
    print("BACKTEST ONLY | Binance PUBLIC MARKET-DATA API | NO DEMO/LIVE ORDERS", flush=True)
    now = int(public_get("/api/v3/time")["serverTime"])
    start_ms, trading_start_ms, end_ms = time_range(args.days, now)
    for symbol in ("BTCUSDC", "ETHUSDC") if args.symbol == "both" else (args.symbol,):
        print("\nFetching", symbol, "public historical 15m/1h candles...", flush=True)
        fast = load_history(symbol, "15m", start_ms, end_ms, args.refresh)
        hourly = load_history(symbol, "1h", start_ms, end_ms, args.refresh)
        result = backtest(
            fast, hourly, start_ms=trading_start_ms,
            capital=args.capital, allocation=args.allocation,
            fee_bps=args.fee_bps, slippage_bps=args.slippage_bps,
            train_fraction=args.train_fraction
        )
        show(symbol, result)
        write_results(symbol, result, trading_start_ms, end_ms)
    print("\nCompleted: historical RESEARCH only; your Demo BTC has not been touched.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
        print(f"BACKTEST STOPPED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
