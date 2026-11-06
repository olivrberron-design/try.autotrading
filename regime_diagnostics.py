#!/usr/bin/env python3
"""Attribute saved daily research returns to prior-close BTC market regimes.

This reads existing ledgers and permitted historical slices. It neither changes
signals nor reruns parameter selection, and it never requests additional data.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from math import isclose, isfinite, prod
from pathlib import Path
import re
from statistics import mean

from autotrading.research_strategies import _validate_series
from chronological_research import episode_series

REGIMES = ("bull", "bear", "transition", "unclassified")
ATTRIBUTION_NOTE = (
    "Regime returns compound possibly noncontiguous days from the original "
    "continuous account. They are conditional performance attribution, not "
    "standalone tradable returns or independently validated regime strategies. "
    "No hypothetical exit/reentry costs are added at regime boundaries."
)


def _utc(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def classify_regimes(series):
    """Map each daily OPEN to a regime observable before that open.

    Day i uses only close[i-1], SMA50 ending i-1, and SMA200 ending i-1.
    Strict inequalities mean equality belongs to transition. The first200
    days have insufficient prior history and are unclassified.
    """
    names, n = _validate_series(series)
    bitcoin = [name for name in names if name.startswith("BTC")]
    if len(bitcoin) != 1:
        raise ValueError("Exactly one BTC quote-pair series is required")
    btc = bitcoin[0]
    bars = series[btc]
    cumulative = [0.0]
    for candle in bars:
        cumulative.append(cumulative[-1] + candle.close)
    result = {}
    for i in range(n):
        regime = "unclassified"
        if i >= 200:
            sma50 = (cumulative[i] - cumulative[i - 50]) / 50
            sma200 = (cumulative[i] - cumulative[i - 200]) / 200
            close = bars[i - 1].close
            if close > sma200 and sma50 > sma200:
                regime = "bull"
            elif close < sma200 and sma50 < sma200:
                regime = "bear"
            else:
                regime = "transition"
        result[bars[i].open_time_ms] = regime
    return btc, result


def _attribute(result, regimes):
    if result is None:
        return None
    curve, returns = result["equity_curve"], result["daily_returns"]
    if len(curve) != len(returns) or not curve:
        raise ValueError("Daily equity and return series must be nonempty and aligned")
    buckets = {name: [] for name in REGIMES}
    runs = {name: 0 for name in REGIMES}
    previous_time = previous_regime = previous_equity = None
    for row, value in zip(curve, returns):
        when, equity = row["open_ms"], row["equity"]
        if when not in regimes or (previous_time is not None and when != previous_time + 86_400_000):
            raise ValueError("Return dates must be contiguous and belong to permitted history")
        if not isfinite(value) or value <= -1 or not isfinite(equity) or equity <= 0:
            raise ValueError("Invalid daily return or equity")
        if previous_equity is not None and not isclose(value, equity / previous_equity - 1, abs_tol=1e-10):
            raise ValueError("Daily returns do not reconcile with the equity curve")
        regime = regimes[when]
        buckets[regime].append(value)
        if regime != previous_regime:
            runs[regime] += 1
        previous_time, previous_regime, previous_equity = when, regime, equity
    total = 100 * (prod(1 + value for value in returns) - 1)
    if "return_pct" in result and not isclose(total, result["return_pct"], abs_tol=1e-8):
        raise ValueError("Compounded daily returns do not reconcile with reported return")
    output = {
        "evaluation_days": len(returns),
        "start_utc": _utc(curve[0]["open_ms"]),
        "end_exclusive_utc": _utc(curve[-1]["open_ms"] + 86_400_000),
        "continuous_account_return_pct": total,
        "regimes": {},
    }
    for name, values in buckets.items():
        output["regimes"][name] = {
            "days": len(values), "contiguous_runs": runs[name],
            "compounded_conditional_return_pct": 100 * (prod(1 + x for x in values) - 1) if values else None,
            "mean_daily_return_pct": 100 * mean(values) if values else None,
            "worst_daily_return_pct": 100 * min(values) if values else None,
            "worst_daily_loss_pct": 100 * max(0, -min(values)) if values else None,
        }
    return output


def analyze_regimes(series, detailed):
    """Analyze runner details with fixed_strategies and walk_forward results."""
    btc, regimes = classify_regimes(series)
    result = {
        "btc_symbol": btc,
        "regime_rule": "Previous completed BTC daily close and SMA50/SMA200; bull: both above SMA200, bear: both below, equality/mixed: transition.",
        "attribution_note": ATTRIBUTION_NOTE,
        "fixed_strategies": {name: _attribute(value, regimes)
                             for name, value in detailed["fixed_strategies"].items()},
    }
    for name in ("walk_forward", "walk_forward_double"):
        if name in detailed:
            result[name] = _attribute(detailed[name]["result"], regimes)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Existing chronological research report directory")
    parser.add_argument("--output", type=Path, required=True, help="Diagnostic JSON destination")
    args = parser.parse_args(argv)
    report_path = args.input / "report.json"
    raw_report = report_path.read_bytes()
    report = json.loads(raw_report)
    output = {
        "source_report_sha256": hashlib.sha256(raw_report).hexdigest(),
        "source_protocol_sha256": report.get("protocol_sha256"),
        "attribution_note": ATTRIBUTION_NOTE,
        "episodes": [],
    }
    for metadata in report["episodes"]:
        episode = metadata["episode"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", episode["name"]):
            raise ValueError("Invalid episode filename")
        # The exact recorded start/end and quote pairs are passed unchanged.
        # allow_download=False refuses absent caches instead of expanding data.
        series, manifests = episode_series(episode, allow_download=False)
        if manifests != metadata["data_manifest"]:
            raise ValueError("Historical data hashes differ from the source research report")
        ledger_path = args.input / (episode["name"] + "-ledgers.json")
        raw_ledger = ledger_path.read_bytes()
        detailed = json.loads(raw_ledger)
        output["episodes"].append({
            "episode": episode,
            "source_ledger_sha256": hashlib.sha256(raw_ledger).hexdigest(),
            "diagnostics": analyze_regimes(series, detailed),
        })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, allow_nan=False) + "\n")
    print("Saved regime attribution:", args.output)


if __name__ == "__main__":
    main()
