"""Long-horizon data availability and alternate Binance timeframe safeguards."""
import unittest
from datetime import datetime, timezone

from autotrading.history import INTERVAL_MS, first_available_open, fetch_range
from autotrading.lab import sample_windows

DAY = 86_400_000


class ExtendedMarketHistoryTests(unittest.TestCase):
    def test_all_requested_binance_intervals_are_known(self):
        self.assertEqual(INTERVAL_MS["5m"], 5 * 60 * 1000)
        self.assertEqual(INTERVAL_MS["6h"], 6 * 60 * 60 * 1000)
        self.assertEqual(INTERVAL_MS["12h"], 12 * 60 * 60 * 1000)
        self.assertEqual(INTERVAL_MS["1d"], DAY)

    def test_symbol_history_probe_keeps_true_listing_date(self):
        historical_open = 1550448000000
        requests = []
        def fake(path, params):
            requests.append((path, params))
            return [[historical_open, "1", "2", "1", "1", "1",
                     historical_open + 3600000 - 1]]
        self.assertEqual(first_available_open("BTCUSDC", "1h", fake), historical_open)
        self.assertEqual(requests[0][0], "/api/v3/klines")
        self.assertEqual(requests[0][1]["startTime"], 0)
        self.assertEqual(requests[0][1]["limit"], 1)

    def test_probe_rejects_missing_and_bad_timestamps(self):
        with self.assertRaises(RuntimeError):
            first_available_open("BTCUSDC", "6h", lambda *_: [])
        with self.assertRaises(RuntimeError):
            first_available_open("BTCUSDC", "1h", lambda *_: [[1234]])
        with self.assertRaises(ValueError):
            first_available_open("INVALID", "1h", lambda *_: [])

    def test_6h_and_12h_candles_are_contiguous(self):
        for interval in ("6h", "12h"):
            span = INTERVAL_MS[interval]
            rows = [[i*span, "100", "102", "99", "101", "10", (i+1)*span-1]
                    for i in range(4)]
            out = fetch_range("BTCUSDC", interval, 0, 4*span-1,
                requester=lambda path, args: rows if args["startTime"] == 0 else [])
            self.assertEqual(len(out), 4)
            self.assertEqual(out[-1].open_time_ms, 3*span)

    def test_years_allow_many_disjoint_six_month_scenarios(self):
        expected_max = {730: 2, 1460: 4, 2190: 7, 2920: 8}
        for study_days, expected in expected_max.items():
            discovery = int(study_days * 0.75)
            n = min(8, int(discovery * 0.8 // 180))
            self.assertEqual(n, expected)
            windows = sample_windows(0, discovery*24, n, 222,
                                     lengths=(180,), bars_per_day=24)
            self.assertEqual(len(windows), n)
            self.assertTrue(all((b-a)==180*24 for a,b in windows))
            for (_, prev_end), (next_start, _) in zip(windows, windows[1:]):
                self.assertLessEqual(prev_end, next_start)
            self.assertLessEqual(windows[-1][1], discovery*24)


if __name__ == "__main__":
    unittest.main()
