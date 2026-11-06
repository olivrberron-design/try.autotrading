"""Independent replay assertions for advanced research signals and filled orders.

Raises AssertionError rather than publishing results when a rule or cash ledger
contradicts the documented strategy. No API keys and no trading endpoints.
"""
from bisect import bisect_right
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from math import isclose, isfinite
from .indicators import atr, ema, rsi


def audit_signals(hours, four, daily, signal_sets, until_index=None):
    """Re-derive expected actions from RAW COMPLETED bars, independently.

    Turtle audit checks every hourly action, including HOLD and absence of
    unearned entries. Trend audit checks every entry and exit (not future bars).
    """
    limit = min(until_index or len(hours), len(hours))
    dclose = [x.close_time_ms for x in daily]
    fclose = [x.close_time_ms for x in four]
    d_atr = atr(daily, 20)
    dp = [d.close for d in daily]
    d50, d200 = ema(dp, 50), ema(dp, 200)
    fp = [f.close for f in four]
    f20, f50 = ema(fp, 20), ema(fp, 50)
    hp = [h.close for h in hours]
    h20, h50 = ema(hp, 20), ema(hp, 50)
    strength = rsi(hp, 14)
    h_atr = atr(hours, 14)
    tally = {name: {"bars_checked": 0, "entries": 0, "exits": 0} for name in signal_sets}
    for i in range(1, limit):
        now = hours[i].close_time_ms
        j = bisect_right(dclose, now) - 1
        k = bisect_right(fclose, now) - 1
        if j >= 0:
            assert dclose[j] <= now, "Future daily candle used"
            assert j + 1 == len(dclose) or dclose[j + 1] > now, "Daily completed-index off-by-one"
        if k >= 0:
            assert fclose[k] <= now, "Future 4h candle used"
            assert k + 1 == len(fclose) or fclose[k + 1] > now, "4h completed-index off-by-one"
        if j < 200 or k < 51 or strength[i - 1] is None:
            for name in signal_sets:
                assert signal_sets[name][i].action == "HOLD", f"Premature {name} signal at {i}"
            continue

        for period, exit_days in ((20, 10), (55, 20)):
            name = f"turtle_daily_{period}"
            if name not in signal_sets:
                continue
            signal = signal_sets[name][i]
            assert len(daily[j-period+1:j+1]) == period
            high = max(x.high for x in daily[j-period+1:j+1])
            low = min(x.low for x in daily[j-exit_days+1:j+1])
            if d_atr[j] is None or d_atr[j] <= 0:
                expected = "HOLD"
            elif hp[i] > high and hp[i-1] <= high:
                expected = "ENTRY_CANDIDATE"
            elif hp[i] < low:
                expected = "EXIT_CANDIDATE"
            else:
                expected = "HOLD"
            assert signal.action == expected, (
                f"{name} {i}: actual {signal.action}, expected {expected}"
            )
            if expected == "ENTRY_CANDIDATE":
                assert isclose(signal.risk_distance, d_atr[j]*2, rel_tol=1e-8), "Turtle ATR risk distance mismatch"
            tally[name]["bars_checked"] += 1
            if expected == "ENTRY_CANDIDATE":
                tally[name]["entries"] += 1
            elif expected == "EXIT_CANDIDATE":
                tally[name]["exits"] += 1

        name = "multi_tf_trend"
        if name in signal_sets:
            sig = signal_sets[name][i]
            if any(v is None for v in (h20[i], h20[i-1], h50[i], f20[k], f50[k], h_atr[i])):
                expected = "HOLD"
            else:
                dtrend = d50[j] > d200[j] and dp[j] > d200[j]
                ftrend = f20[k] > f50[k] and fp[k] > f50[k]
                dip = four[k].low <= f20[k]*1.005 or (
                    k > 0 and four[k-1].low <= f20[k-1]*1.005
                )
                trigger = hp[i-1] <= h20[i-1] and hp[i] > h20[i] and strength[i] > 50
                entry = dtrend and ftrend and dip and trigger
                exit_ = not dtrend or not ftrend or (hp[i] < h50[i] and strength[i] < 45)
                expected = "ENTRY_CANDIDATE" if entry else ("EXIT_CANDIDATE" if exit_ else "HOLD")
            assert sig.action == expected, f"multi_tf_trend {i}: {sig.action} != {expected}"
            if expected == "ENTRY_CANDIDATE":
                assert isclose(sig.risk_distance, 2.5*h_atr[i], rel_tol=1e-8)
            tally[name]["bars_checked"] += 1
            if expected == "ENTRY_CANDIDATE":
                tally[name]["entries"] += 1
            elif expected == "EXIT_CANDIDATE":
                tally[name]["exits"] += 1
    return {"passed": True, "checked_until_hourly_index": limit, "rules": tally}


