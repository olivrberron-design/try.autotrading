import sys
from decimal import Decimal
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from demo_smoke_test import BASE, round_to_step, sell_quantity, symbol_rules


class TestDemo(unittest.TestCase):
    def test_hardcoded_demo_host(self):
        self.assertEqual(BASE, "https://demo-api.binance.com")

    def test_lot_rounding(self):
        self.assertEqual(round_to_step("0.001999", "0.00001"), Decimal("0.00199"))

    def test_market_quantity(self):
        filt = {"LOT_SIZE": {"stepSize": "0.00001", "minQty": "0.00001"},
                "MARKET_LOT_SIZE": {"stepSize": "0", "minQty": "0", "maxQty": "0"}}
        self.assertEqual(sell_quantity(Decimal("0.0003042"), filt), Decimal("0.00030"))

    def test_symbol_not_spot(self):
        with self.assertRaises(RuntimeError):
            symbol_rules({"symbols": [{"symbol": "BTCUSDC", "status": "TRADING",
                                       "isSpotTradingAllowed": False, "orderTypes": ["MARKET"]}]})


if __name__ == "__main__":
    unittest.main()
