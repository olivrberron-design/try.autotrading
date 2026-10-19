"""Pure trading research rules. These functions return signals, never orders."""


def baseline_action(e20_prev, e50_prev, e20, e50, hour_bull,
                    rsi_prev, rsi_now, macd_hist, volume, previous_volume_mean):
    bullish_cross = e20_prev <= e50_prev and e20 > e50
    bearish_cross = e20_prev >= e50_prev and e20 < e50
    momentum_ok = 48 <= rsi_now <= 70 and macd_hist > 0
    volume_ok = volume >= previous_volume_mean * 0.75
    if bullish_cross and hour_bull and momentum_ok and volume_ok:
        return "ENTRY_CANDIDATE"
    if bearish_cross or (rsi_prev >= 45 and rsi_now < 45):
        return "EXIT_CANDIDATE"
    return "HOLD"


def momentum_action(price, e20, e50, rsi_prev, rsi_now,
                    hour_bull, volume, previous_volume_mean):
    if hour_bull and price > e50 and rsi_prev <= 55 < rsi_now and volume >= previous_volume_mean:
        return "ENTRY_CANDIDATE"
    if price < e20 or rsi_now < 45 or not hour_bull:
        return "EXIT_CANDIDATE"
    return "HOLD"


def mean_reversion_action(price, e20, rsi_prev, rsi_now, hour_bull):
    # Wait for RSI recovery rather than automatically catching a falling knife.
    if hour_bull and rsi_prev <= 30 < rsi_now and price < e20:
        return "ENTRY_CANDIDATE"
    if rsi_now >= 55 or price >= e20 or not hour_bull:
        return "EXIT_CANDIDATE"
    return "HOLD"


STRATEGIES = ("baseline", "momentum", "mean_reversion", "ema_pullback", "donchian_20", "donchian_55", "bollinger_reversal", "bollinger_breakout", "macd_crossover", "rsi_trend", "rsi2_dip", "ema_triple", "roc_momentum", "atr_channel", "stochastic_rebound")
