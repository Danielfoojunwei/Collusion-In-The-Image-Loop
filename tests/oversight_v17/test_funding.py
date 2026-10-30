import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from next_batch.oversight_v17.finance import Ledger, FundingHold, seed_from_reconciliation, units
from next_batch.oversight_v17.openrouter import Client, MODEL, payload


class FundingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ledger = Ledger(self.root / 'ledger.sqlite')
        self.ledger.create({'aggregate_units': units(20), 'program_units': units(15),
                            'period_remaining_units': units(10), 'historical_rows': 2090}, .10)

    def tearDown(self):
        self.temp.cleanup()

    def response(self, cost=.001, **changes):
        result = {'id': 'fixture-only', 'model': MODEL, 'provider': 'OpenInference',
                  'usage': {'cost': cost}, 'choices': [{'message': {'content': 'fixture'}}]}
        result.update(changes)
        return json.dumps(result).encode()

    def test_pinned_wire_and_first_receipt(self):
        seen = []
        client = Client(self.ledger, self.root / 'calls', lambda wire: seen.append(json.loads(wire)) or self.response())
        _, receipt = client.chat('dev-1', [{'role': 'user', 'content': 'bounded input'}], kind='research_development', max_tokens=2048)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]['temperature'], 1.0)
        self.assertEqual(seen[0]['reasoning']['effort'], 'high')
        self.assertFalse(seen[0]['provider']['allow_fallbacks'])
        self.assertEqual(receipt['cost_usd'], .001)
        with self.assertRaises(FundingHold):
            client.chat('dev-1', [{'role': 'user', 'content': 'changed input'}])
        self.assertEqual(len(seen), 1)

    def test_ambiguous_response_holds_next_call(self):
        client = Client(self.ledger, self.root / 'calls', lambda _: b'{')
        with self.assertRaises(json.JSONDecodeError):
            client.chat('bad-1', [{'role': 'user', 'content': 'x'}])
        self.assertEqual(self.ledger.summary()['calls'][0]['status'], 'uncertain')
        with self.assertRaises(FundingHold):
            self.ledger.reserve('next-2', 'actor', b'x', .001)

    def test_missing_usage_not_free(self):
        client = Client(self.ledger, self.root / 'calls', lambda _: self.response(usage={}))
        with self.assertRaises(ValueError):
            client.chat('no-cost', [{'role': 'user', 'content': 'x'}])
        self.assertGreater(self.ledger.summary()['new_committed_usd'], 0)

    def test_overrun_is_recorded_and_blocks(self):
        self.ledger.reserve('r1', 'actor', b'wire', .001)
        with self.assertRaises(FundingHold):
            self.ledger.complete('r1', .002)
        self.assertEqual(self.ledger.summary()['calls'][0]['status'], 'overrun')
        self.assertEqual(self.ledger.summary()['new_committed_usd'], .002)
        with self.assertRaises(FundingHold):
            self.ledger.reserve('r2', 'actor', b'wire', .001)

    def test_cumulative_cap_cannot_reset(self):
        with self.assertRaises(FundingHold):
            self.ledger.create({}, 1)
        with self.assertRaises(FundingHold):
            self.ledger.reserve('large', 'actor', b'x', .11)

    def test_unmatched_account_usage_is_charged(self):
        seed = seed_from_reconciliation({'rows': 2090, 'aggregate_committed_usd': 10.687136432,
            'program_committed_usd': 5.249008169, 'uncertain_reserve_usd': 1.0922836,
            'settled_usd': 9.594852832}, {'usage': 19.140089458, 'limit_remaining': 10})
        self.assertEqual(seed['aggregate_units'], units('20.232373058'))
        self.assertEqual(seed['program_units'], units('14.794244795'))

    def test_missing_key_never_reserves(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(FundingHold):
                Client(self.ledger, self.root / 'calls').chat('missing', [{'role': 'user', 'content': 'x'}])
        self.assertEqual(self.ledger.summary()['calls'], [])

    def test_secret_not_in_archive(self):
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'sentinel-secret'}):
            Client(self.ledger, self.root / 'calls', lambda _: self.response()).chat('safe', [{'role': 'user', 'content': 'x'}])
        for path in (self.root / 'calls').rglob('*'):
            if path.is_file():
                self.assertNotIn(b'sentinel-secret', path.read_bytes())

    def test_invalid_limits_and_costs(self):
        for value in [0, -1, True, 65537]:
            with self.assertRaises(FundingHold):
                payload([], value)
        for value in [-1, float('inf'), float('nan')]:
            with self.assertRaises(FundingHold):
                units(value)

    def test_wrong_route_keeps_real_cost(self):
        client = Client(self.ledger, self.root / 'calls', lambda _: self.response(provider='unexpected'))
        with self.assertRaises(FundingHold):
            client.chat('mismatch', [{'role': 'user', 'content': 'x'}])
        self.assertEqual(self.ledger.summary()['new_committed_usd'], .001)


if __name__ == '__main__':
    unittest.main()
