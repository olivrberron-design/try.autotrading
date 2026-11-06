"""Second fixed batch: matured-label regression and causal expert portfolios.

Rules below are chosen before running this batch's historical performance.
All outputs are CLOSED-day target weights for a later-open execution engine.
No sample-wide fit, future label, order endpoint, credentials, or dependency.
"""
from datetime import datetime, timezone
from math import exp, log1p, sqrt

from .research_strategies import (
    _features, _validate_series, MAX_ALLOCATION, WARMUP_BARS,
)

_RIDGE_FEATURE_DESCRIPTION = [
    "log(close/close21ago)/(30day_daily_vol_floor0.005*sqrt21), clipped +/-3",
    "log(close/close63ago)/(30day_daily_vol_floor0.005*sqrt63), clipped +/-3",
    "log(close/close126ago)/(30day_daily_vol_floor0.005*sqrt126), clipped +/-3",
    "log(close/SMA200)/max(30day_daily_vol*sqrt100,0.01), clipped +/-3",
    "20day_absolute_net_change/sum_absolute_daily_changes",
]

MODEL_SPECS = {
    "pooled_ridge_7d": {
        "description": "Monthly pooled cross-asset ridge forecasts next-open to seven-days-later-open simple return. Equal sleeve is active when forecast exceeds 64bps. Matured labels only.",
        "parameters": {"horizon_days": 7, "training_feature_days": 730,
                       "minimum_matured_feature_days": 126, "ridge_l2": 10.0,
                       "label_clip": [-0.5, 0.5], "return_hurdle": 0.0064,
                       "retrain": "first available decision and first UTC calendar day of each new month",
                       "features": _RIDGE_FEATURE_DESCRIPTION,
                       "scaling": "feature means/std computed from that training window only"},
    },
    "pooled_ridge_21d": {
        "description": "Same fixed pooled ridge specification with a 21-day next-open holding-return label and 64bps entry hurdle.",
        "parameters": {"horizon_days": 21, "training_feature_days": 730,
                       "minimum_matured_feature_days": 126, "ridge_l2": 10.0,
                       "label_clip": [-0.5, 0.5], "return_hurdle": 0.0064,
                       "retrain": "first available decision and first UTC calendar day of each new month",
                       "features": _RIDGE_FEATURE_DESCRIPTION,
                       "scaling": "feature means/std computed from that training window only"},
    },
    "btc_regime_equal": {
        "description": "Equal 20% aggregate asset sleeves only when BTC close>SMA200, SMA50>SMA200, and 63-day momentum>0; otherwise cash.",
        "parameters": {"fast_sma": 50, "slow_sma": 200, "momentum_days": 63},
    },
    "btc_regime_inverse_vol": {
        "description": "Same BTC regime gate; active assets share 20% in inverse trailing 30-day daily-return volatility proportions.",
        "parameters": {"fast_sma": 50, "slow_sma": 200, "momentum_days": 63,
                       "vol_days": 30, "daily_vol_floor": 0.005},
    },
    "online_expert_cash_blend": {
        "description": "Exponentially weighted 63-day momentum, 126-day momentum, 55/20-day breakout and cash experts. Scores update only on matured next-open returns, charging modeled 16bps one-way turnover; 126-day exponential forgetting and learning rate 10. Four experts start equal.",
        "parameters": {"experts": ["momentum63", "momentum126", "breakout55_20", "cash"],
                       "forgetting_days": 126, "learning_rate": 10.0,
                       "expert_turnover_cost_bps": 16.0, "label_delay_days": 2},
    },
    "conservative_horizon_blend": {
        "description": "Equal blend of fixed 63-day momentum, 126-day momentum and 55/20-day breakout experts; each expert has equal asset sleeves and at most 20% aggregate exposure.",
        "parameters": {"experts": ["momentum63", "momentum126", "breakout55_20"],
                       "expert_weights": [1 / 3, 1 / 3, 1 / 3]},
    },
}


def _training_feature_indices(asof, horizon):
    """Feature j label uses open[j+1] -> open[j+h+1], known by asof close."""
    return range(max(199, asof - 730), asof - horizon)


