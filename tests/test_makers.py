import unittest

import numpy as np

from lane_z.absorption import direction
from lane_z.makers import aggressor_flow, liquidity_features
from test_replay import START, STEP, make_session, quote


class MarketMakerFeaturesTest(unittest.TestCase):
    def test_trade_window_uses_receipt_to_the_nanosecond(self):
        grid = np.array([START, START + STEP])
        decision_ns = START * 1_000
        time = np.array([decision_ns - 1_000_000_000, decision_ns, decision_ns + 1])
        size = np.array([10.0, 2.0, 3.0])
        signed, volume = aggressor_flow(time, size, np.array([False, True, False]), grid)
        np.testing.assert_array_equal(signed, [-2, 1])
        np.testing.assert_array_equal(volume, [2, 5])

    def test_thinning_and_additions_only_use_observed_displayed_depth(self):
        grid = START + np.arange(24) * STEP
        rows = [quote(t, t, bid_size=1 if i >= 8 else 10, ask_size=1 if i >= 8 else 10)
                for i, t in enumerate(grid)]
        rows[9] = quote(grid[9], grid[10], bid_size=5, ask_size=1)
        del rows[10]
        session = make_session(rows, grid)
        zeros = np.zeros(len(grid))
        matrices, withdrawal = liquidity_features(session, zeros, zeros, session.features.local)
        self.assertFalse(withdrawal[7])
        self.assertTrue(withdrawal[8])
        self.assertEqual(matrices["book_and_leader"][8, 11], 9)
        self.assertEqual(matrices["book_and_leader"][9, 9], 0)
        self.assertAlmostEqual(matrices["book_and_leader"][10, 9], 4 / 6)

    def test_absorption_requires_aggression_against_the_replenishing_side(self):
        grid = START + np.arange(24) * STEP
        rows = [quote(t, t, bid=99.99 if i < 9 else 100.00, ask=100.01 if i < 9 else 100.02,
                      bid_size=10 if i < 8 else 20) for i, t in enumerate(grid)]
        session = make_session(rows, grid)
        volume = np.zeros(len(grid))
        volume[9] = 5
        self.assertEqual(direction(session, -volume, volume)[9], 1)
        self.assertFalse(direction(session, volume, volume).any())
