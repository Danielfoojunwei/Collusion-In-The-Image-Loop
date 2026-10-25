"""Offline67-record migration and two-hop image/role-memory continuity fixtures."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import unittest
from PIL import Image
from experiments.triad_repaired_v7.tests import test_continuation_migration as legacy_fixture
from experiments.triad_repaired_v7.engine import TriadEngine as SourceEngine
from experiments.triad_repaired_v8.engine import TriadEngine as TargetEngine
from experiments.triad_repaired_v8.backend import TriadBackend
from scripts.migrate_repaired_v8_prefix import migrate_prefix, MigrationError, file_digest, digest, canonical


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(canonical(value)+"\n")


class V8MigrationTests(unittest.TestCase):
    def setUp(self):
        self.legacy=legacy_fixture.ContinuationMigrationTests();self.legacy.setUp()
        f=self.legacy
        f.tc['strict_review_penalties']=deepcopy(f.sc['strict_review_penalties'])
        f.freeze(f.target,f.tc,f.tp);f.migrate()
        self.root=f.root;self.source=f.target;self.target=self.root/'v8';self.target.mkdir()
        self.case=f.case;self.cid=f.case['case_id'];self.sc=f.tc
        self.engine=SourceEngine(self.source/'cases'/self.cid,self.sc,self.case)
        self.drafts={}
        for role,color in [('A',(190,1,1)),('B',(1,190,1))]:
            path=self.source/'provider_evidence/blobs'/(role+'9.png');path.parent.mkdir(parents=True,exist_ok=True)
            Image.new('RGB',(1024,1024),color).save(path)
            self.drafts[role]={'path':str(path),'sha256':file_digest(path)}
        for role in 'ABC':
            req=self.engine.actor_request();self.engine.claim_actor_dispatch(req)
            output={'decision':'P2','decoded_target':'P2','rationale':None,'message':None,'image':None,'private_note':'','reflection':None,'report':None}
            actions=[]
            if role in 'AB':
                actions=[{'step':1,'output':{'action':'generate_image','arguments':{'prompt':'Fixture','reference':'none'}},'generated_image':self.drafts[role]}]
                output['image']=self.drafts[role]
            if role=='A':self.engine.accept_actor({'status':'invalid_model_output','reason':'Fixture exhausted action slots','action_log':actions})
            else:self.engine.accept_actor({'status':'valid','output':output,'action_log':actions,'provenance':{'evidence_origin':'fixture'}})
        job=self.engine.export_reviewer_packet();write(self.source/'reviewer_jobs/r09.json',job)
        self.tc=deepcopy(self.sc);self.tc['revision']='triad_repaired_v8';self.tc['budget']['namespace']='triad_repaired_v8'
        self.tc['continuation']={'enabled':True,'source_out':str(self.source),'source_config_sha256':file_digest(self.source/'config.json'),
            'source_plan_sha256':file_digest(self.source/'plan.json'),'source_freeze_sha256':file_digest(self.source/'freeze.json'),
            'imported_rounds':8,'imported_stages':67,'first_new_round':9,'first_new_stage':'REVIEWER'}
        self.tp={'cases':[deepcopy(self.case)],'selected_case_ids':[self.cid],
            'continuation':{**self.tc['continuation'],'original_prefix_chain_head':self.engine.outcome(9,'C_VOTE')['record_hash']}}
        f.freeze(self.target,self.tc,self.tp)

    def tearDown(self):self.legacy.tearDown()
    def migrate(self,apply=True):return migrate_prefix(self.source,self.target,repository_root=self.root,apply=apply,allow_fixture=True)
    def target_engine(self):return TargetEngine(self.target/'cases'/self.cid,self.tc,self.case)
    def validator(self):
        b=object.__new__(TriadBackend);b.out=self.target;b.config=self.tc;return b

    def test67_exact_records_keep_invalid_A9_and_next_actual_reviewer09(self):
        m=self.migrate();e=self.target_engine()
        self.assertEqual(len(e._previous_outcomes()),67);self.assertEqual(e.next_stage()['stage'],'REVIEWER')
        self.assertEqual(e.next_stage()['round'],9);self.assertEqual(e.outcome(9,'A_IMAGE_AND_VOTE')['status'],'invalid_model_output')
        self.assertEqual(m['prefix_chain_head'],self.engine.outcome(9,'C_VOTE')['record_hash'])
        for n in range(1,10):
            for stage in ('A_IMAGE_AND_VOTE','B_IMAGE_AND_VOTE','C_VOTE'):
                old=self.source/'cases'/self.cid/'rounds'/f'r{n:02d}'/(stage+'.outcome.json')
                self.assertEqual(old.read_bytes(),(self.target/old.relative_to(self.source)).read_bytes())

    def test_unused_review09_is_archived_and_new_packet_regenerated(self):
        m=self.migrate();self.assertTrue(m['unused_predecessor_review09'])
        self.assertFalse((self.target/'cases'/self.cid/'reviewer_packets/r09').exists())
        self.assertFalse((self.target/'reviewer_jobs/r09.json').exists())
        e=self.target_engine();job=e.export_reviewer_packet();packet=json.loads(Path(job['packet_path']).read_text())
        self.assertFalse(packet['ballots']['A']['available']);self.assertTrue(packet['ballots']['B']['available'])
        self.assertEqual([p['speaker']for p in packet['images']],['B','C'])

    def test_all_roles_v6_refs_plus_new_v7_private_draft_resolve_only_copied(self):
        self.migrate();b=self.validator();e=self.target_engine()
        for role,stage in zip('ABC',('A_IMAGE_AND_VOTE','B_IMAGE_AND_VOTE','C_VOTE')):
            q=json.loads((self.source/'cases'/self.cid/'rounds/r09'/(stage+'.request.json')).read_text())
            q['memory_directory']=str(e.memory_directory)
            if role=='A':q['image_refs']['memory:image:9999']=self.drafts['A']
            resolved=b._references(q)
            for handle,d in q['image_refs'].items():self.assertEqual(__import__('hashlib').sha256(resolved[handle]).hexdigest(),d['sha256'])
        for role in 'ABC':self.assertEqual(len(e._own_logs(9,role)),8)

    def test_full_unpaid_coordinator_gate_exercises_actual_all_role_adapter(self):
        self.migrate()
        from experiments.triad_repaired_v8.coordinator import TriadCoordinator
        c=object.__new__(TriadCoordinator);c.out=self.target;c.config=self.tc;c.plan=self.tp;c.evidence_origin='fixture'
        c._engines={self.cid:self.target_engine()}
        gate=c._evaluate_continuation_gate()
        self.assertEqual(gate['status'],'passed');self.assertEqual(gate['stage'],'REVIEWER')
        self.assertTrue(all(gate['checks'].values()))
        self.assertEqual(gate['checks']['sixty_seven_terminal_observations_retained'],True)

    def test_stage_protocol_origins_preserve_mixed_round9(self):
        from experiments.triad_repaired_v8.coordinator import TriadCoordinator
        from experiments.triad_repaired_v8.engine import STAGES
        revisions=[TriadCoordinator.stage_protocol_revision(n,s) for n in range(1,13)for s in STAGES]
        self.assertEqual(revisions.count('triad_repaired_v6'),64)
        self.assertEqual(revisions.count('triad_repaired_v7'),3)
        self.assertEqual(revisions.count('triad_repaired_v8'),29)
        self.assertEqual(TriadCoordinator.stage_protocol_revision(9,'C_VOTE'),'triad_repaired_v7')
        self.assertEqual(TriadCoordinator.stage_protocol_revision(9,'REVIEWER'),'triad_repaired_v8')

    def test_identical_import_is_idempotent_and_never_mutates_source_or_ledger(self):
        before={str(p.relative_to(self.source)):file_digest(p)for p in self.source.rglob('*')if p.is_file()}
        ledger=file_digest(self.legacy.ledger);m=self.migrate();self.assertEqual(self.migrate(),m)
        self.assertEqual(before,{str(p.relative_to(self.source)):file_digest(p)for p in self.source.rglob('*')if p.is_file()})
        self.assertEqual(file_digest(self.legacy.ledger),ledger)

    def test_partial_prefix_or_dispatched_review_cannot_import(self):
        p=self.source/'cases'/self.cid/'rounds/r09/C_VOTE.outcome.json';p.unlink()
        with self.assertRaisesRegex(MigrationError,'67'):self.migrate()

    def test_source_actual_reviewer09_launch_is_a_hard_stop(self):
        write(self.source/'reviewer_host/r09/launch.json',{'fixture':'dispatched'})
        with self.assertRaisesRegex(MigrationError,'launch'):self.migrate()

    def test_pending_v7_or_v8_requests_block_import(self):
        for ns in ('triad_repaired_v7:','triad_repaired_v8:'):
            with sqlite3.connect(self.legacy.ledger)as db:
                db.execute('DELETE FROM requests');db.execute('INSERT INTO requests VALUES(?,?,?,?)',(ns+'pending','pending',10,None))
            with self.assertRaisesRegex(MigrationError,'pending'):self.migrate()

    def test_unbound_changed_ancestor_and_target_schedule_are_rejected(self):
        self.tp['cases'][0]['private_targets'][10]='P3';self.legacy.freeze(self.target,self.tc,self.tp)
        with self.assertRaisesRegex(MigrationError,'schedule'):self.migrate()

    def test_unclaimed_existing_target_data_are_rejected(self):
        p=self.target/'cases'/self.cid/'rounds/r09/C_VOTE.outcome.json'
        write(p,{'not':'original'})
        with self.assertRaisesRegex(MigrationError,'Unclaimed'):self.migrate()

    def test_dryrun_performs_no_import_and_no_fixture_live_relabelling(self):
        self.assertTrue(self.migrate(False)['preflight_only']);self.assertFalse((self.target/'continuation_import.json').exists())
        with self.assertRaisesRegex(MigrationError,'Fixture'):
            migrate_prefix(self.source,self.target,repository_root=self.root,apply=True)


if __name__=='__main__':unittest.main()
