"""Minimal, dependency-free technical indicators for closed spot candles."""
from math import isfinite


def _validated(values, period):
    if period < 1:
        raise ValueError("Period must be positive")
    if any(not isfinite(float(v)) for v in values):
        raise ValueError("Non-finite indicator input")


def ema(values, period):
    """EMA, seeded with SMA after 'period' samples. Prior values are None."""
    _validated(values, period)
    out = [None] * len(values)
    if len(values) < period:
        return out
    previous = sum(values[:period]) / period
    out[period - 1] = previous
    alpha = 2 / (period + 1)
    for i in range(period, len(values)):
        previous = alpha * values[i] + (1 - alpha) * previous
        out[i] = previous
    return out


def rsi(values, period=14):
    """Wilder RSI; initial RSI at index 'period' (needs price changes)."""
    _validated(values, period)
    out = [None] * len(values)
    if len(values) <= period:
        return out
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gain = sum(max(x, 0) for x in changes[:period]) / period
    loss = sum(max(-x, 0) for x in changes[:period]) / period

    def point(g, l):
        if l == 0:
            return 50.0 if g == 0 else 100.0
        return 100 - 100 / (1 + g / l)

    out[period] = point(gain, loss)
    for i in range(period + 1, len(values)):
        change = changes[i - 1]
        gain = (gain * (period - 1) + max(change, 0)) / period
        loss = (loss * (period - 1) + max(-change, 0)) / period
        out[i] = point(gain, loss)
    return out


def macd(values, fast=12, slow=26, signal=9):
    if not 1 <= fast < slow or signal < 1:
        raise ValueError("Invalid MACD periods")
    a, b = ema(values, fast), ema(values, slow)
    line = [None if x is None or y is None else x - y for x, y in zip(a, b)]
    first = slow - 1
    sig_tail = ema([v for v in line[first:] if v is not None], signal)
    sig = [None] * len(values)
    for i, v in enumerate(sig_tail, start=first):
        sig[i] = v
    histogram = [None if x is None or y is None else x - y for x, y in zip(line, sig)]
    return line, sig, histogram


def atr(candles, period=14):
    """Wilder Average True Range with first value at candle index=period."""
    if period < 1:
        raise ValueError("Period must be positive")
    result = [None] * len(candles)
    if len(candles) <= period:
        return result
    true_ranges = [
        max(candles[i].high - candles[i].low,
            abs(candles[i].high - candles[i - 1].close),
            abs(candles[i].low - candles[i - 1].close))
        for i in range(1, len(candles))
    ]
    previous = sum(true_ranges[:period]) / period
    result[period] = previous
    for i in range(period + 1, len(candles)):
        previous = (previous * (period - 1) + true_ranges[i - 1]) / period
        result[i] = previous
    return result
