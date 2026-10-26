"""No-provider checks for selector-aware isolated control workflows."""
from copy import deepcopy
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from experiments.triad_emergent_v9.tests.test_counterfactuals import FakeEngine, FakeBackend, raster
from experiments.triad_v9_checkpoint_restart04 import counterfactuals as subject


class ShadowBackend(FakeBackend):
    def __init__(self, images, working_root):
        super().__init__(images)
        self.working_root=Path(working_root)

    def run_actor(self, req):
        value=super().run_actor(req)
        image=value['output']['image']
        if image:
            target=self.working_root/'generated'/Path(image['path']).name
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(Path(image['path']).read_bytes())
            value['output']['image']={**image,'path':str(target)}
            value['actions'][-1]['generated_image']=deepcopy(value['output']['image'])
        return value


class SelectedEngine(FakeEngine):
    def __init__(self, out):
        out.mkdir()
        images = {role: raster(out / (role+'.png'), color) for role, color in
                  [('A','red'),('B','green'),('C','blue')]}
        self.selected = False
        super().__init__(out, images)
        for name in ('freeze.json','config.json','plan.json','BASELINE.json','episode/engine_manifest.json'):
            (out / name).write_text('{}\n')
        checkpoint = out / 'checkpoints_restart04'
        checkpoint.mkdir()
        (checkpoint / 'manifest.json').write_text('{}\n')
        (checkpoint / 'commits').mkdir()
        for n in range(7,13):
            target = out / 'attempts' / f'r{n:02d}' / 'a02' / 'episode/rounds'
            target.mkdir(parents=True)
            (out / 'episode/rounds' / f'r{n:02d}').rename(target / f'r{n:02d}')
            (checkpoint / 'commits' / f'r{n:02d}.json').write_text('{}\n')
        self.selected = True
        self.store = types.SimpleNamespace(root=checkpoint, manifest_path=checkpoint/'manifest.json',
                                           committed_rounds=lambda:12)

    def _path(self, number, stage, suffix='outcome'):
        if self.selected and number>=7:
            return self.out / 'attempts' / f'r{number:02d}' / 'a02/episode/rounds' / f'r{number:02d}' / f'{stage}.{suffix}.json'
        return super()._path(number,stage,suffix)


class ShadowCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.engine=SelectedEngine(Path(self.temp.name)/'run')
        self.host=types.SimpleNamespace(out=self.engine.out,config=self.engine.config,_engine=lambda:self.engine)

    def tearDown(self):
        self.temp.cleanup()

    def test_manifest_pins_selected_attempt_requests_and_ignores_abandoned_attempt(self):
        selected = subject.main_manifest(self.engine)
        self.assertIn('attempts/r08/a02/episode/rounds/r08/C_VOTE.request.json',selected)
        abandoned=self.engine.out/'attempts/r08/a01/episode/rounds/r08/C_VOTE.request.json'
        abandoned.parent.mkdir(parents=True)
        abandoned.write_text('FAILED_PRIVATE_CANARY')
        self.assertEqual(selected,subject.main_manifest(self.engine))
        request=self.engine._path(8,'C_VOTE','request')
        request.write_text(request.read_text()+' ')
        self.assertNotEqual(selected,subject.main_manifest(self.engine))

    def test_fixed24_use_selected_main_and_do_not_write_main_or_read_failed_attempt(self):
        before=subject.main_manifest(self.engine)
        root=self.engine.out/'shadow_attempts/a01'
        backend=ShadowBackend(self.engine.images,root)
        report=subject._run_fixed(subject._ControlView(self.engine,root),backend)
        self.assertEqual(report['observed_shadow_turns'],24)
        self.assertEqual(len(backend.calls),24)
        self.assertEqual(before,subject.main_manifest(self.engine))
        paths=[p for p in root.rglob('*.outcome.json')]
        self.assertEqual(len(paths),24)
        live_requests=list((self.engine.out/'attempts').glob('r*/a02/episode/rounds/r*/*.request.json'))
        self.assertTrue(live_requests)
        repeated=subject._run_fixed(subject._ControlView(self.engine,root),backend)
        self.assertEqual(repeated,report)
        self.assertEqual(len(backend.calls),24)

    def test_technical_fault_restarts_entire_shadow_workflow_and_preserves_failed_outputs(self):
        from experiments.triad_v9_checkpoint_restart04.backend import TechnicalRoundFault
        calls=[]
        class Backend:
            @staticmethod
            def preflight_before_attempt(*args,**kwargs):return {'status':'ready'}
            def __init__(inner,*args,attempt_context,**kwargs):
                inner.context=attempt_context
        def execute(view,backend):
            calls.append(backend.context['attempt_id'])
            if backend.context['attempt']==1:
                subject._save(view.out/'shadows/Aneutral/r02/B.outcome.json',{'failed_attempt_canary':'PRIVATE'})
                raise TechnicalRoundFault('transport_timeout',TimeoutError())
            report={'status':'complete','planned_shadow_turns':24,'observed_shadow_turns':24,
                    'unavailable_shadow_turns':0,'main_preservation_verified':True}
            subject._save(view.out/'shadows/report.json',report)
            return report
        with patch('experiments.triad_v9_checkpoint_restart04.backend.Backend',Backend), \
             patch('experiments.triad_v9_checkpoint_restart04.funding.seal_abandoned_attempt') as seal, \
             patch.object(subject,'_run_fixed',execute):
            report=subject.run_shadows(self.host)
            self.assertEqual(calls,['shadows.a01','shadows.a02'])
            self.assertEqual(report['selected_technical_attempt'],'shadows.a02')
            self.assertEqual(len(report['failed_technical_workflows']),1)
            self.assertTrue((self.engine.out/'shadow_attempts/a01/shadows/Aneutral/r02/B.outcome.json').exists())
            self.assertTrue((self.engine.out/'shadow_attempts/a01/abandonment.json').exists())
            self.assertEqual(seal.call_count,1)
            before=len(calls)
            self.assertEqual(subject.run_shadows(self.host),report)
            self.assertEqual(len(calls),before)

    def test_full_shadow_report_publication_recovery_constructs_no_provider(self):
        from experiments.triad_v9_checkpoint_restart04.backend import Backend
        protected=subject.main_manifest(self.engine)
        root=self.engine.out/'shadow_attempts/a01'
        context={'kind':'shadow','round':12,'attempt':1,'attempt_id':'shadows.a01',
                 'working_root':str(root),'prior_checkpoint_sha256':subject.digest(protected),'legacy':False}
        subject._seal_record(root/'attempt.json',context)
        subject._run_fixed(subject._ControlView(self.engine,root),ShadowBackend(self.engine.images,root))
        with patch('experiments.triad_v9_checkpoint_restart04.backend.Backend',side_effect=AssertionError('No provider during publication recovery')):
            report=subject.run_shadows(self.host)
        self.assertEqual(report['selected_technical_attempt'],'shadows.a01')
        self.assertTrue((self.engine.out/'shadow_checkpoints_restart04/COMMIT.json').exists())

    def test_genuine_semantic_failures_publish_once_without_technical_retry(self):
        class Backend:
            @staticmethod
            def preflight_before_attempt(*args,**kwargs):return {'status':'ready'}
            def __init__(inner,*args,**kwargs):pass
        def execute(view,backend):
            report={'status':'complete','planned_shadow_turns':24,'observed_shadow_turns':24,
                    'unavailable_shadow_turns':0,'main_preservation_verified':True,'valid_decision_pairs':0}
            subject._save(view.out/'shadows/report.json',report)
            return report
        with patch('experiments.triad_v9_checkpoint_restart04.backend.Backend',Backend), \
             patch.object(subject,'_run_fixed',side_effect=execute) as run:
            report=subject.run_shadows(self.host)
        self.assertEqual(run.call_count,1)
        self.assertEqual(report['failed_technical_workflows'],[])
        self.assertEqual(report['valid_decision_pairs'],0)

    def test_cached_report_with_missing_endpoint_holds_without_provider_or_false_commit(self):
        protected=subject.main_manifest(self.engine)
        root=self.engine.out/'shadow_attempts/a01'
        context={'kind':'shadow','round':12,'attempt':1,'attempt_id':'shadows.a01',
                 'working_root':str(root),'prior_checkpoint_sha256':subject.digest(protected),'legacy':False}
        subject._seal_record(root/'attempt.json',context)
        subject._run_fixed(subject._ControlView(self.engine,root),ShadowBackend(self.engine.images,root))
        missing=root/'shadows/Aneutral/r08/C.outcome.json'
        missing.unlink()
        with patch('experiments.triad_v9_checkpoint_restart04.backend.Backend',side_effect=AssertionError('No recovery provider')):
            with self.assertRaisesRegex(subject.ShadowError,'lost a durable endpoint'):
                subject.run_shadows(self.host)
        self.assertFalse((root/'abandonment.json').exists())
        # Remove its dispatch too: without complete endpoint verification, a
        # trusted summary could otherwise falsely authorize final publication.
        missing.with_name('C.dispatch.json').unlink()
        with patch('experiments.triad_v9_checkpoint_restart04.backend.Backend',side_effect=AssertionError('No recovery provider')):
            with self.assertRaisesRegex(subject.ShadowError,'lacks a planned durable endpoint'):
                subject.run_shadows(self.host)
        self.assertFalse((self.engine.out/'shadow_checkpoints_restart04/COMMIT.json').exists())

    def test_completed_workflow_publication_faults_recover_without_repeating_calls(self):
        from experiments.triad_v9_checkpoint_restart04.backend import PermanentTechnicalFault
        for fail_name in ('PUBLISHED_REPORT.json','COMMIT.json','global_report'):
            with self.subTest(boundary=fail_name):
                directory=Path(self.temp.name)/('case_'+fail_name.replace('.','_'))
                engine=SelectedEngine(directory)
                host=types.SimpleNamespace(out=engine.out,config=engine.config,_engine=lambda:engine)
                protected=subject.main_manifest(engine)
                root=engine.out/'shadow_attempts/a01'
                context={'kind':'shadow','round':12,'attempt':1,'attempt_id':'shadows.a01',
                         'working_root':str(root),'prior_checkpoint_sha256':subject.digest(protected),'legacy':False}
                subject._seal_record(root/'attempt.json',context)
                subject._run_fixed(subject._ControlView(engine,root),ShadowBackend(engine.images,root))
                original_save=subject._save
                fired=[]
                def fail_once(path,value):
                    path=Path(path)
                    matches=(path==engine.out/'shadows/report.json') if fail_name=='global_report' else path.name==fail_name
                    if matches and not fired:
                        fired.append(True)
                        raise OSError('fixture durable publication failure')
                    return original_save(path,value)
                with patch('experiments.triad_v9_checkpoint_restart04.backend.Backend',side_effect=AssertionError('No provider for publication')), \
                     patch.object(subject,'_save',side_effect=fail_once):
                    with self.assertRaises(PermanentTechnicalFault):
                        subject.run_shadows(host)
                self.assertTrue(fired)
                self.assertFalse((root/'abandonment.json').exists())
                self.assertFalse((engine.out/'shadow_attempts/a02').exists())
                with patch('experiments.triad_v9_checkpoint_restart04.backend.Backend',side_effect=AssertionError('No recovery provider')):
                    report=subject.run_shadows(host)
                self.assertEqual(report['selected_technical_attempt'],'shadows.a01')
                self.assertEqual(report['observed_shadow_turns'],24)

    def test_real_closed_twelve_round_engine_accepts_isolated_shadow_results_without_active_main(self):
        from experiments.triad_v9_checkpoint_restart04.tests.test_attempts import TransactionFixture,TaggedBackend
        fixture=TransactionFixture()
        fixture.setUp()
        try:
            fixture.setup_store(legacy_rounds=12)
            for name in ('freeze.json','config.json','plan.json','BASELINE.json'):
                (fixture.out/name).write_text('{}\n')
            self.assertIsNone(fixture.engine.store.current())
            self.assertIsNone(fixture.engine.next_stage())
            root=fixture.out/'shadow_attempts/a01'
            before=subject.main_manifest(fixture.engine)
            backend=TaggedBackend(root,fixture.plan,tag='ISOLATED_SHADOW_FIXTURE')
            report=subject._run_fixed(subject._ControlView(fixture.engine,root),backend)
            self.assertEqual(report['observed_shadow_turns'],24)
            self.assertEqual(len(backend.requests),24)
            self.assertIsNone(fixture.engine.store.current())
            self.assertEqual(before,subject.main_manifest(fixture.engine))
            source,_=subject._source_request(fixture.engine,12,'B')
            result=backend.run_actor(source)
            result['output']['image']=fixture.engine.image_for(12,'B')
            result['actions'][-1]['generated_image']=deepcopy(result['output']['image'])
            with self.assertRaisesRegex(Exception,'escaped|outside'):
                subject._ControlView(fixture.engine,root).validate_actor_result(source,result)
        finally:
            fixture.tearDown()

    def test_interrupted_partial_workflow_restarts_all_fixed_endpoints_from_closed_main(self):
        from experiments.triad_v9_checkpoint_restart04.funding import process_start_ticks
        root=self.engine.out/'shadow_attempts/a01'
        protected=subject.main_manifest(self.engine)
        context={'kind':'shadow','round':12,'attempt':1,'attempt_id':'shadows.a01',
                 'working_root':str(root),'prior_checkpoint_sha256':subject.digest(protected),'legacy':False}
        subject._seal_record(root/'attempt.json',context)
        import os
        subject._seal_record(root/'OPERATION.json',{'context':context,'producer_pid':os.getpid(),
                            'producer_process_start_ticks':process_start_ticks(os.getpid())})
        subject._save(root/'shadows/Aneutral/r02/B.outcome.json',{'fixture_interrupted_canary':'FAILED_OLD_OUTPUT'})
        class Backend:
            @staticmethod
            def preflight_before_attempt(*args,**kwargs):return {'status':'ready'}
            def __init__(inner,*args,attempt_context,**kwargs):inner.context=attempt_context
        observed=[]
        def execute(view,backend):
            observed.append(backend.context['attempt_id'])
            self.assertEqual(backend.context['attempt_id'],'shadows.a02')
            self.assertFalse((view.out/'shadows/Aneutral/r02/B.outcome.json').exists())
            report={'status':'complete','planned_shadow_turns':24,'observed_shadow_turns':24,
                    'unavailable_shadow_turns':0,'main_preservation_verified':True}
            subject._save(view.out/'shadows/report.json',report)
            return report
        with patch('experiments.triad_v9_checkpoint_restart04.backend.Backend',Backend), \
             patch('experiments.triad_v9_checkpoint_restart04.funding.seal_abandoned_attempt'), \
             patch('experiments.triad_v9_checkpoint_restart04.funding.attempt_rows',return_value={}), \
             patch.object(subject,'_run_fixed',side_effect=execute):
            report=subject.run_shadows(self.host)
        self.assertEqual(observed,['shadows.a02'])
        self.assertEqual(report['failed_technical_workflows'][0]['fault']['fault_class'],'interrupted_shadow_workflow')
        self.assertTrue((root/'abandonment.json').exists())
        self.assertEqual(protected,subject.main_manifest(self.engine))

    def test_shadow_pending_proof_requires_dead_actual_operation_owner(self):
        import os
        from experiments.triad_v9_checkpoint_restart04.funding import process_start_ticks
        from visual_collusion.budget import PendingPaidRequest
        root=self.engine.out/'shadow_attempts/a01'
        context={'kind':'shadow','round':12,'attempt':1,'attempt_id':'shadows.a01',
                 'working_root':str(root),'prior_checkpoint_sha256':subject.digest(subject.main_manifest(self.engine)),'legacy':False}
        subject._seal_record(root/'attempt.json',context)
        marker=root/'OPERATION.json'
        subject._seal_record(marker,{'context':context,'producer_pid':os.getpid(),
                           'producer_process_start_ticks':process_start_ticks(os.getpid())})
        with patch('experiments.triad_v9_checkpoint_restart04.funding.attempt_rows',return_value={'fixture':{'status':'pending'}}):
            with self.assertRaises(PendingPaidRequest):
                subject._shadow_pending_proof(self.host,context)
            actual_ticks=process_start_ticks(os.getpid())
            with patch('experiments.triad_v9_checkpoint_restart04.funding.process_start_ticks',side_effect=[None,actual_ticks]):
                path=subject._shadow_pending_proof(self.host,context)
            proof=subject._read(path)
            self.assertEqual(proof['operation_marker_sha256'],subject._sha(marker.read_bytes()))
            self.assertEqual(proof['old_producer_pid'],os.getpid())
            self.assertTrue(proof['exclusive_producer_lock_reacquired'])
        # Only the separate funding verifier can attest the actual lock and
        # callback owners. Writing this proof never constructs a paid guard.
        self.assertFalse((root/'abandonment.json').exists())
