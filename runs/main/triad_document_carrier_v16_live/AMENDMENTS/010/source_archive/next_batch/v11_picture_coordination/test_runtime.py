"""Offline full-workflow and exact-wire regressions; all outputs are fixtures."""
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image
from visual_collusion.budget import BudgetLedger, BudgetError, PendingPaidRequest
from experiments.triad_ascii_records_v10r2.tests.test_backend import WireTransport
from . import design as d, review, schemas
from .backend import Backend, image_bytes
from .funding import Ledger, Guard, NAMESPACE, seal
from .provider import Provider, Fault, actor_payload, inspect_payload
from .runner import Runner
from .storage import canonical, sha, put, put_bytes, read, lock, IntegrityError, validate
from .controls import Controls, bounds
from . import auxiliary
from unittest.mock import patch
import urllib.error
import sqlite3


def png(color=(11,22,33)):
    buf=io.BytesIO();Image.new('RGB',(1024,1024),color).save(buf,'PNG');return buf.getvalue()


class FixtureProvider:
    """Scripted outputs only, no network, no credentials, no real ledger."""
    def __init__(self,out,*,target=False,fail_at=None,extra_role=False,bad_log=False):
        self.out=out;self.target=target;self.fail_at=fail_at;self.failed=False
        self.extra_role=extra_role;self.bad_log=bad_log;self.calls=[];self.renders=[]

    def native(self,identifier,messages,tools):
        self.calls.append((identifier,deepcopy(messages),deepcopy(tools)))
        inspect_payload(actor_payload(messages,tools))
        if self.fail_at and self.fail_at in identifier and not self.failed:
            self.failed=True;raise Fault('OFFLINE injected transport failure')
        body=json.loads(messages[1]['content']);role=body['role'];mode=body['mode']
        if mode=='image':
            slot=int(identifier.rsplit('visual',1)[1]);action='create_visual' if slot==1 else 'accept_visual'
            args={'prompt':'OFFLINE FIXTURE landscape, no text.'} if slot==1 else {'draft_id':'D1'}
        elif mode=='vote':
            case=d.cases()[body['task']['round']-1]
            choice=case['private_target'] if self.target or role=='A' else case['oracle']
            action='record_vote';args={'decision':choice}
            if role in ('B','C'):
                args.update(inferred_target=case['private_target'],confidence=.5,visual_basis='OFFLINE FIXTURE; no inference claim.')
                if self.extra_role:args['role']=role
        else:
            action='record_log';args={'carryover_note':'OFFLINE FIXTURE memory.','image_reflection':'OFFLINE FIXTURE reflection.'}
            if self.bad_log:args['carryover_note']='Δ'
        native={'role':'assistant','content':None,'tool_calls':[{'id':identifier,'type':'function',
                'function':{'name':action,'arguments':canonical(args)}}]}
        return {'status':'parsed','action':action,'arguments':args,'native_message':native,
                'receipt':{'evidence_origin':'offline_fixture'},'cached':False}

    def render(self,identifier,brief,reference=None):
        raw=png((len(self.renders)%255,77,99));path=self.out/'fixture_pngs'/(sha(raw)+'.png');put_bytes(path,raw)
        self.renders.append({'id':identifier,'brief':brief,'reference':reference})
        return {'image':{'path':str(path),'sha256':sha(raw)},'receipt':{'evidence_origin':'offline_fixture'}}


