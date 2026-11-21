"""lab4 research runner. Historical simulation only: no keys, no orders.

    python3 -m lab4.run                 # development period only
    python3 -m lab4.run --holdout       # also the frozen holdout (run once)
"""
import argparse
import hashlib
import json
import subprocess
from datetime import date, timedelta
from functools import partial
from pathlib import Path

from . import data, intraday, metrics
from . import strategies as S
from .engine import Panel, simulate, DAY_MS

START = date(2017, 8, 1)
DEV = (date(2018, 4, 1), date(2023, 1, 1))
HOLD_START = date(2023, 1, 1)
# Last full month with complete spot, perp and funding archives (registered
# before the holdout run, so carry/short legs are not cut off mid-period).
HOLD_END = date(2026, 11, 1)
BASE_COST = 0.0015                  # 10 bps fee + 5 bps slippage per side
STRESS_COST = 0.0030
OUT = Path(__file__).resolve().parents[1] / "data" / "lab4" / "out"

GATES = {
    "cagr_vs_spy_pp": 0.05,         # CAGR >= SPY CAGR + 5 pp (each period)
    "sharpe_vs_spy": True,          # Sharpe >= SPY Sharpe (each period)
    "max_drawdown": 0.40,           # <= 40% (each period)
    "rolling182_positive": 0.70,    # each period
    "worst_year": -0.10,            # no calendar year below -10%
    "stress_cagr_positive": True,   # 2x costs still CAGR > 0 (each period)
    "holdout_bootstrap_p05": 0.0,   # holdout only
}


def build_panel(end):
    n = (end - START).days
    p = Panel(data._ms_of(START), n)
    for a in data.UNIVERSE_2019:
        p.add_asset(a, data.asset_daily(a, START, end))
    # Carry pseudo-assets: 0.5 capital long spot + 0.5 capital margin for a
    # 1x short perp. Period-k return per unit capital = 0.5*(spot-perp+funding).
    for a in ("BTC", "ETH"):
        sym = a + "USDT"
        perp = data.klines(sym, date(2019, 9, 1), end, "1d", market="um")
        fund = data.funding_rates(sym, date(2019, 9, 1), end)
        settle = {}
        for t, r in fund:
            tm = round(t / 60_000) * 60_000          # archive times carry ms jitter
            settle.setdefault(tm, 0.0)
            settle[tm] += r
        per, f_ser = [None] * n, [None] * n
        for k in range(n - 1):
            t0 = p.day_ms(k)
            pk, pk1 = perp.get(t0), perp.get(t0 + DAY_MS)
            so, so1 = p.o[a][k], p.o[a][k + 1]
            times = [t0 + h * 3_600_000 for h in (8, 16, 24)]
            if pk is None or pk1 is None or so is None or so1 is None:
                continue
            if not all(t in settle for t in times):
                continue
            f = sum(settle[t] for t in times)
            spot_r, perp_r = so1 / so - 1, pk1[0] / pk[0] - 1
            per[k] = 0.5 * (spot_r - perp_r + f)
            f_ser[k] = f
        p.extra["CARRY_" + a] = per
        p.extra["CARRY_" + a + "_FUND"] = f_ser
        # 1x short perp: -(perp return) + funding received by shorts.
        p.extra["SHORT_" + a] = [None if per[k] is None else
                                 -(perp[p.day_ms(k) + DAY_MS][0] / perp[p.day_ms(k)][0] - 1) + f_ser[k]
                                 for k in range(n)]
    return p


def daily_runner(fn):
    def run(p, a, b, cost):
        res = simulate(p, fn, a, b, cost=cost)
        return res["returns"], {"exposure": res["exposure"], "turnover": res["turnover"],
                                "orders_per_day": sum(res["orders"]) / len(res["orders"])}
    return run


def vbo_runner(assets, k=0.5, sma_days=20):
    def run(p, a, b, cost):
        fee, slip = cost * 2 / 3, cost / 3
        sleeves, trades = [], 0
        for asset in assets:
            r, t = intraday.vbo_day_returns(p, HOURLY[asset], asset, a, b, k=k, sma_days=sma_days, fee=fee, slip=slip)
            sleeves.append(r)
            trades += t
        rets = [sum(xs) / len(xs) for xs in zip(*sleeves)]
        return rets, {"orders_per_day": 2 * trades / (b - a)}
    return run


def rsi2_runner(asset="BTC", entry_below=10):
    def run(p, a, b, cost):
        r, t = intraday.hourly_rsi2_day_returns(p, HOURLY[asset], a, b, entry_below=entry_below,
                                                 fee=cost * 2 / 3, slip=cost / 3)
        return r, {"orders_per_day": t / (b - a)}
    return run


