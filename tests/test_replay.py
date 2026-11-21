import unittest

import numpy as np

from lane_z.data import QUOTE_DTYPE, US_PER_HOUR, Book
from lane_z.model import Features, local_features, relative_basis
from lane_z.replay import Execution, Session, markout, replay, sides, summarize


START = 2 * US_PER_HOUR + 120_000_000
STEP = 250_000


def quote(venue, receive, bid=99.99, ask=100.01, bid_size=10.0, ask_size=10.0):
    return (venue, receive, bid, ask, bid_size, ask_size)


def make_session(rows, grid, signal_end=None):
    rows = np.array(rows, dtype=QUOTE_DTYPE)
    local, mid, observed = local_features(Book.observed(rows), grid)
    return Session(
        "2026-10-01", "NVDA", grid, signal_end or int(grid[-1]),
        Features(local, local, mid, observed), Book.executed(rows),
    )


class ClocksTest(unittest.TestCase):
    def test_newer_source_quote_is_visible_only_after_receipt(self):
        rows = np.array([
            quote(START - 100_000, START - 50_000),
            quote(START + 80_000, START + 300_000, bid=100.49, ask=100.51),
            quote(START + 50_000, START + 350_000, bid=98.99, ask=99.01),
        ], dtype=QUOTE_DTYPE)
        query = np.array([START, START + 250_000, START + 400_000])
        visible, valid = Book.observed(rows).asof(query, 2_000_000)
        self.assertTrue(valid.all())
        np.testing.assert_allclose(visible["bid"], [99.99, 99.99, 100.49])
        venue, _ = Book.executed(rows).asof(query, 2_000_000)
        np.testing.assert_allclose(venue["bid"], [99.99, 100.49, 100.49])

    def test_empty_and_stale_quotes_invalidate_state(self):
        rows = np.array([
            quote(START, START),
            quote(START + 100_000, START + 150_000, ask=float("nan")),
        ], dtype=QUOTE_DTYPE)
        _, valid = Book.observed(rows).asof(
            np.array([START - 1, START, START + 90_000, START + 150_000]),
            50_000,
        )
        np.testing.assert_array_equal(valid, [False, True, False, False])

    def test_basis_uses_prior_window_and_requires_coverage(self):
        target = np.full(245, 100.0)
        external = target.copy()
        external[240] = 101
        external[241:] = 130
        basis = relative_basis(target, external)
        self.assertTrue(np.isnan(basis[:240]).all())
        self.assertAlmostEqual(basis[240], 10_000 * np.log(1.01))
        external[:100] = float("nan")
        self.assertTrue(np.isnan(relative_basis(target, external)[240]))


class ReplayTest(unittest.TestCase):
    def setUp(self):
        self.grid = START + np.arange(24) * STEP
        self.execution = Execution(hold_ms=1_000)

    def test_price_limit_uses_observed_touch(self):
        rows = [
            quote(t, t, bid=99.99 if i == 0 else 100.49, ask=100.01 if i == 0 else 100.51)
            for i, t in enumerate(self.grid)
        ]
        session = make_session(rows, self.grid)
        predictions = np.zeros(len(self.grid))
        predictions[0] = 10
        result = replay(session, sides(session, predictions, 0), self.execution)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(result.missed_entries, 1)
        self.assertFalse(result.trades)

    def test_partial_entry_exit_and_no_double_consumption(self):
        rows = [
            quote(t, t, bid_size=0.1 if i == 6 else 10, ask_size=0.3 if i == 1 else 10)
            for i, t in enumerate(self.grid) if i not in (7, 8, 9)
        ]
        session = make_session(rows, self.grid)
        direction = np.zeros(len(self.grid), dtype=np.int8)
        direction[0] = direction[3] = 1
        result = replay(session, direction, self.execution)
        summary = summarize([result])
        self.assertEqual(summary["attempts"], 1)
        self.assertEqual(summary["partial_entries"], 1)
        self.assertEqual(summary["delayed_exits"], 1)
        self.assertEqual(summary["round_trips"], 1)
        trade = result.trades[0]
        self.assertAlmostEqual(trade.size, 0.3)
        self.assertEqual(trade.exit_time, self.grid[10])
        self.assertAlmostEqual(trade.gross_usd, -0.006)
        self.assertAlmostEqual(trade.fees_usd, 60 * 0.9 / 10_000)
        self.assertAlmostEqual(trade.net_usd, -0.0129)

    def test_unresolved_exit_marks_result_incomplete(self):
        rows = [quote(t, t, bid_size=10.0 if i <= 1 else 0.0) for i, t in enumerate(self.grid)]
        session = make_session(rows, self.grid)
        direction = np.zeros(len(self.grid), dtype=np.int8)
        direction[0] = 1
        summary = summarize([replay(session, direction, self.execution)])
        self.assertEqual(summary["unresolved_positions"], 1)
        self.assertFalse(summary["complete"])

    def test_hourly_guard_and_delayed_label(self):
        grid = 2 * US_PER_HOUR + 29_750_000 + np.arange(24) * STEP
        rows = [quote(t, t, bid=99.99 + i, ask=100.01 + i) for i, t in enumerate(grid)]
        session = make_session(rows, grid, signal_end=int(grid[12]))
        label = markout(session, self.execution)
        self.assertTrue(np.isnan(label[0]))
        self.assertAlmostEqual(label[1], 10_000 * np.log(107 / 102))
        self.assertTrue(np.isnan(label[12]))


if __name__ == "__main__":
    unittest.main()
