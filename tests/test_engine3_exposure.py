"""Intrabar exits must not invent a holding at the subsequent candle close."""
from dataclasses import replace
import unittest

from autotrading.advanced_strategies import AdvancedSignal
from autotrading.audit3 import audit_fills
from autotrading.engine3 import ExecutionConfig, simulate_v3
from autotrading.market import Candle

HOUR = 3_600_000


def bar(i):
    return Candle(i * HOUR, (i + 1) * HOUR - 1, 100., 101., 99., 100., 10.)


class TestEndOfBarExposure(unittest.TestCase):
    def setUp(self):
        self.bars = [bar(i) for i in range(5)]
        self.signals = [AdvancedSignal()] * len(self.bars)
        self.signals[1] = AdvancedSignal("ENTRY_CANDIDATE", 10.)
        self.cfg = ExecutionConfig(fee_bps=0, slippage_bps=0, spread_bps=0,
                                   trailing_atr_multiple=0)

    def test_after_stop_price_rally_does_not_invent_capital_exposure(self):
        low_close = list(self.bars)
        low_close[3] = replace(low_close[3], open=80., low=70., high=500., close=80.)
        high_close = list(low_close)
        high_close[3] = replace(high_close[3], close=500.)
        results = [simulate_v3(bars, self.signals, 0, 5, self.cfg)
                   for bars in (low_close, high_close)]
        # Both paths gap below the 90 stop at the 80 open. The later rally is
        # irrelevant to the account, its closed position, and close exposure.
        for bars, result in zip((low_close, high_close), results):
            self.assertEqual(result["ending_usdc_net_if_liquidated"], 4950.)
            self.assertEqual(result["peak_open_position_notional_usdc"], 250.)
            self.assertEqual(result["peak_open_position_pct_equity"], 5.)
            self.assertEqual(result["mean_position_pct_equity_over_all_bars"], 1.)
            self.assertEqual(result["exposure_pct"], 40.)
            self.assertTrue(audit_fills(bars, self.signals, result, self.cfg)["passed"])
        self.assertEqual(results[0], results[1])

    def test_entry_and_stop_same_bar_count_time_but_no_close_capital(self):
        self.bars[2] = replace(self.bars[2], low=80., high=201., close=200.)
        result = simulate_v3(self.bars, self.signals, 0, 5, self.cfg)
        self.assertEqual(result["ending_usdc_net_if_liquidated"], 4975.)
        self.assertEqual(result["exposure_pct"], 20.)
        self.assertEqual(result["mean_position_pct_equity_over_all_bars"], 0.)
        self.assertEqual(result["peak_open_position_notional_usdc"], 0.)
        self.assertEqual(result["max_entry_allocation_pct_initial_capital"], 5.)
        self.assertTrue(audit_fills(self.bars, self.signals, result, self.cfg)["passed"])

    def test_take_profit_exit_also_has_zero_close_exposure(self):
        self.bars[2] = replace(self.bars[2], high=1001., close=1000.)
        cfg = replace(self.cfg, take_profit_r=2.)
        result = simulate_v3(self.bars, self.signals, 0, 5, cfg)
        self.assertEqual(result["ending_usdc_net_if_liquidated"], 5050.)
        self.assertEqual(result["exposure_pct"], 20.)
        self.assertEqual(result["mean_position_pct_equity_over_all_bars"], 0.)
        self.assertEqual(result["peak_open_position_notional_usdc"], 0.)
        self.assertTrue(audit_fills(self.bars, self.signals, result, cfg)["passed"])

    def test_surviving_position_contributes_to_each_close(self):
        result = simulate_v3(self.bars, self.signals, 0, 5, self.cfg)
        self.assertEqual(result["ending_usdc_net_if_liquidated"], 5000.)
        self.assertEqual(result["exposure_pct"], 60.)
        self.assertEqual(result["mean_position_pct_equity_over_all_bars"], 3.)
        self.assertEqual(result["peak_open_position_notional_usdc"], 250.)
        self.assertTrue(audit_fills(self.bars, self.signals, result, self.cfg)["passed"])


if __name__ == "__main__":
    unittest.main()
