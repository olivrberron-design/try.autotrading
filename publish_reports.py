#!/usr/bin/env python3
"""Prepare public-safe Strategy Lab snapshot for Lovable frontend.

This reads ONLY the JSON outputs of a public market-data experiment.
It never touches account balances, .env files, Binance API keys, or orders.
The GitHub Actions workflow publishes reports/latest.json on success.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "lab"
PUBLIC = ROOT / "reports" / "latest.json"
MARKETS = ("BTCUSDC", "ETHUSDC")


def build_public_snapshot(source, *, run_id=None, source_sha=None):
    if not source.is_dir():
        raise ValueError("Lab output folder does not exist")
    reports = {}
    for symbol in MARKETS:
        path = source / (symbol + ".json")
        if not path.exists():
            continue
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("symbol") != symbol or not isinstance(report.get("strategies"), dict):
            raise ValueError(f"Malformed Strategy Lab report for {symbol}")
        if not isinstance(report.get("sampled_windows"), list):
            raise ValueError("Missing random window details")
        if report.get("holdout") is not None:
            raise ValueError("Do not publicly publish holdout validation from research workflow")
        # Explicit allowlist so future internal state, credentials and user data
        # can never leak to a public repository by an accidental schema change.
        keys = (
            "symbol", "seed", "window_count", "discovery_start_utc",
            "discovery_end_utc", "sealed_holdout_start_utc",
            "sealed_holdout_end_utc", "overlap_pct_of_unique_discovery_bars",
            "cost_assumptions", "regime_definition", "ranked_eligible",
            "selected_by_discovery_only", "passes_basic_discovery_screen",
            "strategies", "sampled_windows", "cautions",
        )
        reports[symbol] = {key: report[key] for key in keys if key in report}

    if not reports:
        raise ValueError("No valid public market-data reports to publish")

    return {
        "schema_version": 1,
        "type": "PUBLIC_HISTORICAL_RESEARCH_ONLY",
        "run_id": str(run_id or ""),
        "source_sha": str(source_sha or ""),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "Binance public spot market-data klines",
        "reports": reports,
        "warnings": [
            "Not financial advice, no trading instructions, no proven alpha.",
            "Prior historical experiments exposed some of the reserved dates.",
            "Costs and fills are modeled, not guaranteed exchange executions.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description="Build public-safe Lovable data feed")
    parser.add_argument("--folder", type=Path)
    parser.add_argument("--output", type=Path, default=PUBLIC)
    parser.add_argument("--run-id")
    parser.add_argument("--source-sha")
    args = parser.parse_args()
    folder = args.folder or max((p for p in DATA.iterdir() if p.is_dir()), key=lambda p: p.name)
    snapshot = build_public_snapshot(folder, run_id=args.run_id, source_sha=args.source_sha)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Public-only snapshot written: {args.output}")
    for symbol, report in snapshot["reports"].items():
        print(f"{symbol}: {len(report['strategies'])} strategies; {len(report['sampled_windows'])} windows")


if __name__ == "__main__":
    main()
