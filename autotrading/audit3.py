"""Independent replay assertions for advanced research signals and filled orders.

Raises AssertionError rather than publishing results when a rule or cash ledger
contradicts the documented strategy. No API keys and no trading endpoints.
"""
from bisect import bisect_right
from math import isclose
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
        assert e["notional_usdc"] <= cfg.capital_usdc * 1.05, "Spot leverage prohibited"
    for t in result["trades"]:
        assert t["entry_utc"] < t["exit_utc"], "Trade exits before entry"
        expected = t["exit_notional_usdc"] - t["exit_fee_usdc"] - t["entry_notional_usdc"] - t["entry_fee_usdc"]
        assert isclose(expected, t["pnl_usdc"], abs_tol=0.002), "Trade PnL accounting mismatch"
        assert t["entry_allocation_pct_equity"] <= cfg.allocation*100 + 0.01, "Position allocation cap exceeded"
        assert t["size_limiter"] in ("allocation_cap", "risk_budget")
    return {
        "passed": True, "buys_checked": len(entries),
        "sells_checked": len(sells),
        "closed_trades_checked": len(result["trades"]),
        "ledger_reconciled": True,
    }
