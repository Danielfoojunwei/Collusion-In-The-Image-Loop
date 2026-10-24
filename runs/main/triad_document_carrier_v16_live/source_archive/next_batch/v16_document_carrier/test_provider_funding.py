"""Offline V16 exact-wire, input-bound and shared-funding contracts."""
from copy import deepcopy
from decimal import Decimal
import base64
import io
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from PIL import Image
from visual_collusion.budget import BudgetLedger,BudgetError,PendingPaidRequest
from experiments.triad_ascii_records_v10r2.tests.test_backend import WireTransport
from next_batch.v15_glm_restored.storage import canonical,sha,put,read,lock,IntegrityError
from next_batch.v15_glm_restored.funding import Ledger as OldLedger,Guard as OldGuard,seal as oldseal,NAMESPACE as OLD_NS
from .funding import AGGREGATE,OLD_V15_OUT,PREFIXES,NAMESPACE,Ledger,Guard,seal,RESERVES,NATIVE_PROFILES,NORMAL_NATIVE_CALLS,NORMAL_RESERVATION_USD,validate_native_wire
from visual_collusion.budget import request_digest
from .provider import Provider,Fault,actor_payload,inspect_payload,NATIVE_RESERVE_USD,MODEL,IMAGE_MODEL,INPUT_LIMITS


def tools(name='publish_document'):
    return [{'type':'function','function':{'name':name,'strict':True,
      'parameters':{'type':'object','properties':{'title':{'type':'string'}},'required':['title'],'additionalProperties':False}}}]


def wire_payload(name='publish_document'):
    return actor_payload(messages(),tools(name))


def messages():return [{'role':'system','content':'OFFLINE document study fixture.'},{'role':'user','content':'OFFLINE public task.'}]


def png(color=(12,34,56)):
    out=io.BytesIO();Image.new('RGB',(1024,1024),color).save(out,'PNG');return out.getvalue()


def hold_other_arm(path,out,ready,release,answer):
    """A subprocess with its own main thread; no real HTTP is performed."""
    out=Path(out)
    try:
        ledger=Ledger(path,out)
        with lock(out/'arms/b01'):
            guard=Guard(path,out,'b01.r01.a01',lambda:None,ledger=ledger)
            def post(body,binding):
                ready.set()
                if not release.wait(15):raise TimeoutError('OFFLINE parent did not release fixture')
                return {'response':{'fixture':True},'receipt':{**binding,'cost_usd':.001}}
            guard.execute('b01.r01.a01.A.native','chat',MODEL,wire_payload(),RESERVES['chat'],post)
        answer.put({'status':'completed'})
    except BaseException as exc:
        ready.set();answer.put({'status':'error','class':type(exc).__name__})


def unreserved_owner_process(path,out,answer,barrier):
    """Two separate CLI-like processes test safe ownership rollover offline."""
    out=Path(out)
    try:
        ledger=Ledger(path,out)
        with lock(out/'arms/b01'):
            guard=Guard(path,out,'b01.r01.a01',lambda:None,ledger=ledger)
            transport=WireTransport(ledger,[{'title':'OFFLINE cross-process ownership fixture'}])
            provider=Provider(guard,out/'evidence',transport=transport,api_key='OFFLINE_FIXTURE_NOT_A_SECRET')
            provider.preflight(minimum_remaining=.1)
            if barrier:
                with patch.object(ledger,'reserve',side_effect=PendingPaidRequest('OFFLINE other-arm reserve barrier')):
                    try:provider.native('b01.r01.a01.A.native',messages(),tools())
                    except PendingPaidRequest:status='paused_unreserved'
                    else:status='wrong_no_pause'
            else:
                first=provider.native('b01.r01.a01.A.native',messages(),tools())
                again=provider.native('b01.r01.a01.A.native',messages(),tools())
                status='completed_and_cached'if first['status']=='parsed'and again['cached']else'wrong_cache'
            answer.put({'status':status,'pid':os.getpid(),'posts':len(transport.posts_exact)})
    except BaseException as failure:
        answer.put({'status':'error','class':type(failure).__name__,'message':str(failure)})


class ProviderFundingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.path=self.root/'budget.sqlite3';self.out=self.root/'run'
        BudgetLedger(self.path,40);self.ledger=Ledger(self.path,self.out)

    def provider(self,actions):
        self.guard=Guard(self.path,self.out,'b01.r01.a01',lambda:None,ledger=self.ledger)
        self.transport=WireTransport(self.ledger,actions)
        provider=Provider(self.guard,self.out/'evidence',transport=self.transport,api_key='OFFLINE_FIXTURE_NOT_A_SECRET')
        provider.preflight(minimum_remaining=.1)
        return provider

    def test_conservative_math_and_whole_study_normal_ceiling(self):
        counts={'publish_document':180,'record_vote':120,'record_log':180,'record_initial_judgment':120}
        expected={'publish_document':Decimal('.030672'),'record_vote':Decimal('.026576'),
                  'record_log':Decimal('.02248'),'record_initial_judgment':Decimal('.0151072')}
        for name,profile in NATIVE_PROFILES.items():
            actual=(Decimal(profile['text_and_schema_utf8_bytes']+profile['image_blocks']*8192)*Decimal('.00000015')
                    +Decimal(2*profile['output_tokens'])*Decimal('.0000005')+Decimal('.002'))
            self.assertEqual(actual,expected[name]);self.assertLessEqual(actual,Decimal(profile['reserve_usd']))
        self.assertEqual(sum(counts.values()),600);self.assertEqual(NORMAL_NATIVE_CALLS,600)
        total=sum(counts[n]*Decimal(p['reserve_usd'])for n,p in NATIVE_PROFILES.items())+180*Decimal('.028')
        self.assertEqual(total,Decimal('19.7568'));self.assertEqual(NORMAL_RESERVATION_USD,total)

    def test_exact_native_wire_route_output_temperature_reasoning_reserve_and_cache(self):
        with lock(self.out):
            p=self.provider([{'action':'publish_document','arguments':{'title':'OFFLINE fixture'}}])
            first=p.native('b01.r01.a01.A_DOCUMENT.native',messages(),tools())
            again=p.native('b01.r01.a01.A_DOCUMENT.native',messages(),tools())
            self.assertEqual(first['status'],'parsed');self.assertEqual(first['arguments'],{'title':'OFFLINE fixture'})
            self.assertTrue(again['cached']);self.assertEqual(len(self.transport.posts_exact),1)
            payload=json.loads(self.transport.posts_exact[0][1])
            self.assertEqual(payload['model'],MODEL);self.assertEqual(payload['temperature'],1.0)
            self.assertEqual(payload['max_tokens'],16384);self.assertEqual(payload['reasoning'],{'effort':'high','exclude':False})
            self.assertEqual(payload['provider']['only'],['open-inference/fp4']);self.assertFalse(payload['provider']['allow_fallbacks'])
            self.assertNotIn('top_k',payload);self.assertNotIn('top_p',payload)
            self.assertEqual(payload['provider']['max_price'],{'prompt':.15,'completion':.5,'request':0})
            row=self.ledger.lookup(NAMESPACE+':b01.r01.a01.A_DOCUMENT.native')
            self.assertEqual(row['reserved_usd'],.0314912)
            self.assertEqual(p._preflight['input_limits'][MODEL],INPUT_LIMITS)
            p.adopt_preflight(p._preflight)

    def test_free_preflight_creates_no_request_and_rejects_old_bound_metadata(self):
        p=self.provider([])
        with self.ledger._connect()as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM requests').fetchone()[0],0)
        old=deepcopy(p._preflight);old['input_limits'][MODEL]['publish_document']['image_blocks']=3
        with self.assertRaises(IntegrityError):p.adopt_preflight(old)
        self.assertEqual(self.transport.posts_exact,[])

    def test_all_four_function_profiles_are_automatically_selected_and_funded(self):
        actions=[{'title':'OFFLINE native profile fixture'}for _ in NATIVE_PROFILES]
        with lock(self.out):
            p=self.provider(actions)
            for i,(name,profile)in enumerate(NATIVE_PROFILES.items(),1):
                result=p.native(f'b01.r01.a01.PROFILE{i}.native',messages(),tools(name))
                self.assertEqual(result['status'],'parsed')
                wire=json.loads(self.transport.posts_exact[-1][1])
                self.assertEqual(wire['tools'][0]['function']['name'],name)
                self.assertEqual(wire['max_tokens'],profile['output_tokens'])
                self.assertEqual(wire['temperature'],1.0);self.assertEqual(wire['reasoning'],{'effort':'high','exclude':False})
                self.assertEqual(self.ledger.lookup(NAMESPACE+f':b01.r01.a01.PROFILE{i}.native')['reserved_usd'],float(profile['reserve_usd']))
            self.assertEqual(p._preflight['reservation']['profiles'],NATIVE_PROFILES)
            old=deepcopy(p._preflight);old['reservation'].pop('profiles')
            with self.assertRaises(IntegrityError):p.adopt_preflight(old)

    def test_unknown_mixed_and_cheap_profiles_cannot_buy_larger_calls(self):
        with self.assertRaises(IntegrityError):actor_payload(messages(),tools('unknown_function'))
        with self.assertRaises(IntegrityError):actor_payload(messages(),tools()+tools('record_log'))
        g=Guard(self.path,self.out,'b01.r01.a01',lambda:None,ledger=self.ledger)
        with lock(self.out):
            # Every reserve here is in the allowed set; the actual wire must
            # still match its own function's precise output/input cost profile.
            for i,(name,reserve)in enumerate([('publish_document','.0151072'),('record_vote','.02248'),('record_log','.0151072')]):
                with self.assertRaises(BudgetError):g.execute(f'b01.r01.a01.BAD{i}.native','chat',MODEL,wire_payload(name),Decimal(reserve),lambda *_:None)
            wrong=wire_payload('record_log');wrong['max_tokens']=16384
            with self.assertRaises(BudgetError):g.execute('b01.r01.a01.BADMAX.native','chat',MODEL,wrong,Decimal('.02248'),lambda *_:None)
            relaxed=wire_payload();relaxed['provider']['max_price']['prompt']=.2
            with self.assertRaises(BudgetError):g.execute('b01.r01.a01.BADPRICE.native','chat',MODEL,relaxed,RESERVES['chat'],lambda *_:None)
        with self.ledger._connect()as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM requests').fetchone()[0],0)

    def test_initial_judgment_profile_has_smaller_exact_text_bound_and_no_images(self):
        p=self.provider([]);payload=wire_payload('record_initial_judgment');used=inspect_payload(payload)[0]
        payload['messages'][1]['content']+='a'*(32768-used)
        self.assertEqual(inspect_payload(payload),(32768,0))
        payload['messages'][1]['content']+='a'
        with self.assertRaises(IntegrityError):inspect_payload(payload)
        mm=messages();mm.append({'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(png()).decode()}}]})
        with self.assertRaises(IntegrityError):p.native('b01.r01.a01.INITIAL.native',mm,tools('record_initial_judgment'))
        wire=wire_payload('record_initial_judgment')
        wire['messages'].append({'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/webp;base64,OFFLINE'}}]})
        with self.assertRaises(BudgetError):validate_native_wire(wire,Decimal('.0151072'))
        self.assertEqual(self.transport.posts_exact,[])

    def test_funding_checks_actual_wire_input_bound_and_typed_pins_before_reserving(self):
        g=Guard(self.path,self.out,'b01.r01.a01',lambda:None,ledger=self.ledger)
        oversized=wire_payload('record_log');oversized['messages'][1]['content']='x'*49153
        mixed=wire_payload();mixed['tools']+=tools('record_vote')
        with lock(self.out):
            for i,payload in enumerate([oversized,mixed]):
                with self.assertRaises(BudgetError):g.execute(f'b01.r01.a01.OV{i}.native','chat',MODEL,payload,RESERVES['chat'],lambda *_:None)
        with self.ledger._connect()as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM requests').fetchone()[0],0)

    def test_all_four_actual_png_inputs_survive_lossless_wire(self):
        with lock(self.out):
            p=self.provider([{'title':'OFFLINE fixture'}]);mm=messages()
            for i in range(4):mm.append({'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(png((i,34,56))).decode()}}]})
            self.assertEqual(inspect_payload(actor_payload(mm,tools()))[1],4)
            p.native('b01.r01.a01.C_VOTE.native',mm,tools())
            wire=json.loads(self.transport.posts_exact[0][1]);urls=[part['image_url']['url']for m in wire['messages']if isinstance(m.get('content'),list)for part in m['content']if part.get('type')=='image_url']
            self.assertEqual(len(urls),4);self.assertTrue(all(u.startswith('data:image/webp;base64,')for u in urls))
            mapping=list((self.out/'evidence/transport_requests').glob('*.json'));self.assertEqual(len(mapping),1)
            self.assertEqual(len(read(mapping[0])['mapping']['images']),4)

    def test_fifth_png_rejected_before_funding_or_post(self):
        p=self.provider([]);mm=messages();block={'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(png()).decode()}}
        mm.append({'role':'user','content':[block]*5})
        with self.assertRaises(IntegrityError):p.native('b01.r01.a01.C_VOTE.native',mm,tools())
        self.assertEqual(self.transport.posts_exact,[])

    def test_text_and_schema_utf8_bound_is_exact_not_character_count(self):
        payload=actor_payload(messages(),tools());used=inspect_payload(payload)[0]
        payload['messages'][1]['content']+='a'*(49152-used)
        self.assertEqual(inspect_payload(payload)[0],49152)
        payload['messages'][1]['content']+='a'
        with self.assertRaises(IntegrityError):inspect_payload(payload)
        payload=actor_payload(messages(),tools());payload['messages'][1]['content']='é'*24576
        with self.assertRaises(IntegrityError):inspect_payload(payload)

    def test_changed_route_temperature_or_output_rejected_locally(self):
        for key,value in [('temperature',2.0),('max_tokens',8192),('model','other/model')]:
            p=actor_payload(messages(),tools());p[key]=value
            with self.assertRaises(IntegrityError):inspect_payload(p)

    def test_outgoing_hard_price_ceiling_cannot_be_relaxed(self):
        for name,value in [('prompt',.2),('completion',.6),('request',.001)]:
            payload=actor_payload(messages(),tools());payload['provider']['max_price'][name]=value
            with self.assertRaises(IntegrityError):inspect_payload(payload)

    def test_native_multiple_calls_only_first_and_invalid_first_never_searches_later(self):
        with lock(self.out):
            p=self.provider([{'title':'OFFLINE fixture'}])
            bad={'id':'bad-first','type':'function','function':{'name':'not_supplied','arguments':'{}'}}
            good={'id':'good-later','type':'function','function':{'name':'publish_document','arguments':'{"title":"good"}'}}
            self.transport.envelope_overrides['choices']=[{'finish_reason':'tool_calls','message':{'role':'assistant','content':None,'tool_calls':[bad,good]}}]
            result=p.native('b01.r01.a01.A_DOCUMENT.native',messages(),tools())
            self.assertEqual(result['status'],'model_invalid');self.assertEqual(result['action'],'not_supplied');self.assertEqual(result['ignored_extra_native_calls'],1)
            self.assertEqual(len(result['native_message']['tool_calls']),2);self.assertEqual(len(self.transport.posts_exact),1)

    def test_one_actual_qwen_png_and_optional_reference_are_funded_exactly(self):
        with lock(self.out):
            p=self.provider([]);a=p.render('b01.r01.a01.A_DOCUMENT.image','OFFLINE figure')
            b=p.render('b01.r01.a01.B_DOCUMENT.image','OFFLINE figure',Path(a['image']['path']).read_bytes())
            self.assertNotEqual(a['image']['sha256'],b['image']['sha256'])
            self.assertEqual(len(self.transport.posts_exact),2)
            for endpoint,body in self.transport.posts_exact:
                self.assertEqual(endpoint,'/images');self.assertEqual(json.loads(body)['model'],IMAGE_MODEL)
            self.assertEqual(self.ledger.lookup(NAMESPACE+':b01.r01.a01.A_DOCUMENT.image')['reserved_usd'],.028)

    def test_unknown_charge_seal_keeps_full_reserve_and_never_reposts(self):
        g=Guard(self.path,self.out,'b05.r12.a03',lambda:None,ledger=self.ledger);calls=[]
        def fail(body,binding):calls.append(binding);raise TimeoutError('OFFLINE lost result')
        with lock(self.out):
            for i in range(1):
                with self.assertRaises(PendingPaidRequest):g.execute('b05.r12.a03.A_DOCUMENT.native','chat',MODEL,wire_payload(),RESERVES['chat'],fail)
            with self.assertRaises(PendingPaidRequest):self.ledger.assert_ready()
            marker=self.out/'batches/b05/attempts/r12/a03/abandoned.json';put(marker,{'status':'abandoned_technical','technical':True,'attempt_id':'b05.r12.a03'})
            self.assertEqual(seal(self.out,marker,self.ledger),1);self.ledger.assert_ready()
            with self.assertRaises(PendingPaidRequest):g.execute('b05.r12.a03.A_DOCUMENT.native','chat',MODEL,wire_payload(),RESERVES['chat'],fail)
        self.assertEqual(len(calls),1)
        row=self.ledger.lookup(NAMESPACE+':b05.r12.a03.A_DOCUMENT.native');self.assertIsNone(row['actual_usd']);self.assertEqual(row['reserved_usd'],.0314912)

    def test_attempt_ids_lock_attempt_boundary_and_fixed_reservation(self):
        for bid in range(1,6):
            for rnd in range(1,13):Guard(self.path,self.out,f'b{bid:02}.r{rnd:02}.a03',lambda:None,ledger=self.ledger)
        for bad in ['b00.r01.a01','b06.r01.a01','b01.r13.a01','b01.r01.a04','r01.a01']:
            with self.assertRaises(BudgetError):Guard(self.path,self.out,bad,lambda:None,ledger=self.ledger)
        g=Guard(self.path,self.out,'b01.r01.a01',lambda:None,ledger=self.ledger)
        with self.assertRaises(BudgetError):g.execute('b01.r01.a01.A.native','chat',MODEL,wire_payload(),RESERVES['chat'],lambda *_:None)
        with lock(self.out):
            with self.assertRaises(BudgetError):g.execute('b02.r01.a01.A.native','chat',MODEL,wire_payload(),RESERVES['chat'],lambda *_:None)
            with self.assertRaises(BudgetError):g.execute('b01.r01.a01.A.native','chat',MODEL,wire_payload(),.01,lambda *_:None)

    def test_previous_namespace_spend_counts_inside_combined24cap(self):
        old=BudgetLedger(self.path,40);rid='triad_glm_restored_v15:fixture';old.reserve(rid,'chat',MODEL,'a'*64,23.99);old.complete(rid,23.99,{'fixture':True},{'cost_usd':23.99})
        with self.assertRaises(BudgetError):self.ledger.reserve(NAMESPACE+':b01.r01.a01.A.native','chat',MODEL,'b'*64,RESERVES['chat'])
        self.assertTrue(OLD_NS+':'in PREFIXES)

    def test_old_v15_unknown_proof_uses_old_run_root_not_new(self):
        oldroot=self.root/'old_v15';oldledger=OldLedger(self.path,oldroot)
        oldguard=OldGuard(self.path,oldroot,'r01.a01',lambda:None,ledger=oldledger)
        with lock(oldroot):
            with self.assertRaises(PendingPaidRequest):oldguard.execute('r01.a01.A.native','chat',MODEL,wire_payload(),.02,lambda *_:(_ for _ in()).throw(TimeoutError()))
            marker=oldroot/'attempts/r01/a01/abandoned.json';put(marker,{'status':'abandoned_technical','technical':True,'attempt_id':'r01.a01'});oldseal(oldroot,marker,oldledger)
        self.assertEqual(self.ledger.v11_root,OLD_V15_OUT)
        # Redirect only the expected OLD-root fixture; proof exists nowhere underV16.
        self.ledger.v11_root=oldroot;self.ledger.assert_ready()
        self.ledger.v11_root=self.out
        with self.assertRaises(PendingPaidRequest):self.ledger.assert_ready()

    def test_http_policy_is_single_post_and_free_get_retries_are_bounded(self):
        p=self.provider([]);p._get_cache.clear()
        with patch.object(p,'_http',side_effect=TimeoutError())as request:
            with self.assertRaises(Fault):p._request('GET','/key')
            self.assertEqual(request.call_count,3)
        permanent=urllib.error.HTTPError('https://example.invalid',401,'fixture',None,None)
        with patch.object(p,'_http',side_effect=permanent)as request:
            with self.assertRaises(Fault)as raised:p._request('GET','/key')
            self.assertTrue(raised.exception.permanent);self.assertEqual(request.call_count,1)
        self.assertEqual(self.transport.posts_exact,[])

    def test_matching_arm_lock_allows_dispatch_and_other_arm_lock_does_not(self):
        g=Guard(self.path,self.out,'b03.r06.a02',lambda:None,ledger=self.ledger)
        with lock(self.out/'arms/b02'):
            with self.assertRaises(BudgetError):g.execute('b03.r06.a02.A.native','chat',MODEL,wire_payload(),RESERVES['chat'],lambda *_:None)
        with lock(self.out/'arms/b03'):
            def post(body,binding):return {'response':{'fixture':True},'receipt':{**binding,'cost_usd':.001}}
            g.execute('b03.r06.a02.A.native','chat',MODEL,wire_payload(),RESERVES['chat'],post)
        self.assertEqual(self.ledger.lookup(NAMESPACE+':b03.r06.a02.A.native')['status'],'completed')
        with self.assertRaises(BudgetError):Guard(self.path,self.out,'b03.r06.a02',lambda:None,ledger=self.ledger,lock_root=self.out/'arms/b02')

    def test_live_other_arm_pending_can_be_funded_concurrently_without_releasing_reserve(self):
        ctx=multiprocessing.get_context('fork');ready=ctx.Event();release=ctx.Event();answer=ctx.Queue()
        child=ctx.Process(target=hold_other_arm,args=(str(self.path),str(self.out),ready,release,answer));child.start()
        try:
            self.assertTrue(ready.wait(10),'Child did not enter its guarded callback')
            row=self.ledger.lookup(NAMESPACE+':b01.r01.a01.A.native');self.assertEqual(row['status'],'pending')
            self.assertEqual(row['reserved_usd'],.0314912);self.assertIsNone(row['actual_usd'])
            self.ledger.assert_ready()
            with lock(self.out/'arms/b02'):
                g=Guard(self.path,self.out,'b02.r01.a01',lambda:None,ledger=self.ledger)
                def post(body,binding):return {'response':{'fixture':True},'receipt':{**binding,'cost_usd':.001}}
                g.execute('b02.r01.a01.A.native','chat',MODEL,wire_payload(),RESERVES['chat'],post)
            still=self.ledger.lookup(NAMESPACE+':b01.r01.a01.A.native');self.assertEqual(still['status'],'pending');self.assertEqual(still['reserved_usd'],.0314912)
        finally:
            release.set();child.join(10)
            if child.is_alive():child.terminate();child.join()
        self.assertEqual(child.exitcode,0);self.assertEqual(answer.get(timeout=2),{'status':'completed'})
        self.ledger.assert_ready()

    def test_unowned_dead_and_uncertain_rows_remain_blocking(self):
        base=BudgetLedger(self.path,40);rid=NAMESPACE+':b01.r01.a01.A.native'
        wire=canonical(wire_payload()).encode();digest=request_digest({'endpoint':'/chat/completions','wire_body_sha256':sha(wire)})
        base.reserve(rid,'chat',MODEL,digest,RESERVES['chat'])
        with self.assertRaises(PendingPaidRequest):self.ledger.assert_ready()
        owner=self.out/'paid_operations'/(sha(rid.encode())+'.json')
        put(owner,{'attempt_id':'b01.r01.a01','request_id':rid,'wire_sha256':sha(wire),'pid':999999999,'start_ticks':'not-live'})
        put(owner.with_suffix('.inflight.json'),{'owner_sha256':sha(owner.read_bytes()),'guard_execute_inflight':True})
        with self.assertRaises(PendingPaidRequest):self.ledger.assert_ready()
        base.uncertain(rid,'OFFLINE uncertain outcome')
        with self.assertRaises(PendingPaidRequest):self.ledger.assert_ready()
        self.assertEqual(self.ledger.lookup(rid)['reserved_usd'],.0314912)

    def test_abandoned_same_arm_requires_matching_lock_and_seal(self):
        g=Guard(self.path,self.out,'b04.r07.a01',lambda:None,ledger=self.ledger)
        with lock(self.out/'arms/b04'):
            with self.assertRaises(PendingPaidRequest):g.execute('b04.r07.a01.A.native','chat',MODEL,wire_payload(),RESERVES['chat'],lambda *_:(_ for _ in()).throw(TimeoutError()))
        with self.assertRaises(PendingPaidRequest):self.ledger.assert_ready()
        marker=self.out/'arms/b04/attempts/r07/a01/abandoned.json';put(marker,{'status':'abandoned_technical','technical':True,'attempt_id':'b04.r07.a01'})
        with lock(self.out/'arms/b03'):
            with self.assertRaises(IntegrityError):seal(self.out,marker,self.ledger)
        with lock(self.out/'arms/b04'):self.assertEqual(seal(self.out,marker,self.ledger),1)
        self.ledger.assert_ready()

    def test_reserve_time_other_arm_barrier_propagates_without_own_row_or_post(self):
        with lock(self.out):
            provider=self.provider([{'title':'OFFLINE fixture'}])
            with patch.object(self.ledger,'reserve',side_effect=PendingPaidRequest('OFFLINE reserve race')):
                with self.assertRaises(PendingPaidRequest):provider.native('b01.r01.a01.A.native',messages(),tools())
            self.assertIsNone(self.ledger.lookup(NAMESPACE+':b01.r01.a01.A.native'))
            self.assertEqual(self.transport.posts_exact,[])
            result=provider.native('b01.r01.a01.A.native',messages(),tools())
            self.assertEqual(result['status'],'parsed');self.assertEqual(len(self.transport.posts_exact),1)
            archive=list((self.out/'unfunded_dispatches').glob('*/*/retirement.json'))
            self.assertEqual(len(archive),1);self.assertFalse(read(archive[0])['ledger_row_existed'])

    def test_two_process_unreserved_owner_rollover_and_completed_native_cache_make_one_post(self):
        ctx=multiprocessing.get_context('fork');answers=[]
        for barrier in(True,False):
            q=ctx.Queue();child=ctx.Process(target=unreserved_owner_process,args=(str(self.path),str(self.out),q,barrier))
            child.start();child.join(20)
            if child.is_alive():child.terminate();child.join();self.fail('OFFLINE ownership fixture timed out')
            self.assertEqual(child.exitcode,0);answers.append(q.get(timeout=2))
        self.assertEqual(answers[0]['status'],'paused_unreserved',answers)
        self.assertEqual(answers[1]['status'],'completed_and_cached',answers)
        self.assertNotEqual(answers[0]['pid'],answers[1]['pid'])
        self.assertEqual([a['posts']for a in answers],[0,1])
        physical=NAMESPACE+':b01.r01.a01.A.native'
        self.assertEqual(self.ledger.lookup(physical)['status'],'completed')
        owner=self.out/'paid_operations'/(sha(physical.encode())+'.json')
        self.assertEqual(read(owner)['pid'],answers[1]['pid'])
        archives=list((self.out/'unfunded_dispatches').glob('*/*/owner.json'))
        self.assertEqual(len(archives),1);self.assertEqual(read(archives[0])['pid'],answers[0]['pid'])
        self.assertEqual(read(archives[0])['wire_sha256'],read(owner)['wire_sha256'])

    def test_own_uncertain_request_stays_held_and_its_owner_is_never_rebound(self):
        with lock(self.out):
            provider=self.provider([])
            original=provider._http
            def broken(method,path,body=None):
                if method=='POST':raise TimeoutError('OFFLINE lost paid response')
                return original(method,path,body)
            provider._http=broken
            with self.assertRaises(Fault):provider.native('b01.r01.a01.A.native',messages(),tools())
            physical=NAMESPACE+':b01.r01.a01.A.native';owner=self.out/'paid_operations'/(sha(physical.encode())+'.json');before=owner.read_bytes()
            marker=self.out/'attempts/r01/a01/abandoned.json';put(marker,{'status':'abandoned_technical','technical':True,'attempt_id':'b01.r01.a01'})
            seal(self.out,marker,self.ledger)
            with self.assertRaises(Fault):provider.native('b01.r01.a01.A.native',messages(),tools())
            self.assertEqual(owner.read_bytes(),before);self.assertFalse((self.out/'unfunded_dispatches').exists())
            self.assertEqual(self.ledger.lookup(physical)['status'],'uncertain')

    def test_interrupt_stops_future_calls_without_new_reservations(self):
        g=Guard(self.path,self.out,'b01.r01.a01',lambda:None,ledger=self.ledger)
        with lock(self.out):
            with self.assertRaisesRegex(IntegrityError,'Operator stop'):
                g.execute('b01.r01.a01.A.native','chat',MODEL,wire_payload(),RESERVES['chat'],lambda *_:(_ for _ in()).throw(KeyboardInterrupt()))
            self.assertTrue((self.out/'STOP_REQUESTED.json').exists())
            with self.assertRaisesRegex(IntegrityError,'Operator stop'):
                g.execute('b01.r01.a01.B.native','chat',MODEL,wire_payload(),RESERVES['chat'],lambda *_:None)
        self.assertIsNone(self.ledger.lookup(NAMESPACE+':b01.r01.a01.B.native'))


if __name__=='__main__':unittest.main()
