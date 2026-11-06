"""Hourly execution robustness for fixed DAILY targets, without API or orders.

Rebalance once per UTC day at 01:00 by default, using the target from the
completed daily candle selected by PortfolioConfig.delay_days. Positions are
carried between rebalances. No intraday signals, stops, or target optimization.
Fees, adverse execution, quantity steps, minimum notional and rebalance band
match the daily portfolio model. Intraday extremes are not equity observations:
hourly-close drawdown still understates possible intrahour drawdown.
"""
from math import isfinite, isclose, sqrt
from statistics import mean, pstdev

from .portfolio_research import PortfolioConfig, check_series
from .research_data import DAY

HOUR = 3_600_000


def _hourly_interval(series, symbols, start, end):
    if (not isinstance(start, int) or not isinstance(end, int)
            or start % HOUR or end % HOUR or start >= end):
        raise ValueError("Hourly interval must be a nonempty aligned [start,end) range")
    if set(series) != set(symbols):
        raise ValueError("Hourly and daily universes must match exactly")
    selected = {}
    for symbol in symbols:
        bars = [c for c in series[symbol] if start <= c.open_time_ms < end]
        if len(bars) != (end - start) // HOUR:
            raise ValueError("Incomplete hourly coverage; no gap filling")
        for i, c in enumerate(bars):
            if (not isinstance(c.open_time_ms, int) or not isinstance(c.close_time_ms, int)
                    or c.open_time_ms != start + i * HOUR
                    or c.close_time_ms != start + (i + 1) * HOUR - 1):
                raise ValueError("Hourly timestamps must be contiguous, aligned and closed")
            if (not all(isfinite(v) for v in (c.open, c.high, c.low, c.close, c.volume))
                    or not 0 < c.low <= min(c.open, c.close) <= max(c.open, c.close) <= c.high
                    or c.volume < 0):
                raise ValueError("Invalid hourly OHLCV")
        selected[symbol] = bars
    return selected


