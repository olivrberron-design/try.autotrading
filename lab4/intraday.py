"""Intraday (day-trading) hypotheses on hourly bars, aggregated to UTC days.

These answer the "trade every day" request directly. Every rule decides from
information available at decision time:
  * volatility breakout (VBO): the entry level for UTC day k is fixed at
    00:00 from the day-k open and day k-1's range; a resting stop-buy fills
    at max(level, hour open) plus slippage; the position is closed at the
    next 00:00 open. Days with any missing hour are skipped (no fabricated
    fills).
  * hourly RSI(2) reversion: signal on a closed hour, fill at the next hour
    open; exit on RSI(2) > 70 or after 6 held hours.
Results are per-day net returns so they can be compared with daily rules.
"""
HOUR_MS = 3_600_000
DAY_MS = 86_400_000


def vbo_day_returns(panel, hourly, asset, k_start, k_end, k=0.5, sma_days=20,
                    fee=0.0010, slip=0.0005):
    """Return (daily_returns, trades) for one asset sleeve, full sleeve size."""
    out, trades = [], 0
    o, h, l, c = panel.o[asset], panel.h[asset], panel.l[asset], panel.c[asset]
    sma = panel.sma(asset, sma_days)
    for d in range(k_start, k_end):
        r = 0.0
        prev_ok = d >= 1 and None not in (h[d - 1], l[d - 1], c[d - 1], sma[d - 1])
        bars = [hourly.get(panel.day_ms(d) + i * HOUR_MS) for i in range(24)]
        if prev_ok and all(bars) and c[d - 1] > sma[d - 1]:
            day_open = bars[0][0]
            level = day_open + k * (h[d - 1] - l[d - 1])
            for bar in bars:
                if bar[1] >= level:
                    entry = max(level, bar[0]) * (1 + slip)
                    exit_ = bars[-1][3] * (1 - slip)  # 23:00 bar close = next 00:00
                    r = exit_ / entry * (1 - fee) ** 2 - 1
                    trades += 1
                    break
        out.append(r)
    return out, trades


def rsi_series(closes, n=2):
    """Wilder RSI over a list of closes (None-safe: resets after gaps)."""
    out = [None] * len(closes)
    gain = loss = None
    for i in range(1, len(closes)):
        a, b = closes[i - 1], closes[i]
        if a is None or b is None:
            gain = loss = None
            continue
        ch = b - a
        g, lo = max(ch, 0.0), max(-ch, 0.0)
        if gain is None:
            gain, loss = g, lo
        else:
            gain = (gain * (n - 1) + g) / n
            loss = (loss * (n - 1) + lo) / n
        out[i] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return out


def hourly_rsi2_day_returns(panel, hourly, k_start, k_end, entry_below=10, exit_above=70,
                            max_hold=6, sma_hours=200, fee=0.0010, slip=0.0005):
    """Hourly RSI(2) dip-buying in an hourly uptrend, aggregated per UTC day."""
    t0 = panel.day_ms(k_start) - (sma_hours + 10) * HOUR_MS
    t1 = panel.day_ms(k_end)
    times = list(range(t0, t1, HOUR_MS))
    bars = [hourly.get(t) for t in times]
    closes = [b[3] if b else None for b in bars]
    rsi = rsi_series(closes)
    sma = [None] * len(times)
    run, s = 0, 0.0
    for i, x in enumerate(closes):
        if x is None:
            run, s = 0, 0.0
            continue
        run += 1
        s += x
        if run > sma_hours:
            s -= closes[i - sma_hours]
        if run >= sma_hours:
            sma[i] = s / sma_hours
    day_ret = {}
    trades = 0
    in_pos, held, pending = False, 0, None   # pending: 'buy'/'sell' at next open
    last_px = None
    for i, t in enumerate(times):
        b = bars[i]
        d = (t - panel.start_ms) // DAY_MS
        g = 1.0
        if b is not None:
            if pending == "buy":
                last_px = b[0] * (1 + slip)
                g *= 1 - fee
                in_pos, held, trades = True, 0, trades + 1
            elif pending == "sell" and in_pos:
                g *= b[0] * (1 - slip) / last_px * (1 - fee)
                last_px, in_pos, trades = None, False, trades + 1
            pending = None
            if in_pos:
                g *= b[3] / last_px
                last_px = b[3]
                held += 1
            if rsi[i] is not None and sma[i] is not None:
                if not in_pos and rsi[i] < entry_below and b[3] > sma[i]:
                    pending = "buy"
                elif in_pos and (rsi[i] > exit_above or held >= max_hold or b[3] < sma[i]):
                    pending = "sell"
        if k_start <= d < k_end:
            day_ret[d] = day_ret.get(d, 1.0) * g
    return [day_ret.get(d, 1.0) - 1 for d in range(k_start, k_end)], trades