def audit_fills(candles, signal_set, result, cfg):
    """Verify execution follows a causal signal and reconciles fiat amounts."""
    actions_at = {bar.close_time_ms: signal_set[i].action for i, bar in enumerate(candles)}
    entries = [x for x in result["fill_events"] if x["action"] == "BUY"]
    sells = [x for x in result["fill_events"] if x["action"] == "SELL"]
    assert len(entries) == result["filled_buys"], "Filled BUY count mismatch"
    assert len(sells) == result["filled_sells"] == len(result["trades"]), "SELL ledger count mismatch"
    assert len(entries) - len(sells) == int(result["open_position_at_end"]), "Open position count inconsistent"
    assert isclose(sum(x["notional_usdc"] for x in entries),
                   result["total_buy_notional_usdc"], rel_tol=1e-6, abs_tol=0.001)
    assert isclose(sum(x["notional_usdc"] for x in sells),
                   result["total_sell_notional_usdc"], rel_tol=1e-6, abs_tol=0.001)
    for e in entries:
        from datetime import datetime
        signal_ms = round(datetime.fromisoformat(e["signal_utc"]).timestamp()*1000)
        fill_ms = round(datetime.fromisoformat(e["utc"]).timestamp()*1000)
        assert actions_at[signal_ms] == "ENTRY_CANDIDATE", "Fill has no eligible entry signal"
        assert fill_ms > signal_ms, "Fill predates completed signal"
        assert e["allocation_pct_initial_capital"] >= 0
        assert e["notional_usdc"] > 0
        assert e["notional_usdc"] <= e["equity_before_entry_usdc"] + 0.01, "Spot leverage prohibited"
        assert e["notional_usdc"] <= e["equity_before_entry_usdc"] * cfg.allocation + 0.02, "Position cap exceeded at entry"
    bars_by_close = {bar.close_time_ms: bar for bar in candles}
    for t in result["trades"]:
        assert t["entry_utc"] < t["exit_utc"], "Trade exits before entry"
        from datetime import datetime
        reason = t["reason"]
        if reason == "signal":
            stamp = t.get("exit_signal_utc")
            assert stamp is not None, "Signal exit lacks decision timestamp"
            signal_ms = round(datetime.fromisoformat(stamp).timestamp()*1000)
            exit_ms = round(datetime.fromisoformat(t["exit_utc"]).timestamp()*1000)
            assert actions_at[signal_ms] == "EXIT_CANDIDATE", "Exit without eligible exit signal"
            assert exit_ms > signal_ms, "Exit executed before signal close"
        elif reason in ("stop_or_trailing_stop", "take_profit"):
            exit_ms = round(datetime.fromisoformat(t["exit_utc"]).timestamp()*1000)
            bar = bars_by_close[exit_ms]
            if reason == "stop_or_trailing_stop":
                assert bar.low <= t["stop_level_at_exit"] + 1e-7, "Stop execution without hourly low breach"
                assert t["exit_price"] <= min(bar.open, t["stop_level_at_exit"]) + 1e-7
            else:
                assert bar.high >= t["target_level_at_exit"] - 1e-7, "Take-profit level never reached"
                assert t["exit_price"] <= t["target_level_at_exit"] + 1e-7
        else:
            assert reason == "circuit_breaker", "Undocumented exit reason"

        expected = t["exit_notional_usdc"] - t["exit_fee_usdc"] - t["entry_notional_usdc"] - t["entry_fee_usdc"]
        assert isclose(expected, t["pnl_usdc"], abs_tol=0.002), "Trade PnL accounting mismatch"
        assert t["entry_allocation_pct_equity"] <= cfg.allocation*100 + 0.01, "Position allocation cap exceeded"
        assert t["size_limiter"] in ("allocation_cap", "risk_budget")
    _audit_account(candles, signal_set, result, cfg)
    return {
        "passed": True, "buys_checked": len(entries),
        "sells_checked": len(sells),
        "closed_trades_checked": len(result["trades"]),
        "ledger_reconciled": True,
    }