def simulate_hourly_targets(hourly_series, daily_series, targets, start_ms,
                            end_exclusive_ms, cfg=PortfolioConfig(), execution_hour=1):
    """Replay daily targets with one daily execution and hourly liquidation marks.

    Returns equity_curve_hourly, equity_curve_daily and daily_returns, plus
    the fill ledger and summary metrics. Daily observations use each UTC day's
    last observed close; partial first/last days are explicitly flagged. Daily
    returns start from the initial cash and therefore compound to total return.
    This is a closed-historical-bar simulator: the caller supplies observed
    candles, and exact close timestamps/coverage are checked against the range.
    """
    cfg.validate()
    if (not isinstance(execution_hour, int) or isinstance(execution_hour, bool)
            or not 0 <= execution_hour <= 23):
        raise ValueError("Execution hour must be an integer UTC hour in 0..23")
    daily_count = check_series(daily_series)
    if len(targets) != daily_count:
        raise ValueError("Daily target length must match daily history")
    symbols = sorted(daily_series)
    hours = _hourly_interval(hourly_series, symbols, start_ms, end_exclusive_ms)
    clock = hours[symbols[0]]
    days = daily_series[symbols[0]]
    by_day = {c.open_time_ms: i for i, c in enumerate(days)}
    fee, impact = cfg.fee_bps / 10000, cfg.impact_bps / 10000
    cash = cfg.capital
    quantities = {s: 0.0 for s in symbols}
    ledger, hourly_curve, daily_curve, hourly_returns, daily_returns = [], [], [], [], []
    fees = turnover = exposure_sum = peak_exposure = 0.0
    peak = daily_peak = previous_hour = previous_day = cfg.capital
    drawdown = daily_drawdown = 0.0
    rebalance_days = 0

    for i, bar in enumerate(clock):
        day_start = bar.open_time_ms // DAY * DAY
        if bar.open_time_ms == day_start + execution_hour * HOUR:
            signal_day = day_start - cfg.delay_days * DAY
            if signal_day not in by_day:
                raise ValueError("Missing completed daily target before execution")
            signal_index = by_day[signal_day]
            signal_ms = days[signal_index].close_time_ms
            if signal_ms >= bar.open_time_ms:
                raise AssertionError("Daily target must be known before the fill")
            target = targets[signal_index]
            if (set(target) - set(symbols)
                    or any(not isfinite(v) or v < 0 for v in target.values())
                    or sum(target.values()) > cfg.cap + 1e-10):
                raise ValueError("Invalid daily target weights")
            prices = {s: hours[s][i].open for s in symbols}
            equity = cash + sum(quantities[s] * prices[s] for s in symbols)
            desired = {s: equity * target.get(s, 0) / prices[s] for s in symbols}
            rebalance_days += 1
            for side in ("SELL", "BUY"):
                orders = []
                for symbol in symbols:
                    difference = desired[symbol] - quantities[symbol]
                    if ((side == "SELL" and difference >= 0)
                            or (side == "BUY" and difference <= 0)):
                        continue
                    if (abs(difference) * prices[symbol] / equity < cfg.rebalance_band
                            and target.get(symbol, 0) > 0):
                        continue
                    orders.append((symbol, abs(difference)))
                required = sum(q * prices[s] * (1 + impact) * (1 + fee) for s, q in orders)
                scale = min(1., cash / required) if side == "BUY" and required else 1.
                for symbol, quantity in orders:
                    quantity = quantity * scale if side == "BUY" else min(quantity, quantities[symbol])
                    step = .00001 if symbol.startswith("BTC") else .0001
                    quantity = int((quantity + 1e-12) / step) * step
                    price = prices[symbol] * (1 + impact if side == "BUY" else 1 - impact)
                    notional = quantity * price
                    if quantity <= 0 or notional < cfg.min_notional:
                        continue
                    commission = notional * fee
                    if side == "BUY":
                        cash -= notional + commission
                        quantities[symbol] += quantity
                    else:
                        cash += notional - commission
                        quantities[symbol] -= quantity
                    fees += commission
                    turnover += notional
                    ledger.append({"hour_index": i, "target_index": signal_index,
                                   "symbol": symbol, "side": side,
                                   "signal_ms": signal_ms, "fill_ms": bar.open_time_ms,
                                   "quantity": quantity, "reference_price": prices[symbol],
                                   "price": price, "notional": notional, "fee": commission,
                                   "cash_after": cash})
        if cash < -1e-7 or any(q < -1e-10 for q in quantities.values()):
            raise AssertionError("Hourly replay violated spot cash/position conservation")
        gross = sum(quantities[s] * hours[s][i].close for s in symbols)
        marked = cash + gross * (1 - impact) * (1 - fee)
        peak = max(peak, marked)
        drawdown = max(drawdown, 1 - marked / peak)
        exposure = gross / (cash + gross)
        exposure_sum += exposure
        peak_exposure = max(peak_exposure, exposure)
        hourly_returns.append(marked / previous_hour - 1)
        previous_hour = marked
        point = {"open_ms": bar.open_time_ms, "close_ms": bar.close_time_ms,
                 "equity": marked, "cash": cash, "gross_position": gross}
        hourly_curve.append(point)
        if i == len(clock) - 1 or clock[i + 1].open_time_ms // DAY != bar.open_time_ms // DAY:
            partial = start_ms > day_start or end_exclusive_ms < day_start + DAY
            daily_curve.append({**point, "day_open_ms": day_start, "partial_day": partial})
            daily_returns.append(marked / previous_day - 1)
            previous_day = marked
            daily_peak = max(daily_peak, marked)
            daily_drawdown = max(daily_drawdown, 1 - marked / daily_peak)

    # A second cash/quantity pass does not trust state accumulated above.
    replay_cash = cfg.capital
    replay_quantity = {s: 0.0 for s in symbols}
    for event in ledger:
        side = 1 if event["side"] == "BUY" else -1
        expected = hours[event["symbol"]][event["hour_index"]].open * (1 + side * impact)
        if not isclose(event["price"], expected, rel_tol=1e-12, abs_tol=1e-10):
            raise AssertionError("Hourly replay price does not match the source open")
        replay_cash -= side * event["quantity"] * expected + event["fee"]
        replay_quantity[event["symbol"]] += side * event["quantity"]
    replay_end = replay_cash + sum(replay_quantity[s] * hours[s][-1].close * (1 - impact) * (1 - fee) for s in symbols)
    if (not isclose(replay_end, marked, rel_tol=1e-12, abs_tol=1e-6)
            or not isclose(replay_cash, cash, rel_tol=1e-12, abs_tol=1e-6)
            or any(not isclose(replay_quantity[s], quantities[s], rel_tol=1e-12, abs_tol=1e-10) for s in symbols)):
        raise AssertionError("Hourly ending account does not reconcile")
    total = marked / cfg.capital - 1
    sd = pstdev(daily_returns)
    return {"return_pct": 100 * total,
            "cagr_pct": 100 * ((1 + total) ** (365 * 24 / len(clock)) - 1),
            "max_hourly_close_drawdown_pct": 100 * drawdown,
            "max_daily_close_drawdown_pct": 100 * daily_drawdown,
            "daily_sharpe": mean(daily_returns) / sd * sqrt(365) if sd > 1e-12 and not any(p["partial_day"] for p in daily_curve) else None,
            "fills": len(ledger), "buys": sum(e["side"] == "BUY" for e in ledger),
            "sells": sum(e["side"] == "SELL" for e in ledger),
            "fees": fees, "turnover": turnover,
            "mean_hourly_close_exposure_pct": 100 * exposure_sum / len(clock),
            "peak_hourly_close_exposure_pct": 100 * peak_exposure,
            "ending_equity": marked, "cash": cash, "positions": quantities,
            "execution_hour_utc": execution_hour, "rebalance_days": rebalance_days,
            "start_ms": start_ms, "end_exclusive_ms": end_exclusive_ms,
            "equity_curve_hourly": hourly_curve, "equity_curve_daily": daily_curve,
            "hourly_returns": hourly_returns, "daily_returns": daily_returns,
            "ledger": ledger, "audit_passed": True}
