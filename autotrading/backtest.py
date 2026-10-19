"""Vectorized-in-time research signals and conservative next-bar spot simulation.

No network connections or API keys are used here. Signals known at the CLOSE
of a candle may only transact at the OPEN of the following 15-minute candle.
"""
from bisect import bisect_right
from datetime import datetime, timezone

from .indicators import ema, rsi, macd
from .rules import STRATEGIES
from .catalog import compute_catalog_signals


def iso_millis(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def compute_signals(fast, hourly):
    """Identical completed-candle signals in research and backtests."""
    return compute_catalog_signals(fast, hourly)


def _validate_costs(capital, allocation, fee_bps, slippage_bps):
    if capital <= 0 or not (0 < allocation <= 1):
        raise ValueError("Capital must be positive and allocation in (0, 1]")
    if not (0 <= fee_bps <= 100 and 0 <= slippage_bps <= 100):
        raise ValueError("Fees/slippage must be 0–100 bps")


def buy_hold(candles, capital, allocation, fee_bps, slippage_bps):
    """Buy at first bar OPEN, hold through end, mark liquidation at final CLOSE."""
    _validate_costs(capital, allocation, fee_bps, slippage_bps)
    fee, slip = fee_bps / 10_000, slippage_bps / 10_000
    invested_budget = capital * allocation
    units = invested_budget / (candles[0].open * (1 + slip) * (1 + fee))
    end_value = (capital - invested_budget
                 + units * candles[-1].close * (1 - slip) * (1 - fee))
    return round((end_value / capital - 1) * 100, 4)


def simulate(candles, signals, first, stop, capital=5000.0,
             allocation=0.2, fee_bps=10.0, slippage_bps=5.0):
    """Simulate [first, stop) with fresh virtual cash and no order crossing split.

    A completed 15m bar's signal executes only on the NEXT bar's open.
    No real Binance orders are ever sent. Unclosed positions are marked to
    approximate net-liquidation value for PnL/drawdown, not counted as wins.
    """
    _validate_costs(capital, allocation, fee_bps, slippage_bps)
    if stop - first < 3 or len(signals) != len(candles):
        raise ValueError("Invalid backtest period/signals")
    fee, slip = fee_bps / 10_000, slippage_bps / 10_000
    cash = float(capital)
    units = 0.0
    entry_price = entry_cost = 0.0
    entry_ms = None
    pending = None
    trades = []
    peak = cash
    max_dd = 0.0
    max_dd_abs = 0.0
    exposure_bars = 0
    fees_total = 0.0
    counts = {"ENTRY_CANDIDATE": 0, "EXIT_CANDIDATE": 0, "HOLD": 0}

    for i in range(first, stop):
        bar = candles[i]
        # Fill yesterday's *signal* at this bar's OPEN, with adverse slippage.
        if pending == "ENTRY_CANDIDATE" and units == 0:
            budget = cash * allocation
            entry_price = bar.open * (1 + slip)
            units = budget / (entry_price * (1 + fee))
            entry_fee = units * entry_price * fee
            fees_total += entry_fee
            entry_cost = units * entry_price + entry_fee
            cash -= entry_cost
            entry_ms = bar.open_time_ms
        elif pending == "EXIT_CANDIDATE" and units > 0:
            exit_price = bar.open * (1 - slip)
            proceeds = units * exit_price
            exit_fee = proceeds * fee
            fees_total += exit_fee
            net_proceeds = proceeds - exit_fee
            cash += net_proceeds
            pnl = net_proceeds - entry_cost
            trades.append({
                "entry_utc": iso_millis(entry_ms),
                "exit_utc": iso_millis(bar.open_time_ms),
                "entry_price": round(entry_price, 6),
                "exit_price": round(exit_price, 6),
                "quantity": round(units, 10),
                "pnl_usdc": round(pnl, 6),
                "pnl_pct_on_allocated": round(pnl / entry_cost * 100, 4)
            })
            units = 0.0
            entry_cost = entry_price = 0.0
            entry_ms = None

        if units:
            exposure_bars += 1
        marked_equity = cash + units * bar.close * (1 - slip) * (1 - fee)
        peak = max(peak, marked_equity)
        dd = max(0, (peak - marked_equity) / peak * 100)
        max_dd = max(max_dd, dd)
        max_dd_abs = max(max_dd_abs, peak - marked_equity)
        pending = None
        signal = signals[i]
        counts[signal] += 1
        if i < stop - 1:  # never carry a pending execution across a split
            if units == 0 and signal == "ENTRY_CANDIDATE":
                pending = signal
            elif units > 0 and signal == "EXIT_CANDIDATE":
                pending = signal

    final_equity = cash + units * candles[stop - 1].close * (1 - slip) * (1 - fee)
    wins = [t["pnl_usdc"] for t in trades if t["pnl_usdc"] > 0]
    losses = [t["pnl_usdc"] for t in trades if t["pnl_usdc"] < 0]
    closed = len(trades)
    pf = sum(wins) / -sum(losses) if losses else None
    return {
        "start_utc": iso_millis(candles[first].open_time_ms),
        "end_utc": iso_millis(candles[stop - 1].close_time_ms),
        "bars": stop - first,
        "starting_usdc": round(capital, 2),
        "ending_usdc_net_if_liquidated": round(final_equity, 4),
        "return_pct": round((final_equity / capital - 1) * 100, 4),
        "max_drawdown_pct": round(max_dd, 4),
        "max_drawdown_usdc": round(max_dd_abs, 4),
        "closed_trades": closed,
        "winning_trades": len(wins),
        "win_rate_pct": round(100 * len(wins) / closed, 2) if closed else None,
        "profit_factor": round(pf, 4) if pf is not None else None,
        "open_position_at_end": units > 0,
        "exposure_pct": round(100 * exposure_bars / (stop - first), 2),
        "fees_paid_on_fills_usdc": round(fees_total, 4),
        "signal_counts": counts,
        "buy_hold_equal_allocation_pct": buy_hold(candles[first:stop], capital, allocation, fee_bps, slippage_bps),
        "buy_hold_full_allocation_pct": buy_hold(candles[first:stop], capital, 1.0, fee_bps, slippage_bps),
        "trades": trades,
    }


def backtest(fast, hourly, *, start_ms, capital=5000.0,
             allocation=0.2, fee_bps=10.0, slippage_bps=5.0,
             train_fraction=0.7):
    if not 0.5 <= train_fraction <= 0.85:
        raise ValueError("Training fraction must be 0.50–0.85")
    first = next((i for i, bar in enumerate(fast) if bar.open_time_ms >= start_ms), None)
    if first is None or len(fast) - first < 300 or first < 200:
        raise ValueError("Not enough warmup or trading candles")
    split = first + int((len(fast) - first) * train_fraction)
    if split <= first + 2 or len(fast) <= split + 2:
        raise ValueError("Training or holdout period too short")

    signals = compute_signals(fast, hourly)
    results = {}
    for name in STRATEGIES:
        train = simulate(fast, signals[name], first, split, capital, allocation, fee_bps, slippage_bps)
        test = simulate(fast, signals[name], split, len(fast), capital, allocation, fee_bps, slippage_bps)
        results[name] = {"training": train, "holdout": test}
    # Selection uses TRAINING period only; holdout is merely reported.
    selected = max(STRATEGIES, key=lambda name: (
        results[name]["training"]["return_pct"]
        - 0.5 * results[name]["training"]["max_drawdown_pct"],
        -results[name]["training"]["max_drawdown_pct"],
    ))
    return {
        "selected_by_training_only": selected,
        "selection_rule": "train return % minus 0.5 * train max drawdown %",
        "train_fraction": train_fraction,
        "cost_assumptions": {
            "capital_usdc": capital, "allocation_fraction": allocation,
            "fee_bps_per_side": fee_bps, "slippage_bps_per_side": slippage_bps,
            "execution": "signal at completed 15m CLOSE; fill at next 15m OPEN",
            "holding": "cash + long-only spot; no leverage; no stop-loss modeled",
        },
        "strategies": results,
    }
