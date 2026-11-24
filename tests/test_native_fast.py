import unittest

from lane_z.native_fast import Book, Signal, replay, signals


def snapshot(time, mid, size=10):
    return Book(time, time, ((mid - 0.005, size),), ((mid + 0.005, size),))


class NativeFastTest(unittest.TestCase):
    def test_reversal_requires_observed_price_and_trade_flow_turn(self):
        books = [snapshot(time, 100 if time < 3_000 else {3_000: 100.15, 3_250: 100.21}.get(time, 100.17))
                 for time in range(0, 5_000, 250)]
        trades = [(time, 1_000, 1_000) for time in (1_500, 2_000, 2_500, 2_750)]
        trades.extend(((3_250, 1_500, -1_500), (3_500, 1_500, -1_500)))

        policies = signals(0, 5_000, books, trades)

        self.assertEqual([(s.time, s.side) for s in policies['continuation']], [(3_000, 1)])
        self.assertEqual([(s.time, s.side) for s in policies['reversal']], [(3_500, -1)])

    def test_replay_rejects_late_entry_beyond_limit_and_charges_both_legs(self):
        decision = snapshot(1_000, 100)
        entry = snapshot(1_500, 100.01)
        exit_book = snapshot(32_000, 100.40)
        signals_to_score = [Signal(1_000, 1, decision)]

        completed = replay([decision, entry, exit_book], signals_to_score, .01, 40_000, 500, 1)
        missed = replay([decision, snapshot(1_500, 100.10), exit_book], signals_to_score, .01, 40_000, 500, 1)

        trade = completed['trades'][0]
        self.assertAlmostEqual(trade['gross_usdc'], .99 * (100.395 - 100.015))
        self.assertAlmostEqual(trade['fees_usdc'], .99 * (100.395 + 100.015) * .00045)
        self.assertAlmostEqual(trade['extra_slippage_usdc'], .99 * (100.395 + 100.015) * .0001)
        self.assertEqual(missed['missed_entries'], 1)
        self.assertEqual(missed['trades'], [])
