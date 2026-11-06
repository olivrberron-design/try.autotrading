"""Fixed, causal daily hypotheses for a shared-capital spot research account.

Each target at index i is known ONLY after candle i closes. The execution
engine must apply it no earlier than the next daily OPEN. Targets allocate
at most 20% of equity in aggregate, with the remainder in non-yielding cash.
These are research hypotheses, not optimized parameters or return claims.
No APIs, credentials, order submission, or third-party packages are used.
"""
from math import exp, isfinite, log, sqrt
from statistics import pstdev

from .indicators import rsi

DAY_MS = 86_400_000
WARMUP_BARS = 240
MAX_ALLOCATION = 0.20

STRATEGY_SPECS = {
    "equal_weight_20": {
        "description": "Daily rebalanced equal-weight 20% exposure baseline; not buy-and-hold.",
        "parameters": {"allocation": 0.20},
    },
    "sma200_trend": {
        "description": "Each asset gets its equal 20% portfolio sleeve while close exceeds SMA200; inactive sleeves stay cash.",
        "parameters": {"sma_days": 200},
    },
    "momentum_vote": {
        "description": "Equal asset sleeves multiplied by fraction of positive 21/63/126-day close-to-close momentum votes.",
        "parameters": {"lookbacks_days": [21, 63, 126]},
    },
    "inverse_vol_trend": {
        "description": "Allocate 20% among close>SMA200 and SMA50>SMA200 assets in inverse 30-day realized volatility proportions.",
        "parameters": {"fast_sma": 50, "slow_sma": 200, "vol_days": 30, "daily_vol_floor": 0.005},
    },
    "dual_momentum_rotation": {
        "description": "Every UTC-epoch-anchored seventh day select highest mean 63/126-day momentum asset if positive and close>SMA200; exit daily on loss of either gate.",
        "parameters": {"lookbacks_days": [63, 126], "rebalance_days": 7, "sma_days": 200},
    },
    "adaptive_efficiency_trend": {
        "description": "Equal sleeves when close>KAMA20, close>SMA200 and 20-day efficiency ratio>=0.25; KAMA uses fast2/slow30 smoothing.",
        "parameters": {"efficiency_days": 20, "min_efficiency": 0.25, "fast": 2, "slow": 30, "sma_days": 200},
    },
    "rsi2_trend_dip": {
        "description": "Equal sleeve enters below RSI2=10 with close>SMA200; exit above RSI2=60, loss of SMA200, or five completed holding days. No same-day reentry.",
        "parameters": {"rsi_days": 2, "entry_below": 10, "exit_above": 60, "max_holding_days": 5, "sma_days": 200},
    },
    "daily_breakout_ensemble": {
        "description": "Two half-sleeves per asset: close above prior 20/55 daily highs enters; close below prior 10/20 daily lows exits. Levels exclude signal bar.",
        "parameters": {"entry_days": [20, 55], "exit_days": [10, 20]},
    },
    "volatility_scaled_momentum": {
        "description": "Momentum-vote sleeves scaled by min(1,35% / annualized trailing 30-day daily-return volatility); never increases leverage.",
        "parameters": {"lookbacks_days": [21, 63, 126], "vol_days": 30, "vol_target_annual": 0.35, "annual_days": 365},
    },
    "online_logistic_momentum": {
        "description": "Online per-asset logistic classifier of next-open to following-open positive return; matured labels only, fixed clipped features, one gradient update per observation. Equal sleeve requires probability>0.55 and close>SMA200.",
        "parameters": {"threshold": 0.55, "learning_rate": 0.03, "l2": 0.001, "label_delay_days": 2,
                       "features": ["intercept", "momentum21", "momentum63", "momentum126", "distance_sma200", "efficiency20"]},
    },
}
STRATEGIES = tuple(STRATEGY_SPECS)


def _validate_series(series):
    if not series:
        raise ValueError("At least one aligned daily asset series is required")
    names = sorted(series)
    n = len(series[names[0]])
    reference = [(b.open_time_ms, b.close_time_ms) for b in series[names[0]]]
    for name in names:
        bars = series[name]
        if len(bars) != n:
            raise ValueError("Asset series lengths must match")
        for i, b in enumerate(bars):
            if (b.open_time_ms, b.close_time_ms) != reference[i]:
                raise ValueError("Asset daily timestamps must match")
            if (b.open_time_ms % DAY_MS != 0 or
                    b.close_time_ms != b.open_time_ms + DAY_MS - 1 or
                    (i and b.open_time_ms != bars[i - 1].open_time_ms + DAY_MS)):
                raise ValueError("Series must contain contiguous completed UTC daily bars")
            values = (b.open, b.high, b.low, b.close, b.volume)
            if (not all(isfinite(v) for v in values) or b.low <= 0 or
                    b.volume < 0 or b.high < max(b.open, b.close, b.low) or
                    b.low > min(b.open, b.close, b.high)):
                raise ValueError("Invalid daily OHLCV candle")
    return names, n


def _clip(value):
    return min(3.0, max(-3.0, value))


