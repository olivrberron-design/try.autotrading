#!/usr/bin/env python3
"""Strategy Lab: one-run reproducible random-window research, NO Binance orders."""
import argparse
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from autotrading.history import load_history, public_get
from autotrading.lab import research

DAY_MS = 86_400_000
BAR_MS = 900_000
OUTPUT = Path(__file__).resolve().parent / "data" / "lab"


def range_for_days(days, server_ms):
    """16 days of warmup before a fixed N-day historical study window."""
    if days not in (365, 540, 730):
        raise ValueError("Choose 365, 540 or 730 days")
    end_ms = server_ms // BAR_MS * BAR_MS - 1
    study_start_ms = end_ms + 1 - days * DAY_MS
    download_start_ms = study_start_ms - 16 * DAY_MS
    return download_start_ms, study_start_ms, end_ms


def percent(value):
    return "n/a" if value is None else f"{value:+.2f}%"


def table(report):
    lines = []
    symbol = report["symbol"]
    lines += [f"### {symbol}", "",
              f"Discovery: {report['discovery_start_utc']} → {report['discovery_end_utc']}; "
              f"sealed future holdout: {report['sealed_holdout_start_utc']} → {report['sealed_holdout_end_utc']}.",
              "",
              "| Strategy | Median absolute return | Median excess vs BH20 | Beat BH20 | Worst window | Max DD | Trades | 0-trade windows | Screen |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    for name, value in report["strategies"].items():
        lines.append(f"| {name} | {percent(value['median_return_pct'])} | {percent(value['median_excess_vs_bh20_pct'])} "
                     f"| {percent(value['beat_bh20_windows_pct'])} "
                     f"| {percent(value['worst_window_return_pct'])} "
                     f"| {percent(value['max_window_drawdown_pct'])} "
                     f"| {value['completed_trades_across_windows']} "
                     f"| {value['zero_trade_windows']} "
                     f"| {'eligible' if value['screen_eligible'] else 'insufficient activity'} |")
    lines += ["", f"**Discovery-selected strategy:** `{report['selected_by_discovery_only'] or 'none'}` "
              f"| **passes minimal screening:** `{report['passes_basic_discovery_screen']}`.",
              f"Overlapping sampled bars: **{report['overlap_pct_of_unique_discovery_bars']}%** (designed to be 0%).",
              "",
              "<details><summary>Performance by regime known at each window START (no future data)</summary>",
              "",
              "| Strategy | Starting regime | Windows | Median excess vs BH20 | Beat BH20 |",
              "|---|---|---:|---:|---:|"]
    for strategy, stats in report["strategies"].items():
        for regime, val in sorted(stats["by_start_regime"].items()):
            lines.append(f"| {strategy} | {regime} | {val['windows']} "
                         f"| {percent(val['median_excess_vs_bh20_pct'])} "
                         f"| {percent(val['beat_bh20_windows_pct'])} |")
    lines += ["", "</details>", ""]
    h = report["holdout"]
    if h is not None:
        if h.get("result"):
            v = h["result"]
            lines += [f"**FINAL HOLDOUT IS NOW EXPOSED for {h['selected_by_discovery_only']}**: "
                      f"{percent(v['return_pct'])} net estimated return, "
                      f"{percent(v['buy_hold_equal_allocation_pct'])} equal-allocation BH, "
                      f"{v['closed_trades']} closed trades, max drawdown {percent(v['max_drawdown_pct'])}.",
                      "**Do not optimize or repeat selection using this period.**", ""]
        else:
            lines += ["**Final holdout exposed: no discovery candidate qualified for evaluation.**", ""]
    else:
        lines += ["🔒 **Final chronological holdout NOT evaluated.** Some recent dates were already exposed by earlier experiments; this is NOT pristine. "
                  "Only click validation when strategy selection and parameters are frozen.", ""]
    return "\n".join(lines)


def write_report(report, destination):
    path = destination / (report["symbol"] + ".json")
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    csvpath = destination / (report["symbol"] + "-random-windows.csv")
    with csvpath.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["window", "start", "end", "duration_days", "regime_at_start",
                         "strategy", "return_pct", "bh20_pct", "excess_pct",
                         "max_drawdown_pct", "closed_trades", "win_rate_pct"])
        for row in report["sampled_windows"]:
            for strategy, item in row["results"].items():
                writer.writerow([
                    row["window"], row["start_utc"], row["end_utc"],
                    row["duration_days"], row["start_regime_1h_ema"], strategy,
                    item["return_pct"], item["buy_hold_equal_allocation_pct"],
                    item["excess_vs_bh20_pct"], item["max_drawdown_pct"],
                    item["closed_trades"], item["win_rate_pct"]
                ])
    return path


