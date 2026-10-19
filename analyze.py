#!/usr/bin/env python3
"""Run read-only BTC/USDC and ETH/USDC technical analysis on Binance Demo."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from autotrading.market import MarketData
from autotrading.strategy import evaluate

ROOT = Path(__file__).resolve().parent
SIGNAL_LOG = ROOT / "data" / "signals.jsonl"


def main():
    parser = argparse.ArgumentParser(description="Read-only technical-analysis baseline; NO TRADES.")
    parser.add_argument("--symbol", choices=["BTCUSDC", "ETHUSDC", "both"], default="both")
    parser.add_argument("--no-log", action="store_true", help="Do not write signal history")
    args = parser.parse_args()
    print("READ-ONLY ANALYSIS – NO API KEYS / NO ORDERS / EXISTING BTC IS UNTOUCHED", flush=True)
    client = MarketData()
    symbols = ["BTCUSDC", "ETHUSDC"] if args.symbol == "both" else [args.symbol]
    for symbol in symbols:
        fast = client.candles(symbol, "15m", 300)
        slow = client.candles(symbol, "1h", 300)
        signal = evaluate(symbol, fast, slow)
        row = {"observed_at_utc": datetime.now(timezone.utc).isoformat(), **signal.to_dict()}
        print(json.dumps(row, ensure_ascii=False, indent=2))
        if not args.no_log:
            SIGNAL_LOG.parent.mkdir(exist_ok=True)
            with SIGNAL_LOG.open("a", encoding="utf8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    if not args.no_log:
        print(f"Signal history saved locally: {SIGNAL_LOG} (excluded from Git).")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as e:
        print(f"ANALYSIS FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
