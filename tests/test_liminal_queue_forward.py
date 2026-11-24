import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from lane_z.liminal_forward import DAY, utc_time
from lane_z.liminal_queue_forward import score
from lane_z.liminal_queued import QueueEpisode


class LiminalQueueForwardTest(unittest.TestCase):
    def setUp(self):
        self.start = 1_795_470_269
        self.request = self.start + 56 * DAY
        self.end = self.start + 63 * DAY
        marks = [
            {'block': {'number': i + 2, 'timestamp': self.request + i * DAY},
             'standard_fee_logic_verified': True,
             'quote': {'reason': None, 'gross_micro_usdc': gross, 'net_micro_usdc': 2_014_000_000}}
            for i, gross in enumerate((2_023_000_000, 2_020_000_000, 2_021_000_000, 2_022_000_000,
                                       2_023_000_000, 2_025_000_000, 2_026_000_000, 2_027_000_000))
        ]
        self.state = {'entry': {'captured': {'block': {'timestamp': self.start}},
                                'request_after_utc': utc_time(self.request)}, 'marks': marks}

    def run_with_comparables(self, episodes):
        module = 'lane_z.liminal_queue_forward'
        terminal = self.state['marks'][-1]['block']
        references = ([], {}, {}, {'blocks': {str(terminal['number']): terminal}}, {}, {})
        with (patch(f'{module}.collect_logs'), patch(f'{module}.collect_details'),
              patch(f'{module}.read_tape', return_value=references),
              patch(f'{module}.reconstruct', return_value=([], episodes, {}, {}))):
            return score(self.state, Path('unused'))

    def test_conditional_queue_is_priced_at_lowest_mark_and_requires_comparable_capacity(self):
        comparable = QueueEpisode(self.request + 3, 0, 'receiver', assets_micro=2_050_000_000,
                                  settled_at=self.end - 10)
        result = self.run_with_comparables([comparable])
        self.assertEqual(Decimal(result['queue_payout_if_settled_usdc']), Decimal('2017.980'))
        self.assertEqual(Decimal(result['instant_pnl_usdc']), Decimal('11.986'))
        self.assertTrue(result['queue_capacity_supported'])
        self.assertTrue(result['queue_beats_cash_if_settled_with_observed_capacity'])
        self.assertEqual(result['queue_actual_settlement'], 'unobserved_paper')

    def test_late_or_overlapping_comparables_cannot_pass(self):
        late = QueueEpisode(self.request + 3, 0, 'receiver', assets_micro=2_150_000_000,
                            settled_at=self.end + 1)
        overlapping = QueueEpisode(self.request + 3, 0, 'receiver', requests=2, assets_micro=2_150_000_000,
                                   settled_at=self.end - 10)
        result = self.run_with_comparables([late, overlapping])
        self.assertFalse(result['queue_capacity_supported'])
        self.assertFalse(result['queue_beats_cash_if_settled_with_observed_capacity'])
        self.assertEqual(result['queue_status'], 'no_comparable_full_payment')
