"""Strategy built from the patterns that survived lab4.patterns discovery.

The feature list and signs below were fixed from DISCOVERY data only
(2018-04 – 2022-12, FDR q=5% + same sign in both halves); see
lab4/PATTERNS.md. No weights are fitted: each surviving cross-sectional
feature contributes its centred cross-sectional rank with the discovered
sign, equally weighted. Chassis identical to xs_mom_top5_vt (weekly Tuesday
grid, top 5, BTC > SMA100 market filter, 40% vol target, no leverage), so the
comparison isolates the ranking signal.
"""
from . import features
from . import strategies as S

# (feature, sign): every feature with at least one DISCOVERY survivor at a
# weekly-or-longer horizon (h = 7 or 28 days), sign = sign of its discovery IC.
# Fixed 2026-11-21 from data/lab4/out/patterns.json before any backtest of it.
PATTERN_SET = (
    ("idio_vol_90", -1),   # low idiosyncratic volatility vs BTC outperforms
    ("corr_btc_90", +1),   # coins moving with BTC outperform
    ("atr_14", -1),        # low recent true range outperforms
    ("drawdown_90", +1),   # close to the 90-day high outperforms
    ("log_qv_30", +1),     # liquid (high quote volume) coins outperform
)


def composite_scores(p, t, pattern_set=PATTERN_SET):
    F = features.all_features(p)
    cols = {}
    for feat, sign in pattern_set:
        vals = {}
        for a in p.assets:
            if not S._tradable(p, a, t):
                continue
            x = F[a][feat][t]
            if x is not None:
                vals[a] = x
        if len(vals) < 6:
            return {}
        names = sorted(vals, key=lambda a: vals[a])
        n = len(names)
        cols[feat] = {a: sign * (i / (n - 1) - 0.5) for i, a in enumerate(names)}
    common = set.intersection(*(set(c) for c in cols.values())) if cols else set()
    return {a: sum(c[a] for c in cols.values()) / len(cols) for a in common}


def pattern_top5_vt(p, t, top=5, rebalance=7, market_sma=100, pattern_set=PATTERN_SET):
    anchor = S._grid_anchor(p, t, rebalance)

    def pick():
        if not pattern_set or not S._btc_risk_on(p, anchor, market_sma):
            return {}
        sc = composite_scores(p, anchor, pattern_set)
        ranked = sorted(((s, a) for a, s in sc.items()), reverse=True)
        chosen = [a for s, a in ranked[:top] if s > 0]
        return {a: 1.0 / len(chosen) for a in chosen} if chosen else {}

    w = p.memo(("pattern_pick", tuple(pattern_set), top, rebalance, market_sma, anchor), pick)
    w = {a: x for a, x in w.items() if p.c[a][t] is not None}
    return S._vol_scale(p, w, t) if w else {}
