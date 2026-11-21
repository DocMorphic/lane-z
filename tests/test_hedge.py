import unittest
from dataclasses import replace

import numpy as np

from lane_z.hedge import BINANCE, LIGHTER, replay_pair, summarize
from test_replay import START, STEP, make_session, quote


class HedgeReplayTest(unittest.TestCase):
    def setUp(self):
        self.grid = START + np.arange(180) * STEP
        self.instructions = np.zeros(len(self.grid), dtype=np.int8)
        self.instructions[0] = 1
        self.steady = make_session([quote(t, t) for t in self.grid], self.grid)

    def test_delaying_one_leg_creates_exposure_on_entry_and_exit(self):
        settings = (replace(LIGHTER, delay_ms=900), BINANCE)
        stats = summarize([replay_pair((self.steady, self.steady), self.instructions, settings)])
        self.assertEqual(stats["hedged_round_trips"], 1)
        self.assertEqual(stats["forced_unwinds_closed"], 0)
        self.assertAlmostEqual(stats["unhedged_ms_p90_if_exposed"], 800)
        self.assertAlmostEqual(stats["gross_before_fees_usd"], -0.0396)
        self.assertAlmostEqual(stats["net_usd"], -0.1386)

    def test_failed_second_leg_triggers_early_unwind_and_charges_it(self):
        rows = [quote(t, t, bid=98.99, ask=99.01) if t > START else quote(t, t) for t in self.grid]
        second = make_session(rows, self.grid)
        stats = summarize([replay_pair((self.steady, second), self.instructions, (LIGHTER, BINANCE))])
        self.assertEqual(stats["one_leg_entries"], 1)
        self.assertEqual(stats["forced_unwinds_closed"], 1)
        self.assertEqual(stats["hedged_round_trips"], 0)
        self.assertAlmostEqual(stats["unhedged_ms_p90_if_exposed"], 1_000)
        self.assertAlmostEqual(stats["forced_unwind_net_usd"], -0.0297)
        self.assertEqual(stats["fees_usd"], 0)
