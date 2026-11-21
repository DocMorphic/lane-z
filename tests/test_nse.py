import unittest
from datetime import date

from lane_z.nse import BUDGET_INR, SYMBOLS, Prices, choose, fee, screen


class IndiaScreenTest(unittest.TestCase):
    def test_fee_has_capped_brokerage_and_asymmetric_tax(self):
        self.assertAlmostEqual(fee(50_000, True), 21.070359)
        self.assertAlmostEqual(fee(50_000, False), 32.070359)
        # At this size the brokerage is capped at INR 20 on both orders.
        self.assertAlmostEqual(fee(100_000, True), 30.340718)
        self.assertAlmostEqual(fee(100_000, False), 52.340718)

    def test_order_uses_prior_close_and_whole_shares(self):
        signal_day, execution_day = date(2026, 9, 1), date(2026, 9, 2)
        previous = dict.fromkeys(SYMBOLS, Prices(100, 100, 100, 100))
        previous["RELIANCE"] = Prices(99, 97, 97, 100)
        today = dict.fromkeys(SYMBOLS, Prices(100, 100, 100, 100))
        today["RELIANCE"] = Prices(98, 99, 99, 97)
        today["TCS"] = Prices(80, 80, 80, 100)
        self.assertEqual(choose(previous)[0], "RELIANCE")
        (actual,) = screen({signal_day: previous, execution_day: today}, 2)
        self.assertTrue(actual["filled"])
        self.assertEqual(actual["symbol"], "RELIANCE")
        self.assertEqual(actual["shares"], 505)
        self.assertLessEqual(actual["entry_notional_inr"], BUDGET_INR)
        today["RELIANCE"] = Prices(99, 100, 100, 97)
        (above_limit,) = screen({signal_day: previous, execution_day: today}, 2)
        self.assertFalse(above_limit["filled"])


if __name__ == "__main__":
    unittest.main()
