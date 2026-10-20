"""Offline no-lookahead, execution and walk-forward tests for Engine 3.0."""
from dataclasses import replace
import unittest
from autotrading.market import Candle
from autotrading.advanced_strategies import (
    AdvancedSignal, advanced_signals, STRATEGIES_V3,
)
from autotrading.engine3 import ExecutionConfig, rounded_quantity, simulate_v3
from autotrading.audit3 import audit_signals, audit_fills
from autotrading.history import current_spot_filters
from autotrading.lab import sample_windows
from advanced_lab import run_research

HOUR = 3_600_000
DAY = 86_400_000


def bar(i, step=HOUR, price=100, high=None, low=None):
    return Candle(
        open_time_ms=i*step, close_time_ms=(i+1)*step-1,
        open=float(price), close=float(price),
        high=float(high if high is not None else price+1),
        low=float(low if low is not None else price-1),
        volume=100.0,
    )


def dummy_actions(n, entries=(1,), exits=()):
    actions = [AdvancedSignal()] * n
    for i in entries:
        actions[i] = AdvancedSignal("ENTRY_CANDIDATE", 10.)
    for i in exits:
        actions[i] = AdvancedSignal("EXIT_CANDIDATE", 0.)
    return actions


class TestExecutionV3(unittest.TestCase):
    def test_no_same_close_execution_and_latency(self):
        c = [bar(i) for i in range(8)]
        sig = dummy_actions(8, entries=(1,), exits=(4,))
        a = simulate_v3(c, sig, 0, 8, ExecutionConfig(
            fee_bps=0, slippage_bps=0, spread_bps=0, latency_bars=1))
        b = simulate_v3(c, sig, 0, 8, ExecutionConfig(
            fee_bps=0, slippage_bps=0, spread_bps=0, latency_bars=2))
        self.assertEqual(a["trades"][0]["entry_utc"], "1970-01-01T02:00:00+00:00")
        self.assertEqual(b["trades"][0]["entry_utc"], "1970-01-01T03:00:00+00:00")

    def test_ambiguous_stop_and_profit_hits_choose_stop(self):
        candles = [bar(0), bar(1), bar(2, high=140, low=70), bar(3)]
        cfg = ExecutionConfig(
            fee_bps=0, slippage_bps=0, spread_bps=0,
            take_profit_r=2, trailing_atr_multiple=0)
        r = simulate_v3(candles, dummy_actions(4), 0, 4, cfg)
        self.assertEqual(r["closed_trades"], 1)
        self.assertEqual(r["trades"][0]["reason"], "stop_or_trailing_stop")
        self.assertAlmostEqual(r["trades"][0]["exit_price"], 90)

    def test_stop_gap_uses_worse_open(self):
        candles = [bar(0), bar(1), bar(2), bar(3, price=80, low=70), bar(4)]
        r = simulate_v3(candles, dummy_actions(5), 0, 5,
                        ExecutionConfig(fee_bps=0, slippage_bps=0, spread_bps=0,
                                        trailing_atr_multiple=0))
        self.assertEqual(r["trades"][0]["exit_price"], 80)

    def test_fee_spread_slippage_reduce_portfolio(self):
        c = [bar(i) for i in range(7)]
        sig = dummy_actions(7, exits=(4,))
        no = simulate_v3(c, sig, 0, 7, ExecutionConfig(
            fee_bps=0, slippage_bps=0, spread_bps=0))
        yes = simulate_v3(c, sig, 0, 7, ExecutionConfig(
            fee_bps=10, slippage_bps=5, spread_bps=2))
        self.assertLess(yes["return_pct"], no["return_pct"])
        self.assertGreater(yes["fees_paid_usdc"], 0)
        self.assertGreater(yes["estimated_spread_slippage_usdc"], 0)

    def test_market_lot_filter_rejects_below_min_notional(self):
        c = [bar(i) for i in range(6)]
        r = simulate_v3(c, dummy_actions(6), 0, 6, ExecutionConfig(
            min_notional="100000", fee_bps=0, slippage_bps=0, spread_bps=0))
        self.assertGreaterEqual(r["blocked_orders_by_filters"], 1)
        self.assertEqual(r["closed_trades"], 0)
        self.assertFalse(r["open_position_at_end"])

    def test_round_down_step_size(self):
        self.assertAlmostEqual(rounded_quantity(1.23456, "0.001"), 1.234)
        self.assertAlmostEqual(rounded_quantity(0.00000001, "0.00001"), 0)

    def test_research_accepts_2_5_and_5_pct_stop_risk_only(self):
        for value in (0.025, 0.05):
            cfg = ExecutionConfig(risk_fraction=value)
            cfg.validate()
            self.assertAlmostEqual(cfg.risk_fraction * cfg.capital_usdc,
                                   125 if value == 0.025 else 250)
        # A higher risk budget cannot override the spot allocation cap.
        candles = [bar(i) for i in range(9)]
        signals = dummy_actions(9, entries=(1,), exits=(4,))
        for value in (0.025, 0.05):
            result = simulate_v3(candles, signals, 0, 9, ExecutionConfig(
                risk_fraction=value, fee_bps=0, slippage_bps=0, spread_bps=0,
                trailing_atr_multiple=0))
            self.assertLessEqual(result["max_entry_allocation_pct_initial_capital"], 20.01)

    def test_invalid_costs_and_risk_refused(self):
        for values in ({"fee_bps": -1}, {"risk_fraction": 1},
                       {"risk_fraction": 0.050001},
                       {"latency_bars": 0}, {"spread_bps": float("nan")}):
            with self.assertRaises(ValueError):
                ExecutionConfig(**values).validate()

    def test_trade_notional_reconciles_and_audit_fills(self):
        c = [bar(i) for i in range(9)]
        sig = dummy_actions(9, entries=(1,), exits=(4,))
        cfg = ExecutionConfig(fee_bps=10, slippage_bps=5, spread_bps=2,
                              trailing_atr_multiple=0, risk_fraction=0.05)
        result = simulate_v3(c, sig, 0, 9, cfg)
        self.assertTrue(audit_fills(c, sig, result, cfg)["passed"])
        self.assertEqual(result["filled_buys"], 1)
        self.assertEqual(result["filled_sells"], 1)
        self.assertGreater(result["total_buy_notional_usdc"], 0)
        self.assertGreater(result["total_sell_notional_usdc"], 0)
        self.assertAlmostEqual(result["gross_traded_notional_usdc"],
                               result["total_buy_notional_usdc"] + result["total_sell_notional_usdc"], places=3)
        self.assertAlmostEqual(result["gross_traded_notional_pct_initial_capital"],
                               result["gross_traded_notional_usdc"] / 5000 * 100, places=2)
        trade = result["trades"][0]
        self.assertLessEqual(trade["entry_allocation_pct_equity"], 20.01)
        self.assertEqual(trade["size_limiter"], "allocation_cap")
        self.assertEqual(trade["signal_utc"], "1970-01-01T01:59:59.999000+00:00")

    def test_larger_allocation_can_increase_deployment_without_implying_profit(self):
        c = [bar(i) for i in range(9)]
        sig = dummy_actions(9, entries=(1,), exits=(4,))
        base = ExecutionConfig(fee_bps=0, slippage_bps=0, spread_bps=0,
                               trailing_atr_multiple=0, risk_fraction=0.05)
        a = simulate_v3(c, sig, 0, 9, replace(base, allocation=0.20))
        b = simulate_v3(c, sig, 0, 9, replace(base, allocation=0.80))
        self.assertGreater(b["total_buy_notional_usdc"], a["total_buy_notional_usdc"])
        self.assertLessEqual(b["peak_open_position_pct_equity"], 80.01)

    def test_mutated_fill_without_signal_is_caught(self):
        c = [bar(i) for i in range(9)]
        sig = dummy_actions(9, entries=(1,), exits=(4,))
        cfg = ExecutionConfig(fee_bps=0, slippage_bps=0, spread_bps=0, trailing_atr_multiple=0)
        result = simulate_v3(c, sig, 0, 9, cfg)
        mutated = dummy_actions(9, entries=(), exits=(4,))
        with self.assertRaises(AssertionError):
            audit_fills(c, mutated, result, cfg)

    def test_equity_metrics_without_trades(self):
        r = simulate_v3([bar(i) for i in range(72)],
                        dummy_actions(72, entries=()), 0, 72)
        self.assertAlmostEqual(r["return_pct"], 0)
        self.assertIsNone(r["profit_factor"])
        self.assertIsNone(r["win_rate_pct"])


