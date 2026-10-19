"""Strategy Lab 2.0: 15 deterministic research hypotheses, no trading API.

All features at index i use only completed candles <=i, and hourly trend
uses only hourly bars completed before the associated 15m close.
Strategies emit ENTRY_CANDIDATE/EXIT_CANDIDATE/HOLD, not orders.
"""
from bisect import bisect_right
from math import sqrt
from .indicators import atr, ema, macd, rsi
from .rules import baseline_action, momentum_action, mean_reversion_action

CATALOG = {
    "baseline": "EMA20/50 cross + RSI + MACD + hourly trend",
    "momentum": "RSI55 breakout plus volume and hourly trend",
    "mean_reversion": "Wilder RSI14 oversold recovery",
    "ema_pullback": "Pullback to EMA20 in hourly uptrend",
    "donchian_20": "20-candle channel breakout; 10-candle exit",
    "donchian_55": "55-candle channel breakout; 20-candle exit",
    "bollinger_reversal": "20-bar lower Bollinger band re-entry",
    "bollinger_breakout": "20-bar upper Bollinger band breakout",
    "macd_crossover": "MACD 12/26/9 signal-line bullish crossover",
    "rsi_trend": "RSI14 55 strength cross in bullish hourly regime",
    "rsi2_dip": "Two-bar RSI rebound after extreme oversold",
    "ema_triple": "EMA20 > EMA50 > EMA100 alignment switch",
    "roc_momentum": "20-bar rate of change cross above +1.5%",
    "atr_channel": "EMA20 + 1.5 ATR Keltner-style breakout",
    "stochastic_rebound": "14-bar stochastic K% bounce above 25",
}


def _rolling_mean(values, period):
    out = [None] * len(values)
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= period:
            running -= values[i - period]
        if i + 1 >= period:
            out[i] = running / period
    return out


def _rolling_std(values, period):
    """Population rolling standard deviation; no future bars."""
    out = [None] * len(values)
    run = 0.0
    run2 = 0.0
    for i, v in enumerate(values):
        run += v
        run2 += v * v
        if i >= period:
            old = values[i - period]
            run -= old
            run2 -= old * old
        if i + 1 >= period:
            mu = run / period
            out[i] = sqrt(max(0.0, run2 / period - mu * mu))
    return out


def _channel(candles, n, upper):
    """Previous n candles ONLY, excluding current candle (no repaint)."""
    data = [c.high if upper else c.low for c in candles]
    out = [None] * len(data)
    for i in range(n, len(data)):
        out[i] = max(data[i - n:i]) if upper else min(data[i - n:i])
    return out


def _stochastic(candles, period=14):
    out = [None] * len(candles)
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    for i in range(period - 1, len(candles)):
        hi = max(highs[i - period + 1:i + 1])
        lo = min(lows[i - period + 1:i + 1])
        out[i] = 50.0 if hi == lo else (candles[i].close - lo) / (hi - lo) * 100
    return out


def _choose(entry, exit_):
    return "ENTRY_CANDIDATE" if entry else ("EXIT_CANDIDATE" if exit_ else "HOLD")


