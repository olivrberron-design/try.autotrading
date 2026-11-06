"""Chronological selection is frozen before each test fold, with real fills."""
from dataclasses import replace
from math import prod
import unittest
from unittest.mock import patch

from autotrading.market import Candle
from autotrading.portfolio_research import PortfolioConfig, simulate_portfolio
from chronological_research import walk_forward

DAY = 86_400_000


def trend(n, rate):
    bars = []
    price = 100.0
    for i in range(n):
        close = price * (1 + rate)
        bars.append(Candle(i * DAY, (i + 1) * DAY - 1, price,
                           max(price, close), min(price, close), close, 100.0))
        price = close
    return bars


def fixture(n=620):
    series = {"BTCUSDT": trend(n, .004), "ETHUSDT": trend(n, -.001)}
    targets = {
        "equal_weight_20": [{"BTCUSDT": .10, "ETHUSDT": .10} for _ in range(n)],
        "alpha": [{"BTCUSDT": .20} for _ in range(n)],
        "loser": [{"ETHUSDT": .20} for _ in range(n)],
    }
    cfg = PortfolioConfig(fee_bps=0, impact_bps=0, rebalance_band=0, min_notional=.001)
    return series, targets, cfg


class TestChronologicalResearch(unittest.TestCase):
    def test_each_selection_uses_only_preceding_365_days(self):
        series, targets, cfg = fixture()
        with patch("chronological_research.simulate_portfolio", wraps=simulate_portfolio) as spy:
            result = walk_forward(series, targets, 1, cfg)
        self.assertEqual([f["chosen"] for f in result["folds"]], ["alpha"] * 3)
        calls = spy.call_args_list
        # Every ranking call ends at the test OPEN, excluding that day's bar.
        for k, fold in enumerate(result["folds"]):
            for call in calls[k * len(targets):(k + 1) * len(targets)]:
                self.assertEqual(call.args[2], fold["test_start"] - 365)
                self.assertEqual(call.args[3], fold["test_start"])
        self.assertEqual(len(calls), 3 * len(targets) + 1)
        self.assertEqual(calls[-1].args[2:4], (366, 620))

    def test_future_prices_do_not_change_prior_selections_or_scores(self):
        series, targets, cfg = fixture()
        original = walk_forward(series, targets, 1, cfg)
        altered = {}
        for symbol, bars in series.items():
            multiplier = .1 if symbol == "BTCUSDT" else 10.0
            altered[symbol] = [replace(b, open=b.open * multiplier,
                                       high=b.high * multiplier, low=b.low * multiplier,
                                       close=b.close * multiplier) if i >= 500 else b
                               for i, b in enumerate(bars)]
        perturbed = walk_forward(altered, targets, 1, cfg)
        for before, after in zip(original["folds"], perturbed["folds"]):
            if before["test_start"] <= 500:
                self.assertEqual(before["chosen"], after["chosen"])
                self.assertEqual(before["training_scores"], after["training_scores"])
        self.assertEqual(original["result"]["daily_returns"][:134],
                         perturbed["result"]["daily_returns"][:134])

    def test_history_extension_preserves_completed_fold_decisions(self):
        series, targets, cfg = fixture()
        complete = walk_forward(series, targets, 1, cfg)
        cut = 500
        prefix = walk_forward({s: bars[:cut] for s, bars in series.items()},
                              {s: rows[:cut] for s, rows in targets.items()}, 1, cfg)
        for full, short in zip(complete["folds"], prefix["folds"]):
            self.assertEqual(full["chosen"], short["chosen"])
            self.assertEqual(full["training_scores"], short["training_scores"])
        self.assertEqual(complete["result"]["daily_returns"][:cut - 366],
                         prefix["result"]["daily_returns"])

    def test_fold_account_stays_continuous_and_partial_tail_is_included(self):
        series, targets, cfg = fixture()
        result = walk_forward(series, targets, 1, cfg)
        self.assertEqual([(f["test_start"], f["test_stop_exclusive"])
                          for f in result["folds"]], [(366, 456), (456, 546), (546, 620)])
        # The same winning target over all folds must equal one uninterrupted
        # simulation, including holdings carried through both fold boundaries.
        continuous = simulate_portfolio(series, targets["alpha"], 366, 620, cfg)
        self.assertEqual(result["result"]["equity_curve"], continuous["equity_curve"])
        self.assertEqual(result["result"]["ledger"], continuous["ledger"])
        compounded = 100 * (prod(1 + f["test_return_pct"] / 100 for f in result["folds"]) - 1)
        self.assertAlmostEqual(compounded, result["result"]["return_pct"], places=10)

    def test_nonqualifying_training_falls_back_to_cash(self):
        series, targets, cfg = fixture()
        # Even profitable strategies are ineligible without strictly positive
        # excess against the equal-weight training benchmark.
        targets["alpha"] = targets["equal_weight_20"]
        result = walk_forward(series, targets, 1, cfg)
        self.assertEqual([f["chosen"] for f in result["folds"]], ["cash"] * 3)
        self.assertEqual(result["result"]["return_pct"], 0)
        self.assertEqual(result["result"]["fills"], 0)

    def test_ties_have_explicit_deterministic_strategy_id_order(self):
        series, targets, cfg = fixture()
        targets["zeta"] = targets["alpha"]
        result = walk_forward(series, targets, 1, cfg)
        self.assertTrue(all(f["chosen"] == "zeta" for f in result["folds"]))

    def test_no_test_period_produces_no_claimed_walk_forward_result(self):
        series, targets, cfg = fixture(366)
        self.assertEqual(walk_forward(series, targets, 1, cfg), {"folds": [], "result": None})

    def test_one_lucky_position_does_not_meet_training_fill_requirement(self):
        series, targets, cfg = fixture()
        result = walk_forward(series, targets, 1, replace(cfg, rebalance_band=.20))
        self.assertTrue(all(f["chosen"] == "cash" for f in result["folds"]))
        self.assertTrue(all(f["training_scores"]["alpha"]["return_pct"] > 0
                            for f in result["folds"]))
        self.assertTrue(all(f["training_scores"]["alpha"]["fills"] < 12
                            for f in result["folds"]))

    def test_baseline_is_not_promoted_as_a_research_candidate(self):
        series, targets, cfg = fixture()
        targets["sma200_trend"] = targets.pop("alpha")
        result = walk_forward(series, targets, 1, cfg)
        self.assertTrue(all(f["chosen"] == "cash" for f in result["folds"]))

    def test_extra_latency_cannot_backdate_model_selection(self):
        series, targets, cfg = fixture()
        with self.assertRaises(ValueError):
            walk_forward(series, targets, 1, replace(cfg, delay_days=2))


if __name__ == "__main__":
    unittest.main()
