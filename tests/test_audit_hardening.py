"""Mutation regressions for data clocks and independently reconciled accounts."""
from dataclasses import asdict, replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from autotrading.advanced_strategies import AdvancedSignal
from autotrading.audit3 import audit_fills
from autotrading.engine3 import ExecutionConfig, simulate_v3
from autotrading.history import load_history, PUBLIC_HOST, validate_candles
from autotrading.market import Candle

HOUR = 3_600_000


def candle(i):
    return Candle(i * HOUR, (i + 1) * HOUR - 1, 100., 101., 99., 100., 10.)


class TestHistoryIntegrity(unittest.TestCase):
    def test_invalid_cached_values_fail_without_network_refresh(self):
        good = [candle(i) for i in range(4)]
        changes = ({"low": -1}, {"high": 90}, {"volume": float("inf")},
                   {"close": float("nan")}, {"close_time_ms": 9 * HOUR},
                   {"close_time_ms": HOUR + 1})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / f"BTCUSDC-1h-0-{4 * HOUR - 1}.json"
            for change in changes:
                with self.subTest(change=change):
                    bars = list(good)
                    bars[1] = replace(bars[1], **change)
                    target.write_text(json.dumps({
                        "source": PUBLIC_HOST, "symbol": "BTCUSDC", "interval": "1h",
                        "start_ms": 0, "end_ms": 4 * HOUR - 1,
                        "candles": [asdict(bar) for bar in bars],
                    }))
                    with patch("autotrading.history.CACHE_DIR", root), patch(
                            "autotrading.history.public_get") as request:
                        with self.assertRaises(RuntimeError):
                            load_history("BTCUSDC", "1h", 0, 4 * HOUR - 1)
                        request.assert_not_called()

    def test_cache_interval_and_range_metadata_must_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / f"BTCUSDC-1h-0-{4 * HOUR - 1}.json"
            for change in ({"interval": "4h"}, {"start_ms": HOUR}, {"end_ms": 8 * HOUR - 1}):
                with self.subTest(change=change):
                    payload = {"source": PUBLIC_HOST, "symbol": "BTCUSDC", "interval": "1h",
                               "start_ms": 0, "end_ms": 4 * HOUR - 1,
                               "candles": [asdict(candle(i)) for i in range(4)], **change}
                    target.write_text(json.dumps(payload))
                    with patch("autotrading.history.CACHE_DIR", root):
                        with self.assertRaises(RuntimeError):
                            load_history("BTCUSDC", "1h", 0, 4 * HOUR - 1)

    def test_wrong_close_clock_rejected_on_download_validation_too(self):
        bars = [candle(i) for i in range(4)]
        bars[2] = replace(bars[2], close_time_ms=bars[2].open_time_ms + 1)
        with self.assertRaises(RuntimeError):
            validate_candles(bars, "1h", 0, 4 * HOUR - 1)


class TestAccountAudit(unittest.TestCase):
    def setUp(self):
        self.bars = [candle(i) for i in range(12)]
        self.signals = [AdvancedSignal()] * len(self.bars)
        self.signals[1] = AdvancedSignal("ENTRY_CANDIDATE", 10.)
        self.signals[5] = AdvancedSignal("EXIT_CANDIDATE")
        self.cfg = ExecutionConfig(trailing_atr_multiple=0, step_size="0.00001")

    def result(self, signals=None):
        return simulate_v3(self.bars, signals or self.signals, 0, len(self.bars), self.cfg)

    def test_flat_and_open_accounts_reconcile_with_fees_and_rounding(self):
        for open_at_end in (False, True):
            with self.subTest(open_at_end=open_at_end):
                signals = list(self.signals)
                if open_at_end:
                    signals[5] = AdvancedSignal()
                result = self.result(signals)
                self.assertEqual(result["open_position_at_end"], open_at_end)
                self.assertTrue(audit_fills(self.bars, signals, result, self.cfg)["ledger_reconciled"])

    def test_fabricated_final_profit_is_rejected(self):
        result = self.result()
        result["ending_usdc_net_if_liquidated"] += 1000
        result["return_pct"] += 20
        with self.assertRaisesRegex(AssertionError, "Final equity"):
            audit_fills(self.bars, self.signals, result, self.cfg)

    def test_incorrect_buy_price_is_rejected(self):
        result = self.result()
        result["fill_events"][0]["execution_price"] *= 0.9
        with self.assertRaisesRegex(AssertionError, "BUY price"):
            audit_fills(self.bars, self.signals, result, self.cfg)

    def test_delayed_buy_cannot_pass_audit_as_default_latency(self):
        result = self.result()
        result["fill_events"][0]["utc"] = result["fill_events"][0]["utc"].replace("02:00", "03:00")
        with self.assertRaisesRegex(AssertionError, "latency"):
            audit_fills(self.bars, self.signals, result, self.cfg)

    def test_unrecorded_fee_and_return_mutations_are_rejected(self):
        for field in ("fees_paid_usdc", "return_pct"):
            with self.subTest(field=field):
                result = self.result()
                result[field] += 1
                with self.assertRaises(AssertionError):
                    audit_fills(self.bars, self.signals, result, self.cfg)

    def test_trade_quantity_must_equal_funded_position(self):
        result = self.result()
        result["trades"][0]["quantity"] *= 2
        with self.assertRaisesRegex(AssertionError, "quantity"):
            audit_fills(self.bars, self.signals, result, self.cfg)


if __name__ == "__main__":
    unittest.main()
