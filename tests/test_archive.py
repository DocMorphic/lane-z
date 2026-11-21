import gzip
import tempfile
import unittest
from pathlib import Path

import numpy as np

from lane_z.archive import arca_quotes, reconstruct_depth
from lane_z.data import QUOTE_DTYPE, US_PER_MS, Book, timestamp


class ArchiveTest(unittest.TestCase):
    def test_depth_applies_each_message_and_waits_for_snapshot_after_gap(self):
        def row(second, kind, seq, previous, side, price, size):
            return second * 1_000_000_000, second * 1_000, None, kind, seq, previous, side, price, size

        updates = [
            row(1, "snapshot", 1, None, "bid", "99", "5"),
            row(1, "snapshot", 1, None, "ask", "101", "6"),
            row(2, "update", 2, 1, "ask", "101", "0"),
            row(2, "update", 2, 1, "ask", "102", "7"),
            row(3, "update", 4, 3, "bid", "100", "2"),
            row(4, "update", 5, 4, "bid", "98", "3"),
            row(5, "snapshot", 6, None, "bid", "100", "1"),
            row(5, "snapshot", 6, None, "ask", "101", "3"),
        ]
        audit = dict(snapshots=0, discarded_updates=0)
        quotes = np.fromiter(reconstruct_depth(updates, audit), dtype=QUOTE_DTYPE)
        np.testing.assert_allclose(quotes["ask"], [101, 102, np.nan, np.nan, 101], equal_nan=True)
        self.assertEqual(audit, dict(snapshots=2, discarded_updates=2))

    def test_arca_quote_fields_and_modeled_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "sample.gz")
            with gzip.open(path, "wt") as sample:
                sample.write("140,30,09:35:00.123456789,NVDA,4,101,130,100,120,R, \n")
                sample.write("140,31,09:35:00.223456789,NVDA,5,101,100,100,100,N, \n")
                sample.write("140,32,09:35:00.223456789,TSLA,5,901,100,900,100,R, \n")
            market = arca_quotes([path], "2026-07-01", ("NVDA",), 125)["NVDA"]
        self.assertEqual(len(market), 2)
        self.assertEqual(market[0]["venue"], timestamp("2026-07-01", 9, 35) + 123_456)
        self.assertEqual(market[0]["receive"] - market[0]["venue"], 125 * US_PER_MS)
        self.assertEqual((market[0]["bid"], market[0]["ask_size"]), (100, 130))
        _, usable = Book.observed(market).asof(market["receive"], 2_000_000)
        np.testing.assert_array_equal(usable, [True, False])
        self.assertTrue(np.isnan(market[1]["bid"]))


if __name__ == "__main__":
    unittest.main()
