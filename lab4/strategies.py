"""Registered lab4 hypotheses. ``f(panel, t)`` may only read index <= t."""
import math

MIN_AGE = 120          # asset becomes tradable on its 120th available day
VOL_TARGET = 0.40      # annualised portfolio volatility target
CARRY_ASSETS = ("CARRY_BTC", "CARRY_ETH")
SHORT_ASSETS = ("SHORT_BTC", "SHORT_ETH")   # 1x short perp, capital = margin


# Rebalance grids are anchored to absolute UTC dates, not to the panel start,
# so a forward/paper panel that starts later rebalances on the same days as
# the backtest. Phase = 2017-08-01 (a Tuesday): weekly decisions are taken at
# the close of each Tuesday (UTC), exactly as in the frozen research runs.
GRID_EPOCH_DAY = 17379  # days from 1970-01-01 to 2017-08-01


def _grid_anchor(p, t, period):
    day = p.start_ms // 86_400_000 + t
    return t - ((day - GRID_EPOCH_DAY) % period)


def _tradable(p, a, t):
    return (a in p.c and p.c[a][t] is not None and p.age(a)[t] >= MIN_AGE)


def _btc_risk_on(p, t, n=100):
    s = p.sma("BTC", n)[t]
    return s is not None and p.c["BTC"][t] > s


def _vol_scale(p, weights, t, target=VOL_TARGET, n=30):
    """Scale weights so a correlation-agnostic (sum w*vol) estimate hits target.

    Summing individual vols overstates portfolio vol (assumes correlation 1),
    which errs on the conservative side. Never scales above gross 1.0.
    """
    if not weights:
        return {}
    est = sum(w * (p.vol(a, n)[t] or 1.0) for a, w in weights.items())
    k = min(1.0 / sum(weights.values()), target / est) if est > 0 else 0
    return {a: w * k for a, w in weights.items()}


def xs_mom_top(p, t, lookback=28, top=5, rebalance=7, market_sma=100):
    # Rebalance on a fixed weekly grid; between rebalances hold the last pick.
    anchor = _grid_anchor(p, t, rebalance)
    key = ("xs", lookback, top, rebalance, market_sma, anchor)

    def pick():
        if not _btc_risk_on(p, anchor, market_sma):
            return {}
        scored = []
        for a in p.assets:
            if a in CARRY_ASSETS or not _tradable(p, a, anchor):
                continue
            r = p.ret(a, lookback)[anchor]
            if r is not None:
                scored.append((r, a))
        scored.sort(reverse=True)
        chosen = [a for r, a in scored[:top] if r > 0]
        return {a: 1.0 / top for a in chosen}

    w = p.memo(key, pick)
    # Exit early if an asset stopped trading since the anchor.
    return {a: x for a, x in w.items() if p.c[a][t] is not None}


def xs_mom_top5(p, t):
    return xs_mom_top(p, t)


def xs_mom_top5_vt(p, t):
    w = xs_mom_top(p, t)
    if not w:
        return {}
    w = {a: 1.0 / len(w) for a in w}
    return _vol_scale(p, w, t)


def _mom_vote(p, a, t, lbs=(20, 60, 120)):
    votes = [p.ret(a, n)[t] for n in lbs]
    if any(v is None for v in votes):
        return False
    return sum(v > 0 for v in votes) >= 2


def _inv_vol(p, names, t, n=30):
    inv = {a: 1.0 / max(p.vol(a, n)[t] or 1.0, 0.05) for a in names}
    s = sum(inv.values())
    return {a: x / s for a, x in inv.items()} if s else {}


def tsmom_universe_vt(p, t, sma=100):
    names = []
    for a in p.assets:
        if a in CARRY_ASSETS or not _tradable(p, a, t):
            continue
        s = p.sma(a, sma)[t]
        if s is not None and p.c[a][t] > s and _mom_vote(p, a, t):
            names.append(a)
    return _vol_scale(p, _inv_vol(p, names, t), t)


def btceth_trend_vt(p, t, fast=50, slow=200):
    names = []
    for a in ("BTC", "ETH"):
        f, s = p.sma(a, fast)[t], p.sma(a, slow)[t]
        if f is not None and s is not None and p.c[a][t] > f > s:
            names.append(a)
    return _vol_scale(p, _inv_vol(p, names, t), t)


def funding_carry(p, t, threshold=0.05, days=7):
    out = []
    for a in CARRY_ASSETS:
        fund = p.extra.get(a + "_FUND")
        if fund is None or t - days + 1 < 0:
            continue
        xs = fund[t - days + 1:t + 1]
        if any(x is None for x in xs):
            continue
        annual = sum(xs) / days * 365
        if annual > threshold:
            out.append(a)
    return {a: 1.0 / len(CARRY_ASSETS) for a in out}


def carry_trend_blend(p, t):
    w = {a: x * 0.5 for a, x in funding_carry(p, t).items()}
    for a, x in btceth_trend_vt(p, t).items():
        w[a] = w.get(a, 0) + 0.5 * x
    return w


# -- dev iteration v2 (added after development results, before any holdout) --

