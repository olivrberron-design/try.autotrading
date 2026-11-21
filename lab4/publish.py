"""Write the compact public summary reports/lab4-latest.json from a local run.

    python3 -m lab4.publish   (after: python3 -m lab4.run --holdout)
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEYS = ("cagr", "sharpe", "max_drawdown", "rolling182_positive", "worst_year", "stress_cagr",
        "avg_exposure", "orders_per_day", "excess_cagr_vs_spy", "bootstrap_p05_ann_mean")


def slim(m):
    out = {k: m.get(k) for k in KEYS if m.get(k) is not None}
    out["years"] = {y: v["return"] for y, v in m["years"].items()}
    out["regimes_ann_mean"] = {k: v["ann_mean"] for k, v in m["regimes"].items()}
    if "gates" in m:
        out["failed_gates"] = [k for k, v in m["gates"].items() if not v]
    return out


def main():
    src = json.loads((ROOT / "data" / "lab4" / "out" / "report-holdout.json").read_text())
    rep = {
        "type": "PUBLIC_HISTORICAL_RESEARCH_ONLY",
        "research_status": "NO_VALIDATED_WINNER" if not any(
            s.get("passes_all_gates") for s in src["strategies"].values()) else "HISTORICAL_CANDIDATE",
        "git_head_frozen": src["git_head"],
        "source_sha256": src["source_sha256"],
        "protocol": "lab4/PROTOCOL.md",
        "costs_per_side": {"base": src["base_cost_per_side"], "stress": src["stress_cost_per_side"]},
        "gates": src["gates"],
        "periods": {k: {"start": v["start"], "end_exclusive": v["end_exclusive"],
                        "spy": {x: v["spy"][x] for x in ("cagr", "sharpe", "max_drawdown", "years")}}
                    for k, v in src["periods"].items()},
        "benchmarks": {n: {p: slim(m) for p, m in by.items()} for n, by in src["benchmarks"].items()},
        "strategies": {n: {p: slim(m) for p, m in by.items() if isinstance(m, dict)}
                       for n, by in src["strategies"].items()},
        "regime_learned_map": src.get("regime_learned_map"),
    }
    out = ROOT / "reports" / "lab4-latest.json"
    out.write_text(json.dumps(rep, indent=1, sort_keys=True))
    print("wrote", out)


if __name__ == "__main__":
    main()
