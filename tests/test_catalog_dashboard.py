"""Offline invariant tests for Strategy Lab 2.0's 15 signals and HTML report."""
import json
from pathlib import Path
import tempfile
import unittest

from autotrading.catalog import CATALOG, compute_catalog_signals
from autotrading.market import Candle
from autotrading.rules import STRATEGIES
from build_dashboard import build


def candle(i, step, price):
    return Candle(
        open_time_ms=i * step, close_time_ms=(i + 1) * step - 1,
        open=float(price), high=float(price + 1), low=float(price - 1),
        close=float(price), volume=100.0
    )


class TestCatalog(unittest.TestCase):
    def setUp(self):
        self.fast = [candle(i, 900_000, 100 + i * 0.03) for i in range(1500)]
        self.hourly = [candle(i, 3_600_000, 100 + i * 0.12) for i in range(400)]

    def test_fifteen_rules_are_registered(self):
        self.assertEqual(len(CATALOG), 15)
        self.assertEqual(set(CATALOG), set(STRATEGIES))
        result = compute_catalog_signals(self.fast, self.hourly)
        self.assertEqual(set(result), set(STRATEGIES))
        for name, values in result.items():
            self.assertEqual(len(values), len(self.fast), name)
            self.assertTrue(set(values).issubset(
                {"HOLD", "ENTRY_CANDIDATE", "EXIT_CANDIDATE"}), name)

    def test_future_candles_cannot_change_old_signals(self):
        prior = compute_catalog_signals(self.fast, self.hourly)
        # Append drastic FUTURE price changes only. Already-completed signals
        # must not change due to their presence.
        fast2 = self.fast + [candle(i, 900_000, 10000)
                             for i in range(1500, 1504)]
        hourly2 = self.hourly + [candle(400, 3_600_000, 10000)]
        after = compute_catalog_signals(fast2, hourly2)
        for name in CATALOG:
            self.assertEqual(prior[name], after[name][:len(self.fast)], name)


class TestBrowserDashboard(unittest.TestCase):
    def test_dashboard_renders_without_network_or_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, dest = root / "reports", root / "site"
            source.mkdir()
            report = {
                "symbol": "BTCUSDC",
                "strategies": {"baseline": {}},
                "sampled_windows": [],
                "overlap_pct_of_unique_discovery_bars": 0,
            }
            (source / "BTCUSDC.json").write_text(json.dumps(report), encoding="utf-8")
            page = build(source, dest)
            html = page.read_text(encoding="utf-8")
            self.assertIn("Strategy Lab", html)
            self.assertIn('"symbol":"BTCUSDC"', html)
            self.assertIn("random", html.lower())
            self.assertNotIn("__PAYLOAD__", html)
            self.assertNotIn("BINANCE_DEMO_API_SECRET", html)
            self.assertNotIn("https://demo-api.binance.com/api/v3/order", html)

    def test_reject_empty_report_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                build(root, root / "site")


if __name__ == "__main__":
    unittest.main()