def main():
    parser = argparse.ArgumentParser(description="Read-only Strategy Lab (historical data, zero orders)")
    parser.add_argument("--days", type=int, choices=(365, 540, 730), default=365)
    parser.add_argument("--symbol", choices=("BTCUSDC", "ETHUSDC", "both"), default="both")
    parser.add_argument("--windows", type=int, choices=(12, 24, 36), default=24)
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducible windows")
    parser.add_argument("--mode", choices=("discover", "validate"), default="discover",
                        help="validate EXPOSES the chronological holdout!")
    args = parser.parse_args()
    if args.mode == "validate":
        print("WARNING: FINAL chronological HOLDOUT will be exposed. No future tuning on that period.")
    print("RESEARCH-ONLY | PUBLIC MARKET DATA | NO ACCOUNT KEYS / NO ORDERS", flush=True)
    server_ms = int(public_get("/api/v3/time")["serverTime"])
    download_start_ms, trade_start_ms, end_ms = range_for_days(args.days, server_ms)
    folder = OUTPUT / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder.mkdir(parents=True, exist_ok=True)
    heading = [
        "# AutoTrading Strategy Lab",
        "",
        f"Mode: **{args.mode}**; {args.days} days; {args.windows} random windows per asset; "
        f"fixed seed `{args.seed}`.",
        "All orders are simulated. The existing Binance Demo BTC is untouched.",
        "",
    ]
    for symbol in ("BTCUSDC", "ETHUSDC") if args.symbol == "both" else (args.symbol,):
        print(f"\nDownloading {symbol}, 15-minute and hourly candles…", flush=True)
        fast = load_history(symbol, "15m", download_start_ms, end_ms)
        hourly = load_history(symbol, "1h", download_start_ms, end_ms)
        result = research(symbol, fast, hourly, trading_start_ms=trade_start_ms,
                          n_windows=args.windows, seed=args.seed,
                          validate=(args.mode == "validate"))
        write_report(result, folder)
        heading.append(table(result))
        for name, item in result["strategies"].items():
            print(f"{symbol} {name}: median excess "
                  f"{percent(item['median_excess_vs_bh20_pct'])}, "
                  f"beat BH20 {percent(item['beat_bh20_windows_pct'])}, "
                  f"n={item['windows']}", flush=True)
        print("Discovery selection:", result["selected_by_discovery_only"],
              "| passes screen:", result["passes_basic_discovery_screen"], flush=True)
    heading += [
        "### How to interpret", "",
        "* BH20 = equal 20% allocation buy-and-hold after the same estimated fees and slippage.",
        "* Fee assumption: 0.10% each side. Slippage: 0.05% each side.",
        "* Windows are disjoint; market movements may still be correlated and trade observations are not statistically independent.",
        "* Disjoint random windows are sampled ONLY in the earlier discovery era (75%). "
        "The final 25% is chronological and excluded unless mode=validate.",
        "* These strategies and parameters are research hypotheses, not a profitable trading system.",
        "* Rankings and regime groups are descriptive. Classifying a regime at the start "
        "does not imply the same regime persisted for the entire window.",
        "* Earlier 2026 research already exposed some reserved dates; a pristine holdout requires future forward DEMO data.",
        "", "**Detailed JSON/CSV results:** download the artifact at the bottom of this GitHub Actions run.",
        "",
    ]
    summary = "\n".join(heading)
    (folder / "SUMMARY.md").write_text(summary, encoding="utf-8")
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as file:
            file.write(summary)
    print(f"\nFinished. Results: {folder}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, KeyError, TypeError, OSError) as exc:
        print("STRATEGY LAB FAILED:", type(exc).__name__, str(exc), file=sys.stderr)
        raise SystemExit(1)
