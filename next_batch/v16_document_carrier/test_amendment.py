"""Explicit scripted fixtures for the technical recovery transaction; no API use."""
from copy import deepcopy
from contextlib import contextmanager
from decimal import Decimal
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from next_batch.v15_glm_restored.storage import (
    IntegrityError, canonical, lock, put, put_bytes, read, sha,
)
from . import amendment as a, design, prompts, funding


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

    @contextmanager
    def price_profile(self, metadata_repair):
        budget = deepcopy(design.CONFIG['budget'])
        sampling = deepcopy(design.CONFIG['sampling'])
        if metadata_repair:
            normal = '18.528'
            caps = {'prompt': '0.15', 'completion': '0.10'}
            reserves = {'publish': '0.0323104', 'vote': '0.0216608',
                        'log': '0.0208416', 'initial': '0.0110112'}
            sampling['maximum_actor_user_text_characters'] = 81920
            sampling['stage_text_and_schema_utf8_byte_limits'] = {
                'publish':81920,'vote':81920,'log':81920,'initial':49152}
        else:
            normal = '12.13824'
            caps = {'prompt': '0.10', 'completion': '0.05'}
            reserves = {'publish': '0.0167456', 'vote': '0.0114208',
                        'log': '0.0110112', 'initial': '0.006096'}
            sampling['maximum_actor_user_text_characters'] = 40000
            sampling['stage_text_and_schema_utf8_byte_limits'] = {
                'publish':49152,'vote':49152,'log':49152,'initial':32768}
        budget.update(normal_reservation_ceiling_usd=normal,
                      hard_unit_price_caps_usd_per_million=caps,
                      actor_stage_reserves_usd=reserves)
        profiles = deepcopy(funding.NATIVE_PROFILES)
        for role, function in [('publish','publish_document'), ('vote','record_vote'),
                               ('log','record_log'), ('initial','record_initial_judgment')]:
            profiles[function]['reserve_usd'] = reserves[role]
            profiles[function]['text_and_schema_utf8_bytes'] = sampling['stage_text_and_schema_utf8_byte_limits'][role]
        with patch.object(funding, 'NORMAL_RESERVATION_USD', Decimal(normal)), \
                patch.dict(funding.NATIVE_PROFILES, profiles, clear=True), \
                patch.dict(design.CONFIG['budget'], budget, clear=True), \
                patch.dict(design.CONFIG['sampling'], sampling, clear=True):
            yield

    def prepare_unpaid_metadata_repair(self):
        with self.price_profile(False):
            # Rebuild only this scripted old-format fixture before transaction1.
            # This never touches a live freeze or the production ledger.
            old = read(self.out/'CONFIG.json')
            old['sampling'] = deepcopy(design.CONFIG['sampling'])
            old['sampling']['max_output_tokens'] = 16384
            old['sampling']['stage_max_output_tokens']['publish'] = 16384
            (self.out/'CONFIG.json').unlink()
            put(self.out/'CONFIG.json', old)
            frozen = read(self.out/'freeze.json')
            frozen['config_sha256'] = sha((self.out/'CONFIG.json').read_bytes())
            (self.out/'freeze.json').unlink()
            put(self.out/'freeze.json', frozen)
            self.apply()
        a.release_stop(self.out, fixture=True)
        for arm in a.ARMS:
            put(self.out/'arms'/arm/'attempts/r01/a02/context.json', {
                'attempt_id': arm+'.r01.a02', 'previous_checkpoint_sha256': None,
                'case_sha256': sha(canonical(design.cases()[0]).encode()),
                'evidence_origin': 'OFFLINE_SCRIPTED_FIXTURE'})
        put(self.out/'driver/FREE_PRICE_DIAGNOSTIC.json', {
            'evidence_origin': 'OFFLINE_SCRIPTED_FIXTURE',
            'failure': 'Catalog versus pinned endpoint metadata price check.'})
        put(self.out/'STOP_REQUESTED.json', {'reason': 'OFFLINE scripted free-preflight hold'})
        self.source.write_text('# OFFLINE fixture route-price metadata repair\n')

    def test_unpaid_metadata_repair_appends_without_rewind_or_ledger_change(self):
        self.prepare_unpaid_metadata_repair()
        before = a._ledger_snapshot(self.ledger)
        original_first = (self.out/'AMENDMENTS/001/freeze.json').read_bytes()
        contexts = {arm: (self.out/'arms'/arm/'attempts/r01/a02/context.json').read_bytes()
                    for arm in a.ARMS}
        with self.price_profile(True):
            result = a.apply_preflight_metadata_recovery(self.runner, self.ledger)
        self.assertEqual(result['rewound_attempts'], 0)
        self.assertEqual(result['new_paid_calls'], 0)
        self.assertTrue((self.out/'STOP_REQUESTED.json').exists())
        self.assertEqual(a._ledger_snapshot(self.ledger), before)
        self.assertEqual((self.out/'AMENDMENTS/001/freeze.json').read_bytes(), original_first)
        for arm in a.ARMS:
            root = self.out/'arms'/arm/'attempts/r01/a02'
            self.assertEqual((root/'context.json').read_bytes(), contexts[arm])
            self.assertFalse((root/'abandoned.json').exists())
        new = self.out/'AMENDMENTS/002'
        old = read(self.out/'AMENDMENTS/001/CONFIG.json')
        config = read(new/'CONFIG.json')
        self.assertEqual({key:value for key,value in old.items() if key not in ('budget','normal_reservation_ceiling_usd','sampling')},
                         {key:value for key,value in config.items() if key not in ('budget','normal_reservation_ceiling_usd','sampling')})
        input_fields = ('maximum_actor_user_text_characters','stage_text_and_schema_utf8_byte_limits')
        self.assertEqual({key:value for key,value in old['sampling'].items()if key not in input_fields},
                         {key:value for key,value in config['sampling'].items()if key not in input_fields})
        self.assertEqual(config['sampling']['maximum_actor_user_text_characters'],81920)
        self.assertEqual(config['sampling']['stage_text_and_schema_utf8_byte_limits'],
                         {'publish':81920,'vote':81920,'log':81920,'initial':49152})
        self.assertEqual(config['normal_reservation_ceiling_usd'], 18.528)
        self.assertEqual(read(new/'RECOVERY_RECEIPT.json')['moved_artifacts'], [])
        self.assertEqual(a.verify_chain(self.out, fixture=True)['directory'], new)
        a.release_stop(self.out, fixture=True)
        self.assertFalse((self.out/'STOP_REQUESTED.json').exists())

    def test_unpaid_metadata_repair_is_idempotent(self):
        self.prepare_unpaid_metadata_repair()
        with self.price_profile(True):
            first = a.apply_preflight_metadata_recovery(self.runner, self.ledger)
            second = a.apply_preflight_metadata_recovery(self.runner, self.ledger)
        self.assertEqual(second['status'], 'already_applied')
        self.assertEqual(first['freeze_sha256'], second['freeze_sha256'])

    def test_new_paid_identity_after_first_amendment_blocks_metadata_repair(self):
        self.prepare_unpaid_metadata_repair()
        with self.ledger._connect() as db:
            db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?)',
                       (a.NAMESPACE+'b01.r01.a02.B_INITIAL.native','completed','chat',
                        'z-ai/glm-5.3-flash','c'*64,6096000,10000))
        with self.price_profile(True), self.assertRaises(IntegrityError):
            a.apply_preflight_metadata_recovery(self.runner, self.ledger)
        self.assertFalse((self.out/'AMENDMENTS/002/freeze.json').exists())

    def test_work_in_unfunded_context_blocks_no_rewind_metadata_repair(self):
        self.prepare_unpaid_metadata_repair()
        put(self.out/'arms/b03/attempts/r01/a02/stages/B_INITIAL.json',
            {'evidence_origin': 'OFFLINE_SCRIPTED_FIXTURE'})
        with self.price_profile(True), self.assertRaises(IntegrityError):
            a.apply_preflight_metadata_recovery(self.runner, self.ledger)

    def test_metadata_repair_retains_and_verifies_free_failure_diagnostic(self):
        self.prepare_unpaid_metadata_repair()
        with self.price_profile(True):
            a.apply_preflight_metadata_recovery(self.runner, self.ledger)
        archive = self.out/'AMENDMENTS/002/FREE_PRICE_DIAGNOSTIC.original.json'
        self.assertEqual(archive.read_bytes(), (self.out/'driver/FREE_PRICE_DIAGNOSTIC.json').read_bytes())
        archive.write_text('CORRUPTED_OFFLINE_DIAGNOSTIC_ARCHIVE')
        with self.assertRaises(IntegrityError):
            a.verify_chain(self.out, fixture=True)

    def test_metadata_repair_rejects_actual_sampling_changes(self):
        self.prepare_unpaid_metadata_repair()
        with self.price_profile(True), patch.dict(design.CONFIG['sampling'], {'temperature':1.2}):
            with self.assertRaises(IntegrityError):
                a.apply_preflight_metadata_recovery(self.runner, self.ledger)

    @contextmanager
    def relace_route(self):
        with patch.dict(design.CONFIG['routes'], {
                'actors':'relace','actor_precision':'unreported','image':'alibaba','fallbacks':False}, clear=True):
            yield

    def prepare_provider_route_repair(self):
        old_routes = {'actors':'open-inference/fp4','image':'alibaba','fallbacks':False}
        # Script the old provider condition before either fixture amendment.
        old = read(self.out/'CONFIG.json')
        old['routes'] = deepcopy(old_routes)
        (self.out/'CONFIG.json').unlink()
        put(self.out/'CONFIG.json', old)
        frozen = read(self.out/'freeze.json')
        frozen['config_sha256'] = sha((self.out/'CONFIG.json').read_bytes())
        (self.out/'freeze.json').unlink()
        put(self.out/'freeze.json', frozen)
        with patch.dict(design.CONFIG['routes'], old_routes, clear=True):
            self.prepare_unpaid_metadata_repair()
            with self.price_profile(True):
                a.apply_preflight_metadata_recovery(self.runner, self.ledger)
        a.release_stop(self.out, fixture=True)
        for arm in a.ARMS:
            identifier = a.NAMESPACE+arm+'.r01.a02.B_INITIAL.native'
            logical = identifier.removeprefix(a.NAMESPACE)
            payload = {'model':'z-ai/glm-5.3-flash','tools':[{'function':{'name':'record_initial_judgment'}}],
                       'evidence_origin':'OFFLINE_SCRIPTED_FIXTURE'}
            wire = sha(canonical(payload).encode())
            with self.ledger._connect() as db:
                db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?)', (
                    identifier,'uncertain','chat','z-ai/glm-5.3-flash',
                    a.request_digest({'endpoint':'/chat/completions','wire_body_sha256':wire}),
                    11011200,None))
            root = self.out/'arms'/arm/'attempts/r01/a02'
            put(root/'hold.json', {'code':'paid_outcome_unresolved','evidence_origin':'OFFLINE_SCRIPTED_FIXTURE'})
            put(root/'inflight/B_INITIAL.json', {'stage':'B_INITIAL','request_sha256':'a'*64,
                'evidence_origin':'OFFLINE_SCRIPTED_FIXTURE'})
            owner = self.out/'paid_operations'/(sha(identifier.encode())+'.json')
            put(owner, {'attempt_id':arm+'.r01.a02','request_id':identifier,
                        'pid':99999999,'start_ticks':'OFFLINE_NO_PROCESS','wire_sha256':wire,
                        'evidence_origin':'OFFLINE_SCRIPTED_FIXTURE'})
            put(owner.with_suffix('.finished.json'), {'owner_sha256':sha(owner.read_bytes())})
            put(self.out/'provider_evidence/transport_requests'/(sha(logical.encode())+'.json'), {
                'request_id':logical,'endpoint':'/chat/completions','logical_payload':payload,
                'mapping':{'wire_body_sha256':wire,'images':[]},'evidence_origin':'OFFLINE_SCRIPTED_FIXTURE'})
            put(self.out/'provider_evidence/transport_failures'/(sha(identifier.encode())+'.json'), {
                'request_id':identifier,'failure_class':'HTTPError','http_status':404,
                'request_not_reposted':True,'router_diagnostic':{'message':
                    'Only relace is available; provider.only permits open-inference/fp4.'},
                'evidence_origin':'OFFLINE_SCRIPTED_FIXTURE'})
        put(self.out/'driver/FREE_ROUTE_DIAGNOSTIC.json', {
            'evidence_origin':'OFFLINE_SCRIPTED_FIXTURE','reason':'Pinned provider unavailable; Relace advertised.'})
        put(self.out/'STOP_REQUESTED.json', {'reason':'OFFLINE scripted provider404 hold'})
        self.source.write_text('# OFFLINE fixture provider route repair\n')

    def test_provider_repair_keeps_unknown_invoices_and_seals_full_reserves(self):
        self.prepare_provider_route_repair()
        before = a._ledger_snapshot(self.ledger)
        holds = {arm:(self.out/'arms'/arm/'attempts/r01/a02/hold.json').read_bytes()for arm in a.ARMS}
        with self.price_profile(True), self.relace_route():
            result = a.apply_provider_route_recovery(self.runner,self.ledger)
        self.assertEqual(result['all_arms_next_attempt'],'r01.a03')
        self.assertEqual(result['unknown_reservations_forgiven'],0)
        self.assertEqual(a._ledger_snapshot(self.ledger),before)
        for arm in a.ARMS:
            root = self.out/'arms'/arm/'attempts/r01/a02'
            self.assertEqual((root/'hold.json').read_bytes(),holds[arm])
            self.assertEqual(read(root/'abandoned.json')['status'],'abandoned_technical')
            proof = self.out/'funding_seals'/(sha((a.NAMESPACE+arm+'.r01.a02.B_INITIAL.native').encode())+'.json')
            self.assertTrue(read(proof)['full_unknown_reservation_retained'])
            self.assertFalse((root.parent/'a03').exists())
        directory = self.out/'AMENDMENTS/003'
        old = read(self.out/'AMENDMENTS/002/CONFIG.json')
        new = read(directory/'CONFIG.json')
        self.assertEqual({k:v for k,v in old.items()if k not in('routes','technical_amendment')},
                         {k:v for k,v in new.items()if k not in('routes','technical_amendment')})
        self.assertEqual(new['routes']['actors'],'relace')
        self.assertEqual(new['routes']['actor_precision'],'unreported')
        self.assertEqual(a.verify_chain(self.out,fixture=True)['directory'],directory)
        a.release_stop(self.out,fixture=True)

    def test_provider_repair_is_idempotent_and_checks_funding_proof_integrity(self):
        self.prepare_provider_route_repair()
        with self.price_profile(True),self.relace_route():
            first=a.apply_provider_route_recovery(self.runner,self.ledger)
            second=a.apply_provider_route_recovery(self.runner,self.ledger)
        self.assertEqual(first['freeze_sha256'],second['freeze_sha256'])
        proof=next((self.out/'funding_seals').glob('*.json'))
        proof.write_text('CORRUPTED_OFFLINE_SEAL')
        with self.assertRaises(IntegrityError):a.verify_chain(self.out,fixture=True)

    def test_provider_repair_refuses_model_output_or_non404_scope(self):
        self.prepare_provider_route_repair()
        identifier=a.NAMESPACE+'b01.r01.a02.B_INITIAL.native'
        failure=self.out/'provider_evidence/transport_failures'/(sha(identifier.encode())+'.json')
        raw=failure.read_bytes();body=read(failure);body['http_status']=500;failure.unlink();put(failure,body)
        with self.price_profile(True),self.relace_route(),self.assertRaises(IntegrityError):
            a.apply_provider_route_recovery(self.runner,self.ledger)
        failure.write_bytes(raw)
        put(self.out/'provider_evidence/observed_responses/OFFLINE_FORBIDDEN_RESPONSE.json', {
            'binding':{'request_id':identifier},'response':{'content':'OFFLINE forbidden model response'}})
        with self.price_profile(True),self.relace_route(),self.assertRaises(IntegrityError):
            a.apply_provider_route_recovery(self.runner,self.ledger)

    def test_provider_repair_blocks_more_paid_work_or_any_stage(self):
        self.prepare_provider_route_repair()
        put(self.out/'arms/b02/attempts/r01/a02/stages/B_INITIAL.json',
            {'evidence_origin':'OFFLINE_FORBIDDEN_SCRIPTED_STAGE'})
        with self.price_profile(True),self.relace_route(),self.assertRaises(IntegrityError):
            a.apply_provider_route_recovery(self.runner,self.ledger)
        (self.out/'arms/b02/attempts/r01/a02/stages/B_INITIAL.json').unlink()
        with self.ledger._connect()as db:
            db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?)',(
                a.NAMESPACE+'b01.r01.a02.C_INITIAL.native','completed','chat',
                'z-ai/glm-5.3-flash','d'*64,11011200,1))
        with self.price_profile(True),self.relace_route(),self.assertRaises(IntegrityError):
            a.apply_provider_route_recovery(self.runner,self.ledger)

    def test_provider_repair_resumes_after_interrupted_seal(self):
        self.prepare_provider_route_repair()
        original=funding.seal;calls=[]
        def interrupted(*args):
            result=original(*args);calls.append(result)
            if len(calls)==1:raise RuntimeError('OFFLINE scripted interruption after proof creation')
            return result
        with self.price_profile(True),self.relace_route(),patch.object(funding,'seal',side_effect=interrupted):
            with self.assertRaises(RuntimeError):a.apply_provider_route_recovery(self.runner,self.ledger)
        self.assertEqual(a.active_freeze(self.out)['directory'],self.out/'AMENDMENTS/002')
        with self.price_profile(True),self.relace_route():
            a.apply_provider_route_recovery(self.runner,self.ledger)
        a.verify_chain(self.out,fixture=True)

    def test_provider_repair_requires_producer_locks_and_unchanged_resources(self):
        self.prepare_provider_route_repair()
        with self.price_profile(True),self.relace_route(),lock(self.out/'arms/b04'):
            with self.assertRaises(IntegrityError):a.apply_provider_route_recovery(self.runner,self.ledger)
        with self.price_profile(True),self.relace_route(),patch.dict(design.CONFIG['sampling'],{'temperature':1.2}):
            with self.assertRaises(IntegrityError):a.apply_provider_route_recovery(self.runner,self.ledger)


if __name__ == '__main__':
    unittest.main()
