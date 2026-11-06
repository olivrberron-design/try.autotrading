"""Prior-close regime boundaries, causal attribution, and input integrity."""
from dataclasses import replace
import json
from math import prod
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from autotrading.market import Candle
from regime_diagnostics import analyze_regimes, classify_regimes, main

DAY = 86_400_000


def bars(prices):
    return [Candle(i * DAY, (i + 1) * DAY - 1, p, p, p, p, 100)
            for i, p in enumerate(prices)]


def account(first, daily_returns):
    equity, curve = 5000.0, []
    for i, value in enumerate(daily_returns, first):
        equity *= 1 + value
        curve.append({"open_ms": i * DAY, "equity": equity})
    return {"equity_curve": curve, "daily_returns": daily_returns,
            "return_pct": 100 * (prod(1 + r for r in daily_returns) - 1)}


class TestRegimeDiagnostics(unittest.TestCase):
    def test_current_bar_cannot_change_its_own_regime(self):
        prices = [100.] * 150 + [50.] * 50 + [10_000.] * 5
        series = {"BTCUSDT": bars(prices)}
        _, regimes = classify_regimes(series)
        self.assertEqual(regimes[199 * DAY], "unclassified")
        self.assertEqual(regimes[200 * DAY], "bear")
        self.assertEqual(regimes[201 * DAY], "bull")
        altered = {"BTCUSDT": [replace(b, open=25, close=25, high=25, low=25)
                                if i >= 200 else b for i, b in enumerate(series["BTCUSDT"])]}
        _, changed = classify_regimes(altered)
        self.assertEqual(changed[200 * DAY], regimes[200 * DAY])
        self.assertEqual(changed[201 * DAY], "bear")

    def test_future_mutation_and_history_extension_preserve_prior_classification(self):
        series = {"BTCUSDT": bars([100 + i for i in range(500)])}
        _, full = classify_regimes(series)
        _, prefix = classify_regimes({"BTCUSDT": series["BTCUSDT"][:350]})
        self.assertEqual(prefix, {t: label for t, label in full.items() if t < 350 * DAY})
        altered = {"BTCUSDT": [replace(b, open=1, close=1, high=1, low=1)
                                if i >= 350 else b for i, b in enumerate(series["BTCUSDT"])]}
        _, changed = classify_regimes(altered)
        self.assertEqual({t: label for t, label in full.items() if t <= 350 * DAY},
                         {t: label for t, label in changed.items() if t <= 350 * DAY})

    def test_equal_moving_averages_are_transition_and_insufficient_history_unclassified(self):
        _, labels = classify_regimes({"BTCUSDC": bars([100.] * 250)})
        self.assertTrue(all(labels[i * DAY] == "unclassified" for i in range(200)))
        self.assertTrue(all(labels[i * DAY] == "transition" for i in range(200, 250)))

    def test_conditional_attribution_and_walk_forward_counts(self):
        prices = [100.] * 150 + [50.] * 50 + [10_000.] * 5
        series = {"BTCUSDT": bars(prices)}
        fixed = account(199, [.01, -.02, .03, -.04])
        wf = account(201, [.03, -.04])
        result = analyze_regimes(series, {"fixed_strategies": {"example": fixed},
                                         "walk_forward": {"result": wf},
                                         "walk_forward_double": {"result": None}})
        buckets = result["fixed_strategies"]["example"]["regimes"]
        self.assertEqual([buckets[k]["days"] for k in ("unclassified", "bear", "bull", "transition")], [1, 1, 2, 0])
        self.assertAlmostEqual(buckets["bull"]["compounded_conditional_return_pct"], 100 * (1.03 * .96 - 1))
        self.assertAlmostEqual(buckets["bull"]["mean_daily_return_pct"], -.5)
        self.assertEqual(buckets["bull"]["worst_daily_loss_pct"], 4)
        self.assertIsNone(buckets["transition"]["compounded_conditional_return_pct"])
        self.assertEqual(result["walk_forward"]["evaluation_days"], 2)
        self.assertIsNone(result["walk_forward_double"])
        self.assertIn("noncontiguous", result["attribution_note"])
        self.assertIn("not standalone", result["attribution_note"])

    def test_misaligned_returns_and_unknown_dates_fail_closed(self):
        series = {"BTCUSDT": bars([100.] * 250)}
        broken = account(240, [.01, .02])
        broken["daily_returns"][1] = .5
        with self.assertRaises(ValueError):
            analyze_regimes(series, {"fixed_strategies": {"broken": broken}})
        with self.assertRaises(ValueError):
            analyze_regimes(series, {"fixed_strategies": {"outside": account(250, [.01])}})
        with self.assertRaises(ValueError):
            analyze_regimes(series, {"fixed_strategies": {"length": {"equity_curve": [], "daily_returns": [.01]}}})

    def test_cli_reads_exact_saved_episode_without_downloading(self):
        series = {"BTCUSDT": bars([100.] * 250)}
        episode = {"name": "synthetic", "download_start": "1970-01-01", "end_exclusive": "1970-09-08", "symbols": ["BTCUSDT"]}
        manifests = [{"symbol": "BTCUSDT", "sha256": "synthetic"}]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "report.json").write_text(json.dumps({"episodes": [{"episode": episode, "data_manifest": manifests}]}))
            (path / "synthetic-ledgers.json").write_text(json.dumps({"fixed_strategies": {"example": account(240, [.01, -.01])}}))
            with patch("regime_diagnostics.episode_series", return_value=(series, manifests)) as load:
                main(["--input", str(path), "--output", str(path / "diagnostics.json")])
            load.assert_called_once_with(episode, allow_download=False)
            saved = json.loads((path / "diagnostics.json").read_text())
            self.assertEqual(saved["episodes"][0]["episode"], episode)
            self.assertEqual(len(saved["source_report_sha256"]), 64)
            with patch("regime_diagnostics.episode_series", return_value=(series, [])):
                with self.assertRaises(ValueError):
                    main(["--input", str(path), "--output", str(path / "bad.json")])
            self.assertFalse((path / "bad.json").exists())


if __name__ == "__main__":
    unittest.main()
