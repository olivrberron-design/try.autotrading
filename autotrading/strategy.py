"""Explainable, read-only spot research baseline. Signals are NOT orders."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from statistics import mean
from .indicators import atr, ema, macd, rsi


@dataclass(frozen=True)
class Signal:
    symbol: str
    action: str
    closed_candle_utc: str
    last_price: float
    hourly_trend: str
    indicators: dict
    explanation: str

    def to_dict(self):
        return asdict(self)


def evaluate(symbol, candles_15m, candles_1h):
    """Assess a completed 15m bar with a completed 1h trend filter.

    ENTRY_CANDIDATE means research signal, never a buy order.
    EXIT_CANDIDATE means research signal, never authorization to sell holdings.
    All indicators are calculated using closed candles only.
    """
    if len(candles_15m) < 100 or len(candles_1h) < 200:
        raise ValueError("Not enough history for indicator warmup")
    closes = [c.close for c in candles_15m]
    hour_closes = [c.close for c in candles_1h]
    ema20, ema50 = ema(closes, 20), ema(closes, 50)
    hour50, hour200 = ema(hour_closes, 50), ema(hour_closes, 200)
    strength = rsi(closes, 14)
    macd_line, macd_signal, histogram = macd(closes)
    volatility = atr(candles_15m)
    # Baseline excludes the current candle's volume to prevent dilution.
    previous_20_vol = mean(c.volume for c in candles_15m[-21:-1])
    last = candles_15m[-1]
    timestamp = datetime.fromtimestamp(last.close_time_ms / 1000, tz=timezone.utc).isoformat()
    trend_up = hour50[-1] > hour200[-1]
    cross_up = ema20[-2] <= ema50[-2] and ema20[-1] > ema50[-1]
    cross_down = ema20[-2] >= ema50[-2] and ema20[-1] < ema50[-1]
    momentum_ok = 48 <= strength[-1] <= 70 and histogram[-1] > 0
    volume_ok = last.volume >= previous_20_vol * 0.75
    if cross_up and trend_up and momentum_ok and volume_ok:
        action = "ENTRY_CANDIDATE"
        reason = "15m EMA20 crossed above EMA50; 1h bullish regime, RSI, MACD and volume filters agree."
    elif cross_down or (strength[-2] >= 45 and strength[-1] < 45):
        action = "EXIT_CANDIDATE"
        reason = "15m EMA bearish cross or RSI crossed below 45. Research-only signal; do not automatically liquidate any holdings."
    else:
        action = "HOLD"
        reason = "Baseline entry/exit conditions did not align on the latest completed candle."
    indicators = {
        "ema20_15m": round(ema20[-1], 4),
        "ema50_15m": round(ema50[-1], 4),
        "ema50_1h": round(hour50[-1], 4),
        "ema200_1h": round(hour200[-1], 4),
        "rsi14_15m": round(strength[-1], 2),
        "macd_hist_15m": round(histogram[-1], 6),
        "atr14_15m": round(volatility[-1], 4),
        "volume_15m": round(last.volume, 5),
        "previous20_volume_avg": round(previous_20_vol, 5),
    }
    return Signal(
        symbol=symbol, action=action, closed_candle_utc=timestamp,
        last_price=last.close, hourly_trend="UP" if trend_up else "DOWN_OR_FLAT",
        indicators=indicators, explanation=reason
    )
