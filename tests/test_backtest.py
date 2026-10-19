"""Offline backtesting tests: no API key, no network, no order submission."""
from datetime import datetime, timezone
import unittest

from autotrading.backtest import backtest, buy_hold, compute_signals, simulate
from autotrading.history import fetch_range, time_range, validate_candles
from autotrading.market import Candle


STEP = 900_000


def bar(i, price, interval_ms=STEP):
    return Candle(
        open_time_ms=i * interval_ms,
        close_time_ms=(i + 1) * interval_ms - 1,
        open=float(price), high=float(price) + 2,
        low=float(price) - 2, close=float(price), volume=100.0
    )


class TestBacktest(unittest.TestCase):
    def test_next_open_execution_and_completed_trade(self):
        # Signal on bar 1 close -> buy at bar 2 OPEN (100).
        # Exit signal on bar 3 close -> sell at bar 4 OPEN (120).
        candles = [bar(0, 100), bar(1, 100), bar(2, 100),
                   bar(3, 110), bar(4, 120), bar(5, 120)]
        actions = ["HOLD", "ENTRY_CANDIDATE", "HOLD",
                   "EXIT_CANDIDATE", "HOLD", "HOLD"]
        result = simulate(candles, actions, 0, 6,
                          capital=5000, allocation=0.2, fee_bps=0, slippage_bps=0)
        self.assertEqual(result["closed_trades"], 1)
        self.assertAlmostEqual(result["ending_usdc_net_if_liquidated"], 5200)
        self.assertEqual(result["trades"][0]["entry_price"], 100)
        self.assertEqual(result["trades"][0]["exit_price"], 120)

    def test_fees_slippage_lower_returns(self):
        candles = [bar(0, 100), bar(1, 100), bar(2, 100),
                   bar(3, 110), bar(4, 120), bar(5, 120)]
        signals = ["HOLD", "ENTRY_CANDIDATE", "HOLD", "EXIT_CANDIDATE", "HOLD", "HOLD"]
        free = simulate(candles, signals, 0, 6, fee_bps=0, slippage_bps=0)
        costly = simulate(candles, signals, 0, 6, fee_bps=10, slippage_bps=5)
        self.assertLess(costly["return_pct"], free["return_pct"])
        self.assertGreater(costly["fees_paid_on_fills_usdc"], 0)

    def test_no_split_boundary_leak(self):
        candles = [bar(i, 100) for i in range(6)]
        signals = ["HOLD", "ENTRY_CANDIDATE", "HOLD", "HOLD", "HOLD", "HOLD"]
        result = simulate(candles, signals, 2, 6, fee_bps=0, slippage_bps=0)
        self.assertEqual(result["return_pct"], 0)
        self.assertFalse(result["open_position_at_end"])

    def test_holdout_no_trades_reports_none(self):
        candles = [bar(i, 100) for i in range(6)]
        report = simulate(candles, ["HOLD"] * 6, 0, 6)
        self.assertEqual(report["closed_trades"], 0)
        self.assertIsNone(report["win_rate_pct"])
        self.assertIsNone(report["profit_factor"])

    def test_buy_hold_costs(self):
        candles = [bar(0, 100), bar(1, 110)]
        self.assertAlmostEqual(buy_hold(candles, 5000, 0.2, 0, 0), 2.0)
        self.assertLess(buy_hold(candles, 5000, 0.2, 10, 5), 2.0)

    def test_invalid_fee_rejected(self):
        candles = [bar(i, 100) for i in range(4)]
        with self.assertRaises(ValueError):
            simulate(candles, ["HOLD"] * 4, 0, 4, fee_bps=-1)

    def test_train_test_isolation(self):
        candles = [bar(i, 100) for i in range(1800)]
        hourly = [bar(i, 100, 3_600_000) for i in range(450)]
        result = backtest(candles, hourly, start_ms=candles[900].open_time_ms)
        self.assertEqual(set(result["strategies"]), {"baseline", "momentum", "mean_reversion"})
        for strat in result["strategies"].values():
            self.assertEqual(strat["training"]["return_pct"], 0.0)
            self.assertEqual(strat["holdout"]["return_pct"], 0.0)

    def test_hourly_future_does_not_change_old_signals(self):
        candles = [bar(i, 100) for i in range(1400)]
        hourly = [bar(i, 100, 3_600_000) for i in range(350)]
        before = compute_signals(candles, hourly)
        hourly.append(bar(350, 300, 3_600_000))
        after = compute_signals(candles, hourly)
        for name in before:
            self.assertEqual(before[name], after[name])


class TestHistoricalData(unittest.TestCase):
    def test_time_range_has_16_day_warmup(self):
        now = 2000 * STEP + 99
        first, trade_start, end = time_range(180, now)
        self.assertEqual(end + 1, 2000 * STEP)
        self.assertEqual(trade_start - first, 16 * 86400_000)

    def test_pagination_complete(self):
        rows = [
            [i * STEP, "100", "102", "98", "100", "5",
             (i + 1) * STEP - 1] for i in range(4)
        ]
        def fake(path, params):
            self.assertEqual(path, "/api/v3/klines")
            return [r for r in rows if r[0] >= params["startTime"]]
        candles = fetch_range("BTCUSDC", "15m", 0, 4 * STEP - 1, requester=fake)
        self.assertEqual(len(candles), 4)

    def test_gap_rejected(self):
        candles = [bar(0, 100), bar(1, 100), bar(3, 100)]
        with self.assertRaises(RuntimeError):
            validate_candles(candles, "15m", 0, 4 * STEP - 1)


if __name__ == "__main__":
    unittest.main()
