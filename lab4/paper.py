"""Forward paper test: frozen strategies on NEW daily data. No keys, no orders.

Every run re-simulates each registered strategy from its first fill day with
the same engine and costs as the backtest, using only public Binance data:

  * the decision for day D uses candles that closed by 00:00 UTC of day D;
  * the hypothetical fill is at the official open of day D (00:00 UTC);
  * equity is marked open-to-open, exactly like lab4.engine.simulate.

The code a strategy depends on is hashed at registration. If any of those
files changes, the run stops: changing a strategy means a NEW registration
with a NEW start date, never silently rewriting the forward record.

    python3 -m lab4.paper                     # update paper/forward.json
    python3 -m lab4.paper --register ID FN    # register (see main())
"""
import argparse
import hashlib
import importlib
import json
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import data, metrics
from . import strategies as S
from .engine import DAY_MS, Panel, simulate

ROOT = Path(__file__).resolve().parents[1]
PAPER = ROOT / "paper"
REGISTRY = PAPER / "registry.json"
FORWARD = PAPER / "forward.json"
WARMUP_DAYS = 420
BENCHMARKS = {"bh_btc": S.bh_btc, "bh_btceth": S.bh_btceth}


def file_sha(rel):
    return hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()


def load_registry():
    if REGISTRY.exists():
        return json.loads(REGISTRY.read_text())
    return {"note": "Frozen forward paper test. Hypothetical fills only; no orders.", "strategies": []}


def resolve(entry):
    return getattr(importlib.import_module(entry["module"]), entry["function"])


def today_opens(assets, today):
    """Official open of the current UTC day (fixed once the day starts)."""
    out = {}
    t0 = data._ms_of(today)
    for a in assets:
        sym = data.SYMBOL_ALIASES.get(a, [(a + "USDT", "")])[-1][0]
        blob = data._get(f"{data.API}?symbol={sym}&interval=1d&startTime={t0}&limit=1")
        rows = json.loads(blob) if blob else []
        if rows and int(rows[0][0]) == t0:
            out[a] = float(rows[0][1])
    return out


def build_panel(start, today):
    """Closed daily candles in [start, today) plus today's open."""
    n = (today - start).days + 1
    p = Panel(data._ms_of(start), n)
    opens = today_opens(data.UNIVERSE_2019, today)
    for a in data.UNIVERSE_2019:
        rows = data.asset_daily(a, start, today)
        if a in opens:
            rows[data._ms_of(today)] = (opens[a], None, None, None, None)
        p.add_asset(a, rows)
    return p


def spy_since(first_fill):
    """SPY total-return since the last close before ``first_fill`` (best effort)."""
    try:
        px = data.spy_total_return(first_fill - timedelta(days=10), date.today() + timedelta(days=1))
    except Exception as exc:  # Yahoo can rate-limit CI runners; never fail the run
        return {"error": type(exc).__name__}
    days = sorted(px)
    base = [d for d in days if d < first_fill.isoformat()]
    after = [d for d in days if d >= first_fill.isoformat()]
    if not base or not after:
        return {"total_return": 0.0, "last_close": base[-1] if base else None}
    return {"base_close_date": base[-1], "last_close_date": after[-1],
            "total_return": px[after[-1]] / px[base[-1]] - 1}


def run_one(p, fn, first, today_idx, cost):
    if first >= today_idx:
        return {"returns": [], "orders": [], "turnover": [], "exposure": []}
    return simulate(p, fn, first, today_idx, cost=cost)


def update(today=None):
    reg = load_registry()
    if not reg["strategies"]:
        raise SystemExit("No registered strategies")
    for e in reg["strategies"]:
        for rel, sha in e["frozen_files"].items():
            if file_sha(rel) != sha:
                raise SystemExit(f"{rel} changed since {e['id']} was registered: re-register explicitly")
    today = today or datetime.now(timezone.utc).date()
    earliest = min(date.fromisoformat(e["first_fill_day"]) for e in reg["strategies"])
    start = earliest - timedelta(days=WARMUP_DAYS)
    p = build_panel(start, today)
    t_idx = p.index_of(today)
    out = {"type": "FORWARD_PAPER_TEST_NO_ORDERS",
           "updated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "latest_open_day": today.isoformat(),
           "fill_model": "official 00:00 UTC daily open, cost per side as registered",
           "strategies": {}}
    for e in reg["strategies"]:
        first_day = date.fromisoformat(e["first_fill_day"])
        first = p.index_of(first_day)
        res = run_one(p, resolve(e), first, t_idx, e["cost_per_side"])
        eq = metrics.equity(res["returns"])
        curve = [[p.date(first + i + 1).isoformat(), round(v, 6)] for i, v in enumerate(eq)]
        decisions = []
        for k in range(first, t_idx + 1):
            w = resolve(e)(p, k - 1)
            decisions.append({"decided_at_close_of": p.date(k - 1).isoformat(),
                              "fill_open": p.date(k).isoformat(),
                              "weights": {a: round(x, 4) for a, x in sorted(w.items()) if x > 1e-6}})
        bench = {}
        for name, fn in BENCHMARKS.items():
            b = run_one(p, fn, first, t_idx, e["cost_per_side"])
            bench[name] = metrics.equity(b["returns"])[-1] - 1 if b["returns"] else 0.0
        out["strategies"][e["id"]] = {
            "registered_utc": e["registered_utc"],
            "first_fill_day": e["first_fill_day"],
            "days": len(res["returns"]),
            "total_return": (eq[-1] - 1) if eq else 0.0,
            "max_drawdown": metrics.max_drawdown(res["returns"]),
            "orders": int(sum(res["orders"])),
            "benchmarks_total_return": bench,
            "spy": spy_since(first_day),
            "today_target": decisions[-1] if decisions else None,
            "equity_curve": curve,
            "decisions": decisions,
        }
    PAPER.mkdir(exist_ok=True)
    FORWARD.write_text(json.dumps(out, indent=1, sort_keys=True))
    return out


def register(sid, module, function, files, cost=0.0015, now=None):
    """Freeze a strategy. Its first decision uses the first daily close AFTER
    registration, so every evaluated day is genuinely new data."""
    now = now or datetime.now(timezone.utc)
    reg = load_registry()
    if any(e["id"] == sid for e in reg["strategies"]):
        raise SystemExit(f"{sid} already registered; use a new id for a changed strategy")
    first_fill = (now.date() + timedelta(days=1)).isoformat()
    entry = {"id": sid, "module": module, "function": function,
             "registered_utc": now.isoformat(timespec="seconds"),
             "first_fill_day": first_fill, "cost_per_side": cost,
             "frozen_files": {rel: file_sha(rel) for rel in files}}
    resolve(entry)  # must import
    reg["strategies"].append(entry)
    PAPER.mkdir(exist_ok=True)
    REGISTRY.write_text(json.dumps(reg, indent=1))
    return entry


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--register", nargs=3, metavar=("ID", "MODULE", "FUNCTION"))
    ap.add_argument("--files", nargs="*", default=["lab4/engine.py", "lab4/strategies.py", "lab4/data.py"])
    args = ap.parse_args()
    if args.register:
        print(json.dumps(register(*args.register, files=args.files), indent=1))
        return
    out = update()
    for sid, s in out["strategies"].items():
        print(f"{sid}: {s['days']} days, return {s['total_return']*100:.2f}%, "
              f"orders {s['orders']}, today target {s['today_target']['weights'] if s['today_target'] else {}}")


if __name__ == "__main__":
    main()
