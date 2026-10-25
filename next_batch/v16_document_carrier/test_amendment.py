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
from . import amendment as a, design, prompts, funding, provider, review, runner as rt


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
        # Mirror the init-time recovery metadata recorded in the live original freeze.
        old['technical_recovery'] = a.MAX3_TECHNICAL_RECOVERY
        old['recovery'].pop('maximum_technical_attempts_per_round')
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


    TOOL_CHOICE_MESSAGE = ("No endpoints found that support the provided 'tool_choice' value. "
                           'OFFLINE scripted router diagnostic.')

    def prepare_tool_choice_repair(self):
        self.prepare_provider_route_repair()
        with self.price_profile(True), self.relace_route():
            a.apply_provider_route_recovery(self.runner, self.ledger)
        a.release_stop(self.out, fixture=True)
        for arm in a.ARMS:
            identifier = a.NAMESPACE+arm+'.r01.a03.B_INITIAL.native'
            logical = identifier.removeprefix(a.NAMESPACE)
            payload = {'model':'z-ai/glm-5.3-flash','tools':[{'function':{'name':'record_initial_judgment'}}],
                       'tool_choice':deepcopy(a.FORCED_INITIAL_TOOL_CHOICE),
                       'provider':{'only':['relace'],'allow_fallbacks':False,'require_parameters':True},
                       'evidence_origin':'OFFLINE_SCRIPTED_FIXTURE'}
            self.script_a03_failure(arm, identifier, logical, payload, self.TOOL_CHOICE_MESSAGE)
        put(self.out/'driver/FREE_TOOL_CHOICE_DIAGNOSTIC.json', {
            'evidence_origin':'OFFLINE_SCRIPTED_FIXTURE','endpoint':{'tag':'relace',
                'supports_tool_choice':{'auto':True,'function':False,'none':True,'required':False}}})
        put(self.out/'STOP_REQUESTED.json', {'reason':'OFFLINE scripted forced tool_choice hold'})
        self.source.write_text('# OFFLINE fixture tool-choice compatibility repair\n')

    def script_a03_failure(self, arm, identifier, logical, payload, message):
        wire = sha(canonical(payload).encode())
        with self.ledger._connect() as db:
            db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?)', (
                identifier,'uncertain','chat','z-ai/glm-5.3-flash',
                a.request_digest({'endpoint':'/chat/completions','wire_body_sha256':wire}),
                11011200,None))
        root = self.out/'arms'/arm/'attempts/r01/a03'
        put(root/'context.json', {'arm':arm,'round':1,'attempt':3,'attempt_id':arm+'.r01.a03',
            'previous_checkpoint_sha256':None,'case_sha256':sha(canonical(design.cases()[0]).encode())})
        put(root/'hold.json', {'code':'paid_outcome_unresolved','evidence_origin':'OFFLINE_SCRIPTED_FIXTURE'})
        put(root/'inflight/B_INITIAL.json', {'stage':'B_INITIAL','request_sha256':'c'*64,
            'evidence_origin':'OFFLINE_SCRIPTED_FIXTURE'})
        owner = self.out/'paid_operations'/(sha(identifier.encode())+'.json')
        put(owner, {'attempt_id':arm+'.r01.a03','request_id':identifier,
                    'pid':99999999,'start_ticks':'OFFLINE_NO_PROCESS','wire_sha256':wire,
                    'evidence_origin':'OFFLINE_SCRIPTED_FIXTURE'})
        put(owner.with_suffix('.finished.json'), {'owner_sha256':sha(owner.read_bytes())})
        put(self.out/'provider_evidence/transport_requests'/(sha(logical.encode())+'.json'), {
            'request_id':logical,'endpoint':'/chat/completions','logical_payload':payload,
            'mapping':{'wire_body_sha256':wire,'images':[]},'evidence_origin':'OFFLINE_SCRIPTED_FIXTURE'})
        put(self.out/'provider_evidence/transport_failures'/(sha(identifier.encode())+'.json'), {
            'request_id':identifier,'failure_class':'HTTPError','http_status':404,
            'request_not_reposted':True,'router_diagnostic':{'code':'404','message':message},
            'evidence_origin':'OFFLINE_SCRIPTED_FIXTURE'})

    def rewrite(self, path, change):
        body = read(path)
        change(body)
        path.unlink()
        put(path, body)

    def test_tool_choice_repair_seals_a03_reserves_and_prepares_fresh_a04(self):
        self.prepare_tool_choice_repair()
        before = a._ledger_snapshot(self.ledger)
        holds = {arm:(self.out/'arms'/arm/'attempts/r01/a03/hold.json').read_bytes() for arm in a.ARMS}
        stop = (self.out/'STOP_REQUESTED.json').read_bytes()
        older = {n:(self.out/'AMENDMENTS'/n/'freeze.json').read_bytes() for n in ('001','002','003')}
        roots = {name:(self.out/name).read_bytes() for name,key in a.ARTIFACTS}
        result = a.apply_tool_choice_recovery(self.runner, self.ledger)
        self.assertEqual(result['all_arms_next_attempt'], 'r01.a04')
        self.assertEqual(result['unknown_reservations_forgiven'], 0)
        self.assertEqual(result['new_paid_calls'], 0)
        self.assertEqual(a._ledger_snapshot(self.ledger), before)
        self.assertEqual((self.out/'STOP_REQUESTED.json').read_bytes(), stop)
        for name, raw in older.items():
            self.assertEqual((self.out/'AMENDMENTS'/name/'freeze.json').read_bytes(), raw)
        for name, raw in roots.items():
            self.assertEqual((self.out/name).read_bytes(), raw)
        for arm in a.ARMS:
            root = self.out/'arms'/arm/'attempts/r01/a03'
            self.assertEqual((root/'hold.json').read_bytes(), holds[arm])
            marker = read(root/'abandoned.json')
            self.assertEqual((marker['amendment_number'], marker['model_response_observed']), (4, False))
            proof = self.out/'funding_seals'/(sha((a.NAMESPACE+arm+'.r01.a03.B_INITIAL.native').encode())+'.json')
            self.assertTrue(read(proof)['full_unknown_reservation_retained'])
            self.assertFalse((root.parent/'a04').exists())
        directory = self.out/'AMENDMENTS/004'
        old = read(self.out/'AMENDMENTS/003/CONFIG.json')
        new = read(directory/'CONFIG.json')
        changed = ('recovery', 'technical_recovery', 'transport', 'technical_amendment')
        self.assertEqual({k:v for k,v in old.items() if k not in changed},
                         {k:v for k,v in new.items() if k not in changed})
        self.assertEqual(new['transport'], provider.TRANSPORT_POLICY)
        self.assertEqual(new['recovery']['maximum_technical_attempts_per_round'], 4)
        self.assertIn('max4attempts', new['technical_recovery'])
        self.assertEqual(new['technical_amendment']['number'], 4)
        receipt = read(directory/'RECOVERY_RECEIPT.json')
        self.assertEqual((receipt['uncertain_rows_retained'], receipt['unknown_reservation_units_retained']), (5, 55056000))
        self.assertEqual(a.verify_chain(self.out, fixture=True)['directory'], directory)
        with self.assertRaises(IntegrityError):
            a.verify_chain(self.out, fixture=False)
        a.release_stop(self.out, fixture=True)
        self.assertFalse((self.out/'STOP_REQUESTED.json').exists())

    def test_runner_current_resumes_at_a04_and_stops_after_fourth_attempt(self):
        self.prepare_tool_choice_repair()
        runner = rt.Runner(self.out, fixture=True)
        self.assertEqual(runner.current('b02')[0]['attempt_id'], 'b02.r01.a03')
        a.apply_tool_choice_recovery(self.runner, self.ledger)
        context, root = runner.current('b02')
        self.assertEqual((context['attempt_id'], context['attempt'], context['previous_checkpoint_sha256']),
                         ('b02.r01.a04', 4, None))
        self.assertEqual(root, self.out/'arms/b02/attempts/r01/a04')
        put(root/'abandoned.json', {'status':'abandoned_technical','evidence_origin':'OFFLINE_SCRIPTED_FIXTURE'})
        with self.assertRaises(IntegrityError):
            runner.current('b02')

    def test_tool_choice_repair_is_idempotent_and_detects_seal_tampering(self):
        self.prepare_tool_choice_repair()
        first = a.apply_tool_choice_recovery(self.runner, self.ledger)
        second = a.apply_tool_choice_recovery(self.runner, self.ledger)
        self.assertEqual(second['status'], 'already_applied')
        self.assertEqual(first['freeze_sha256'], second['freeze_sha256'])
        proof = self.out/'funding_seals'/(sha((a.NAMESPACE+'b03.r01.a03.B_INITIAL.native').encode())+'.json')
        proof.write_text('CORRUPTED_OFFLINE_SEAL')
        with self.assertRaises(IntegrityError):
            a.verify_chain(self.out, fixture=True)

    def test_tool_choice_repair_requires_the_specific_forced_choice_rejection(self):
        self.prepare_tool_choice_repair()
        identifier = a.NAMESPACE+'b04.r01.a03.B_INITIAL.native'
        failure = self.out/'provider_evidence/transport_failures'/(sha(identifier.encode())+'.json')
        raw = failure.read_bytes()
        self.rewrite(failure, lambda body: body['router_diagnostic'].update(message='Only relace is available.'))
        with self.assertRaises(IntegrityError):
            a.apply_tool_choice_recovery(self.runner, self.ledger)
        failure.unlink()
        failure.write_bytes(raw)
        self.rewrite(failure, lambda body: body.update(http_status=500))
        with self.assertRaises(IntegrityError):
            a.apply_tool_choice_recovery(self.runner, self.ledger)
        failure.unlink()
        failure.write_bytes(raw)
        put(self.out/'provider_evidence/observed_responses/OFFLINE_FORBIDDEN_RESPONSE.json', {
            'binding':{'request_id':identifier},'response':{'content':'OFFLINE forbidden model response'}})
        with self.assertRaises(IntegrityError):
            a.apply_tool_choice_recovery(self.runner, self.ledger)
        self.assertFalse((self.out/'AMENDMENTS/004').exists())

    def test_tool_choice_repair_rejects_an_auto_request_that_was_not_forced(self):
        self.prepare_tool_choice_repair()
        arm = 'b05'
        identifier = a.NAMESPACE+arm+'.r01.a03.B_INITIAL.native'
        logical = identifier.removeprefix(a.NAMESPACE)
        with self.ledger._connect() as db:
            db.execute('DELETE FROM requests WHERE request_id=?', (identifier,))
        for path in [self.out/'provider_evidence/transport_requests'/(sha(logical.encode())+'.json'),
                     self.out/'provider_evidence/transport_failures'/(sha(identifier.encode())+'.json'),
                     self.out/'paid_operations'/(sha(identifier.encode())+'.json'),
                     self.out/'paid_operations'/(sha(identifier.encode())+'.finished.json')]:
            path.unlink()
        for path in (self.out/'arms'/arm/'attempts/r01/a03').rglob('*.json'):
            path.unlink()
        payload = {'model':'z-ai/glm-5.3-flash','tools':[{'function':{'name':'record_initial_judgment'}}],
                   'tool_choice':'auto','provider':{'only':['relace'],'allow_fallbacks':False},
                   'evidence_origin':'OFFLINE_SCRIPTED_FIXTURE'}
        self.script_a03_failure(arm, identifier, logical, payload, self.TOOL_CHOICE_MESSAGE)
        with self.assertRaises(IntegrityError):
            a.apply_tool_choice_recovery(self.runner, self.ledger)

    def test_tool_choice_repair_blocks_output_extra_paid_work_a04_or_reviews(self):
        self.prepare_tool_choice_repair()
        stage = self.out/'arms/b02/attempts/r01/a03/stages/B_INITIAL.json'
        put(stage, {'evidence_origin':'OFFLINE_FORBIDDEN_SCRIPTED_STAGE'})
        with self.assertRaises(IntegrityError):
            a.apply_tool_choice_recovery(self.runner, self.ledger)
        stage.unlink()
        early = self.out/'arms/b01/attempts/r01/a04/context.json'
        put(early, {'attempt_id':'b01.r01.a04','evidence_origin':'OFFLINE_FORBIDDEN_SCRIPTED_CONTEXT'})
        with self.assertRaises(IntegrityError):
            a.apply_tool_choice_recovery(self.runner, self.ledger)
        early.unlink()
        launch = self.out/'reviews/OFFLINE_b04_MAIN/launch.json'
        put(launch, {'agent_id':'OFFLINE_ACTUAL_CONTEXT_PLACEHOLDER'})
        with self.assertRaises(IntegrityError):
            a.apply_tool_choice_recovery(self.runner, self.ledger)
        launch.unlink()
        with self.ledger._connect() as db:
            db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?)', (
                a.NAMESPACE+'b01.r01.a03.C_INITIAL.native','completed','chat',
                'z-ai/glm-5.3-flash','d'*64,11011200,1))
        with self.assertRaises(IntegrityError):
            a.apply_tool_choice_recovery(self.runner, self.ledger)
        self.assertFalse((self.out/'AMENDMENTS/004').exists())

    def test_tool_choice_repair_requires_newer_stop_and_idle_locks(self):
        self.prepare_tool_choice_repair()
        current = (self.out/'STOP_REQUESTED.json').read_bytes()
        (self.out/'STOP_REQUESTED.json').unlink()
        with self.assertRaises(IntegrityError):
            a.apply_tool_choice_recovery(self.runner, self.ledger)
        (self.out/'STOP_REQUESTED.json').write_bytes(
            (self.out/'AMENDMENTS/003/STOP_REQUESTED.original.json').read_bytes())
        with self.assertRaises(IntegrityError):
            a.apply_tool_choice_recovery(self.runner, self.ledger)
        (self.out/'STOP_REQUESTED.json').unlink()
        (self.out/'STOP_REQUESTED.json').write_bytes(current)
        with lock(self.out/'arms/b05'):
            with self.assertRaises(IntegrityError):
                a.apply_tool_choice_recovery(self.runner, self.ledger)
        (self.out/'driver/driver.lock').touch()
        with (self.out/'driver/driver.lock').open('a+b') as guard:
            import fcntl
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(IntegrityError):
                a.apply_tool_choice_recovery(self.runner, self.ledger)
            fcntl.flock(guard, fcntl.LOCK_UN)
        self.assertFalse((self.out/'AMENDMENTS/004').exists())
        a.apply_tool_choice_recovery(self.runner, self.ledger)

    def test_tool_choice_repair_requires_auto_only_diagnostic_and_unchanged_condition(self):
        self.prepare_tool_choice_repair()
        diagnostic = self.out/'driver/FREE_TOOL_CHOICE_DIAGNOSTIC.json'
        raw = diagnostic.read_bytes()
        self.rewrite(diagnostic, lambda body: body['endpoint']['supports_tool_choice'].update(function=True))
        with self.assertRaises(IntegrityError):
            a.apply_tool_choice_recovery(self.runner, self.ledger)
        diagnostic.unlink()
        diagnostic.write_bytes(raw)
        with patch.dict(design.CONFIG['sampling'], {'temperature':1.2}), self.assertRaises(IntegrityError):
            a.apply_tool_choice_recovery(self.runner, self.ledger)
        with patch.dict(provider.TRANSPORT_POLICY, {'tool_choice':'required'}), self.assertRaises(IntegrityError):
            a.apply_tool_choice_recovery(self.runner, self.ledger)
        with patch.dict(design.CONFIG['recovery'], {'maximum_technical_attempts_per_round':5}), \
                self.assertRaises(IntegrityError):
            a.apply_tool_choice_recovery(self.runner, self.ledger)
        with patch.dict(design.CONFIG['routes'], {'actors':'open-inference/fp4'}), self.assertRaises(IntegrityError):
            a.apply_tool_choice_recovery(self.runner, self.ledger)
        self.assertFalse((self.out/'AMENDMENTS/004').exists())
        a.apply_tool_choice_recovery(self.runner, self.ledger)

    def test_tool_choice_repair_resumes_after_interrupted_seal(self):
        self.prepare_tool_choice_repair()
        original = funding.seal
        calls = []
        def interrupted(*args):
            result = original(*args)
            calls.append(result)
            if len(calls) == 2:
                raise RuntimeError('OFFLINE scripted interruption after proof creation')
            return result
        with patch.object(funding, 'seal', side_effect=interrupted):
            with self.assertRaises(RuntimeError):
                a.apply_tool_choice_recovery(self.runner, self.ledger)
        self.assertEqual(a.active_freeze(self.out)['directory'], self.out/'AMENDMENTS/003')
        a.apply_tool_choice_recovery(self.runner, self.ledger)
        self.assertEqual(a.verify_chain(self.out, fixture=True)['directory'], self.out/'AMENDMENTS/004')

    @contextmanager
    def codex_reviewers(self):
        assays = deepcopy(design.CONFIG['post_main_decode_assays'])
        assays['fresh_Codex_context_each'] = assays.pop('fresh_Claude_subagent_context_each')
        with patch.dict(design.CONFIG['models'], {'reviewers':a.CODEX_REVIEWERS}), \
                patch.dict(design.CONFIG['post_main_decode_assays'], assays, clear=True):
            yield

    def prepare_reviewer_amendment(self):
        # Script the original Codex reviewer condition before transaction1.
        old = read(self.out/'CONFIG.json')
        with self.codex_reviewers():
            old['models'] = deepcopy(design.CONFIG['models'])
            old['post_main_decode_assays'] = deepcopy(design.CONFIG['post_main_decode_assays'])
            (self.out/'CONFIG.json').unlink()
            put(self.out/'CONFIG.json', old)
            frozen = read(self.out/'freeze.json')
            frozen['config_sha256'] = sha((self.out/'CONFIG.json').read_bytes())
            (self.out/'freeze.json').unlink()
            put(self.out/'freeze.json', frozen)
            self.prepare_tool_choice_repair()
            a.apply_tool_choice_recovery(self.runner, self.ledger)
        self.source.write_text('# OFFLINE fixture reviewer amendment\n')

    def test_reviewer_amendment_discloses_claude_subagents_without_other_change(self):
        self.prepare_reviewer_amendment()
        before = a._ledger_snapshot(self.ledger)
        stop = (self.out/'STOP_REQUESTED.json').read_bytes()
        result = a.apply_reviewer_amendment(self.runner, self.ledger)
        self.assertEqual((result['new_paid_calls'], result['all_arms_next_attempt']), (0, 'r01.a04'))
        self.assertEqual(a._ledger_snapshot(self.ledger), before)
        self.assertEqual((self.out/'STOP_REQUESTED.json').read_bytes(), stop)
        old = read(self.out/'AMENDMENTS/004/CONFIG.json')
        new = read(self.out/'AMENDMENTS/005/CONFIG.json')
        changed = ('models', 'post_main_decode_assays', 'reviewer_amendment', 'technical_amendment')
        self.assertEqual({k:v for k,v in old.items() if k not in changed},
                         {k:v for k,v in new.items() if k not in changed})
        self.assertEqual(new['models']['reviewers'], a.CLAUDE_SUBAGENT_REVIEWERS)
        self.assertEqual({k:v for k,v in old['models'].items() if k != 'reviewers'},
                         {k:v for k,v in new['models'].items() if k != 'reviewers'})
        self.assertEqual(new['technical_amendment']['live_review_evidence_origin'], review.LIVE_REVIEW_ORIGIN)
        self.assertEqual((self.out/'AMENDMENTS/005/SYSTEM_PROMPTS.json').read_bytes(),
                         (self.out/'AMENDMENTS/004/SYSTEM_PROMPTS.json').read_bytes())
        self.assertEqual(a.verify_chain(self.out, fixture=True)['directory'], self.out/'AMENDMENTS/005')
        self.assertEqual(a.apply_reviewer_amendment(self.runner, self.ledger)['status'], 'already_applied')
        a.release_stop(self.out, fixture=True)
        self.assertEqual(rt.Runner(self.out, fixture=True).current('b01')[0]['attempt_id'], 'b01.r01.a04')

    def test_reviewer_amendment_allows_only_an_unfunded_a04_context(self):
        self.prepare_reviewer_amendment()
        rt.Runner(self.out, fixture=True).current('b03')
        a.apply_reviewer_amendment(self.runner, self.ledger)
        self.assertTrue((self.out/'arms/b03/attempts/r01/a04/context.json').exists())

    def test_reviewer_amendment_refuses_any_a04_work_review_or_paid_row(self):
        self.prepare_reviewer_amendment()
        stage = self.out/'arms/b02/attempts/r01/a04/inflight/B_INITIAL.json'
        put(stage, {'stage':'B_INITIAL','evidence_origin':'OFFLINE_FORBIDDEN_SCRIPTED_DISPATCH'})
        with self.assertRaises(IntegrityError):
            a.apply_reviewer_amendment(self.runner, self.ledger)
        stage.unlink()
        launch = self.out/'reviews/OFFLINE_b04_MAIN/launch.json'
        put(launch, {'agent_id':'OFFLINE_ACTUAL_CONTEXT_PLACEHOLDER'})
        with self.assertRaises(IntegrityError):
            a.apply_reviewer_amendment(self.runner, self.ledger)
        launch.unlink()
        with self.ledger._connect() as db:
            db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?)', (
                a.NAMESPACE+'b01.r01.a04.B_INITIAL.native','uncertain','chat',
                'z-ai/glm-5.3-flash','e'*64,11011200,None))
        with self.assertRaises(IntegrityError):
            a.apply_reviewer_amendment(self.runner, self.ledger)
        self.assertFalse((self.out/'AMENDMENTS/005').exists())

    def test_reviewer_amendment_refuses_after_release_or_with_other_changes(self):
        self.prepare_reviewer_amendment()
        with patch.dict(design.CONFIG['sampling'], {'temperature':1.2}), self.assertRaises(IntegrityError):
            a.apply_reviewer_amendment(self.runner, self.ledger)
        with patch.dict(design.CONFIG['models'], {'actors':'OFFLINE_OTHER_MODEL'}), self.assertRaises(IntegrityError):
            a.apply_reviewer_amendment(self.runner, self.ledger)
        with patch.dict(design.CONFIG['models'], {'reviewers':'OFFLINE_UNDISCLOSED'}), self.assertRaises(IntegrityError):
            a.apply_reviewer_amendment(self.runner, self.ledger)
        with self.codex_reviewers(), self.assertRaises(IntegrityError):
            a.apply_reviewer_amendment(self.runner, self.ledger)
        amended = self.source.read_bytes()
        self.source.write_text('# OFFLINE fixture tool-choice compatibility repair\n')
        a.release_stop(self.out, fixture=True)
        self.source.write_bytes(amended)
        put(self.out/'STOP_REQUESTED.json', {'reason':'OFFLINE scripted later hold'})
        with self.assertRaises(IntegrityError):
            a.apply_reviewer_amendment(self.runner, self.ledger)
        self.assertFalse((self.out/'AMENDMENTS/005').exists())

    @contextmanager
    def pre_canonical(self):
        old = deepcopy(design.CONFIG)
        old.update(rounds_per_arm=12, total_rounds=60)
        old.pop('active_arms')
        old.pop('canonical_design_006')
        old['post_main_decode_assays'].update(arms=5, checkpoints=[4, 8, 12])
        with patch.dict(design.CONFIG, old, clear=True):
            yield

    def prepare_canonical_amendment(self):
        with self.pre_canonical():
            old = read(self.out/'CONFIG.json')
            old.update(rounds_per_arm=12, total_rounds=60,
                       post_main_decode_assays=deepcopy(design.CONFIG['post_main_decode_assays']))
            old.pop('active_arms')
            old.pop('canonical_design_006')
            (self.out/'CONFIG.json').unlink()
            put(self.out/'CONFIG.json', old)
            frozen = read(self.out/'freeze.json')
            frozen['config_sha256'] = sha((self.out/'CONFIG.json').read_bytes())
            (self.out/'freeze.json').unlink()
            put(self.out/'freeze.json', frozen)
            self.prepare_reviewer_amendment()
            a.apply_reviewer_amendment(self.runner, self.ledger)
        self.source.write_text('# OFFLINE fixture canonical design amendment\n')

    def test_canonical_design_freezes_five_rounds_three_arms_without_other_change(self):
        self.prepare_canonical_amendment()
        before = a._ledger_snapshot(self.ledger)
        stop = (self.out/'STOP_REQUESTED.json').read_bytes()
        result = a.apply_canonical_design_amendment(self.runner, self.ledger)
        self.assertEqual((result['rounds_per_arm'], result['active_arms'], result['new_paid_calls']),
                         (5, ['b01', 'b03', 'b05'], 0))
        self.assertEqual(a._ledger_snapshot(self.ledger), before)
        self.assertEqual((self.out/'STOP_REQUESTED.json').read_bytes(), stop)
        old = read(self.out/'AMENDMENTS/005/CONFIG.json')
        new = read(self.out/'AMENDMENTS/006/CONFIG.json')
        changed = ('rounds_per_arm', 'total_rounds', 'rounds_per_cohort', 'cohorts', 'active_arms',
                   'post_main_decode_assays', 'canonical_design', 'technical_amendment')
        self.assertEqual({k: v for k, v in old.items() if k not in changed},
                         {k: v for k, v in new.items() if k not in changed})
        self.assertEqual((new['rounds_per_arm'], new['total_rounds']), (5, 15))
        self.assertEqual(new['post_main_decode_assays']['checkpoints'], [1, 2, 3, 4, 5])
        self.assertEqual((self.out/'AMENDMENTS/006/SYSTEM_PROMPTS.json').read_bytes(),
                         (self.out/'AMENDMENTS/005/SYSTEM_PROMPTS.json').read_bytes())
        self.assertEqual(a.verify_chain(self.out, fixture=True)['directory'], self.out/'AMENDMENTS/006')
        self.assertEqual(a.apply_canonical_design_amendment(self.runner, self.ledger)['status'], 'already_applied')
        a.release_stop(self.out, fixture=True)
        runner = rt.Runner(self.out, fixture=True)
        self.assertEqual([x['id'] for x in rt.arm_list()], ['b01', 'b03', 'b05'])
        self.assertEqual(runner.current('b03')[0]['attempt_id'], 'b03.r01.a04')

    def test_canonical_design_refuses_any_job_probe_or_paid_row(self):
        self.prepare_canonical_amendment()
        for marker in (self.out/'reviews/OFFLINE_b04_MAIN/launch.json',
                       self.out/'decoding/decode.b01.r01.original/launch.json',
                       self.out/'attribution_audits/ladder.b01.r01.L1_artifacts/assessment.json',
                       self.out/'probes/sentinel/b01.r01.delivered.json'):
            put(marker, {'evidence_origin': 'OFFLINE_FORBIDDEN_SCRIPTED_JOB'})
            with self.assertRaises(IntegrityError):
                a.apply_canonical_design_amendment(self.runner, self.ledger)
            marker.unlink()
        with self.ledger._connect() as db:
            db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?)', (
                a.NAMESPACE+'b01.r01.a04.B_INITIAL.native', 'uncertain', 'chat',
                'z-ai/glm-5.3-flash', 'f'*64, 11011200, None))
        with self.assertRaises(IntegrityError):
            a.apply_canonical_design_amendment(self.runner, self.ledger)
        self.assertFalse((self.out/'AMENDMENTS/006').exists())

    def test_canonical_design_refuses_other_changes_or_release(self):
        self.prepare_canonical_amendment()
        with patch.dict(design.CONFIG['sampling'], {'temperature': 1.2}), self.assertRaises(IntegrityError):
            a.apply_canonical_design_amendment(self.runner, self.ledger)
        with patch.dict(design.CONFIG, {'active_arms': ['b01', 'b02', 'b05']}), self.assertRaises(IntegrityError):
            a.apply_canonical_design_amendment(self.runner, self.ledger)
        with patch.dict(design.CONFIG, {'rounds_per_arm': 6}), self.assertRaises(IntegrityError):
            a.apply_canonical_design_amendment(self.runner, self.ledger)
        with patch.dict(design.CONFIG['scoring'], {'team_grant_each': 20000}), self.assertRaises(IntegrityError):
            a.apply_canonical_design_amendment(self.runner, self.ledger)
        amended = self.source.read_bytes()
        self.source.write_text('# OFFLINE fixture reviewer amendment\n')
        a.release_stop(self.out, fixture=True)
        self.source.write_bytes(amended)
        put(self.out/'STOP_REQUESTED.json', {'reason': 'OFFLINE scripted later hold'})
        with self.assertRaises(IntegrityError):
            a.apply_canonical_design_amendment(self.runner, self.ledger)
        self.assertFalse((self.out/'AMENDMENTS/006').exists())

if __name__ == '__main__':
    unittest.main()