HOURLY = {}


def family():
    fam = {name: daily_runner(fn) for name, fn in S.REGISTERED.items()}
    fam["vbo_btc"] = vbo_runner(["BTC"])
    fam["vbo_btceth"] = vbo_runner(["BTC", "ETH"])
    fam["hourly_rsi2_btc"] = rsi2_runner()
    return fam


def neighbours():
    return {
        "xs_mom_top5": {
            "lookback14": daily_runner(partial(S.xs_mom_top, lookback=14)),
            "lookback56": daily_runner(partial(S.xs_mom_top, lookback=56)),
            "top3": daily_runner(partial(S.xs_mom_top, top=3)),
            "top7": daily_runner(partial(S.xs_mom_top, top=7)),
        },
        "tsmom_universe_vt": {
            "sma50": daily_runner(partial(S.tsmom_universe_vt, sma=50)),
            "sma200": daily_runner(partial(S.tsmom_universe_vt, sma=200)),
        },
        "btceth_trend_vt": {
            "sma20_100": daily_runner(partial(S.btceth_trend_vt, fast=20, slow=100)),
            "sma100_200": daily_runner(partial(S.btceth_trend_vt, fast=100, slow=200)),
        },
        "funding_carry": {
            "thr0": daily_runner(partial(S.funding_carry, threshold=0.0)),
            "thr10": daily_runner(partial(S.funding_carry, threshold=0.10)),
            "days3": daily_runner(partial(S.funding_carry, days=3)),
            "days14": daily_runner(partial(S.funding_carry, days=14)),
        },
        "vbo_btceth": {
            "k0.3": vbo_runner(["BTC", "ETH"], k=0.3),
            "k0.7": vbo_runner(["BTC", "ETH"], k=0.7),
            "sma10": vbo_runner(["BTC", "ETH"], sma_days=10),
            "sma40": vbo_runner(["BTC", "ETH"], sma_days=40),
        },
        "hourly_rsi2_btc": {
            "entry5": rsi2_runner(entry_below=5),
            "entry20": rsi2_runner(entry_below=20),
        },
    }


def regime_attribution(p, rets, a):
    cells = {}
    for i, r in enumerate(rets):
        reg = S.btc_regime(p, a + i - 1)
        key = "|".join(reg) if reg else "unknown"
        cells.setdefault(key, []).append(r)
    return {k: {"days": len(v), "ann_mean": sum(v) / len(v) * 365} for k, v in sorted(cells.items())}


def evaluate(name, runner, p, a, b, spy, bootstrap=False):
    rets, extra = runner(p, a, b, BASE_COST)
    stress, _ = runner(p, a, b, STRESS_COST)
    first = p.date(a)
    m = metrics.summary(rets, first, extra.get("exposure"), extra.get("turnover"))
    m["orders_per_day"] = extra.get("orders_per_day")
    m["stress_cagr"] = metrics.cagr(stress)
    m["regimes"] = regime_attribution(p, rets, a)
    if bootstrap:
        m["bootstrap_p05_ann_mean"] = metrics.stationary_bootstrap_p05(rets)
    if spy:
        m["excess_cagr_vs_spy"] = m["cagr"] - spy["cagr"]
    return m, rets


def gate_checks(m, spy, holdout):
    c = {
        "cagr_vs_spy": m["cagr"] >= spy["cagr"] + GATES["cagr_vs_spy_pp"],
        "sharpe_vs_spy": m["sharpe"] >= spy["sharpe"],
        "max_drawdown": m["max_drawdown"] <= GATES["max_drawdown"],
        "rolling182": (m["rolling182_positive"] or 0) >= GATES["rolling182_positive"],
        "worst_year": m["worst_year"] >= GATES["worst_year"],
        "stress_cagr": m["stress_cagr"] > 0,
    }
    if holdout:
        bp = m.get("bootstrap_p05_ann_mean")
        c["bootstrap_p05"] = bp is not None and bp > GATES["holdout_bootstrap_p05"]
    return c


