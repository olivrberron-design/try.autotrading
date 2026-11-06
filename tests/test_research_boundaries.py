"""Research inputs must share a numeraire and contain only completed days."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from autotrading.market import Candle
from autotrading.portfolio_research import simulate_portfolio
from autotrading.research_data import DAY, load_daily


def bars():
    return [Candle(i * DAY, (i + 1) * DAY - 1,
                   100., 101., 99., 100., 10.) for i in range(4)]


class TestResearchBoundaries(unittest.TestCase):
    def test_shared_cash_rejects_implicit_usdt_usdc_conversion(self):
        series = {"BTCUSDT": bars(), "ETHUSDC": bars()}
        targets = [{"BTCUSDT": .1, "ETHUSDC": .1}] * 4
        with self.assertRaisesRegex(ValueError, "quote currency"):
            simulate_portfolio(series, targets, 1)

    def test_unknown_instrument_cannot_inherit_a_btc_or_eth_quantity_step(self):
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            simulate_portfolio({"DOGEUSDT": bars()}, [{"DOGEUSDT": .2}] * 4, 1)

    def test_current_unfinished_day_is_rejected_before_request(self):
        get = Mock()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "already completed"):
                load_daily("BTCUSDT", 100 * DAY, 101 * DAY, directory,
                           requester=get, now_ms=100 * DAY + 3_600_000)
        get.assert_not_called()

    def test_future_cached_day_is_rejected_without_reading_it(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / f"BTCUSDT-daily-{100 * DAY}-{101 * DAY}.json"
            target.write_text("This malformed future cache must not even be parsed.")
            with self.assertRaisesRegex(ValueError, "already completed"):
                load_daily("BTCUSDT", 100 * DAY, 101 * DAY, directory,
                           now_ms=100 * DAY + 3_600_000)

    def test_last_completed_day_is_accepted_at_utc_midnight(self):
        start, end = 99 * DAY, 100 * DAY
        get = Mock(return_value=[[start, "100", "101", "99", "100", "10", end - 1]])
        with tempfile.TemporaryDirectory() as directory:
            result, manifest = load_daily("BTCUSDT", start, end, directory,
                                          requester=get, now_ms=end)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].close_time_ms, end - 1)
        self.assertEqual(manifest["end_exclusive_ms"], end)


if __name__ == "__main__":
    unittest.main()
