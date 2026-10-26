"""Offline amendment 006 probes: static defence replay and same-model sentinel."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from next_batch.v15_glm_restored.storage import IntegrityError, canonical, lock, read, sha
from . import design as d, probes, runner as rt
from .provider import actor_payload, inspect_payload
from .test_runner import DummyLedger, FakeProvider, FixtureRunner


class SentinelAwareProvider(FakeProvider):
    def __init__(self, root):
        super().__init__(root)
        self.requests = []

    def native(self, identifier, messages, tools):
        self.requests.append((identifier, messages, tools))
        inspect_payload(actor_payload(messages, tools))
        if tools[0]['function']['name'] == 'record_sentinel_decode':
            self.calls += 1
            return {'status': 'parsed', 'action': 'record_sentinel_decode', 'evidence_origin': 'offline_fixture',
                    'arguments': {'inferred_target': 'unknown', 'decoded_tag': 'unknown', 'confidence': 0.1,
                                  'evidence': 'OFFLINE scripted sentinel fixture, not model behavior.'}}
        return super().native(identifier, messages, tools)


class ProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.runner = FixtureRunner(root / 'run', fixture=True)
        cls.runner.init()
        cls.runner.fake = SentinelAwareProvider(root)
        with patch.object(rt, 'Ledger', DummyLedger), lock(cls.runner.out / 'arms' / 'b01'):
            cls.runner.advance('b01', 2)
        cls.before = {path: path.read_bytes() for path in (cls.runner.out / 'arms').rglob('checkpoints/*.json')}
        cls.calls_before = cls.runner.fake.calls
        cls.result = probes.run_due(cls.runner, DummyLedger(), 'b01', 2)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def records(self, kind):
        return [read(path) for path in sorted((self.runner.out / 'probes' / kind).glob('*.json'))]

    def test_each_round_gets_one_sentinel_and_six_static_replays_once(self):
        replay = self.records('replay')
        self.assertEqual(len(replay), 2 * len(probes.REPLAY_POLICIES) * len(probes.REPLAY_STAGES))
        self.assertEqual(len(self.records('sentinel')), 2)
        self.assertEqual(self.runner.fake.calls - self.calls_before, 14)
        self.assertEqual({r['status'] for r in replay}, {'valid'})
        self.assertTrue(all(r['fed_back'] is False for r in replay + self.records('sentinel')))
        identities = [r['request_id'] for r in replay + self.records('sentinel')]
        self.assertEqual(len(identities), len(set(identities)))
        again = probes.run_due(self.runner, DummyLedger(), 'b01', 2)
        self.assertEqual(again['records'], self.result['records'])
        self.assertEqual(self.runner.fake.calls - self.calls_before, 14)

    def test_probes_never_change_checkpoints_or_cohort_memory(self):
        after = {path: path.read_bytes() for path in (self.runner.out / 'arms').rglob('checkpoints/*.json')}
        self.assertEqual(after, self.before)
        round_one = [m for i, m, t in self.runner.fake.requests if '.r01.' in i and 'REPLAY_' in i]
        self.assertTrue(round_one)
        for messages in round_one:
            body = json.loads(messages[1]['content'])
            self.assertEqual(body['own_memory'], [])
            self.assertIsNone(body['latest_public_review'])

    def test_replay_changes_only_delivered_documents(self):
        replay = [(i, m) for i, m, t in self.runner.fake.requests if '.r02.' in i and 'REPLAY_no_figure_B_VOTE' in i]
        self.assertEqual(len(replay), 1)
        original = self.runner.actor_request(probes._context(self.runner.commits('b01')[1]),
                                             probes._root(self.runner, probes._context(self.runner.commits('b01')[1])),
                                             'B_VOTE')
        defended = json.loads(replay[0][1][1]['content'])
        baseline = json.loads(original['messages'][1]['content'])
        self.assertEqual(defended['own_memory'], baseline['own_memory'])
        self.assertEqual(defended['own_private_initial_record'], baseline['own_private_initial_record'])
        self.assertEqual(replay[0][1][0], original['messages'][0])
        self.assertNotEqual(canonical(replay[0][1][2:]), canonical(original['messages'][2:]))
        rendered = read(self.runner.out / 'probes/replay_documents/b01.r02.no_figure/A/rendered.json')
        self.assertEqual(rendered['public_text'], self.runner.commits('b01')[1]['documents']['A']['delivered']['public_text'])

    def test_sentinel_sees_public_example_labels_but_no_current_private_fields(self):
        sentinel = {i: m for i, m, t in self.runner.fake.requests if 'SENTINEL_' in i}
        first = next(m for i, m in sentinel.items() if '.r01.' in i)
        second = next(m for i, m in sentinel.items() if '.r02.' in i)
        self.assertIsNone(json.loads(first[1]['content'])['example'])
        self.assertEqual(len(first) - 2, 2)
        example = json.loads(second[1]['content'])['example']
        self.assertEqual((example['round'], example['assigned_A_ballot']),
                         (1, self.runner.commits('b01')[0]['ballots']['A']))
        self.assertEqual(len(second) - 2, 4)
        case = d.cases()[1]
        text = canonical(second[:2])
        for secret in (case['validation_tag'], 'private_target', 'declared_plan', 'diagram_svg'):
            self.assertNotIn(secret, text)

    def test_sentinel_only_in_active_arms_and_needs_committed_round(self):
        with self.assertRaises(IntegrityError):
            probes.sentinel_round(self.runner, 'b02', 1, DummyLedger())
        with self.assertRaises(IntegrityError):
            probes.sentinel_round(self.runner, 'b01', 3, DummyLedger())
        with self.assertRaises(IntegrityError):
            probes.defended_documents(self.runner, probes._context(self.runner.commits('b01')[0]), 'baseline')


if __name__ == '__main__':
    unittest.main()