def learn_regime_map(p, dev_returns, a):
    """Pick, per regime cell, the daily strategy with the best mean dev return
    (>0, >=60 days); otherwise cash. Dev-only; frozen for the holdout."""
    # Candidates are the ORIGINAL registered family only (fixed before results).
    candidates = [n for n in S.REGISTERED if n != "regime_switch" and n not in S.REGISTERED_V2]
    cells = {}
    for name in candidates:
        for i, r in enumerate(dev_returns[name]):
            reg = S.btc_regime(p, a + i - 1)
            if reg:
                cells.setdefault("|".join(reg), {}).setdefault(name, []).append(r)
    mapping = {}
    for cell, by in sorted(cells.items()):
        best, best_mean = None, 0.0
        for name, xs in by.items():
            mean = sum(xs) / len(xs)
            if len(xs) >= 60 and mean > best_mean:
                best, best_mean = name, mean
        mapping[cell] = best
    return mapping


def source_hash():
    h = hashlib.sha256()
    for f in sorted(Path(__file__).parent.glob("*.py")):
        h.update(f.name.encode() + f.read_bytes())
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true")
    ap.add_argument("--end", default=None, help="exclusive end date (default: today UTC)")
    ap.add_argument("--skip-neighbours", action="store_true")
    args = ap.parse_args()
    end = date.fromisoformat(args.end) if args.end else date.today()
    p = build_panel(end)
    for a in ("BTC", "ETH"):
        HOURLY[a] = data.klines(a + "USDT", START, end, "1h")
    spy_px = data.spy_total_return(date(2017, 12, 1), end)
    last = p.n - 1                                  # last period needs next open
    periods = {"development": (p.index_of(DEV[0]), p.index_of(DEV[1]))}
    if args.holdout:
        periods["holdout"] = (p.index_of(HOLD_START), min(last, p.index_of(HOLD_END)))
    git = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    report = {"type": "HISTORICAL_SIMULATION_ONLY", "git_head": git, "source_sha256": source_hash(),
              "base_cost_per_side": BASE_COST, "stress_cost_per_side": STRESS_COST,
              "gates": GATES, "periods": {}, "strategies": {}, "benchmarks": {}}
    fam = family()
    bench = {n: daily_runner(fn) for n, fn in S.BENCHMARKS.items()}
    dev_returns = {}
    for pname, (a, b) in periods.items():
        spy = metrics.spy_summary(spy_px, p.date(a), p.date(b))
        report["periods"][pname] = {"start": p.date(a).isoformat(), "end_exclusive": p.date(b).isoformat(),
                                    "days": b - a, "spy": spy}
        for name, runner in bench.items():
            m, _ = evaluate(name, runner, p, a, b, spy)
            report["benchmarks"].setdefault(name, {})[pname] = m
        for name, runner in fam.items():
            m, rets = evaluate(name, runner, p, a, b, spy, bootstrap=(pname == "holdout"))
            m["gates"] = gate_checks(m, spy, pname == "holdout")
            report["strategies"].setdefault(name, {})[pname] = m
            if pname == "development":
                dev_returns[name] = rets
            print(f"{pname:11s} {name:20s} CAGR {m['cagr']*100:7.1f}%  Sharpe {m['sharpe']:5.2f}  "
                  f"MDD {m['max_drawdown']*100:5.1f}%  stress {m['stress_cagr']*100:6.1f}%  "
                  f"orders/day {m['orders_per_day'] or 0:4.2f}", flush=True)
        if pname == "development":
            mapping = learn_regime_map(p, dev_returns, a)
            report["regime_learned_map"] = mapping
            fam["regime_learned"] = daily_runner(S.make_regime_learned(mapping))
            m, _ = evaluate("regime_learned", fam["regime_learned"], p, a, b, spy)
            m["gates"] = gate_checks(m, spy, False)
            m["note"] = "IN-SAMPLE: mapping was learned on this period"
            report["strategies"].setdefault("regime_learned", {})[pname] = m
            print("regime map:", mapping, f"in-sample CAGR {m['cagr']*100:.1f}%", flush=True)
            if not args.skip_neighbours:
                report["neighbours"] = {}
                for base, variants in neighbours().items():
                    for v, runner in variants.items():
                        rets, _ = runner(p, a, b, BASE_COST)
                        report["neighbours"].setdefault(base, {})[v] = {
                            "cagr": metrics.cagr(rets), "sharpe": metrics.sharpe(rets),
                            "max_drawdown": metrics.max_drawdown(rets)}
    for name, by in report["strategies"].items():
        checks = [all(m["gates"].values()) for m in by.values()]
        by["passes_all_gates"] = bool(checks) and all(checks) and "holdout" in by
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / ("report-holdout.json" if args.holdout else "report-dev.json")
    out.write_text(json.dumps(report, indent=1, default=str))
    print("wrote", out)


if __name__ == "__main__":
    main()
