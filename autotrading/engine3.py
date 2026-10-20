"""Backtesting Engine 3.0: research-only hourly spot execution simulator.

Conservative rules:
* Only signals from CLOSED bars; execution no earlier than next bar's OPEN.
* Stops are monitored with OHLC. At bar open, a gap through a stop fills
  at the worse opening price. If target and stop both touch, stop wins.
* A trailing stop can ratchet upward ONLY after the bar closes; it cannot
  profit from knowing a high before the low on the same candle.
* Orders are rounded down against declared Binance MARKET_LOT_SIZE/LOT_SIZE,
  min quantity and MIN_NOTIONAL. Filters supplied by caller; live filters
  do not represent historical changes.
* Per-pair research accounts, not an aggregate multi-asset portfolio.
No Binance credentials or order submission exist in this module.
"""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from math import sqrt
from statistics import mean, pstdev
import math

from .backtest import buy_hold


@dataclass(frozen=True)
class ExecutionConfig:
    capital_usdc: float = 5000.0
    allocation: float = 0.20
    risk_fraction: float = 0.005
    max_account_loss_pct: float = 15.0
    fee_bps: float = 10.0
    slippage_bps: float = 5.0
    spread_bps: float = 2.0
    latency_bars: int = 1
    trailing_atr_multiple: float = 2.0
    take_profit_r: float = 0.0  # optional, 0 disables target
    min_qty: str = "0"
    step_size: str = "0"
    min_notional: str = "0"

    def validate(self):
        numeric = (self.capital_usdc, self.allocation, self.risk_fraction,
                   self.max_account_loss_pct, self.fee_bps, self.slippage_bps,
                   self.spread_bps, self.trailing_atr_multiple, self.take_profit_r)
        if any(not math.isfinite(v) for v in numeric):
            raise ValueError("Non-finite simulation parameter")
        if not (self.capital_usdc > 0 and 0 < self.allocation <= 1
                and 0 < self.risk_fraction <= 0.05
                and 0 < self.max_account_loss_pct <= 100):
            raise ValueError("Unsafe capital/exposure/risk settings")
        if any(not 0 <= x <= 100 for x in (
                self.fee_bps, self.slippage_bps, self.spread_bps)):
            raise ValueError("Costs must be in 0..100 bps")
        if not (1 <= self.latency_bars <= 8) or self.trailing_atr_multiple < 0 or self.take_profit_r < 0:
            raise ValueError("Invalid latency, trailing or target settings")
        for v in (self.min_qty, self.step_size, self.min_notional):
            if Decimal(str(v)) < 0 or not Decimal(str(v)).is_finite():
                raise ValueError("Invalid Binance quantity filters")


def rounded_quantity(qty, step_size):
    if qty <= 0:
        return 0.0
    step = Decimal(str(step_size))
    if step <= 0:
        return float(qty)
    d = Decimal(str(qty))
    return float((d / step).to_integral_value(rounding=ROUND_DOWN) * step)


