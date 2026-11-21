"""Offline tests for lab4 research code: synthetic data only, no network."""
import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lab4 import intraday, metrics  # noqa: E402
from lab4 import strategies as S  # noqa: E402
from lab4.engine import DAY_MS, Panel, simulate  # noqa: E402

ASSETS = ("BTC", "ETH", "XRP", "LTC", "ADA", "BNB", "EOS", "TRX")


def synthetic_panel(n=700, seed=1, corrupt_after=None, corrupt_seed=99):
    """Random-walk OHLCV panel incl. carry/short pseudo-assets. If
    ``corrupt_after`` is given, every value after that index is re-drawn from a
    different random stream (future data changes, the past does not)."""
    rng = random.Random(seed)
    bad = random.Random(corrupt_seed)

    def draw(k, fn):
        clean = fn(rng)                      # always consume the clean stream
        if corrupt_after is not None and k > corrupt_after:
            return fn(bad)
        return clean

    p = Panel(0, n)
    for j, a in enumerate(ASSETS):
        px, rows = 100.0 * (j + 1), {}
        for k in range(n):
            r, e1, e2, vol = draw(k, lambda g: (g.gauss(0.001, 0.04), abs(g.gauss(0, 0.01)),
                                                abs(g.gauss(0, 0.01)), g.uniform(1e6, 5e6)))
            o = px
            c = px * math.exp(r)
            rows[k * DAY_MS] = (o, max(o, c) * (1 + e1), min(o, c) * (1 - e2), c, vol)
            px = c
        p.add_asset(a, rows)
    for a in ("BTC", "ETH"):
        fund, carry, short = [], [], []
        for k in range(n):
            f, x1, x2 = draw(k, lambda g: (g.gauss(0.0003, 0.0003), g.gauss(0, 0.001), g.gauss(0, 0.04)))
            fund.append(f)
            carry.append(0.5 * (x1 + f))
            short.append(x2 + f)
        p.extra["CARRY_" + a + "_FUND"] = fund
        p.extra["CARRY_" + a] = carry
        p.extra["SHORT_" + a] = short
    return p


class NoLookAheadTest(unittest.TestCase):
    def test_weights_ignore_future_data(self):
        active = set()
        for t in (420, 455, 520, 610):
            clean = synthetic_panel()
            dirty = synthetic_panel(corrupt_after=t)
            for name, fn in {**S.REGISTERED, **S.BENCHMARKS}.items():
                a, b = fn(clean, t), fn(dirty, t)
                self.assertEqual(set(a), set(b), f"{name} at t={t}")
                for k in a:
                    self.assertAlmostEqual(a[k], b[k], places=12, msg=f"{name} at t={t}")
                if a:
                    active.add(name)
        # the test is only meaningful if strategies actually hold positions
        self.assertGreaterEqual(len(active), len(S.REGISTERED) - 2, sorted(active))

    def test_regime_uses_past_only(self):
        t = 500
        self.assertEqual(S.btc_regime(synthetic_panel(), t),
                         S.btc_regime(synthetic_panel(corrupt_after=t), t))

    def test_gross_exposure_never_exceeds_one(self):
        p = synthetic_panel()
        for name, fn in S.REGISTERED.items():
            for t in range(300, 690, 13):
                self.assertLessEqual(sum(fn(p, t).values()), 1.0 + 1e-9, name)


class EngineTest(unittest.TestCase):
    def test_buy_and_hold_accounting(self):
        p = Panel(0, 5)
        p.add_asset("BTC", {k * DAY_MS: (100.0 + k, 101.0 + k, 99.0 + k, 100.5 + k, 1.0) for k in range(5)})
        res = simulate(p, lambda panel, t: {"BTC": 1.0}, 1, 4, cost=0.001, band=0)
        eq = metrics.equity(res["returns"])[-1]
        # buy at open[1]=101 paying 0.1%, mark to open[4]=104, no further trades
        self.assertAlmostEqual(eq, (1 - 0.001) * 104 / 101, places=12)
        self.assertEqual(res["orders"], [1, 0, 0])

    def test_signal_executes_next_open(self):
        p = Panel(0, 4)
        p.add_asset("BTC", {k * DAY_MS: (10.0 * (k + 1), 99, 1, 10.0 * (k + 1), 1.0) for k in range(4)})
        seen = []
        simulate(p, lambda panel, t: seen.append(t) or {}, 1, 3)
        self.assertEqual(seen, [0, 1])  # period k decided with data up to k-1

    def test_round_trip_costs(self):
        p = Panel(0, 4)
        p.add_asset("BTC", {k * DAY_MS: (100.0, 100.0, 100.0, 100.0, 1.0) for k in range(4)})
        res = simulate(p, lambda panel, t: {"BTC": 1.0} if t == 0 else {}, 1, 3, cost=0.0015, band=0)
        self.assertAlmostEqual(metrics.equity(res["returns"])[-1], (1 - 0.0015) ** 2, places=12)