class TestSignalsV3(unittest.TestCase):
    def test_closed_higher_timeframe_features_do_not_repaint(self):
        # Match hourly, fourhour and daily common clock origins.
        h = [bar(i, HOUR, 100+0.02*i) for i in range(320*24)]
        f = [bar(i, 4*HOUR, 100+0.08*i) for i in range(320*6)]
        d = [bar(i, DAY, 100+0.48*i) for i in range(320)]
        baseline = advanced_signals(h, f, d)
        self.assertEqual(set(baseline), set(STRATEGIES_V3))
        h2 = h + [bar(len(h), HOUR, 20000)]
        f2 = f + [bar(len(f), 4*HOUR, 20000)]
        d2 = d + [bar(len(d), DAY, 20000)]
        after = advanced_signals(h2, f2, d2)
        for name in STRATEGIES_V3:
            self.assertEqual(baseline[name], after[name][:len(h)])

    def test_turtle_daily_breakout_uses_previous_closed_days_not_future_high(self):
        # Current UTC day 220 has an eventual high=130, which is NOT known
        # at 12:59; its completed hourly 120 close should break prior
        # 20/55 completed daily highs of 101.
        h = [bar(i, HOUR) for i in range(270*24)]
        f = [bar(i, 4*HOUR) for i in range(270*6)]
        d = [bar(i, DAY) for i in range(270)]
        target = 220*24 + 12
        h[target] = bar(target, HOUR, 120, high=121, low=99)
        d[220] = bar(220, DAY, 100, high=130, low=99)
        out = advanced_signals(h, f, d)
        self.assertEqual(out["turtle_daily_20"][target].action, "ENTRY_CANDIDATE")
        self.assertEqual(out["turtle_daily_55"][target].action, "ENTRY_CANDIDATE")

    def test_independent_strategy_rules_audit_catches_mutations(self):
        h = [bar(i, HOUR) for i in range(260*24)]
        f = [bar(i, 4*HOUR) for i in range(260*6)]
        d = [bar(i, DAY) for i in range(260)]
        sig = advanced_signals(h, f, d)
        self.assertTrue(audit_signals(h, f, d, sig, until_index=235*24)["passed"])
        altered = {name: list(values) for name, values in sig.items()}
        altered["turtle_daily_20"][220*24] = AdvancedSignal("ENTRY_CANDIDATE", 10.0)
        with self.assertRaises(AssertionError):
            audit_signals(h, f, d, altered, until_index=235*24)

    def test_hourly_breakout_does_not_use_current_partial_day(self):
        h = [bar(i, HOUR, 100 + i*0.001) for i in range(300*24)]
        f = [bar(i, 4*HOUR, 100 + i*0.004) for i in range(300*6)]
        d = [bar(i, DAY, 100 + i*0.024) for i in range(300)]
        out = advanced_signals(h, f, d)
        self.assertTrue(all(isinstance(x, AdvancedSignal) for x in out["turtle_daily_20"]))

    def test_current_exchange_filters_parsing(self):
        def fetch(path, params):
            self.assertEqual(path, "/api/v3/exchangeInfo")
            return {"symbols": [{"symbol": "BTCUSDC", "status": "TRADING",
                     "filters": [
                        {"filterType": "LOT_SIZE", "stepSize": "0.00001", "minQty": "0.00001"},
                        {"filterType": "MARKET_LOT_SIZE", "stepSize": "0", "minQty": "0"},
                        {"filterType": "MIN_NOTIONAL", "minNotional": "5"},
                     ]}]}
        f = current_spot_filters("BTCUSDC", requester=fetch)
        self.assertEqual(f["step_size"], "0.00001")
        self.assertEqual(f["min_notional"], "5")

    def test_two_nonoverlapping_180_day_windows_fit_730_day_discovery(self):
        # 730 days * 75% = 547.5d discovery, 24 bars/day.
        boundary = 547 * 24
        w = sample_windows(0, boundary, 2, seed=42+180,
                           lengths=(180,), bars_per_day=24)
        self.assertEqual(len(w), 2)
        self.assertTrue(all((stop-start) == 180*24 for start, stop in w))
        self.assertLessEqual(w[0][1], w[1][0])
        self.assertLessEqual(w[1][1], boundary)

    def test_discovery_only_walkforward_uses_earlier_data(self):
        h = [bar(i, HOUR) for i in range(560*24)]
        f = [bar(i, 4*HOUR) for i in range(560*6)]
        d = [bar(i, DAY) for i in range(560)]
        r = run_research("BTCUSDC", h, f, d,
                         ExecutionConfig(), trading_start_ms=240*DAY,
                         requested_windows=12, seed=42)
        self.assertFalse(r["reserved_holdout_evaluated"])
        for fold in r["walk_forward"]["folds"]:
            self.assertLess(fold["training_end_utc"], fold["test_start_utc"])
            self.assertLess(fold["test_end_utc"], r["reserved_holdout_start_utc"])
        self.assertLessEqual(r["random_window_count_used"], 4)
        self.assertEqual(len(r["long_stress_windows"]), 1)
        self.assertEqual(r["long_stress_windows"][0]["duration_days"], 180)
        self.assertTrue(r["rule_audit"]["passed"])
        self.assertTrue(all(x["execution_audit"]["passed"] for x in r["continuous_discovery"].values()))
        self.assertEqual(len(r["capital_utilization_sensitivity"]["turtle_daily_20"]), 4)



if __name__ == "__main__":
    unittest.main()