def utc(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def simulate_v3(candles, signals, first, stop, cfg=ExecutionConfig()):
    cfg.validate()
    if len(candles) != len(signals) or first < 0 or stop > len(candles) or stop - first < 3:
        raise ValueError("Invalid simulation bounds")
    buy_load = (cfg.slippage_bps + cfg.spread_bps / 2) / 10_000
    fee = cfg.fee_bps / 10_000
    cash = cfg.capital_usdc
    qty = 0.0
    cost = 0.0
    entry_ms = None
    entry_px = 0.0
    stop_px = 0.0
    target_px = 0.0
    distance = 0.0
    pending = None
    closed = []
    equity_daily = []
    max_equity = cfg.capital_usdc
    max_dd = 0.0
    fee_paid = 0.0
    impact_paid = 0.0
    turnover = 0.0
    exposure_bars = 0
    blocked_orders = 0
    circuit_breaker = False
    entry_notional_total = 0.0
    exit_notional_total = 0.0
    peak_position_notional = 0.0
    peak_allocation_equity_pct = 0.0
    gross_exposure_sum_pct = 0.0
    entries_count = 0
    entry_allocations = []
    fill_events = []
    entry_details = {}

    def close_position(px, when_ms, reason, reference):
        nonlocal cash, qty, cost, entry_px, entry_ms, fee_paid
        nonlocal turnover, impact_paid, stop_px, target_px, distance
        nonlocal exit_notional_total, entry_details
        net = qty * px * (1 - fee)
        exit_fee = qty * px * fee
        fee_paid += exit_fee
        impact_paid += max(0, reference - px) * qty
        turnover += qty * px
        exit_notional_total += qty * px
        pnl = net - cost
        cash += net
        closed.append({
            "entry_utc": utc(entry_ms), "exit_utc": utc(when_ms),
            "entry_price": round(entry_px, 8), "exit_price": round(px, 8),
            "quantity": round(qty, 10), "reason": reason,
            "entry_notional_usdc": round(cost - (cost * fee / (1 + fee)), 5),
            "exit_notional_usdc": round(qty * px, 5),
            "entry_fee_usdc": round(cost * fee / (1 + fee), 5),
            "exit_fee_usdc": round(exit_fee, 5),
            "holding_hours": round((when_ms - entry_ms) / 3_600_000, 3),
            **entry_details,
            "pnl_usdc": round(pnl, 5),
            "pnl_pct_on_allocated": round(pnl / cost * 100, 4),
        })
        fill_events.append({"action": "SELL", "reason": reason, "utc": utc(when_ms),
                            "notional_usdc": round(qty * px, 5),
                            "execution_price": round(px, 8)})
        entry_details = {}
        qty = cost = entry_px = stop_px = target_px = distance = 0.0
        entry_ms = None

    for i in range(first, stop):
        candle = candles[i]
        # Pending close-of-bar signals execute on a LATER bar open.
        if pending and pending[0] == i:
            _, action, risk, signal_ms = pending
            if action == "EXIT_CANDIDATE" and qty > 0:
                reference = candle.open
                close_position(reference * (1 - buy_load), candle.open_time_ms,
                               "signal", reference)
            elif action == "ENTRY_CANDIDATE" and qty == 0 and not circuit_breaker and risk > 0:
                reference = candle.open
                entry_price = reference * (1 + buy_load)
                eq = cash
                max_budget = min(cash, eq * cfg.allocation)
                allocation_limit_units = max_budget / (entry_price * (1 + fee))
                risk_limit_units = cfg.risk_fraction * eq / risk
                desired_qty = min(allocation_limit_units, risk_limit_units)
                units = rounded_quantity(desired_qty, cfg.step_size)
                if units < float(cfg.min_qty) or units * entry_price < float(cfg.min_notional) or units <= 0:
                    blocked_orders += 1
                else:
                    gross = units * entry_price
                    entry_fee = gross * fee
                    if gross + entry_fee <= cash * (1 + 1e-10):
                        qty = units
                        entry_px = entry_price
                        cost = gross + entry_fee
                        cash -= cost
                        entry_ms = candle.open_time_ms
                        distance = risk
                        stop_px = max(0.00000001, entry_px - distance)
                        target_px = entry_px + cfg.take_profit_r * distance if cfg.take_profit_r else 0.0
                        fee_paid += entry_fee
                        impact_paid += qty * (entry_px - reference)
                        turnover += gross
                        entry_notional_total += gross
                        entries_count += 1
                        entry_allocations.append(100 * gross / cfg.capital_usdc)
                        entry_details = {
                            "signal_utc": utc(signal_ms),
                            "entry_allocation_pct_initial_capital": round(100 * gross / cfg.capital_usdc, 4),
                            "entry_allocation_pct_equity": round(100 * gross / eq, 4),
                            "risk_budget_usdc": round(cfg.risk_fraction * eq, 5),
                            "stop_distance_usdc_per_unit": round(risk, 8),
                            "initial_stop_price": round(stop_px, 8),
                            "size_limiter": "risk_budget" if risk_limit_units < allocation_limit_units else "allocation_cap",
                        }
                        fill_events.append({
                            "action": "BUY", "utc": utc(candle.open_time_ms),
                            "signal_utc": utc(signal_ms), "notional_usdc": round(gross, 5),
                            "execution_price": round(entry_price, 8),
                            "allocation_pct_initial_capital": round(100 * gross / cfg.capital_usdc, 4),
                        })
                    else:
                        blocked_orders += 1
            pending = None

        if qty > 0:
            exposure_bars += 1
            live_notional = qty * candle.close
            peak_position_notional = max(peak_position_notional, live_notional)
            running_equity = cash + live_notional
            percent = 100 * live_notional / running_equity if running_equity > 0 else 0
            gross_exposure_sum_pct += percent
            peak_allocation_equity_pct = max(peak_allocation_equity_pct, percent)
            # STOP wins ambiguous intrabar order. A stop gap is filled at OPEN.
            if candle.low <= stop_px:
                reference = min(candle.open, stop_px)
                close_position(reference * (1 - buy_load), candle.close_time_ms,
                               "stop_or_trailing_stop", reference)
            elif target_px and candle.high >= target_px:
                # Target is a *limit* in reality; conservative price capped
                # at the target, with adverse cost for uncertainty.
                reference = target_px
                close_position(reference * (1 - buy_load), candle.close_time_ms,
                               "take_profit", reference)
            elif cfg.trailing_atr_multiple > 0:
                # Trailing update AFTER checking current candle's low; the
                # new stop becomes effective on the next candle only.
                next_stop = candle.high - distance * cfg.trailing_atr_multiple / 2.0
                stop_px = max(stop_px, min(next_stop, candle.close))

        # Hypothetical liquidation net of fees, spread and adverse slip.
        marked_equity = cash + qty * candle.close * (1 - buy_load) * (1 - fee)
        max_equity = max(max_equity, marked_equity)
        max_dd = max(max_dd, 100 * (1 - marked_equity / max_equity))
        if marked_equity <= cfg.capital_usdc * (1 - cfg.max_account_loss_pct / 100):
            circuit_breaker = True

        if (i == stop - 1 or
                datetime.fromtimestamp(candle.close_time_ms / 1000, timezone.utc).date() !=
                datetime.fromtimestamp(candles[i + 1].close_time_ms / 1000, timezone.utc).date()):
            equity_daily.append({
                "utc": utc(candle.close_time_ms),
                "equity_usdc": round(marked_equity, 6),
            })
        if i < stop - cfg.latency_bars:
            if circuit_breaker and qty > 0 and pending is None:
                pending = (i + cfg.latency_bars, "EXIT_CANDIDATE", 0, candle.close_time_ms)
            elif pending is None:
                signal = signals[i]
                if qty == 0 and signal.action == "ENTRY_CANDIDATE" and not circuit_breaker:
                    pending = (i + cfg.latency_bars, signal.action, signal.risk_distance, candle.close_time_ms)
                elif qty > 0 and signal.action == "EXIT_CANDIDATE":
                    pending = (i + cfg.latency_bars, signal.action, 0, candle.close_time_ms)

    final_equity = cash + qty * candles[stop - 1].close * (1 - buy_load) * (1 - fee)
    daily_returns = []
    previous = cfg.capital_usdc
    for p in equity_daily:
        v = p["equity_usdc"]
        if previous > 0:
            daily_returns.append(v / previous - 1)
        previous = v
    sharpe = None
    if len(daily_returns) >= 3 and pstdev(daily_returns) > 1e-12:
        sharpe = mean(daily_returns) / pstdev(daily_returns) * sqrt(365)
    wins = [t["pnl_usdc"] for t in closed if t["pnl_usdc"] > 0]
    losses = [t["pnl_usdc"] for t in closed if t["pnl_usdc"] < 0]
    profit_factor = sum(wins) / -sum(losses) if losses else None
    bh20 = buy_hold(candles[first:stop], cfg.capital_usdc, cfg.allocation,
                    cfg.fee_bps, cfg.slippage_bps + cfg.spread_bps / 2)
    return {
        "start_utc": utc(candles[first].open_time_ms),
        "end_utc": utc(candles[stop - 1].close_time_ms),
        "bars": stop - first,
        "starting_usdc": cfg.capital_usdc,
        "ending_usdc_net_if_liquidated": round(final_equity, 4),
        "return_pct": round(100 * (final_equity / cfg.capital_usdc - 1), 4),
        "max_drawdown_pct": round(max_dd, 4),
        "closed_trades": len(closed),
        "win_rate_pct": round(100 * len(wins) / len(closed), 2) if closed else None,
        "profit_factor": round(profit_factor, 4) if profit_factor is not None else None,
        "sharpe_annualized_365d": round(sharpe, 4) if sharpe is not None else None,
        "turnover_pct_initial_capital": round(100 * turnover / cfg.capital_usdc, 2),
        "filled_buys": entries_count,
        "filled_sells": len(closed),
        "total_buy_notional_usdc": round(entry_notional_total, 4),
        "total_sell_notional_usdc": round(exit_notional_total, 4),
        "gross_traded_notional_usdc": round(turnover, 4),
        "gross_traded_notional_pct_initial_capital": round(100 * turnover / cfg.capital_usdc, 2),
        "avg_entry_allocation_pct_initial_capital": round(sum(entry_allocations) / len(entry_allocations), 3) if entry_allocations else None,
        "max_entry_allocation_pct_initial_capital": round(max(entry_allocations), 3) if entry_allocations else None,
        "peak_open_position_notional_usdc": round(peak_position_notional, 4),
        "peak_open_position_pct_equity": round(peak_allocation_equity_pct, 3),
        "mean_position_pct_equity_over_all_bars": round(gross_exposure_sum_pct / (stop - first), 3),
        "exposure_pct": round(100 * exposure_bars / (stop - first), 2),
        "fees_paid_usdc": round(fee_paid, 4),
        "estimated_spread_slippage_usdc": round(impact_paid, 4),
        "blocked_orders_by_filters": blocked_orders,
        "circuit_breaker_triggered": circuit_breaker,
        "open_position_at_end": qty > 0,
        "buy_hold_equal_allocation_pct": bh20,
        "excess_vs_bh20_pct": round(100 * (final_equity / cfg.capital_usdc - 1) - bh20, 4),
        "cash_benchmark_pct": 0.0,
        "trades": closed,
        "fill_events": fill_events,
        "equity_curve_daily": equity_daily,
    }