def btceth_trend_ls_vt(p, t, fast=50, slow=200):
    """Long BTC/ETH in an up-trend (close > SMA50 > SMA200), 1x short perp in a
    down-trend (close < SMA50 < SMA200), cash otherwise; inverse-vol, 40% vol
    target. Shorts exist only where perp + funding data exist (2019-09+)."""
    longs, shorts = [], []
    for a in ("BTC", "ETH"):
        f, s, c = p.sma(a, fast)[t], p.sma(a, slow)[t], p.c[a][t]
        if f is None or s is None or c is None:
            continue
        if c > f > s:
            longs.append(a)
        elif c < f < s and p.extra.get("CARRY_" + a + "_FUND", [None] * (t + 1))[t] is not None:
            shorts.append(a)
    w = _inv_vol(p, longs + shorts, t)
    w = _vol_scale(p, w, t)
    return {("SHORT_" + a if a in shorts else a): x for a, x in w.items()}


def _with_carry(p, t, w):
    free = max(0.0, 1.0 - sum(w.values()))
    out = dict(w)
    for a in funding_carry(p, t):
        out[a] = out.get(a, 0) + free / len(CARRY_ASSETS)
    return out


def allweather_trend_carry(p, t):
    """Long/short BTC/ETH trend; all idle capital in funding carry when on."""
    return _with_carry(p, t, btceth_trend_ls_vt(p, t))


def core_blend(p, t):
    """Fixed capital split: 50% cross-sectional momentum (vol-targeted),
    50% all-weather trend+carry. No selection, no fitted weights."""
    w = {}
    for part in (xs_mom_top5_vt(p, t), allweather_trend_carry(p, t)):
        for a, x in part.items():
            w[a] = w.get(a, 0) + 0.5 * x
    return w


# -- dev iteration v3 (shorts hurt in development; long-only + carry) ---------

def trend_carry_overlay(p, t):
    """Long-only BTC/ETH trend sleeve; all idle capital in funding carry."""
    return _with_carry(p, t, btceth_trend_vt(p, t))


def core_blend_lo(p, t):
    """50% vol-targeted cross-sectional momentum + 50% trend_carry_overlay."""
    w = {}
    for part in (xs_mom_top5_vt(p, t), trend_carry_overlay(p, t)):
        for a, x in part.items():
            w[a] = w.get(a, 0) + 0.5 * x
    return w


# -- regime recognition ------------------------------------------------------

