"""Offline no-lookahead, execution and walk-forward tests for Engine 3.0."""
from dataclasses import replace
import unittest
from autotrading.market import Candle
from autotrading.advanced_strategies import (
    AdvancedSignal, advanced_signals, STRATEGIES_V3,
)
from autotrading.engine3 import ExecutionConfig, rounded_quantity, simulate_v3
from autotrading.history import current_spot_filters
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

    def test_invalid_costs_and_risk_refused(self):
        for values in ({"fee_bps": -1}, {"risk_fraction": 1},
                       {"latency_bars": 0}, {"spread_bps": float("nan")}):
            with self.assertRaises(ValueError):
                ExecutionConfig(**values).validate()

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

    def test_discovery_only_walkforward_uses_earlier_data(self):
        h = [bar(i, HOUR) for i in range(510*24)]
        f = [bar(i, 4*HOUR) for i in range(510*6)]
        d = [bar(i, DAY) for i in range(510)]
        r = run_research("BTCUSDC", h, f, d,
                         ExecutionConfig(), trading_start_ms=240*DAY,
                         requested_windows=12, seed=42)
        self.assertFalse(r["reserved_holdout_evaluated"])
        for fold in r["walk_forward"]["folds"]:
            self.assertLess(fold["training_end_utc"], fold["test_start_utc"])
            self.assertLess(fold["test_end_utc"], r["reserved_holdout_start_utc"])
        self.assertLessEqual(r["random_window_count_used"], 4)


if __name__ == "__main__":
    unittest.main()