def compute_catalog_signals(fast, hourly):
    if len(fast) < 200 or len(hourly) < 210:
        raise ValueError("Need >=200 completed 15m and >=210 1h candles")
    prices = [b.close for b in fast]
    hours = [b.close for b in hourly]
    closes_ms = [b.close_time_ms for b in hourly]
    e20, e50, e100 = ema(prices, 20), ema(prices, 50), ema(prices, 100)
    h50, h200 = ema(hours, 50), ema(hours, 200)
    strength, short_rsi = rsi(prices, 14), rsi(prices, 2)
    ml, ms, hist = macd(prices)
    ranges = atr(fast, 14)
    bbmid = _rolling_mean(prices, 20)
    bbstd = _rolling_std(prices, 20)
    don20_hi, don10_lo = _channel(fast, 20, True), _channel(fast, 10, False)
    don55_hi, don20_lo = _channel(fast, 55, True), _channel(fast, 20, False)
    stochastic = _stochastic(fast)
    volumes = [c.volume for c in fast]
    volmean = _rolling_mean(volumes, 20)
    roc20 = [None] * len(fast)
    for i in range(20, len(fast)):
        roc20[i] = (prices[i] / prices[i - 20] - 1) * 100

    out = {name: ["HOLD"] * len(fast) for name in CATALOG}
    for i in range(200, len(fast)):
        # New hourly candle only becomes available after it is closed.
        j = bisect_right(closes_ms, fast[i].close_time_ms) - 1
        if j < 199 or h200[j] is None:
            continue
        price = prices[i]
        prev = prices[i - 1]
        bull = h50[j] > h200[j]
        trend_ok = e20[i] > e50[i]
        vavg = volmean[i - 1]
        if vavg is None or vavg <= 0:
            continue
        r, rp = strength[i], strength[i - 1]
        hist_now = hist[i]
        if any(x is None for x in (r, rp, hist_now, ranges[i],
                                   e100[i], bbmid[i], bbstd[i], stochastic[i],
                                   roc20[i], roc20[i - 1], short_rsi[i - 1])):
            continue
        vol = volumes[i]
        out["baseline"][i] = baseline_action(
            e20[i - 1], e50[i - 1], e20[i], e50[i],
            bull, rp, r, hist_now, vol, vavg,
        )
        out["momentum"][i] = momentum_action(
            price, e20[i], e50[i], rp, r, bull, vol, vavg
        )
        out["mean_reversion"][i] = mean_reversion_action(
            price, e20[i], rp, r, bull
        )
        out["ema_pullback"][i] = _choose(
            bull and trend_ok and prev <= e20[i - 1] and price > e20[i] and r >= 45,
            price < e50[i] or not bull
        )
        out["donchian_20"][i] = _choose(
            bull and prev <= don20_hi[i - 1] and price > don20_hi[i] and vol >= vavg,
            price < don10_lo[i]
        )
        out["donchian_55"][i] = _choose(
            bull and prev <= don55_hi[i - 1] and price > don55_hi[i],
            price < don20_lo[i]
        )
        lo_now = bbmid[i] - 2 * bbstd[i]
        lo_prev = bbmid[i - 1] - 2 * bbstd[i - 1]
        hi_now = bbmid[i] + 2 * bbstd[i]
        hi_prev = bbmid[i - 1] + 2 * bbstd[i - 1]
        out["bollinger_reversal"][i] = _choose(
            bull and prev < lo_prev and price >= lo_now and r < 55,
            price >= bbmid[i] or not bull
        )
        out["bollinger_breakout"][i] = _choose(
            bull and prev <= hi_prev and price > hi_now and vol >= 1.2 * vavg,
            price < bbmid[i] or not bull
        )
        out["macd_crossover"][i] = _choose(
            bull and ml[i - 1] <= ms[i - 1] and ml[i] > ms[i] and hist_now > 0,
            ml[i] < ms[i] or not bull
        )
        out["rsi_trend"][i] = _choose(
            bull and rp <= 55 < r and trend_ok,
            r < 45 or not bull
        )
        out["rsi2_dip"][i] = _choose(
            bull and short_rsi[i - 1] <= 10 < short_rsi[i] and price > e50[i],
            short_rsi[i] >= 70 or price < e50[i] or not bull
        )
        up = e20[i] > e50[i] > e100[i]
        up_prev = e20[i-1] > e50[i-1] > e100[i-1]
        out["ema_triple"][i] = _choose(
            bull and up and not up_prev,
            e20[i] < e50[i] or not bull
        )
        out["roc_momentum"][i] = _choose(
            bull and roc20[i - 1] <= 1.5 < roc20[i] and trend_ok,
            roc20[i] < 0 or not bull
        )
        upper = e20[i] + 1.5 * ranges[i]
        upper_prev = e20[i - 1] + 1.5 * ranges[i - 1]
        out["atr_channel"][i] = _choose(
            bull and prev <= upper_prev and price > upper and vol > vavg,
            price < e20[i] or not bull
        )
        out["stochastic_rebound"][i] = _choose(
            bull and stochastic[i - 1] <= 25 < stochastic[i] and trend_ok,
            stochastic[i] >= 75 or not bull
        )
    return out
