"""Three multi-timeframe, causal long-only research strategies.

The 20/55 Donchian levels are *daily* candles, unlike the existing 15-minute
short-channel experiments. This is a spot-long adaptation, NOT an exact
reproduction of Dennis/Eckhardt futures pyramiding or short-selling.

All decisions use COMPLETED OHLCV bars. The engine fills an entry at a
subsequent hourly OPEN; it never trades at the same bar's closing price.
"""
from bisect import bisect_right
from dataclasses import dataclass
from .indicators import atr, ema, rsi

STRATEGIES_V3 = {
    "turtle_daily_20": "Previous 20 DAILY highs / 10 DAILY lows; 2x daily ATR stop",
    "turtle_daily_55": "Previous 55 DAILY highs / 20 DAILY lows; 2x daily ATR stop",
    "multi_tf_trend": "Daily EMA50/200 regime, 4h EMA20 pullback, 1h EMA20 recovery",
}


@dataclass(frozen=True)
class AdvancedSignal:
    action: str = "HOLD"
    risk_distance: float = 0.0


def _closed_idx(closes, ms):
    return bisect_right(closes, ms) - 1


def advanced_signals(hourly, fourhour, daily):
    """Return a signal per CLOSED 1h bar, with fixed-at-signal risk distance.

    Daily and 4h signals only become available once their candles CLOSE.
    A 1h bar ending at 23:59:59.999 UTC may use that completed daily bar;
    order execution is only possible at 00:00 UTC or later.
    """
    if len(hourly) < 220 or len(fourhour) < 80 or len(daily) < 210:
        raise ValueError("Not enough hourly/4h/daily history (daily EMA200 warmup)")
    output = {k: [AdvancedSignal()] * len(hourly) for k in STRATEGIES_V3}
    hp = [x.close for x in hourly]
    fp = [x.close for x in fourhour]
    dp = [x.close for x in daily]
    d50, d200 = ema(dp, 50), ema(dp, 200)
    f20, f50 = ema(fp, 20), ema(fp, 50)
    h20, h50 = ema(hp, 20), ema(hp, 50)
    h_rsi = rsi(hp, 14)
    d_atr = atr(daily, 20)
    h_atr = atr(hourly, 14)
    dc = [x.close_time_ms for x in daily]
    fc = [x.close_time_ms for x in fourhour]
    for i in range(1, len(hourly)):
        now = hourly[i].close_time_ms
        j = _closed_idx(dc, now)
        k = _closed_idx(fc, now)
        if j < 200 or k < 51 or h_rsi[i - 1] is None:
            continue
        close = hourly[i].close
        bullish = d50[j] > d200[j] and dp[j] > d200[j]
        for lookback, exit_days, name in (
            (20, 10, "turtle_daily_20"),
            (55, 20, "turtle_daily_55"),
        ):
            # Use the last N COMPLETED daily bars including index j;
            # an unclosed current UTC day is never in the daily lookup.
            if j < lookback or d_atr[j] is None:
                continue
            upper = max(b.high for b in daily[j-lookback+1:j+1])
            lower = min(b.low for b in daily[j-exit_days+1:j+1])
            # Entire day j is already closed when this signal is emitted.
            distance = 2.0 * d_atr[j]
            if distance <= 0:
                continue
            action = "HOLD"
            if close > upper and hourly[i-1].close <= upper and bullish:
                action = "ENTRY_CANDIDATE"
            elif close < lower or not bullish:
                action = "EXIT_CANDIDATE"
            output[name][i] = AdvancedSignal(action, distance)

        # 4h pullback setup must itself be from a fully closed candle.
        # 1h entry trigger: price recaptures 1h EMA20, RSI recovers >50.
        if any(v is None for v in (h20[i], h20[i-1], h50[i], f20[k], f50[k], h_atr[i])):
            continue
        fourhour_bull = f20[k] > f50[k] and fp[k] > f50[k]
        recent_pullback = (fourhour[k].low <= f20[k] * 1.005 or
                           (k > 0 and fourhour[k-1].low <= f20[k-1] * 1.005))
        entry = (bullish and fourhour_bull and recent_pullback
                 and hourly[i-1].close <= h20[i-1] and close > h20[i]
                 and h_rsi[i] > 50)
        exit_ = (not bullish or not fourhour_bull or
                 (close < h50[i] and h_rsi[i] < 45))
        distance = 2.5 * h_atr[i]
        output["multi_tf_trend"][i] = AdvancedSignal(
            "ENTRY_CANDIDATE" if entry else ("EXIT_CANDIDATE" if exit_ else "HOLD"),
            distance,
        )
    return output
