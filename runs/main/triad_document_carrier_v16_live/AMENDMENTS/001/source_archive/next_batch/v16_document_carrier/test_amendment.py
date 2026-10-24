"""Explicit scripted fixtures for the technical recovery transaction; no API use."""
from copy import deepcopy
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from next_batch.v15_glm_restored.storage import (
    IntegrityError, canonical, lock, put, put_bytes, read, sha,
)
from . import amendment as a, design, prompts


class FixtureLedger:
    def __init__(self, path):
        self.path = path
        with self._connect() as db:
            db.execute('CREATE TABLE requests(request_id TEXT PRIMARY KEY,status TEXT,kind TEXT,model TEXT,request_hash TEXT,reserved_units INTEGER,actual_units INTEGER)')
            db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?)',
                       (a.NAMESPACE+'b01.r01.a01.A_PUBLISH.native', 'completed', 'chat',
                        'z-ai/glm-5.3-flash', 'a'*64, 31491200, 695924))
            db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?)',
                       ('older_namespace:uncertain', 'uncertain', 'chat',
                        'OLD_OFFLINE_FIXTURE', 'b'*64, 1000000, None))

    def _connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db


class FixtureRunner:
    fixture = True

    def __init__(self, out, source):
        self.out, self.source = out, source

    def source_files(self):
        return [self.source]


class AmendmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.out = self.root/'OFFLINE_FIXTURE_RUN'
        self.out.mkdir()
        self.source = self.root/'OFFLINE_FIXTURE_source.py'
        self.source.write_text('# OFFLINE fixture original source\n')
        self.runner = FixtureRunner(self.out, self.source)
        old = deepcopy(design.CONFIG)
        old['offline_fixture'] = True
        old['sampling']['max_output_tokens'] = 16384
        old['sampling']['stage_max_output_tokens']['publish'] = 16384
        old['normal_reservation_ceiling_usd'] = 19.7568
        put(self.out/'CONFIG.json', old)
        put(self.out/'RESEARCHER_ONLY_CASES.json', design.cases())
        systems = {arm['id']+'.'+stage: prompts.actor_system(stage[0], stage, arm['id'])
                   for arm in design.ARMS for stage in design.STAGES}
        put(self.out/'SYSTEM_PROMPTS.json', systems)
        archived = self.out/'source_archive/OFFLINE_FIXTURE_source.py'
        put_bytes(archived, self.source.read_bytes())
        put(self.out/'SOURCE_FILES.json', {'files': [{'path': str(self.source),
            'sha256': sha(self.source.read_bytes()), 'archived_path': str(archived)}]})
        frozen = {key: sha((self.out/name).read_bytes()) for name,key in a.ARTIFACTS}
        frozen['evidence_origin'] = 'OFFLINE_SCRIPTED_FIXTURE'
        put(self.out/'freeze.json', frozen)
        put(self.out/'STOP_REQUESTED.json', {'reason': 'OFFLINE scripted completion-cap hold'})
        self.original = {name: (self.out/name).read_bytes() for name,key in a.ARTIFACTS}
        self.original['freeze.json'] = (self.out/'freeze.json').read_bytes()
        for arm in a.ARMS:
            path = self.out/'arms'/arm/'attempts/r01/a01'
            put(path/'context.json', {'attempt_id': arm+'.r01.a01',
                'previous_checkpoint_sha256': None, 'evidence_origin': 'OFFLINE_SCRIPTED_FIXTURE'})
            put(path/'stages/B_INITIAL.json', {'evidence_origin': 'OFFLINE_SCRIPTED_FIXTURE'})
        cp = self.out/'arms/b01/checkpoints/r01.json'
        value = {'round': 1, 'status': 'behavioral_invalid',
                 'behavioral_failure': {'stage': 'A_PUBLISH'},
                 'evidence_origin': 'OFFLINE_SCRIPTED_FIXTURE'}
        put(cp, value)
        put(self.out/'arms/b01/attempts/r01/a01/stages/COMMIT.json', value)
        put(self.out/'arms/b01/attempts/r02/a01/context.json',
            {'round': 2, 'previous_checkpoint_sha256': sha(cp.read_bytes()),
             'evidence_origin': 'OFFLINE_SCRIPTED_FIXTURE'})
        put(self.out/'reviews/OFFLINE_b04_MAIN/public/packet.json',
            {'round': 1, 'kind': 'MAIN', 'evidence_origin': 'OFFLINE_SCRIPTED_FIXTURE'})
        put(self.out/'provider_evidence/observed_responses/OFFLINE_capped.json', {
            'evidence_origin': 'OFFLINE_SCRIPTED_FIXTURE',
            'binding': {'request_id': a.NAMESPACE+'b01.r01.a01.A_PUBLISH.native'},
            'response': {'usage': {'completion_tokens': 16384,
                                  'completion_tokens_details': {'reasoning_tokens': 14928}},
                         'choices': [{'finish_reason': 'tool_calls',
                                      'message': {'tool_calls': [{'function': {'arguments': '{"document":'}}]}}]}})
        self.ledger = FixtureLedger(self.root/'OFFLINE_budget.sqlite')
        self.source.write_text('# OFFLINE fixture amended source\n')

    def tearDown(self):
        self.temporary.cleanup()

    def apply(self):
        return a.apply_truncation_recovery(self.runner, self.ledger)

    def test_full_recovery_preserves_originals_raw_attempts_and_ledger(self):
        before = a._ledger_snapshot(self.ledger)
        result = self.apply()
        self.assertEqual(result['status'], 'amended_held_for_free_preflight')
        self.assertTrue((self.out/'STOP_REQUESTED.json').exists())
        self.assertEqual(a._ledger_snapshot(self.ledger), before)
        for name,raw in self.original.items():
            self.assertEqual((self.out/name).read_bytes(), raw)
        for arm in a.ARMS:
            old = self.out/'arms'/arm/'attempts/r01/a01'
            self.assertTrue((old/'stages/B_INITIAL.json').exists())
            self.assertEqual(read(old/'abandoned.json')['status'], 'abandoned_technical')
        base = self.out/'AMENDMENTS/001'
        self.assertTrue((base/'superseded/checkpoints/b01/r01.json').exists())
        self.assertFalse((self.out/'arms/b01/checkpoints/r01.json').exists())
        self.assertTrue((self.out/'arms/b01/attempts/r01/a01/stages/COMMIT.json').exists())
        self.assertFalse((self.out/'arms/b01/attempts/r02/a01/context.json').exists())
        self.assertTrue((base/'superseded/unfunded_future_contexts/b01/r02/a01/context.json').exists())
        self.assertFalse(read(self.out/'reviews/OFFLINE_b04_MAIN/SUPERSEDED_TECHNICAL.json')['judgment_observed'])
        self.assertEqual(a.verify_chain(self.out, fixture=True)['directory'], base)

    def test_original_and_current_source_verification_are_distinct(self):
        with self.assertRaises(IntegrityError):
            a.verify_chain(self.out, fixture=True)
        a.verify_chain(self.out, fixture=True, verify_current=False)
        self.apply()
        a.verify_chain(self.out, fixture=True)
        (self.out/'source_archive/OFFLINE_FIXTURE_source.py').write_text('CORRUPTED')
        with self.assertRaises(IntegrityError):
            a.verify_chain(self.out, fixture=True)

    def test_current_source_change_after_amendment_is_rejected(self):
        self.apply()
        self.source.write_text('CORRUPTED_CURRENT_SOURCE')
        with self.assertRaises(IntegrityError):
            a.verify_chain(self.out, fixture=True)

    def test_retained_raw_cap_response_and_stop_archives_are_verified(self):
        self.apply()
        paths = [self.out/'arms/b03/attempts/r01/a01/stages/B_INITIAL.json',
                 self.out/'provider_evidence/observed_responses/OFFLINE_capped.json',
                 self.out/'AMENDMENTS/001/STOP_REQUESTED.original.json']
        for path in paths:
            with self.subTest(artifact=path.name):
                original = path.read_bytes()
                path.write_bytes(b'CORRUPTED_OFFLINE_FIXTURE')
                with self.assertRaises(IntegrityError):
                    a.verify_chain(self.out, fixture=True)
                path.write_bytes(original)

    def test_idempotent_completed_recovery_makes_no_new_mutations(self):
        first = self.apply()
        second = self.apply()
        self.assertEqual(second['status'], 'already_applied')
        self.assertEqual(first['freeze_sha256'], second['freeze_sha256'])

    def test_interrupted_move_resumes_same_intent_without_losing_data(self):
        original = a._move_file
        calls = []
        def fail_after_first_move(*args):
            original(*args)
            calls.append(args)
            if len(calls) == 1:
                raise RuntimeError('OFFLINE scripted process interruption')
        with patch.object(a, '_move_file', side_effect=fail_after_first_move):
            with self.assertRaises(RuntimeError):
                self.apply()
        self.assertEqual(a.active_freeze(self.out)['directory'], self.out)
        self.assertTrue((self.out/'STOP_REQUESTED.json').exists())
        result = self.apply()
        self.assertEqual(result['status'], 'amended_held_for_free_preflight')
        a.verify_chain(self.out, fixture=True)

    def test_release_requires_complete_verified_amendment_and_archives_stop(self):
        with self.assertRaises(IntegrityError):
            a.release_stop(self.out, fixture=True)
        self.apply()
        result = a.release_stop(self.out, fixture=True)
        self.assertFalse((self.out/'STOP_REQUESTED.json').exists())
        self.assertTrue((self.out/'AMENDMENTS/001/STOP_REQUESTED.original.json').exists())
        self.assertEqual(result['new_paid_calls'], 0)
        self.assertEqual(a.release_stop(self.out, fixture=True)['status'], result['status'])

    def test_release_cannot_clear_a_newer_operator_hold(self):
        self.apply()
        (self.out/'STOP_REQUESTED.json').unlink()
        put(self.out/'STOP_REQUESTED.json', {'reason': 'OFFLINE newer unrelated hold'})
        with self.assertRaises(IntegrityError):
            a.release_stop(self.out, fixture=True)

    def test_unresolved_or_later_paid_identity_blocks_recovery(self):
        with self.ledger._connect() as db:
            db.execute("UPDATE requests SET status='uncertain' WHERE request_id LIKE ?", (a.NAMESPACE+'%',))
        with self.assertRaises(IntegrityError):
            self.apply()
        with self.ledger._connect() as db:
            db.execute("UPDATE requests SET status='completed', request_id=? WHERE request_id LIKE ?", (a.NAMESPACE+'b01.r02.a01.B_INITIAL.native',a.NAMESPACE+'%'))
        with self.assertRaises(IntegrityError):
            self.apply()

    def test_active_producer_lock_blocks_offline_recovery(self):
        with lock(self.out/'arms/b03'):
            with self.assertRaises(IntegrityError):
                self.apply()

    def test_launched_review_or_complete_round_cannot_be_superseded(self):
        put(self.out/'reviews/OFFLINE_b04_MAIN/launch.json', {'agent_id': 'OFFLINE_ACTUAL_CONTEXT_PLACEHOLDER'})
        with self.assertRaises(IntegrityError):
            self.apply()
        (self.out/'reviews/OFFLINE_b04_MAIN/launch.json').unlink()
        cp = self.out/'arms/b01/checkpoints/r01.json'
        cp.unlink()
        put(cp, {'round': 1, 'status': 'complete', 'evidence_origin': 'OFFLINE_SCRIPTED_FIXTURE'})
        with self.assertRaises(IntegrityError):
            self.apply()

    def test_fixture_cannot_be_promoted_to_live(self):
        self.apply()
        with self.assertRaises(IntegrityError):
            a.verify_chain(self.out, fixture=False)


if __name__ == '__main__':
    unittest.main()
