"""Regression/expert causality and unavailable-model behavior on synthetic data."""
from dataclasses import replace
from math import sin
import unittest

from autotrading.market import Candle
from autotrading.research_models import (
    build_model_targets, MODEL_SPECS, _fit_ridge, _training_feature_indices,
    _update_expert_scores, _solve,
)
from autotrading.research_strategies import _features, MAX_ALLOCATION

DAY = 86_400_000


def candles(n, drift=.001, phase=0):
    result = []
    price = 100.0
    for i in range(n):
        close = price * (1 + drift + .01 * sin(i * .23 + phase))
        result.append(Candle(i * DAY, (i + 1) * DAY - 1, price,
                             max(price, close), min(price, close), close, 100.0))
        price = close
    return result


class TestResearchModels(unittest.TestCase):
    def test_prefix_invariance_including_monthly_refits(self):
        series = {"BTCUSDT": candles(850), "ETHUSDT": candles(850, .0015, 1.1)}
        full = build_model_targets(series)
        for cut in (239, 241, 334, 365, 459, 700):
            prefix = build_model_targets({name: bars[:cut] for name, bars in series.items()})
            for name in MODEL_SPECS:
                self.assertEqual(full[name][:cut], prefix[name], name)

    def test_future_candle_mutation_preserves_prior_targets(self):
        series = {"BTCUSDT": candles(600), "ETHUSDT": candles(600, .0015, 1.1)}
        original = build_model_targets(series)
        altered = {name: [replace(b, open=b.open * .2, close=b.close * .2,
                                 high=b.high * .2, low=b.low * .2) if i >= 450 else b
                          for i, b in enumerate(bars)] for name, bars in series.items()}
        changed = build_model_targets(altered)
        for name in MODEL_SPECS:
            self.assertEqual(original[name][:450], changed[name][:450], name)

    def test_ridge_labels_are_matured_and_scaling_is_train_local(self):
        series = {"BTCUSDT": candles(700), "ETHUSDT": candles(700, .002, .7)}
        features = {name: _features(bars) for name, bars in series.items()}
        for horizon in (7, 21):
            model = _fit_ridge(series, features, 500, horizon)
            self.assertEqual(model["latest_label_open_index"], 500)
            self.assertEqual(model["latest_feature_index"], 500 - horizon - 1)
            self.assertEqual(model["observations"], model["feature_days"] * 2)
            # Arbitrarily replace every unmatured label's future open, and
            # every future feature. Neither coefficients nor scaling may move.
            altered = {name: [replace(b, open=b.open * 10) if i > 500 else b
                              for i, b in enumerate(bars)] for name, bars in series.items()}
            fake_features = {name: [dict(f, x=[999] * 6) if f is not None and i > 500 - horizon - 1 else f
                                    for i, f in enumerate(items)] for name, items in features.items()}
            self.assertEqual(model, _fit_ridge(altered, fake_features, 500, horizon))

    def test_training_window_minimum_and_730_day_bound(self):
        for horizon in (7, 21):
            indices = _training_feature_indices(1500, horizon)
            self.assertEqual(indices[0], 770)
            self.assertEqual(indices[-1] + horizon + 1, 1500)
            series = {"BTCUSDT": candles(400)}
            features = {"BTCUSDT": _features(series["BTCUSDT"])}
            self.assertIsNone(_fit_ridge(series, features, 324 + horizon, horizon))
            self.assertIsNotNone(_fit_ridge(series, features, 325 + horizon, horizon))

    def test_no_model_position_before_enough_matured_training(self):
        series = {"BTCUSDT": candles(600, .006), "ETHUSDT": candles(600, .006, 1.1)}
        result = build_model_targets(series)
        for horizon in (7, 21):
            name = f"pooled_ridge_{horizon}d"
            self.assertTrue(all(not x for x in result[name][:325 + horizon]))
            self.assertTrue(any(result[name][400:]), name)

    def test_expert_score_uses_decision_two_days_earlier(self):
        bars = [Candle(i * DAY, (i + 1) * DAY - 1, p, p, p, p, 1)
                for i, p in enumerate((100., 100., 200., 50.))]
        series = {"BTCUSDT": bars}
        experts = {"old": [{"BTCUSDT": .2}, {}, {}, {}],
                   "too_recent": [{}, {"BTCUSDT": .2}, {}, {}],
                   "cash": [{}, {}, {}, {}]}
        scores = {name: 0.0 for name in experts}
        _update_expert_scores(scores, experts, series, 2)
        self.assertGreater(scores["old"], 0)
        self.assertEqual(scores["too_recent"], 0)
        self.assertEqual(scores["cash"], 0)

    def test_total_allocation_bounds_cash_warmup_and_asset_order(self):
        a, b = candles(600), candles(600, -.001, .8)
        result = build_model_targets({"BTCUSDC": a, "ETHUSDC": b})
        self.assertEqual(result, build_model_targets({"ETHUSDC": b, "BTCUSDC": a}))
        for name, rows in result.items():
            self.assertTrue(all(not row for row in rows[:240]), name)
            for row in rows:
                self.assertLessEqual(sum(row.values()), MAX_ALLOCATION + 1e-12)
                self.assertTrue(all(0 < weight <= MAX_ALLOCATION for weight in row.values()))

    def test_market_regime_requires_explicit_unambiguous_btc_series(self):
        with self.assertRaises(ValueError):
            build_model_targets({"ETHUSDT": candles(300)})
        with self.assertRaises(ValueError):
            build_model_targets({"BTCUSDT": candles(300), "BTCUSDC": candles(300)})

    def test_linear_solver_handles_pivoting_and_rejects_singular(self):
        solution = _solve([[0, 2], [3, 4]], [4, 11])
        self.assertAlmostEqual(solution[0], 1)
        self.assertAlmostEqual(solution[1], 2)
        with self.assertRaises(ValueError):
            _solve([[1, 1], [2, 2]], [1, 2])

    def test_constant_features_do_not_create_singular_fit_or_spurious_signal(self):
        bars = [Candle(i * DAY, (i + 1) * DAY - 1, 100, 100, 100, 100, 1)
                for i in range(500)]
        result = build_model_targets({"BTCUSDT": bars, "ETHUSDT": bars})
        self.assertTrue(all(not row for targets in result.values() for row in targets))


if __name__ == "__main__":
    unittest.main()