def _audit_account(candles, signal_set, result, cfg):
    """Reconstruct cash and holdings from causal fills, never from reported PnL.

    Ledger values are rounded for JSON export. Recompute exact model prices and
    sizes from candles/config, then compare the exported values with explicit
    rounding tolerances. This also audits an unclosed final position.
    """
    def millis(value):
        return round(datetime.fromisoformat(value).timestamp() * 1000)

    def same(actual, expected, message, tolerance=0.002):
        assert isfinite(actual) and isfinite(expected) and isclose(
            actual, expected, rel_tol=1e-9, abs_tol=tolerance), message

    by_open = {bar.open_time_ms: i for i, bar in enumerate(candles)}
    by_close = {bar.close_time_ms: i for i, bar in enumerate(candles)}
    first = by_open[millis(result["start_utc"])]
    final = by_close[millis(result["end_utc"])]
    assert final >= first, "Invalid report period"
    same(result["starting_usdc"], cfg.capital_usdc, "Starting capital mismatch")
    assert result["bars"] == final - first + 1, "Report bar count mismatch"
    fee = cfg.fee_bps / 10_000
    impact = (cfg.slippage_bps + cfg.spread_bps / 2) / 10_000
    cash, quantity, fees, turnover = cfg.capital_usdc, 0.0, 0.0, 0.0
    previous_ms, active_entry, trade_index = -1, None, 0

    for event in result["fill_events"]:
        when = millis(event["utc"])
        assert when >= previous_ms, "Fill ledger is out of order"
        previous_ms = when
        assert candles[first].open_time_ms <= when <= candles[final].close_time_ms, "Fill outside report period"
        action = event["action"]
        if action == "BUY":
            assert quantity == 0, "Second BUY before previous position closes"
            signal_ms = millis(event["signal_utc"])
            signal_index = by_close[signal_ms]
            fill_index = by_open[when]
            assert first <= signal_index < fill_index <= final, "Entry crosses simulation boundary"
            assert fill_index == signal_index + cfg.latency_bars, "BUY violates configured execution latency"
            signal = signal_set[signal_index]
            assert signal.action == "ENTRY_CANDIDATE" and signal.risk_distance > 0, "Invalid BUY risk signal"
            price = candles[fill_index].open * (1 + impact)
            same(event["execution_price"], price, "BUY price does not match source candle", 1e-7)
            same(event["equity_before_entry_usdc"], cash, "Entry equity does not match cash ledger")
            quantity = min(cash * cfg.allocation / (price * (1 + fee)),
                           cfg.risk_fraction * cash / signal.risk_distance)
            step = Decimal(str(cfg.step_size))
            if step > 0:
                quantity = float((Decimal(str(quantity)) / step).to_integral_value(rounding=ROUND_DOWN) * step)
            gross = quantity * price
            same(event["notional_usdc"], gross, "BUY notional does not match risk/quantity sizing")
            assert quantity >= float(cfg.min_qty) and gross >= float(cfg.min_notional), "BUY violates quantity filters"
            same(event["allocation_pct_initial_capital"], 100 * gross / cfg.capital_usdc,
                 "Reported entry allocation mismatch", 0.0001)
            cash -= gross * (1 + fee)
            assert cash >= -0.002, "Negative cash in spot account"
            active_entry = (event, price, quantity, gross * (1 + fee))
        else:
            assert action == "SELL" and quantity > 0 and active_entry is not None, "SELL has no funded position"
            trade = result["trades"][trade_index]
            trade_index += 1
            entry, entry_price, entry_qty, entry_cost = active_entry
            assert trade["entry_utc"] == entry["utc"] and trade["exit_utc"] == event["utc"], "Trade/fill chronology mismatch"
            assert trade["reason"] == event["reason"], "Trade/fill exit reason mismatch"
            same(trade["quantity"], entry_qty, "SELL quantity does not match held quantity", 1e-8)
            same(trade["entry_price"], entry_price, "Trade entry price differs from BUY", 1e-7)
            reason = event["reason"]
            if reason in ("signal", "circuit_breaker"):
                signal_ms = millis(event["signal_utc"])
                fill_index = by_open[when]
                assert fill_index == by_close[signal_ms] + cfg.latency_bars, "SELL violates configured execution latency"
                reference = candles[fill_index].open
            elif reason == "stop_or_trailing_stop":
                reference = min(candles[by_close[when]].open, trade["stop_level_at_exit"])
            else:
                assert reason == "take_profit", "Unknown SELL reason"
                reference = trade["target_level_at_exit"]
            price = reference * (1 - impact)
            same(event["execution_price"], price, "SELL price does not match source candle/trigger", 1e-7)
            same(trade["exit_price"], price, "Trade exit price differs from SELL", 1e-7)
            gross = quantity * price
            same(event["notional_usdc"], gross, "SELL notional does not match held quantity")
            same(trade["exit_notional_usdc"], gross, "Trade exit notional mismatch")
            same(trade["entry_notional_usdc"], entry_qty * entry_price, "Trade entry notional mismatch")
            same(trade["entry_fee_usdc"], entry_qty * entry_price * fee, "Entry fee mismatch")
            same(trade["exit_fee_usdc"], gross * fee, "Exit fee mismatch")
            same(trade["pnl_usdc"], gross * (1 - fee) - entry_cost, "Trade PnL differs from cash flows")
            cash += gross * (1 - fee)
            quantity, active_entry = 0.0, None
        fees += gross * fee
        turnover += gross

    assert bool(quantity) == result["open_position_at_end"], "Final holdings mismatch"
    ending = cash + quantity * candles[final].close * (1 - impact) * (1 - fee)
    same(result["ending_usdc_net_if_liquidated"], ending, "Final equity does not reconcile to cash and holdings")
    same(result["return_pct"], 100 * (ending / cfg.capital_usdc - 1), "Reported return does not reconcile", 0.0001)
    same(result["fees_paid_usdc"], fees, "Paid fees do not reconcile")
    same(result["gross_traded_notional_usdc"], turnover, "Total turnover does not reconcile")
