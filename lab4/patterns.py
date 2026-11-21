"""Pattern discovery with multiple-testing control. Historical research only.

    python3 -m lab4.patterns

Three families, all evaluated on the DISCOVERY period (2018-04 – 2022-12),
its two halves, and then reported on 2023-01 – 2026-10 (validation):

  XS  - cross-sectional: does feature rank predict which coins beat the others
        over the next h days? Statistic: mean Spearman IC per date (Fama-MacBeth),
        non-overlapping dates (every h-th day on the Tuesday grid for h>1).
  TS  - market timing on BTC: does BTC's own feature predict BTC's next-h-day
        return? Spearman correlation over non-overlapping dates.
  CAL - calendar: BTC day-of-week and UTC hour-of-day mean returns.

Benjamini-Hochberg FDR (q = 5%) over ALL tests, plus a stability rule: same
sign and |t| >= 1 in both discovery halves. Survivors are hypotheses, not edges.
"""
import json
import math
from datetime import date
from pathlib import Path

from . import data, features
from . import run as R

H = (1, 7, 28)
HALF_SPLIT = date(2020, 7, 1)
VALID = (date(2023, 1, 1), date(2026, 11, 1))
OUT = Path(__file__).resolve().parents[1] / "data" / "lab4" / "out"


def rank(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2.0
        i = j + 1
    return r


def spearman(x, y):
    if len(x) < 3:
        return None
    rx, ry = rank(x), rank(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    sy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if sx == 0 or sy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / (sx * sy)


def t_of(xs):
    n = len(xs)
    if n < 5:
        return None, None, n
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return m, (m / sd * math.sqrt(n) if sd > 0 else 0.0), n


def p_two_sided(t):
    return math.erfc(abs(t) / math.sqrt(2))


def fwd(p, a, t, h):
    o = p.o[a]
    if t + 1 + h < p.n and o[t + 1] is not None and o[t + 1 + h] is not None:
        return o[t + 1 + h] / o[t + 1] - 1
    return None


def grid(p, a, b, h):
    """Decision dates in [a, b) whose forward window ends before b."""
    if h == 1:
        ks = range(a, b)
    else:
        first = next(k for k in range(a, a + 7) if (p.start_ms // 86_400_000 + k - 17379) % 7 == 0)
        ks = range(first, b, h)
    return [k for k in ks if k + 1 + h <= b]


def xs_series(p, F, feat, h, ks):
    ics, spreads = [], []
    for t in ks:
        xs, ys = [], []
        for a in p.assets:
            if p.age(a)[t] < 120:
                continue
            x = F[a][feat][t]
            y = fwd(p, a, t, h)
            if x is not None and y is not None:
                xs.append(x)
                ys.append(y)
        if len(xs) >= 8:
            ic = spearman(xs, ys)
            if ic is not None:
                ics.append(ic)
                top = sorted(zip(xs, ys), reverse=True)[:3]
                spreads.append(sum(y for _, y in top) / 3 - sum(ys) / len(ys))
    return ics, spreads


def ts_series(p, F, feat, h, ks):
    xs, ys = [], []
    for t in ks:
        x, y = F["BTC"][feat][t], fwd(p, "BTC", t, h)
        if x is not None and y is not None:
            xs.append(x)
            ys.append(y)
    return xs, ys


def ts_stat(xs, ys):
    rho = spearman(xs, ys)
    n = len(xs)
    if rho is None or n < 10 or abs(rho) >= 1:
        return None, None, n
    return rho, rho * math.sqrt((n - 2) / (1 - rho * rho)), n


def bh(pvals, q=0.05):
    """Benjamini-Hochberg: returns set of indices rejected."""
    order = sorted(range(len(pvals)), key=lambda i: pvals[i])
    m, cutoff = len(pvals), -1
    for rank_i, i in enumerate(order, 1):
        if pvals[i] <= q * rank_i / m:
            cutoff = rank_i
    return set(order[:cutoff]) if cutoff > 0 else set()


def calendar_tests(p, hourly, a, b):
    """BTC day-of-week (open->open) and UTC hour-of-day returns vs the rest."""
    out = []
    daily = [(k, R_open(p, k)) for k in range(a, b)]
    daily = [(k, r) for k, r in daily if r is not None]
    for wd in range(7):
        ins = [r for k, r in daily if p.date(k).weekday() == wd]
        outs = [r for k, r in daily if p.date(k).weekday() != wd]
        out.append(("CAL", f"weekday_{wd}", 1, welch(ins, outs)))
    t0, t1 = p.day_ms(a), p.day_ms(b)
    hrs = {}
    for t, bar in hourly.items():
        if t0 <= t < t1:
            hrs.setdefault((t // 3_600_000) % 24, []).append(bar[3] / bar[0] - 1)
    allr = [x for v in hrs.values() for x in v]
    for hh in range(24):
        ins = hrs.get(hh, [])
        rest_n = len(allr) - len(ins)
        if rest_n <= 0:
            continue
        out.append(("CAL", f"hour_{hh:02d}", 0, welch_fast(ins, allr)))
    return out


def R_open(p, k):
    o = p.o["BTC"]
    if k + 1 < p.n and o[k] is not None and o[k + 1] is not None:
        return o[k + 1] / o[k] - 1
    return None


def welch(a, b):
    if len(a) < 10 or len(b) < 10:
        return None
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1)
    vb = sum((x - mb) ** 2 for x in b) / (len(b) - 1)
    se = math.sqrt(va / len(a) + vb / len(b))
    return {"stat": ma - mb, "t": (ma - mb) / se if se > 0 else 0.0, "n": len(a)}


def welch_fast(ins, allr):
    """Group vs all-others using totals (hour-of-day has ~40k observations)."""
    n_all, s_all = len(allr), sum(allr)
    ss_all = sum(x * x for x in allr)
    n1, s1, ss1 = len(ins), sum(ins), sum(x * x for x in ins)
    n2, s2, ss2 = n_all - n1, s_all - s1, ss_all - ss1
    if n1 < 10 or n2 < 10:
        return None
    m1, m2 = s1 / n1, s2 / n2
    v1, v2 = (ss1 - n1 * m1 * m1) / (n1 - 1), (ss2 - n2 * m2 * m2) / (n2 - 1)
    se = math.sqrt(v1 / n1 + v2 / n2)
    return {"stat": m1 - m2, "t": (m1 - m2) / se if se > 0 else 0.0, "n": n1}


def evaluate_period(p, F, hourly, a, b):
    res = {}
    for feat in features.FEATURES:
        for h in H:
            ks = grid(p, a, b, h)
            ics, spreads = xs_series(p, F, feat, h, ks)
            m, t, n = t_of(ics)
            if m is not None:
                res[("XS", feat, h)] = {"stat": m, "t": t, "n": n,
                                        "top3_minus_mean": sum(spreads) / len(spreads)}
            if feat in features.MARKET_RELATIVE:
                continue
            xs, ys = ts_series(p, F, feat, h, ks)
            rho, t, n = ts_stat(xs, ys)
            if rho is not None:
                res[("TS", feat, h)] = {"stat": rho, "t": t, "n": n}
    for fam, name, h, st in calendar_tests(p, hourly, a, b):
        if st:
            res[(fam, name, h)] = st
    return res


def backtest():
    """Evaluate the frozen pattern strategy exactly like the lab4 family."""
    from . import metrics
    from . import pattern_strategy as PS
    from . import strategies as S
    end = date(2026, 11, 21)
    p = R.build_panel(end)
    spy_px = data.spy_total_return(date(2017, 12, 1), end)
    out = {"pattern_set": PS.PATTERN_SET, "strategies": {}}
    runners = {"pattern_top5_vt": R.daily_runner(PS.pattern_top5_vt),
               "xs_mom_top5_vt": R.daily_runner(S.xs_mom_top5_vt)}
    periods = {"development": (p.index_of(R.DEV[0]), p.index_of(R.DEV[1])),
               "holdout": (p.index_of(R.HOLD_START), p.index_of(R.HOLD_END))}
    for pname, (a, b) in periods.items():
        spy = metrics.spy_summary(spy_px, p.date(a), p.date(b))
        out.setdefault("spy", {})[pname] = spy
        for name, runner in runners.items():
            m, _ = R.evaluate(name, runner, p, a, b, spy, bootstrap=(pname == "holdout"))
            m["gates"] = R.gate_checks(m, spy, pname == "holdout")
            out["strategies"].setdefault(name, {})[pname] = m
            print(f"{pname:11s} {name:16s} CAGR {m['cagr']*100:6.1f}% Sharpe {m['sharpe']:4.2f} "
                  f"MDD {m['max_drawdown']*100:5.1f}% worstY {m['worst_year']*100:6.1f}% "
                  f"roll+ {m['rolling182_positive']:.2f} stress {m['stress_cagr']*100:5.1f}% "
                  f"orders/day {m['orders_per_day']:.2f} | SPY {spy['cagr']*100:.1f}%/{spy['sharpe']:.2f} "
                  f"FAIL {[k for k, v in m['gates'].items() if not v]}", flush=True)
    (OUT / "pattern-backtest.json").write_text(json.dumps(out, indent=1, default=str))


def main():
    import sys
    if "--backtest" in sys.argv:
        return backtest()
    end = date(2026, 11, 21)
    p = R.build_panel(end)
    features.attach_orderflow(p, R.START, end)
    F = features.all_features(p)
    hourly = data.klines("BTCUSDT", R.START, end, "1h")
    periods = {
        "discovery": (p.index_of(R.DEV[0]), p.index_of(R.DEV[1])),
        "half_a": (p.index_of(R.DEV[0]), p.index_of(HALF_SPLIT)),
        "half_b": (p.index_of(HALF_SPLIT), p.index_of(R.DEV[1])),
        "validation": (p.index_of(VALID[0]), p.index_of(VALID[1])),
    }
    stats = {name: evaluate_period(p, F, hourly, a, b) for name, (a, b) in periods.items()}
    keys = sorted(stats["discovery"])
    pvals = [p_two_sided(stats["discovery"][k]["t"]) for k in keys]
    rejected = bh(pvals)
    rows = []
    for i, k in enumerate(keys):
        d = stats["discovery"][k]
        ha, hb = stats["half_a"].get(k), stats["half_b"].get(k)
        v = stats["validation"].get(k)
        stable = bool(ha and hb and ha["t"] * d["t"] > 0 and hb["t"] * d["t"] > 0
                      and abs(ha["t"]) >= 1 and abs(hb["t"]) >= 1)
        rows.append({
            "family": k[0], "feature": k[1], "horizon_days": k[2],
            "discovery": d, "p": pvals[i], "fdr_pass": i in rejected, "stable_halves": stable,
            "survivor": (i in rejected) and stable,
            "half_a_t": ha["t"] if ha else None, "half_b_t": hb["t"] if hb else None,
            "validation": v,
            "validation_same_sign": bool(v and v["t"] * d["t"] > 0),
        })
    rows.sort(key=lambda r: r["p"])
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "patterns.json").write_text(json.dumps({
        "tests": len(rows), "fdr_q": 0.05, "periods": {k: [p.date(a).isoformat(), p.date(b).isoformat()]
                                                       for k, (a, b) in periods.items()},
        "rows": rows}, indent=1))
    surv = [r for r in rows if r["survivor"]]
    print(f"tests {len(rows)}, FDR pass {sum(r['fdr_pass'] for r in rows)}, survivors {len(surv)}")
    for r in surv:
        v = r["validation"] or {}
        print(f"{r['family']:3s} {r['feature']:22s} h={r['horizon_days']:2d} disc stat {r['discovery']['stat']:+.4f} "
              f"t {r['discovery']['t']:+6.2f} | halves {r['half_a_t']:+.1f}/{r['half_b_t']:+.1f} | "
              f"valid stat {v.get('stat', float('nan')):+.4f} t {v.get('t', float('nan')):+6.2f}"
              + (f" | top3-mean {r['discovery'].get('top3_minus_mean', 0)*100:+.2f}%" if r['family'] == 'XS' else ""))


if __name__ == "__main__":
    main()
