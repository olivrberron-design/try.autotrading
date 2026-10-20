#!/usr/bin/env python3
"""Publish ONLY allowlisted historical research summaries (not account data).

Research feed is PUBLIC while try.autotrading repo is public. No credentials.
"""
import json
from pathlib import Path
import os
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent


def sanitized(src, run_id=""):
    if src.get("schema_version") != 3 or src.get("type") != "PUBLIC_HISTORICAL_RESEARCH_ONLY":
        raise ValueError("Not an Engine 3.0 historical-only report")
    markets = {}
    for symbol, research in src.get("markets", {}).items():
        if symbol not in ("BTCUSDC", "ETHUSDC"):
            raise ValueError("Unexpected research symbol")
        if research.get("reserved_holdout_evaluated") is not False:
            raise ValueError("Refusing to publish holdout data")
        keys = (
            "symbol", "engine", "strategy_definitions", "config",
            "discovery_start_utc", "discovery_end_utc",
            "reserved_holdout_start_utc", "reserved_holdout_end_utc",
            "reserved_holdout_evaluated", "random_window_count_requested",
            "random_window_count_used", "stress_windows", "walk_forward",
            "cautions", "filter_source",
        )
        data = {k: research[k] for k in keys if k in research}
        data["continuous_discovery"] = {
            name: {"stats": block["stats"], "equity_curve_daily": block["equity_curve_daily"]}
            for name, block in research["continuous_discovery"].items()
        }
        markets[symbol] = data
    if not markets:
        raise ValueError("No historical markets in report")
    return {
        "schema_version": 3, "type": "PUBLIC_HISTORICAL_RESEARCH_ONLY",
        "run_id": str(run_id), "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_sha": src.get("source_sha", ""), "markets": markets,
        "warning": "Simulated, research only. Previously viewed dates contaminate the historical holdout.",
    }


def main():
    report = json.loads((ROOT / "data/advanced/latest.json").read_text(encoding="utf-8"))
    out = sanitized(report, run_id=os.environ.get("GITHUB_RUN_ID", ""))
    target = ROOT / "reports/advanced-latest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8")
    print("Published research-only Engine3 feed, symbols:", ", ".join(out["markets"]))


if __name__ == "__main__":
    main()
