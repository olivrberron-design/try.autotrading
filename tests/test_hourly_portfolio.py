"""Hand-calculated cash, timing, hourly risk, and prefix checks; no network."""
from dataclasses import replace
import math
import unittest

from autotrading.hourly_portfolio import HOUR, simulate_hourly_targets
from autotrading.market import Candle
from autotrading.portfolio_research import PortfolioConfig, simulate_portfolio
from autotrading.research_data import DAY


def candle(open_ms, step, price=100.):
    return Candle(open_ms, open_ms + step - 1, price, price, price, price, 10.)


class HourlyPortfolioTests(unittest.TestCase):
    def setUp(self):
        self.days = {"BTCUSDT": [candle(i * DAY, DAY) for i in range(5)]}
        self.hours = {"BTCUSDT": [candle(i * HOUR, HOUR) for i in range(5 * 24)]}
        self.targets = [{"BTCUSDT": .2}] * 5
        self.free = PortfolioConfig(fee_bps=0, impact_bps=0, rebalance_band=0)

    def run_case(self, cfg=None, **kwargs):
        return simulate_hourly_targets(self.hours, self.days, self.targets,
                                       DAY, 4 * DAY, cfg or self.free, **kwargs)

    def test_flat_market_cash_and_once_daily_execution(self):
        result = self.run_case()
        self.assertEqual(result["ending_equity"], 5000.)
        self.assertEqual(result["cash"], 4000.)
        self.assertEqual(result["positions"], {"BTCUSDT": 10.})
        self.assertEqual(result["fills"], 1)
        self.assertEqual(result["rebalance_days"], 3)
        fill = result["ledger"][0]
        self.assertEqual(fill["fill_ms"], DAY + HOUR)
        self.assertEqual(fill["signal_ms"], DAY - 1)
        self.assertEqual(result["equity_curve_hourly"][0]["gross_position"], 0)
        self.assertEqual(result["equity_curve_hourly"][1]["gross_position"], 1000)
        self.assertEqual(result["daily_returns"], [0., 0., 0.])

    def test_known_0100_gap_uses_later_open_and_conserves_cash(self):
        self.targets = [{"BTCUSDT": .2}, {}, {}, {}, {}]
        self.hours["BTCUSDT"][25] = candle(25 * HOUR, HOUR, 200.)
        self.hours["BTCUSDT"][49] = candle(49 * HOUR, HOUR, 300.)
        result = self.run_case()
        self.assertEqual([e["fill_ms"] for e in result["ledger"]], [25 * HOUR, 49 * HOUR])
        self.assertEqual([e["quantity"] for e in result["ledger"]], [5., 5.])
        self.assertEqual(result["ending_equity"], 5500.)
        self.assertEqual(result["cash"], 5500.)
        self.assertEqual(result["positions"]["BTCUSDT"], 0.)

    def test_costs_match_hand_calculated_round_trip(self):
        self.targets = [{"BTCUSDT": .2}, {}, {}, {}, {}]
        cfg = replace(self.free, fee_bps=10, impact_bps=6)
        result = self.run_case(cfg)
        expected = 5000 - 10 * 100 * 1.0006 * 1.001 + 10 * 100 * .9994 * .999
        self.assertAlmostEqual(result["ending_equity"], expected)
        self.assertAlmostEqual(result["fees"], 10 * 100 * 1.0006 * .001 + 10 * 100 * .9994 * .001)

    def test_hourly_drawdown_sees_recovered_intraday_loss(self):
        self.hours["BTCUSDT"][30] = candle(30 * HOUR, HOUR, 50.)
        result = self.run_case()
        self.assertAlmostEqual(result["max_hourly_close_drawdown_pct"], 10.)
        self.assertEqual(result["max_daily_close_drawdown_pct"], 0.)
        self.assertEqual(result["fills"], 1)  # No trade at the intraday dip.

    def test_future_daily_target_and_hourly_price_cannot_change_prefix(self):
        before = self.run_case()
        self.targets[3] = {}
        self.targets[4] = {}
        self.hours["BTCUSDT"][4 * 24 + 1] = candle((4 * 24 + 1) * HOUR, HOUR, 999.)
        self.days["BTCUSDT"][4] = candle(4 * DAY, DAY, 999.)
        self.assertEqual(before, self.run_case())

    def test_delay_days_uses_older_daily_target(self):
        self.targets = [{"BTCUSDT": .2}, {}, {}, {}, {}]
        result = simulate_hourly_targets(self.hours, self.days, self.targets,
                                        2 * DAY, 4 * DAY, replace(self.free, delay_days=2))
        self.assertEqual(result["ledger"][0]["signal_ms"], DAY - 1)
        self.assertEqual(result["ledger"][0]["fill_ms"], 2 * DAY + HOUR)

    def test_multiasset_full_allocation_never_overdraws_cash(self):
        self.days["ETHUSDT"] = list(self.days["BTCUSDT"])
        self.hours["ETHUSDT"] = list(self.hours["BTCUSDT"])
        self.targets = [{"BTCUSDT": .5, "ETHUSDT": .5}] * 5
        result = self.run_case(PortfolioConfig(cap=1., fee_bps=100, impact_bps=100, rebalance_band=0))
        self.assertGreaterEqual(result["cash"], 0)
        self.assertTrue(all(e["cash_after"] >= 0 for e in result["ledger"]))

    def test_subminimum_order_is_not_assumed_filled(self):
        self.targets = [{"BTCUSDT": .0001}] * 5  # 0.50 quote units, below 5.
        result = self.run_case()
        self.assertEqual(result["fills"], 0)
        self.assertEqual(result["ending_equity"], 5000.)

    def test_zero_target_exits_even_inside_rebalance_band(self):
        self.targets = [{"BTCUSDT": .2}, {}, {}, {}, {}]
        self.hours["BTCUSDT"][49] = candle(49 * HOUR, HOUR, 1.)
        result = self.run_case(replace(self.free, rebalance_band=.01))
        self.assertEqual(result["sells"], 1)
        self.assertEqual(result["ledger"][1]["notional"], 10.)
        self.assertLess(10 / 4010, .01)
        self.assertEqual(result["positions"]["BTCUSDT"], 0.)
        self.assertEqual(result["ending_equity"], 4010.)

    def test_daily_and_hourly_models_agree_without_intraday_movement(self):
        self.targets = [{"BTCUSDT": .2}, {}, {"BTCUSDT": .2}, {}, {}]
        cfg = PortfolioConfig()
        hourly = self.run_case(cfg)
        daily = simulate_portfolio(self.days, self.targets, 1, 4, cfg)
        for key in ("ending_equity", "cash", "fees", "turnover", "return_pct"):
            self.assertAlmostEqual(hourly[key], daily[key])
        self.assertEqual(hourly["positions"], daily["positions"])

    def test_daily_returns_compound_and_partial_days_are_labeled(self):
        result = simulate_hourly_targets(self.hours, self.days, self.targets,
                                        DAY + HOUR, 3 * DAY + 2 * HOUR, self.free)
        self.assertEqual([x["partial_day"] for x in result["equity_curve_daily"]], [True, False, True])
        self.assertAlmostEqual(math.prod(1 + r for r in result["daily_returns"]), result["ending_equity"] / 5000)
        self.assertIsNone(result["daily_sharpe"])

    def test_invalid_hourly_coverage_clocks_and_values_are_rejected(self):
        original = list(self.hours["BTCUSDT"])
        corruptions = [original[:30] + original[31:],
                       original[:30] + [replace(original[30], close_time_ms=original[30].close_time_ms + 1)] + original[31:],
                       original[:30] + [replace(original[30], close=float("nan"))] + original[31:],
                       original[:30] + [original[29]] + original[31:]]
        for bars in corruptions:
            with self.subTest():
                self.hours["BTCUSDT"] = bars
                with self.assertRaises(ValueError):
                    self.run_case()

    def test_missing_prior_target_and_mixed_quotes_are_rejected(self):
        with self.assertRaises(ValueError):
            simulate_hourly_targets(self.hours, self.days, self.targets, 0, DAY, self.free)
        self.hours["ETHUSDC"] = list(self.hours["BTCUSDT"])
        self.days["ETHUSDC"] = list(self.days["BTCUSDT"])
        with self.assertRaises(ValueError):
            self.run_case()


if __name__ == "__main__":
    unittest.main()
