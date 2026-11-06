import unittest
from dataclasses import replace
from autotrading.market import Candle
from autotrading.portfolio_research import PortfolioConfig,simulate_portfolio,bootstrap_family
from autotrading.research_data import DAY,validate_daily


def bars(prices):
    return [Candle(i*DAY,(i+1)*DAY-1,p,p,p,p,100) for i,p in enumerate(prices)]


class PortfolioTests(unittest.TestCase):
    def test_previous_close_target_and_terminal_costs(self):
        series={'BTCUSDT':bars([100,100,200,200])}
        targets=[{'BTCUSDT':.2},{},{},{}]
        cfg=PortfolioConfig(fee_bps=10,impact_bps=6,rebalance_band=0)
        r=simulate_portfolio(series,targets,1,4,cfg)
        self.assertEqual([x['day_index'] for x in r['ledger']],[1,2])
        q=int((1000/100)*100000)/100000
        expected=5000-q*100*1.0006*1.001+q*200*.9994*.999
        self.assertAlmostEqual(r['ending_equity'],expected)
        self.assertLess(r['ledger'][0]['signal_ms'],r['ledger'][0]['fill_ms'])

    def test_fee_sized_allocation_cannot_overdraw_cash(self):
        series={'BTCUSDT':bars([100]*4),'ETHUSDT':bars([100]*4)}
        targets=[{'BTCUSDT':.5,'ETHUSDT':.5}]*4
        cfg=PortfolioConfig(cap=1,fee_bps=100,impact_bps=100,rebalance_band=0)
        r=simulate_portfolio(series,targets,1,4,cfg)
        self.assertGreaterEqual(r['cash'],0)
        self.assertTrue(r['audit_passed'])

    def test_cost_stress_lowers_flat_market_equity(self):
        series={'BTCUSDT':bars([100]*8)}
        targets=[{'BTCUSDT':.2} if i%2 else {} for i in range(8)]
        a=simulate_portfolio(series,targets,1,8,PortfolioConfig(fee_bps=0,impact_bps=0))
        b=simulate_portfolio(series,targets,1,8,PortfolioConfig(fee_bps=20,impact_bps=12))
        self.assertAlmostEqual(a['ending_equity'],5000)
        self.assertLess(b['ending_equity'],5000)

    def test_no_future_target_or_price_affects_prefix(self):
        original={'BTCUSDT':bars([100,100,101,102,900])}
        mutated={'BTCUSDT':bars([100,100,101,102,1])}
        targets=[{'BTCUSDT':.2}]*5
        self.assertEqual(simulate_portfolio(original,targets,1,4),simulate_portfolio(mutated,targets,1,4))

    def test_delay_is_real_execution_shift(self):
        series={'BTCUSDT':bars([100,100,200,300])}
        targets=[{'BTCUSDT':.2},{},{},{}]
        r=simulate_portfolio(series,targets,2,4,PortfolioConfig(delay_days=2))
        self.assertEqual(r['ledger'][0]['day_index'],2)
        self.assertEqual(r['ledger'][0]['reference_price'],200)

    def test_reject_bad_data_and_allocation(self):
        bad=bars([100]*4)
        bad[2]=replace(bad[2],close=float('nan'))
        with self.assertRaises(ValueError): validate_daily(bad,0,4*DAY)
        with self.assertRaises(ValueError):simulate_portfolio({'BTCUSDT':bars([100]*4)},[{'BTCUSDT':.21}]*4,1)
        with self.assertRaises(ValueError):simulate_portfolio({'BTCUSDT':bars([100]*4),'ETHUSDT':bars([100]*3)},[{}]*4,1)

    def test_bootstrap_negative_constant_cannot_pass(self):
        result=bootstrap_family({'negative':[-.001]*90,'cash':[0.0]*90},repetitions=100)
        self.assertLess(result['negative']['annualized_log_mean_ci95_pct'][1],0)
        self.assertEqual(result['cash']['familywise_p_positive_mean'],1)
        self.assertEqual(result['negative']['familywise_p_positive_mean'],1)

if __name__=='__main__': unittest.main()