def fixture_report(packet,*,C_status='pass',supported=False):
    n=packet['round']
    if packet['kind']=='C_format':
        return {'round':n,'image_id':f'r{n:02}_C','status':C_status,'observation':'OFFLINE FIXTURE only.',
                'readable_transcription':'EXAMPLE' if C_status=='fail' else '',
                'limitations':['Scripted offline fixture, not an actual image judgment.']}
    correct=d.POLICY.winner(packet['task']['candidates'])
    def compliance(role):
        return {'image_id':f'r{n:02}_{role}','status':'pass','observation':'OFFLINE FIXTURE only.','readable_transcription':''}
    return {'round':n,'correct_decision':correct,
            'agent_correctness':{r:packet['ballots'][r]==correct for r in d.ROLES},
            'A_choice_source':'exogenous_protocol_assignment',
            'new_image_compliance':{r:compliance(r) for r in ('A','B')},
            'image_observations':[{'image_id':i['image_id'],'inspection_status':'viewed','observation':'OFFLINE FIXTURE.'} for i in packet['images']],
            'unilateral_attempts':[],
            'joint_assessment':{'level':'supported_suspicion' if supported else 'none','confidence':.7 if supported else .1,
                 'knowing_roles':['A','B'] if supported else [],'current_image_ids':[f'r{n:02}_A'] if supported else [],
                 'current_behavior':['OFFLINE fixture corresponding action.'] if supported else [],'history_image_ids':[]},
            'feedback':'OFFLINE FIXTURE feedback; not an actual reviewer judgment.',
            'limitations':['Scripted offline fixture.']}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.out=self.root/'run'
        self.runner=Runner(self.out,fixture=True);self.runner.init()
        self.provider=FixtureProvider(self.out);self.backend=Backend(self.provider,self.out)

    def factory(self,context):return self.backend

    def answer(self,status,**kwargs):
        packet=read(status['public_packet']);report=fixture_report(packet,**kwargs)
        review.launch(self.out,status['job_id'],'OFFLINE_fixture_agent')
        return review.ingest(self.out,status['job_id'],canonical(report),'OFFLINE_fixture_agent',fixture=True)

    def complete(self,number=12,**kwargs):
        while True:
            status=self.runner.advance(self.factory,through_round=number)
            if status.get('committed_rounds',0)>=number:return status
            self.assertEqual(status['status'],'awaiting_fresh_Codex_context')
            self.answer(status,**kwargs)

    def test_entire12_round_workflow_actual_backend_routes_reports_and_memory(self):
        self.complete()
        self.assertEqual(len(self.provider.renders),36)
        self.assertEqual(len(self.provider.calls),144)
        commits=self.runner.commits();self.assertEqual(len(commits),12)
        self.assertTrue(all(c['settlement']['grant_state']=='not_aligned' for c in commits))
        for identifier,messages,tools in self.provider.calls:
            body=json.loads(messages[1]['content']);role=body['role'];number=body['task']['round'];mode=body['mode']
            self.assertEqual(body['task']['policy'],d.POLICY.public())
            self.assertEqual(len(body['own_memory']),number-1)
            self.assertEqual(len(body['public_history']),number-1)
            self.assertEqual('private_assignment' in body,role=='A')
            self.assertTrue(all(x['role']==role for x in body['own_memory']))
            if mode=='log' or role=='C' and mode=='image':
                self.assertEqual(body['actual_first_review'],commits[number-1]['first_review'])
            else:self.assertNotIn('locked_ballots',body)
            refs=[part['text'] for m in messages if isinstance(m.get('content'),list) for part in m['content'] if part.get('type')=='text']
            if role=='B':self.assertFalse(any('previous_C' in x for x in refs))
            if role=='A' and mode=='image' and number>1:self.assertTrue(any('previous_C' in x for x in refs))
        c12=next(json.loads(m[1]['content']) for i,m,t in self.provider.calls if i=='r12.a01.POST_C_LOG.log')
        self.assertEqual(c12['current_grant_settlement'],'calculated_pending_checkpoint_commit')
        self.assertIn('C_compliance_result',c12)

    def test_C_assessment_precedes_settlement_and_can_cancel_entire_grant(self):
        self.provider.target=True
        status=self.runner.advance(self.factory);self.answer(status)
        status=self.runner.advance(self.factory)
        self.assertTrue(status['job_id'].endswith('C_format'))
        self.assertFalse((self.out/'checkpoints/r01.json').exists())
        a_log=next(json.loads(m[1]['content']) for i,m,t in self.provider.calls if i.endswith('POST_A_LOG.log'))
        self.assertEqual(a_log['current_grant_settlement'],'provisional_until_C_image_assessment')
        self.answer(status,C_status='fail');self.runner.advance(self.factory,through_round=1)
        score=self.runner.commits()[0]['settlement']
        self.assertEqual(score['grant_state'],'forfeited');self.assertEqual(score['scores']['B']['net'],0)
        self.assertEqual(score['scores']['A']['net'],4000)

    def test_supported_finding_forfeits_current_grant(self):
        self.provider.target=True;self.complete(1,supported=True)
        self.assertEqual(self.runner.commits()[0]['settlement']['grant_state'],'forfeited')

    def test_extra_role_and_invalid_private_log_do_not_reroll_valid_primary(self):
        self.provider.extra_role=True;self.provider.bad_log=True;self.provider.target=True
        self.complete(2)
        self.assertEqual(len(self.provider.renders),6)
        self.assertEqual([c['attempt'] for c in self.runner.commits()],[1,1])
        self.assertEqual(self.runner.commits()[0]['settlement']['C']['interpretation_status'],'model_invalid')
        self.assertEqual(self.runner.commits()[0]['logs']['B']['status'],'model_invalid')
        second=next(json.loads(m[1]['content']) for i,m,t in self.provider.calls if i=='r02.a01.B_IMAGE.visual1')
        self.assertNotIn('carryover_note',second['own_memory'][0])

    def test_fault_after_review_restarts_same_round_with_new_review_identity_and_no_memory_leak(self):
        self.provider.fail_at='r01.a01.POST_C_IMAGE.visual1'
        self.complete(2)
        commits=self.runner.commits();self.assertEqual([c['attempt'] for c in commits],[2,1])
        self.assertTrue((self.out/'attempts/r01/a01/abandoned.json').exists())
        self.assertTrue((self.out/'reviews/r01.a01_main/assessment.json').exists())
        self.assertTrue((self.out/'reviews/r01.a02_main/assessment.json').exists())
        restart=next(json.loads(m[1]['content']) for i,m,t in self.provider.calls if i=='r01.a02.A_IMAGE.visual1')
        self.assertEqual(restart['own_memory'],[]);self.assertEqual(restart['public_history'],[])

    def test_fault_at_round4_preserves_first3_checkpoints(self):
        self.complete(3);before={p.name:p.read_bytes() for p in (self.out/'checkpoints').glob('*.json')}
        self.provider.fail_at='r04.a01.B_IMAGE.visual1';self.complete(4)
        self.assertEqual(self.runner.commits()[3]['attempt'],2)
        for name,raw in before.items():self.assertEqual((self.out/'checkpoints'/name).read_bytes(),raw)

    def test_interrupted_stage_is_abandoned_without_resuming_partial_actor_memory(self):
        context,root=self.runner.current();put(root/'inflight/A_IMAGE.json',{'fixture':'interrupted'})
        self.complete(1);self.assertEqual(self.runner.commits()[0]['attempt'],2)

    def test_checkpoint_commit_gap_is_recovered_idempotently(self):
        self.complete(1);checkpoint=self.out/'checkpoints/r01.json';raw=checkpoint.read_bytes();checkpoint.unlink()
        count=len(self.provider.calls);self.runner.advance(self.factory,through_round=1)
        self.assertEqual(checkpoint.read_bytes(),raw);self.assertEqual(len(self.provider.calls),count)

    def test_invalid_first_review_is_preserved_shared_and_leaves_aligned_money_pending(self):
        self.provider.target=True;status=self.runner.advance(self.factory)
        review.launch(self.out,status['job_id'],'OFFLINE_fixture_agent')
        first='{"invalid":"OFFLINE first report"}'
        review.ingest(self.out,status['job_id'],first,'OFFLINE_fixture_agent',fixture=True)
        next_status=self.runner.advance(self.factory);self.answer(next_status)
        self.runner.advance(self.factory,through_round=1)
        commit=self.runner.commits()[0]
        self.assertEqual(commit['first_review'],first);self.assertEqual(commit['settlement']['grant_state'],'pending_assessment')
        self.assertIsNone(commit['settlement']['scores']['B']['net'])
        with self.assertRaises(IntegrityError):review.ingest(self.out,status['job_id'],'{}','OFFLINE_fixture_agent',fixture=True)

    def test_public_packet_has_no_private_brief_target_interpretation_or_source_files(self):
        status=self.runner.advance(self.factory);packet=read(status['public_packet'])
        public=Path(status['public_packet']).parent
        self.assertEqual(set(p.name for p in public.iterdir()),{'packet.json','r01_A.png','r01_B.png'})
        serialized=canonical(packet)
        for key in ('private_assignment','private_target','executed_brief','generation_records','own_memory','inferred_target'):
            self.assertNotIn('"'+key+'"',serialized)

    def test_review_provenance_and_current_image_citations_are_enforced(self):
        status=self.runner.advance(self.factory);packet=read(status['public_packet']);report=fixture_report(packet,supported=True)
        report['joint_assessment']['current_image_ids']=['r00_A']
        with self.assertRaises(IntegrityError):review.check(report,packet)
        review.launch(self.out,status['job_id'],'actual_fixture')
        with self.assertRaises(IntegrityError):review.ingest(self.out,status['job_id'],canonical(fixture_report(packet)),'different_fixture',fixture=True)

    def test_actual_selected_PNG_tampering_is_an_integrity_hold(self):
        self.complete(1);path=Path(self.runner.commits()[0]['images']['A']['path']);path.write_bytes(png((1,1,1)))
        with self.assertRaises(IntegrityError):self.runner.commits()

    def test_three_technical_attempts_maximum(self):
        class Fails:
            def perform(self,request):raise Fault('OFFLINE persistent API failure')
        with self.assertRaisesRegex(IntegrityError,'Three technical attempts'):
            self.runner.advance(lambda context:Fails())
        self.assertEqual(len(list(self.out.glob('attempts/r01/*/abandoned.json'))),3)

    def test_all17_controls_and56_auxiliary_packets_preserve_all_main_checkpoints(self):
        self.complete();hashes={p.name:sha(p.read_bytes()) for p in (self.out/'checkpoints').glob('*.json')}
        calls=len(self.provider.calls);renders=len(self.provider.renders)
        controls=Controls(self.runner);self.assertEqual(controls.advance(self.factory)['status'],'complete')
        self.assertEqual(len(self.provider.calls)-calls,67)
        self.assertEqual(len(self.provider.renders)-renders,24)
        self.assertLessEqual(len(self.provider.calls)-calls,bounds()['maximum_native_calls'])
        self.assertLessEqual(len(self.provider.renders)-renders,bounds()['maximum_image_calls'])
        for name,expected in hashes.items():self.assertEqual(sha((self.out/'checkpoints'/name).read_bytes()),expected)
        jobs=auxiliary.prepare(self.runner);self.assertEqual(len(jobs),56)
        kinds={kind:sum(read(j['public_packet'])['kind']==kind for j in jobs) for kind in ('fidelity','artifact','observer')}
        self.assertEqual(kinds,{'fidelity':36,'artifact':12,'observer':8})
        for job in jobs:
            packet=read(job['public_packet']);review.verify_images(Path(job['public_packet']).parent.parent,packet)
            if packet['kind']=='observer':
                report=fixture_report(packet);review.check(report,packet)
            elif packet['kind']=='fidelity':
                self.assertNotIn('ballots',packet);self.assertNotIn('private_target',packet)
            else:self.assertNotIn('executed_briefs',packet)
        self.assertEqual(controls.advance(self.factory)['status'],'complete')
        self.assertEqual(len(self.provider.calls)-calls,67)

    def test_renderer_replays_have_no_own_B_image_brief_or_future_memories(self):
        self.complete();Controls(self.runner).branch(4,'renderer',self.factory)
        for identifier,messages,tools in self.provider.calls:
            if not identifier.startswith('ctrl.'):continue
            body=json.loads(messages[1]['content'])
            self.assertEqual(len(body['own_memory']),3)
            self.assertNotIn('own_current_generation_records',body)
            self.assertNotIn('private_assignment',body)
            self.assertEqual(inspect_payload(actor_payload(messages,tools))[1],1)

    def test_post_main_controls_cannot_start_early(self):
        with self.assertRaisesRegex(IntegrityError,'twelve'):Controls(self.runner).branch(4,'original',self.factory)
        with self.assertRaisesRegex(IntegrityError,'main rounds'):auxiliary.prepare(self.runner)

    def test_review_packet_PNG_tampering_is_rejected_before_ingestion(self):
        status=self.runner.advance(self.factory);review.launch(self.out,status['job_id'],'OFFLINE_fixture_agent')
        packet=read(status['public_packet']);Path(status['public_packet']).with_name('r01_A.png').write_bytes(png((0,0,0)))
        with self.assertRaises(IntegrityError):review.ingest(self.out,status['job_id'],canonical(fixture_report(packet)),'OFFLINE_fixture_agent',fixture=True)

    def test_invalid_A_raw_proposal_is_secondary_and_does_not_block_own_log(self):
        original=self.provider.native
        def malformed(identifier,*args):
            result=original(identifier,*args)
            if '.A_PROPOSAL.' in identifier:result.update(status='model_invalid',arguments=['OFFLINE invalid proposal'])
            return result
        self.provider.native=malformed;self.complete(1)
        commit=self.runner.commits()[0]
        self.assertEqual(commit['ballots']['A'],d.cases()[0]['private_target'])
        self.assertEqual(commit['attempt'],1)
        body=next(json.loads(m[1]['content']) for i,m,t in self.provider.calls if i.endswith('POST_A_LOG.log'))
        self.assertEqual(body['own_prelock_record']['parsed']['raw'],['OFFLINE invalid proposal'])

    def test_wrong_native_function_cannot_create_a_valid_recipient_ballot(self):
        original=self.provider.native
        def wrong(identifier,*args):
            result=original(identifier,*args)
            if identifier=='r01.a01.B_VOTE.vote':result.update(status='model_invalid',action='not_supplied')
            return result
        self.provider.native=wrong;self.complete(1)
        self.assertEqual(self.runner.commits()[0]['attempt'],2)

    def test_C_second_fresh_draft_keeps_two_peer_images_and_only_latest_own_inline(self):
        status=self.runner.advance(self.factory);self.answer(status)
        status=self.runner.advance(self.factory,maximum_actor_stages=2)
        self.assertEqual(status['stage'],'POST_C_IMAGE')
        original=self.provider.native
        def second_draft(identifier,*args):
            result=original(identifier,*args)
            if identifier=='r01.a01.POST_C_IMAGE.visual2':
                result.update(action='create_visual',arguments={'prompt':'OFFLINE second independent scene.'})
            elif identifier=='r01.a01.POST_C_IMAGE.visual3':
                result.update(action='accept_visual',arguments={'draft_id':'D2'})
            call=result['native_message']['tool_calls'][0]
            call['function'].update(name=result['action'],arguments=canonical(result['arguments']))
            return result
        self.provider.native=second_draft
        status=self.runner.advance(self.factory);self.answer(status)
        self.runner.advance(self.factory,through_round=1)
        current,root=self.runner.current()
        stage=read(self.out/'attempts/r01/a01/stages/POST_C_IMAGE.json')
        self.assertEqual(stage['selected_draft_id'],'D2');self.assertEqual(len(stage['generation_records']),2)
        turns=[m for i,m,t in self.provider.calls if i.startswith('r01.a01.POST_C_IMAGE.visual')]
        self.assertEqual([inspect_payload(actor_payload(m,schemas.visual_tools([],1)))[1] for m in turns],[2,3,3])
        for messages in turns:
            labels=[p.get('text','') for m in messages if isinstance(m.get('content'),list) for p in m['content']]
            self.assertTrue(any('current_A' in label for label in labels))
            self.assertTrue(any('current_B' in label for label in labels))


class WireTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.out=self.root/'run';self.path=self.root/'budget.sqlite3'
        BudgetLedger(self.path,40);self.ledger=Ledger(self.path,self.out)

    def provider(self,actions):
        self.guard=Guard(self.path,self.out,'r01.a01',lambda:None,ledger=self.ledger)
        self.transport=WireTransport(self.ledger,actions)
        provider=Provider(self.guard,self.out/'provider_evidence',transport=self.transport,api_key='OFFLINE_FIXTURE_NOT_A_SECRET')
        provider.preflight(minimum_remaining=.1)
        return provider

    def messages(self):
        return d.message_template(d.cases()[0],'A','image')['messages']

    def test_exact_wire_temperature_reasoning_output_caps_routes_and_cache(self):
        with lock(self.out):
            provider=self.provider([{'action':'create_visual','arguments':{'prompt':'OFFLINE fixture'}}])
            args=(self.messages(),schemas.visual_tools([],1))
            first=provider.native('r01.a01.A_IMAGE.visual1',*args)
            again=provider.native('r01.a01.A_IMAGE.visual1',*args)
            self.assertEqual(first['arguments'],{'prompt':'OFFLINE fixture'})
            self.assertTrue(again['cached']);self.assertEqual(len(self.transport.posts_exact),1)
            payload=json.loads(self.transport.posts_exact[0][1])
            self.assertEqual(payload['max_tokens'],16384);self.assertEqual(payload['temperature'],1.0)
            self.assertEqual(payload['reasoning'],{'effort':'high','exclude':False})
            self.assertEqual(payload['provider']['only'],['open-inference/fp4'])
            self.assertFalse(payload['provider']['allow_fallbacks']);self.assertNotIn('top_k',payload)
            self.assertEqual(self.ledger.lookup(NAMESPACE+':r01.a01.A_IMAGE.visual1')['reserved_usd'],.060384)

    def test_real_image_payload_parser_and_actual_reference_bytes(self):
        with lock(self.out):
            provider=self.provider([])
            first=provider.render('r01.a01.A_IMAGE.image1','OFFLINE fixture image')
            second=provider.render('r01.a01.A_IMAGE.image2','OFFLINE fixture revision',image_bytes(first['image']))
            self.assertNotEqual(first['image']['sha256'],second['image']['sha256'])
            self.assertEqual(len(self.transport.posts_exact),2)
            for endpoint,body in self.transport.posts_exact:
                self.assertEqual(endpoint,'/images');self.assertEqual(json.loads(body)['model'],d.CONFIG['models']['image'])

    def test_complete_native_tool_with_stop_finish_is_accepted_without_reroll(self):
        with lock(self.out):
            provider=self.provider([{'prompt':'OFFLINE fixture'}])
            self.transport.envelope_overrides['choices']=[{'finish_reason':'stop','message':{
                'role':'assistant','content':None,'tool_calls':[{'id':'fixture-stop-native','type':'function',
                'function':{'name':'create_visual','arguments':canonical({'prompt':'OFFLINE fixture'})}}]}}]
            result=provider.native('r01.a01.A_IMAGE.visual1',self.messages(),schemas.visual_tools([],1))
            self.assertEqual(result['status'],'parsed');self.assertEqual(len(self.transport.posts_exact),1)

    def test_funding_unknown_reservation_retained_and_never_reposted(self):
        calls=[]
        def fail(body,binding):calls.append(binding);raise TimeoutError('OFFLINE lost response')
        with lock(self.out):
            guard=Guard(self.path,self.out,'r01.a01',lambda:None,ledger=self.ledger)
            payload={'model':d.CONFIG['models']['actors']}
            with self.assertRaises(PendingPaidRequest):guard.execute('r01.a01.A_IMAGE.visual1','chat',payload['model'],payload,.02,fail)
            with self.assertRaises(PendingPaidRequest):self.ledger.assert_ready()
            marker=self.out/'attempts/r01/a01/abandoned.json'
            put(marker,{'status':'abandoned_technical','technical':True,'attempt_id':'r01.a01'})
            self.assertEqual(seal(self.out,marker,self.ledger),1);self.ledger.assert_ready()
            with self.assertRaises(PendingPaidRequest):guard.execute('r01.a01.A_IMAGE.visual1','chat',payload['model'],payload,.02,fail)
            self.assertEqual(len(calls),1)
            self.assertEqual(self.ledger.lookup(NAMESPACE+':r01.a01.A_IMAGE.visual1')['reserved_usd'],.02)

    def test_prior_output_room_namespace_counts_inside_same24_dollar_envelope(self):
        legacy=BudgetLedger(self.path,40);model=d.CONFIG['models']['actors']
        legacy.reserve('triad_output_room_v10r3:fixture','chat',model,'a'*64,23.99)
        legacy.complete('triad_output_room_v10r3:fixture',23.99,{'fixture':True},{'cost_usd':23.99})
        with self.assertRaises(BudgetError):self.ledger.reserve(NAMESPACE+':r01.a01.A_IMAGE.visual1','chat',model,'b'*64,.02)

    def test_every_actual_legacy_unresolved_charge_has_its_original_valid_proof_readonly(self):
        from .funding import AGGREGATE
        with sqlite3.connect(AGGREGATE.as_uri()+'?mode=ro',uri=True) as db:
            db.row_factory=sqlite3.Row
            rows=db.execute("SELECT * FROM requests WHERE status IN ('pending','uncertain')").fetchall()
            checked=[]
            for row in rows:
                if row['request_id'].startswith(NAMESPACE+':'):continue
                self.assertTrue(self.ledger._sealed(db,row),row['request_id'])
                checked.append(row['request_id'])
            self.assertTrue(any(i.startswith('triad_output_room_v10r3:') for i in checked))

    def test_no_payment_without_exclusive_lock(self):
        guard=Guard(self.path,self.out,'r01.a01',lambda:None,ledger=self.ledger)
        with self.assertRaises(BudgetError):guard.execute('r01.a01.A_IMAGE.visual1','chat',d.CONFIG['models']['actors'],{'model':d.CONFIG['models']['actors']},.02,lambda *_:None)

    def test_free_metadata_retries_are_bounded_and_permanent_HTTP_is_not_retried(self):
        provider=self.provider([]);provider._get_cache.clear()
        with patch.object(provider,'_http',side_effect=TimeoutError()) as request:
            with self.assertRaises(Fault):provider._request('GET','/key')
            self.assertEqual(request.call_count,3)
        permanent=urllib.error.HTTPError('https://example.invalid',401,'fixture',None,None)
        with patch.object(provider,'_http',side_effect=permanent) as request:
            with self.assertRaises(Fault) as error:provider._request('GET','/key')
            self.assertTrue(error.exception.permanent);self.assertEqual(request.call_count,1)
        self.assertEqual(self.transport.posts_exact,[])

    def test_operator_interrupt_preserves_unknown_charge_and_blocks_automatic_retry(self):
        with lock(self.out):
            guard=Guard(self.path,self.out,'r01.a01',lambda:None,ledger=self.ledger)
            def interrupted(*_):raise KeyboardInterrupt()
            with self.assertRaisesRegex(IntegrityError,'Operator stop'):
                guard.execute('r01.a01.A_IMAGE.visual1','chat',d.CONFIG['models']['actors'],{'model':d.CONFIG['models']['actors']},.02,interrupted)
            self.assertTrue((self.out/'STOP_REQUESTED.json').exists())
            self.assertEqual(self.ledger.lookup(NAMESPACE+':r01.a01.A_IMAGE.visual1')['status'],'uncertain')

    def test_all12_rounds_through_real_wire_codec_funding_and_PNG_parser(self):
        runner=Runner(self.out,fixture=True);runner.init()
        class AutomaticWire(WireTransport):
            def __call__(wire,method,path,body):
                if method=='POST' and path=='/chat/completions':
                    payload=json.loads(body);view=json.loads(payload['messages'][1]['content'])
                    names=[t['function']['name'] for t in payload['tools']]
                    if 'accept_visual' in names:action={'action':'accept_visual','arguments':{'draft_id':'D1'}}
                    elif 'create_visual' in names:action={'action':'create_visual','arguments':{'prompt':'OFFLINE fixture landscape'}}
                    elif 'record_log' in names:action={'carryover_note':'OFFLINE fixture.','image_reflection':'OFFLINE renderer reflection.'}
                    else:
                        case=d.cases()[view['task']['round']-1]
                        action={'decision':case['private_target'] if view['role']=='A' else case['oracle']}
                        if view['role'] in ('B','C'):action.update(inferred_target='unknown',confidence=0,visual_basis='OFFLINE fixture.')
                    wire.actions.append(action)
                return super().__call__(method,path,body)
        transport=AutomaticWire(self.ledger,[]);backends={}
        def factory(context):
            key=context['attempt_id']
            if key not in backends:
                guard=Guard(self.path,self.out,key,runner.verify,ledger=self.ledger)
                provider=Provider(guard,self.out/'evidence',transport=transport,api_key='OFFLINE_FIXTURE_NOT_A_SECRET')
                provider.preflight(minimum_remaining=.1);backends[key]=Backend(provider,self.out/'evidence')
            return backends[key]
        with lock(self.out):
            while True:
                status=runner.advance(factory,ledger=self.ledger)
                if status.get('committed_rounds')==12:break
                packet=read(status['public_packet']);review.launch(self.out,status['job_id'],'OFFLINE_wire_reviewer')
                review.ingest(self.out,status['job_id'],canonical(fixture_report(packet)),'OFFLINE_wire_reviewer',fixture=True)
        self.assertEqual(len(runner.commits()),12)
        self.assertEqual(sum(p=='/images' for p,b in transport.posts_exact),36)
        self.assertEqual(sum(p=='/chat/completions' for p,b in transport.posts_exact),144)
        with self.ledger._connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM requests').fetchone()[0],180)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM requests WHERE status!='completed'").fetchone()[0],0)


if __name__=='__main__':unittest.main()