def btc_regime(p, t):
    """(trend, vol) state of BTC known at the close of day t, or None.

    trend: bull = close > SMA200 and SMA50 > SMA200; bear = close < SMA200 and
    SMA50 < SMA200; otherwise transition. vol: 'hi' when 30-day realised vol is
    above its trailing 365-day median (computed from past values only).
    """
    c = p.c["BTC"][t]
    s50, s200 = p.sma("BTC", 50)[t], p.sma("BTC", 200)[t]
    if c is None or s50 is None or s200 is None:
        return None
    if c > s200 and s50 > s200:
        trend = "bull"
    elif c < s200 and s50 < s200:
        trend = "bear"
    else:
        trend = "transition"

    def med_build():
        v = p.vol("BTC", 30)
        out = [None] * p.n
        for k in range(p.n):
            xs = [x for x in v[max(0, k - 364):k + 1] if x is not None]
            if len(xs) >= 180:
                xs.sort()
                out[k] = xs[len(xs) // 2]
        return out

    med = p.memo(("vol_median", "BTC"), med_build)[t]
    v = p.vol("BTC", 30)[t]
    if med is None or v is None:
        return None
    return trend, ("hi" if v > med else "lo")


def regime_switch(p, t):
    """Fixed economic mapping (registered, not fitted):
    bull -> diversified vol-targeted trend sleeve; any regime -> idle capital
    goes to BTC/ETH funding carry while trailing funding > 5%/yr; else cash."""
    reg = btc_regime(p, t)
    w = dict(tsmom_universe_vt(p, t)) if reg and reg[0] == "bull" else {}
    free = max(0.0, 1.0 - sum(w.values()))
    for a in funding_carry(p, t):
        w[a] = w.get(a, 0) + free / len(CARRY_ASSETS)
    return w


def make_regime_learned(mapping):
    """Strategy that follows ``mapping[(trend, vol)] -> strategy id`` (None=cash).
    The mapping must be learned on development data only and then frozen."""
    def regime_learned(p, t):
        reg = btc_regime(p, t)
        name = mapping.get("|".join(reg)) if reg else None
        return dict(REGISTERED[name](p, t)) if name else {}
    return regime_learned


# -- cross-sectional ridge ranker ("complex" model) ---------------------------

RIDGE_LAMBDA = 10.0
RIDGE_FEATURES = ("r7", "r28", "r90", "vol30", "dist_sma100", "volume_trend")


def _features(p, a, t):
    if not _tradable(p, a, t):
        return None
    r7, r28, r90 = p.ret(a, 7)[t], p.ret(a, 28)[t], p.ret(a, 90)[t]
    v, s = p.vol(a, 30)[t], p.sma(a, 100)[t]
    qv = p.qv[a]
    if None in (r7, r28, r90, v, s) or any(x is None for x in qv[t - 89:t + 1]):
        return None
    vt = (sum(qv[t - 6:t + 1]) / 7) / (sum(qv[t - 89:t + 1]) / 90) - 1
    return [r7, r28, r90, v, p.c[a][t] / s - 1, vt]


def _cross_section(p, t):
    """z-scored features per asset at day t (only assets with full features)."""
    def build():
        rows = {}
        for a in p.assets:
            if a in CARRY_ASSETS:
                continue
            f = _features(p, a, t)
            if f is not None:
                rows[a] = f
        if len(rows) < 6:
            return {}
        cols = list(zip(*rows.values()))
        z = {}
        for a, f in rows.items():
            zz = []
            for j, x in enumerate(f):
                m = sum(cols[j]) / len(cols[j])
                sd = (sum((y - m) ** 2 for y in cols[j]) / len(cols[j])) ** 0.5
                zz.append((x - m) / sd if sd > 0 else 0.0)
            z[a] = zz
        return z
    return p.memo(("xsz", t), build)


def _solve(A, b):
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for i in range(n):
        piv = max(range(i, n), key=lambda r: abs(M[r][i]))
        M[i], M[piv] = M[piv], M[i]
        for r in range(n):
            if r != i and M[i][i] != 0:
                f = M[r][i] / M[i][i]
                M[r] = [x - f * y for x, y in zip(M[r], M[i])]
    return [M[i][n] / M[i][i] if M[i][i] else 0.0 for i in range(n)]


def _ridge_coef(p, anchor, rebalance=7, horizon=7):
    """Fit on weekly samples s with matured labels: open(s+1)->open(s+1+h)
    must be known at the decision close of ``anchor`` => s + 1 + h <= anchor."""
    def build():
        nf = len(RIDGE_FEATURES)
        A = [[0.0] * nf for _ in range(nf)]
        b = [0.0] * nf
        n = 0
        for s in range(anchor % rebalance, anchor - horizon, rebalance):  # same phase as anchor
            if s + 1 + horizon > anchor:
                break
            z = _cross_section(p, s)
            labels = {}
            for a in z:
                o1, o2 = p.o[a][s + 1], p.o[a][s + 1 + horizon]
                if o1 and o2:
                    labels[a] = o2 / o1 - 1
            if len(labels) < 6:
                continue
            mu = sum(labels.values()) / len(labels)
            for a, y in labels.items():
                x = z[a]
                for i in range(nf):
                    b[i] += x[i] * (y - mu)
                    for j in range(nf):
                        A[i][j] += x[i] * x[j]
                n += 1
        if n < 300:
            return None
        for i in range(nf):
            A[i][i] += RIDGE_LAMBDA
        return _solve(A, b)
    return p.memo(("ridge", anchor), build)


def xs_ridge_rank(p, t, top=5, rebalance=7, market_sma=100):
    anchor = _grid_anchor(p, t, rebalance)

    def pick():
        if not _btc_risk_on(p, anchor, market_sma):
            return {}
        coef = _ridge_coef(p, anchor)
        if coef is None:
            return {}
        z = _cross_section(p, anchor)
        scored = sorted(((sum(c * x for c, x in zip(coef, f)), a) for a, f in z.items()), reverse=True)
        chosen = [a for s, a in scored[:top] if s > 0]
        return {a: 1.0 / top for a in chosen}

    w = p.memo(("ridge_pick", top, rebalance, market_sma, anchor), pick)
    return {a: x for a, x in w.items() if p.c[a][t] is not None}


# -- benchmarks -------------------------------------------------------------

def bh_btc(p, t):
    return {"BTC": 1.0}


def bh_btceth(p, t):
    return {"BTC": 0.5, "ETH": 0.5}


def ew_universe(p, t):
    anchor = _grid_anchor(p, t, 30)
    names = p.memo(("ew", anchor), lambda: [
        a for a in p.assets if a not in CARRY_ASSETS and _tradable(p, a, anchor)])
    names = [a for a in names if p.c[a][t] is not None]
    return {a: 1.0 / len(names) for a in names} if names else {}


REGISTERED = {
    "xs_mom_top5": xs_mom_top5,
    "xs_mom_top5_vt": xs_mom_top5_vt,
    "tsmom_universe_vt": tsmom_universe_vt,
    "btceth_trend_vt": btceth_trend_vt,
    "funding_carry": funding_carry,
    "carry_trend_blend": carry_trend_blend,
    "regime_switch": regime_switch,
    "xs_ridge_rank": xs_ridge_rank,
}
REGISTERED_V2 = {
    "btceth_trend_ls_vt": btceth_trend_ls_vt,
    "allweather_trend_carry": allweather_trend_carry,
    "core_blend": core_blend,
    "trend_carry_overlay": trend_carry_overlay,
    "core_blend_lo": core_blend_lo,
}
REGISTERED.update(REGISTERED_V2)
BENCHMARKS = {"bh_btc": bh_btc, "bh_btceth": bh_btceth, "ew_universe": ew_universe}