def _solve(matrix, rhs):
    """Small dense linear system with partial pivoting, no external library."""
    n = len(rhs)
    a = [list(row) + [value] for row, value in zip(matrix, rhs)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(a[row][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise ValueError("Singular ridge system")
        a[col], a[pivot] = a[pivot], a[col]
        divisor = a[col][col]
        a[col] = [v / divisor for v in a[col]]
        for row in range(n):
            if row == col:
                continue
            factor = a[row][col]
            a[row] = [left - factor * right for left, right in zip(a[row], a[col])]
    return [a[i][-1] for i in range(n)]


def _fit_ridge(series, features, asof, horizon):
    indices = _training_feature_indices(asof, horizon)
    if len(indices) < 126:
        return None
    names = sorted(series)
    rows, labels = [], []
    for j in indices:
        for name in names:
            # Features are fixed causal transforms of momentum, SMA distance
            # and efficiency. Exclude the intercept before train-only scaling.
            rows.append(features[name][j]["x"][1:])
            bars = series[name]
            future_open = bars[j + horizon + 1].open
            entry_open = bars[j + 1].open
            labels.append(min(0.5, max(-0.5, future_open / entry_open - 1)))
    count, width = len(rows), len(rows[0])
    means = [sum(row[k] for row in rows) / count for k in range(width)]
    scales = [sqrt(sum((row[k] - means[k]) ** 2 for row in rows) / count)
              for k in range(width)]
    scales = [v if v > 1e-12 else 1.0 for v in scales]
    matrix = [[0.0] * (width + 1) for _ in range(width + 1)]
    rhs = [0.0] * (width + 1)
    for row, label in zip(rows, labels):
        x = [1.0] + [(v - center) / scale for v, center, scale in zip(row, means, scales)]
        for a, value in enumerate(x):
            rhs[a] += value * label
            for b, other in enumerate(x):
                matrix[a][b] += value * other
    # Standard sum-of-squares ridge objective; intercept remains unpenalized.
    for k in range(1, width + 1):
        matrix[k][k] += 10.0
    return {"coefficients": _solve(matrix, rhs), "means": means, "scales": scales,
            "fitted_at_index": asof, "latest_feature_index": indices[-1],
            "latest_label_open_index": indices[-1] + horizon + 1,
            "feature_days": len(indices), "observations": count}


def _predict(model, feature):
    x = [1.0] + [(v - center) / scale for v, center, scale in
                 zip(feature["x"][1:], model["means"], model["scales"])]
    return sum(v * w for v, w in zip(x, model["coefficients"]))


def _expert_targets(series, features, names, n):
    output = {name: [{} for _ in range(n)]
              for name in ("momentum63", "momentum126", "breakout55_20", "cash")}
    active = {name: False for name in names}
    sleeve = MAX_ALLOCATION / len(names)
    for i in range(WARMUP_BARS, n):
        for name in names:
            bars, feature = series[name], features[name][i]
            for k, expert in ((1, "momentum63"), (2, "momentum126")):
                if feature["momentum"][k] > 0:
                    output[expert][i][name] = sleeve
            if active[name]:
                if bars[i].close < min(b.low for b in bars[i - 20:i]):
                    active[name] = False
            elif bars[i].close > max(b.high for b in bars[i - 55:i]):
                active[name] = True
            if active[name]:
                output["breakout55_20"][i][name] = sleeve
    return output


def _update_expert_scores(scores, experts, series, i):
    """At close i, execute/evaluate only expert decision i-2, never i-1."""
    if i < 2:
        return
    decay = exp(-1 / 126)
    for expert, targets in experts.items():
        held = targets[i - 2]
        before = targets[i - 3] if i >= 3 else {}
        gross = sum(weight * (series[name][i].open / series[name][i - 1].open - 1)
                    for name, weight in held.items())
        # This is a causal expert scoring approximation. Actual execution,
        # rebalance bands, cash, fees and drift belong to the portfolio engine.
        turnover = sum(abs(held.get(name, 0) - before.get(name, 0)) for name in series)
        net = gross - turnover * 0.0016
        scores[expert] = decay * scores[expert] + 10 * log1p(net)


def build_model_targets(series):
    """Return six aligned, bounded target series; no historical performance run.

    A single BTC quote pair must identify the market regime; quotes are never
    joined or substituted. Model fitting waits for 126 fully matured feature
    days per asset and occurs only at the fixed monthly schedule.
    """
    names, n = _validate_series(series)
    btc_names = [name for name in names if name.startswith("BTC")]
    if len(btc_names) != 1:
        raise ValueError("Exactly one BTC quote-pair series is required for market regime")
    out = {name: [{} for _ in range(n)] for name in MODEL_SPECS}
    if not n:
        return out
    btc = btc_names[0]
    features = {name: _features(series[name]) for name in names}
    experts = _expert_targets(series, features, names, n)
    scores = {name: 0.0 for name in experts}
    models = {7: None, 21: None}
    last_month = None
    sleeve = MAX_ALLOCATION / len(names)
    for i in range(n):
        _update_expert_scores(scores, experts, series, i)
        if i < WARMUP_BARS:
            continue
        now = datetime.fromtimestamp(series[btc][i].close_time_ms / 1000, timezone.utc)
        month = (now.year, now.month)
        if month != last_month:
            models = {h: _fit_ridge(series, features, i, h) for h in models}
            last_month = month
        for horizon, model in models.items():
            if model is not None:
                out[f"pooled_ridge_{horizon}d"][i] = {
                    name: sleeve for name in names
                    if _predict(model, features[name][i]) > 0.0064}
        bf = features[btc][i]
        if (series[btc][i].close > bf["sma200"] and
                bf["sma50"] > bf["sma200"] and bf["momentum"][1] > 0):
            out["btc_regime_equal"][i] = {name: sleeve for name in names}
            inverse = {name: 1 / max(features[name][i]["vol"], 0.005) for name in names}
            total = sum(inverse.values())
            out["btc_regime_inverse_vol"][i] = {name: MAX_ALLOCATION * v / total for name, v in inverse.items()}
        maximum = max(scores.values())
        probabilities = {expert: exp(score - maximum) for expert, score in scores.items()}
        total = sum(probabilities.values())
        for name in names:
            online = sum(probabilities[expert] / total * targets[i].get(name, 0)
                         for expert, targets in experts.items())
            fixed = sum(experts[expert][i].get(name, 0)
                        for expert in ("momentum63", "momentum126", "breakout55_20")) / 3
            if online > 0:
                out["online_expert_cash_blend"][i][name] = online
            if fixed > 0:
                out["conservative_horizon_blend"][i][name] = fixed
    return out