class IntradayTest(unittest.TestCase):
    def test_vbo_stop_fill_and_day_close_exit(self):
        p = Panel(0, 4)
        # day 1: range 10, close 105 > SMA2 (102.5) -> trend filter passes for day 2
        p.add_asset("BTC", {0: (99.0, 101.0, 98.0, 100.0, 1.0),
                            DAY_MS: (100.0, 110.0, 100.0, 105.0, 1.0),
                            2 * DAY_MS: (105.0, 120.0, 104.0, 117.0, 1.0),
                            3 * DAY_MS: (117.0, 118.0, 116.0, 117.0, 1.0)})
        hourly = {}
        for h in range(24):
            o = 105.0 + h * 0.5
            hourly[2 * DAY_MS + h * 3_600_000] = (o, o + 0.5, o - 0.2, o + 0.5, 1.0)
        # level = 105 + 0.5*10 = 110 -> first hour with high >= 110 is h=9 (open 109.5)
        rets, trades = intraday.vbo_day_returns(p, hourly, "BTC", 2, 3, k=0.5, sma_days=2, fee=0.001, slip=0.0)
        self.assertEqual(trades, 1)
        exit_px = 105.0 + 23 * 0.5 + 0.5
        self.assertAlmostEqual(rets[0], exit_px / 110.0 * 0.999 ** 2 - 1, places=12)

    def test_vbo_skips_incomplete_day(self):
        p = Panel(0, 2)
        p.add_asset("BTC", {0: (100.0, 110.0, 100.0, 105.0, 1.0), DAY_MS: (105.0, 130.0, 104.0, 120.0, 1.0)})
        hourly = {DAY_MS + h * 3_600_000: (105.0, 130.0, 104.0, 120.0, 1.0) for h in range(23)}
        rets, trades = intraday.vbo_day_returns(p, hourly, "BTC", 1, 2, sma_days=1)
        self.assertEqual((rets, trades), ([0.0], 0))


class MetricsTest(unittest.TestCase):
    def test_drawdown_and_cagr(self):
        self.assertAlmostEqual(metrics.max_drawdown([0.1, -0.5, 0.2]), 0.5)
        self.assertAlmostEqual(metrics.cagr([0.0] * 364 + [0.1]), 0.1)

    def test_spy_summary_uses_prior_close_as_base(self):
        from datetime import date
        px = {"2020-12-31": 100.0, "2021-01-04": 110.0, "2021-06-30": 121.0}
        s = metrics.spy_summary(px, date(2021, 1, 1), date(2021, 7, 1))
        self.assertAlmostEqual(s["total_return"], 0.21)


class PaperTest(unittest.TestCase):
    def test_rebalance_grid_independent_of_panel_start(self):
        """A paper panel starting later must rebalance on the same UTC days."""
        full = synthetic_panel(n=700)
        shift = 5
        late = Panel(shift * DAY_MS, 700 - shift)
        for a in ASSETS:
            late.add_asset(a, {(k + shift) * DAY_MS: (full.o[a][k + shift], full.h[a][k + shift],
                                                       full.l[a][k + shift], full.c[a][k + shift],
                                                       full.qv[a][k + shift]) for k in range(700 - shift)})
        for t in range(500, 690, 3):
            for name in ("xs_mom_top5", "xs_mom_top5_vt", "btceth_trend_vt"):
                a, b = S.REGISTERED[name](full, t), S.REGISTERED[name](late, t - shift)
                self.assertEqual(set(a), set(b), f"{name} t={t}")
                for k in a:
                    self.assertAlmostEqual(a[k], b[k], places=12)

    def test_changed_frozen_file_stops_the_run(self):
        import json
        import tempfile
        from lab4 import paper
        with tempfile.TemporaryDirectory() as d:
            old = paper.REGISTRY
            paper.REGISTRY = Path(d) / "registry.json"
            try:
                paper.REGISTRY.write_text(json.dumps({"strategies": [{
                    "id": "x", "module": "lab4.strategies", "function": "xs_mom_top5_vt",
                    "registered_utc": "2026-01-01T00:00:00+00:00", "first_fill_day": "2026-01-02",
                    "cost_per_side": 0.0015, "frozen_files": {"lab4/strategies.py": "0" * 64}}]}))
                with self.assertRaises(SystemExit):
                    paper.update()
            finally:
                paper.REGISTRY = old


if __name__ == "__main__":
    unittest.main()
