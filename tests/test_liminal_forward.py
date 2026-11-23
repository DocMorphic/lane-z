import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from lane_z.liminal_forward import DAY, HOLD_DAYS, DataUnavailable, apply_capture, position, tick


class LiminalForwardTest(unittest.TestCase):
    def setUp(self):
        self.block = {'number': 100, 'timestamp': 1_795_470_269, 'hash': 'entry'}
        self.state = {
            'active': position(self.block, '1900000000000000000000'), 'status': 'running',
            'last_block': self.block, 'next_at': self.block['timestamp'] + DAY, 'streak': 0,
            'results': [], 'observations': [],
        }

    def capture(self, timestamp, micro=2_020_000_000, reason=None, reentry=True):
        return {
            'block': {'number': 101 + len(self.state['observations']), 'timestamp': timestamp, 'hash': 'captured'},
            'observation': 'archival_recovery',
            'quote': {'reason': reason, 'net_micro_usdc': micro},
            'entry': {'shares_wei': '1850000000000000000000'} if reentry else None,
        }

    def test_profitable_daily_observation_is_not_a_completed_period(self):
        day_one = self.state['next_at']
        apply_capture(self.state, self.capture(day_one))
        self.assertEqual(self.state['streak'], 0)
        self.assertEqual(self.state['results'], [])
        self.assertEqual(self.state['next_at'], day_one + DAY)
        self.assertIn('paper_pnl_if_redeemed_usdc', self.state['observations'][0])

    def test_only_three_consecutive_completed_periods_qualify(self):
        for period in range(3):
            closing_day = self.state['active']['due']
            self.state['next_at'] = closing_day
            apply_capture(self.state, self.capture(closing_day))
            self.assertEqual(self.state['streak'], period + 1)
        self.assertEqual(self.state['status'], 'qualified')
        self.assertIsNone(self.state['next_at'])
        self.assertIsNone(self.state['active'])
        self.assertEqual(len(self.state['results']), 3)
        self.assertEqual(self.state['results'][0]['exit_block'], self.state['results'][1]['entry_block'])
        self.assertEqual(self.state['results'][1]['observation'], 'archival_recovery')

    def test_positive_profit_below_cash_breaks_streak(self):
        self.state['streak'] = 2
        closing_day = self.state['active']['due']
        self.state['next_at'] = closing_day
        apply_capture(self.state, self.capture(closing_day, micro=2_012_000_000))
        self.assertEqual(self.state['streak'], 0)
        self.assertEqual(self.state['results'][0]['net_pnl_usdc'], '9.988')
        self.assertFalse(self.state['results'][0]['passed'])
        self.assertEqual(self.state['active']['due'], closing_day + HOLD_DAYS * DAY)

    def test_unavailable_redemption_cannot_be_replaced_by_later_profitable_quote(self):
        self.state['streak'] = 1
        closing_day = self.state['active']['due']
        self.state['next_at'] = closing_day
        apply_capture(self.state, self.capture(closing_day, reason='insufficient_instant_redemption_reserve'))
        self.assertEqual(self.state['streak'], 0)
        self.assertTrue(self.state['active']['awaiting_exit'])
        self.assertNotIn('net_pnl_usdc', self.state['results'][0])
        apply_capture(self.state, self.capture(closing_day + DAY))
        self.assertEqual(len(self.state['results']), 1)
        self.assertFalse(self.state['results'][0]['passed'])
        self.assertEqual(self.state['streak'], 0)
        self.assertEqual(self.state['active']['entry_block']['timestamp'], closing_day + DAY)

    def test_a_rollover_gap_breaks_continuity(self):
        self.state['streak'] = 1
        closing_day = self.state['active']['due']
        self.state['next_at'] = closing_day
        apply_capture(self.state, self.capture(closing_day, reentry=False))
        self.assertTrue(self.state['results'][0]['passed'])
        self.assertIsNone(self.state['active'])
        self.assertEqual(self.state['streak'], 0)

    def test_first_block_after_chain_gap_can_also_be_the_exit_checkpoint(self):
        closing_day = self.state['active']['due']
        self.state['next_at'] = closing_day - DAY
        apply_capture(self.state, self.capture(closing_day + 17))
        self.assertTrue(self.state['results'][0]['passed'])
        self.assertEqual(self.state['active']['entry_block']['timestamp'], closing_day + 17)
        self.assertEqual(self.state['next_at'], closing_day + DAY + 17)

    def test_missing_old_daily_mark_can_advance_but_terminal_checkpoint_cannot(self):
        end = self.state['active']['due']
        self.state['next_at'] = end - DAY
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            path.write_text(json.dumps(self.state))
            with patch('lane_z.liminal_forward.time', return_value=end + DAY), patch(
                'lane_z.liminal_forward.verify_capture', side_effect=DataUnavailable('archive offline')
            ):
                updated, changed = tick(path)
                self.assertTrue(changed)
                self.assertEqual(updated['next_at'], end)
                self.assertEqual(len(updated['missing_marks']), 1)
                with self.assertRaises(DataUnavailable):
                    tick(path)
            self.assertEqual(json.loads(path.read_text())['next_at'], end)
