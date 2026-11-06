"""Causality, data alignment, and portfolio constraints of daily hypotheses."""
from dataclasses import replace
from math import sin
import unittest

from autotrading.market import Candle
from autotrading.research_strategies import (
    build_targets, MAX_ALLOCATION, STRATEGY_SPECS, WARMUP_BARS,
)

DAY = 86_400_000


def candles(n, drift=0.001, phase=0):
    result = []
    previous = 100.0
    for i in range(n):
        close = previous * (1 + drift + 0.012 * sin(i * 0.31 + phase))
        result.append(Candle(i * DAY, (i + 1) * DAY - 1, previous,
                             max(close, previous) * 1.001,
                             min(close, previous) * 0.999, close, 100.0))
        previous = close
    return result


class TestDailyResearchStrategies(unittest.TestCase):
    def test_future_extension_cannot_change_any_past_target(self):
        series = {"BTCUSDT": candles(710), "ETHUSDT": candles(710, phase=1.2)}
        complete = build_targets(series)
        for cut in (239, 241, 305, 509):
            prefix = build_targets({k: v[:cut] for k, v in series.items()})
            for strategy in complete:
                self.assertEqual(prefix[strategy], complete[strategy][:cut], strategy)

    def test_future_price_changes_cannot_change_earlier_targets(self):
        series = {"BTCUSDT": candles(430), "ETHUSDT": candles(430, phase=1.2)}
        base = build_targets(series)
        altered = {k: [replace(b, open=b.open * 2, high=b.high * 2,
                              low=b.low * 2, close=b.close * 2) if i >= 350 else b
                       for i, b in enumerate(v)] for k, v in series.items()}
        changed = build_targets(altered)
        for strategy in base:
            self.assertEqual(base[strategy][:350], changed[strategy][:350], strategy)

    def test_allocation_cap_cash_warmup_and_no_shorts(self):
        out = build_targets({"A": candles(800), "B": candles(800, -0.0005, 1.7),
                             "C": candles(800, 0.002, 0.8)})
        self.assertEqual(set(out), set(STRATEGY_SPECS))
        for strategy, targets in out.items():
            self.assertTrue(all(not t for t in targets[:WARMUP_BARS]), strategy)
            for target in targets:
                self.assertLessEqual(sum(target.values()), MAX_ALLOCATION + 1e-14, strategy)
                self.assertTrue(all(0 < w <= MAX_ALLOCATION for w in target.values()), strategy)

    def test_asset_order_does_not_change_targets(self):
        a, b = candles(400), candles(400, phase=0.8)
        self.assertEqual(build_targets({"A": a, "B": b}), build_targets({"B": b, "A": a}))

    def test_monotonic_trend_activates_breakouts_and_momentum(self):
        bars = []
        for i in range(300):
            p = 100.0 + i
            bars.append(Candle(i * DAY, (i + 1) * DAY - 1, p, p, p, p, 100))
        out = build_targets({"A": bars})
        for name in ("momentum_vote", "daily_breakout_ensemble", "sma200_trend",
                     "adaptive_efficiency_trend", "dual_momentum_rotation"):
            self.assertEqual(out[name][240], {"A": 0.20}, name)
        self.assertEqual(out["rsi2_trend_dip"][240], {})

    def test_alignment_gap_and_invalid_candles_fail(self):
        bars = candles(260)
        with self.assertRaises(ValueError):
            build_targets({"A": bars, "B": bars[:-1]})
        shifted = [replace(b, open_time_ms=b.open_time_ms + DAY,
                           close_time_ms=b.close_time_ms + DAY) for b in bars]
        with self.assertRaises(ValueError):
            build_targets({"A": bars, "B": shifted})
        with self.assertRaises(ValueError):
            build_targets({"A": bars[:200] + bars[201:]})
        with self.assertRaises(ValueError):
            build_targets({"A": [replace(bars[0], close=float("nan"))] + bars[1:]})
        with self.assertRaises(ValueError):
            build_targets({"A": [replace(bars[0], close_time_ms=DAY)] + bars[1:]})

    def test_empty_asset_collection_rejected(self):
        with self.assertRaises(ValueError):
            build_targets({})
        self.assertTrue(all(v == [] for v in build_targets({"A": []}).values()))


if __name__ == "__main__":
    unittest.main()