def _features(bars):
    prices = [b.close for b in bars]
    sums = [0.0]
    for p in prices:
        sums.append(sums[-1] + p)
    changes = [0.0] + [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    returns = [0.0] + [prices[i] / prices[i - 1] - 1 for i in range(1, len(prices))]
    out = []
    kama = None
    for i, close in enumerate(prices):
        er = 0.0
        if i >= 20:
            variation = sum(abs(v) for v in changes[i - 19:i + 1])
            er = abs(close - prices[i - 20]) / variation if variation else 0.0
            if kama is None:
                kama = sum(prices[i - 19:i + 1]) / 20
            smoothing = (er * (2 / 3 - 2 / 31) + 2 / 31) ** 2
            kama += smoothing * (close - kama)
        if i < 199:
            out.append(None)
            continue
        sma200 = (sums[i + 1] - sums[i - 199]) / 200
        sma50 = (sums[i + 1] - sums[i - 49]) / 50
        vol = pstdev(returns[i - 29:i + 1])
        mom = [close / prices[i - lookback] - 1 for lookback in (21, 63, 126)]
        # Fixed feature transformations; no fitted scaler, no sample-wide fit.
        x = [1.0] + [_clip(log(close / prices[i - k]) / sqrt(k) / max(vol, 0.005))
                      for k in (21, 63, 126)]
        x += [_clip(log(close / sma200) / max(vol * sqrt(100), 0.01)), er]
        out.append({"sma200": sma200, "sma50": sma50, "vol": vol,
                    "momentum": mom, "efficiency": er, "kama": kama, "x": x})
    return out


def build_targets(series):
    """Return strategy -> aligned list of sparse asset target-weight dicts.

    The first 240 bars are cash for every strategy. The current closed bar
    participates in indicators, while breakout levels use strictly prior bars.
    Empty dict means cash. No sizing depends on future prices or sample length.
    """
    names, n = _validate_series(series)
    output = {name: [{} for _ in range(n)] for name in STRATEGIES}
    if not n:
        return output
    features = {name: _features(series[name]) for name in names}
    rsi2 = {name: rsi([b.close for b in series[name]], 2) for name in names}
    sleeve = MAX_ALLOCATION / len(names)
    breakout = {name: [False, False] for name in names}
    dip_since = {name: None for name in names}
    weights = {name: [0.0] * 6 for name in names}
    updates = {name: 0 for name in names}
    rotation = None
    for i in range(n):
        # Sample j predicts open[j+2]/open[j+1]. Both opens are observable
        # by close i ONLY when j <= i-2. Train before today's prediction.
        probabilities = {}
        for name in names:
            f = features[name][i]
            if i >= 2 and features[name][i - 2] is not None:
                x = features[name][i - 2]["x"]
                b = weights[name]
                prediction = 1 / (1 + exp(-sum(v * w for v, w in zip(x, b))))
                label = float(series[name][i].open > series[name][i - 1].open)
                rate = 0.03 / sqrt(1 + updates[name] / 100)
                for k in range(len(b)):
                    regularizer = 0.001 * b[k] if k else 0.0
                    b[k] += rate * ((label - prediction) * x[k] - regularizer)
                updates[name] += 1
            if f is not None:
                score = sum(v * w for v, w in zip(f["x"], weights[name]))
                probabilities[name] = 1 / (1 + exp(-score))
        if i < WARMUP_BARS:
            continue
        inverse = {}
        rotation_scores = {}
        for name in names:
            bars, f = series[name], features[name][i]
            close = bars[i].close
            above = close > f["sma200"]
            vote = sum(m > 0 for m in f["momentum"]) / 3
            output["equal_weight_20"][i][name] = sleeve
            if above:
                output["sma200_trend"][i][name] = sleeve
            if vote:
                output["momentum_vote"][i][name] = sleeve * vote
                scale = min(1.0, 0.35 / max(f["vol"] * sqrt(365), 1e-12))
                output["volatility_scaled_momentum"][i][name] = sleeve * vote * scale
            if above and f["sma50"] > f["sma200"]:
                inverse[name] = 1 / max(f["vol"], 0.005)
            momentum_score = sum(f["momentum"][1:]) / 2
            if above and momentum_score > 0:
                rotation_scores[name] = momentum_score
            if above and close > f["kama"] and f["efficiency"] >= 0.25:
                output["adaptive_efficiency_trend"][i][name] = sleeve

            started = dip_since[name]
            if started is not None:
                if not above or rsi2[name][i] > 60 or i - started >= 5:
                    dip_since[name] = None
                else:
                    output["rsi2_trend_dip"][i][name] = sleeve
            elif above and rsi2[name][i] < 10:
                dip_since[name] = i
                output["rsi2_trend_dip"][i][name] = sleeve

            for k, (entry, exit_) in enumerate(((20, 10), (55, 20))):
                if breakout[name][k]:
                    if close < min(b.low for b in bars[i - exit_:i]):
                        breakout[name][k] = False
                elif close > max(b.high for b in bars[i - entry:i]):
                    breakout[name][k] = True
            breakout_weight = sleeve * sum(breakout[name]) / 2
            if breakout_weight:
                output["daily_breakout_ensemble"][i][name] = breakout_weight
            if above and probabilities[name] > 0.55:
                output["online_logistic_momentum"][i][name] = sleeve
        if inverse:
            total = sum(inverse.values())
            output["inverse_vol_trend"][i] = {name: MAX_ALLOCATION * v / total for name, v in inverse.items()}
        epoch_day = series[names[0]][i].open_time_ms // DAY_MS
        if i == WARMUP_BARS or epoch_day % 7 == 0:
            # Sorted names make equal-score tie handling deterministic.
            rotation = max(sorted(rotation_scores), key=lambda name: rotation_scores[name]) if rotation_scores else None
        if rotation not in rotation_scores:
            rotation = None
        if rotation is not None:
            output["dual_momentum_rotation"][i] = {rotation: MAX_ALLOCATION}
    return output
