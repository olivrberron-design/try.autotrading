"""Offline random-window Strategy Lab tests; deterministic and no API access."""
import unittest

from autotrading.indicators import ema
from autotrading.lab import (
    DAY_BARS, overlapped_fraction, regime_at, research, sample_windows,
)
from autotrading.market import Candle
from autotrading.catalog import CATALOG
from autotrading.rules import STRATEGIES
from strategy_lab import range_for_days, table

BAR = 900_000
HOUR = 3_600_000


def candle(i, step, price=100.0):
    return Candle(
        open_time_ms=i * step, close_time_ms=(i + 1) * step - 1,
        open=price, high=price + 1, low=price - 1,
        close=price, volume=100,
    )


class TestRandomWindows(unittest.TestCase):
    def test_reproducible_without_holdout_leak(self):
        first, boundary = 16 * DAY_BARS, 200 * DAY_BARS
        a = sample_windows(first, boundary, 12, seed=123)
        b = sample_windows(first, boundary, 12, seed=123)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 12)
        self.assertTrue(all(a[i][1] <= a[i+1][0] for i in range(len(a)-1)))
        self.assertEqual(overlapped_fraction(a), 0)
        self.assertTrue(all(first <= start < stop <= boundary
                            for start, stop in a))
        self.assertTrue(all((stop - start) % DAY_BARS == 0
                            for start, stop in a))

    def test_fifteen_distinct_strategies(self):
        self.assertEqual(len(CATALOG), 15)
        self.assertEqual(set(CATALOG), set(STRATEGIES))

    def test_invalid_windows_rejected(self):
        with self.assertRaises(ValueError):
            sample_windows(0, 90 * DAY_BARS, 24, seed=42)

    def test_overlap_not_independent(self):
        self.assertEqual(overlapped_fraction([(0, 5), (3, 8)]), 25.0)
        self.assertEqual(overlapped_fraction([(0, 4), (4, 8)]), 0.0)

    def test_regime_uses_only_past_closed_bars(self):
        hours = [candle(i, HOUR) for i in range(260)]
        closes = [c.close_time_ms for c in hours]
        trend = ema([c.close for c in hours], 50)
        slow = ema([c.close for c in hours], 200)
        moment = hours[220].open_time_ms
        base = regime_at(moment, closes, trend, slow)
        hours[240:] = [candle(i, HOUR, price=150.0) for i in range(240, 260)]
        trend2 = ema([c.close for c in hours], 50)
        slow2 = ema([c.close for c in hours], 200)
        self.assertEqual(base, regime_at(moment, closes, trend2, slow2))
        self.assertEqual(base, "sideways")

    def test_365_540_730_days(self):
        for days in (365, 540, 730):
            a, b, end = range_for_days(days, 1_000_000_000_000)
            self.assertEqual(b - a, 16 * 86_400_000)
            self.assertEqual(end + 1 - b, days * 86_400_000)

    def test_discovery_seals_holdout_until_validate(self):
        # ~216 days including 16 days of warmup, 200 days of trading.
        fast = [candle(i, BAR) for i in range(216 * DAY_BARS)]
        hours = [candle(i, HOUR) for i in range(216 * 24)]
        start = 16 * DAY_BARS * BAR
        report = research("BTCUSDC", fast, hours, trading_start_ms=start,
                          n_windows=4, seed=42)
        self.assertIsNone(report["holdout"])
        self.assertIsNone(report["selected_by_discovery_only"])
        self.assertEqual(len(report["sampled_windows"]), 4)
        self.assertEqual(report["overlap_pct_of_unique_discovery_bars"], 0.0)
        self.assertEqual(len(report["strategies"]), 15)
        self.assertTrue(all(r["start_regime_1h_ema"] == "sideways"
                            for r in report["sampled_windows"]))
        self.assertIn("Final chronological holdout NOT evaluated", table(report))

    def test_no_hidden_account_access(self):
        from autotrading.history import PUBLIC_HOST
        self.assertEqual(PUBLIC_HOST, "https://data-api.binance.vision")


if __name__ == "__main__":
    unittest.main()
