import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autotrading.indicators import atr, ema, macd, rsi
from autotrading.market import Candle, parse_closed_candles, MarketData
from autotrading.strategy import evaluate


def candle(i, price, volume=100):
    return Candle(i * 900000, (i + 1) * 900000 - 1,
                  price, price + 1, price - 1, price, volume)


class TestIndicators(unittest.TestCase):
    def test_ema_seed(self):
        self.assertEqual(ema([1, 2, 3, 4], 3), [None, None, 2, 3])

    def test_rsi_increasing(self):
        self.assertEqual(rsi(list(range(30)), 14)[-1], 100.0)

    def test_rsi_flat(self):
        self.assertEqual(rsi([10.0] * 30, 14)[-1], 50.0)

    def test_macd_warmup(self):
        line, signal, hist = macd([float(x) for x in range(100)])
        self.assertIsNone(hist[32])
        self.assertIsNotNone(hist[33])
        self.assertEqual(len(line), 100)
        self.assertEqual(len(signal), 100)

    def test_atr_flat(self):
        series = [candle(i, 100) for i in range(50)]
        self.assertEqual(atr(series, 14)[-1], 2.0)


class TestMarketAndSignals(unittest.TestCase):
    def test_discard_open_candle(self):
        raw = [[0, "100", "101", "99", "100", "10", 1000],
               [1001, "100", "101", "99", "100", "11", 2000]]
        result = parse_closed_candles(raw, 2000)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].close_time_ms, 1000)

    def test_reject_out_of_order(self):
        raw = [[2, "100", "101", "99", "100", "10", 1000],
               [1, "100", "101", "99", "100", "11", 2000]]
        with self.assertRaises(ValueError):
            parse_closed_candles(raw, 3000)

    def test_market_rejects_unsupported_pair(self):
        with self.assertRaises(ValueError):
            MarketData().candles("BTCUSDT", "15m")

    def test_no_order_execution_and_no_forced_signal(self):
        # Monotonically rising series has no new EMA crossover on last bar.
        fast = [candle(i, 100 + i * 0.1) for i in range(300)]
        slow = [candle(i, 100 + i * 0.5) for i in range(300)]
        decision = evaluate("BTCUSDC", fast, slow)
        self.assertEqual(decision.action, "HOLD")
        self.assertEqual(decision.hourly_trend, "UP")
        self.assertIn("rsi14_15m", decision.indicators)


if __name__ == "__main__":
    unittest.main()
