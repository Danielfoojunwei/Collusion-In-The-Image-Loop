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
from experiments.triad_ascii_records_v10r2.tests.test_backend import WireTransport as PreviousWireTransport
from experiments.pressure_interview_v1.tests.test_live_provider import ScriptedWireTransport
from next_batch.v15_glm_restored.storage import canonical,sha,put,read,lock,IntegrityError
from next_batch.v15_glm_restored.funding import Ledger as OldLedger,Guard as OldGuard,seal as oldseal,NAMESPACE as OLD_NS
from .funding import AGGREGATE,OLD_V15_OUT,PREFIXES,NAMESPACE,Ledger,Guard,seal,RESERVES,NATIVE_PROFILES,NORMAL_NATIVE_CALLS,NORMAL_RESERVATION_USD,validate_native_wire,ACTOR_PROVIDER_TAG,ACTOR_PROVIDER_NAME,MODEL_BINDING_KEY
from visual_collusion.budget import request_digest
from visual_collusion.providers import PreflightError
from .provider import Provider,Fault,actor_payload,inspect_payload,NATIVE_RESERVE_USD,MODEL,IMAGE_MODEL,INPUT_LIMITS,MAX_OUTPUT_TOKENS,MAX_INPUT_RESERVE_TOKENS,MIN_CONTEXT_TOKENS,AMENDMENT_PROFILE,MODEL_BINDING_FILENAME,REQUIRED_NATIVE_PARAMETERS,TRANSPORT_POLICY,paid_deadline_seconds,content_json_arguments


class WireTransport(PreviousWireTransport):
    """Offline metadata for the same currently observed pinned route prices."""
    def __init__(self,ledger,actions):
        super().__init__(ledger,actions)
        for row in self.models['data']:
            if row.get('id')==MODEL:
                row['pricing']={'prompt':'0.00000015','completion':'0.0000005','input_cache_read':'0.00000003'}
        for endpoint in self.chat_endpoints[MODEL]['data']['endpoints']:
            endpoint.update(provider_name='Relace',tag='relace',quantization='unknown')
            endpoint['pricing']={'prompt':'0.0000000502','completion':'0.0000000709','input_cache_read':'0.00000003'}
            endpoint['supported_parameters']=[x for x in endpoint['supported_parameters']if x not in('response_format','structured_outputs')]
            endpoint['supported_parameters'].append('reasoning_effort')
            endpoint['supports_tool_choice']={'none':True,'auto':True,'required':False,'function':False}

    def __call__(self,method,path,body):
        if method=='POST'and path=='/chat/completions':
            # The reusable older fixture assumes a forced function. This fixture
            # consumes the actual auto-choice wire unchanged and scripts a tool
            # response; it does not rewrite the outgoing request for that helper.
            result=ScriptedWireTransport.__call__(self,method,path,body)
            payload=json.loads(body);expected=payload['tools'][0]['function']['name']
            arguments=result['choices'][0]['message']['content']
            try:wrapped=json.loads(arguments)
            except(ValueError,TypeError):wrapped=None
            if isinstance(wrapped,dict)and set(wrapped)=={'action','arguments'}and wrapped['action']==expected:
                arguments=json.dumps(wrapped['arguments'],ensure_ascii=False)
            result['provider']='Relace'
            result['choices']=[{'finish_reason':'tool_calls','message':{'role':'assistant','content':None,
                'tool_calls':[{'id':'fixture-native-'+str(len(self.posts_exact)),'type':'function',
                               'function':{'name':expected,'arguments':arguments}}]}}]
            result.update(deepcopy(self.envelope_overrides))
        else:result=super().__call__(method,path,body)
        return result


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

    def provider(self,actions,attempt_id='b01.r01.a01'):
        self.guard=Guard(self.path,self.out,attempt_id,lambda:None,ledger=self.ledger)
        self.transport=WireTransport(self.ledger,actions)
        provider=Provider(self.guard,self.out/'evidence',transport=self.transport,api_key='OFFLINE_FIXTURE_NOT_A_SECRET')
        provider.preflight(minimum_remaining=.1)
        return provider

    def test_conservative_math_and_whole_study_normal_ceiling(self):
        counts={'publish_document':180,'record_vote':120,'record_log':180,'record_initial_judgment':120}
        expected={'publish_document':Decimal('.0323104'),'record_vote':Decimal('.0216608'),
                  'record_log':Decimal('.0208416'),'record_initial_judgment':Decimal('.0110112')}
        for name,profile in NATIVE_PROFILES.items():
            actual=(Decimal(profile['text_and_schema_utf8_bytes']+profile['image_blocks']*8192)*Decimal('.00000015')
                    +Decimal(2*profile['output_tokens'])*Decimal('.00000010')+Decimal('.002'))
            self.assertEqual(actual,expected[name]);self.assertLessEqual(actual,Decimal(profile['reserve_usd']))
        self.assertEqual(sum(counts.values()),600);self.assertEqual(NORMAL_NATIVE_CALLS,600)
        total=sum(counts[n]*Decimal(p['reserve_usd'])for n,p in NATIVE_PROFILES.items())+180*Decimal('.028')
        self.assertEqual(total,Decimal('18.528'));self.assertEqual(NORMAL_RESERVATION_USD,total)

    def test_exact_native_wire_route_output_temperature_reasoning_reserve_and_cache(self):
        with lock(self.out):
            p=self.provider([{'action':'publish_document','arguments':{'title':'OFFLINE fixture'}}])
            first=p.native('b01.r01.a01.A_DOCUMENT.native',messages(),tools())
            again=p.native('b01.r01.a01.A_DOCUMENT.native',messages(),tools())
            self.assertEqual(first['status'],'parsed');self.assertEqual(first['arguments'],{'title':'OFFLINE fixture'})
            self.assertTrue(again['cached']);self.assertEqual(len(self.transport.posts_exact),1)
            payload=json.loads(self.transport.posts_exact[0][1])
            self.assertEqual(payload['model'],MODEL);self.assertEqual(payload['temperature'],1.0)
            self.assertEqual(payload['max_tokens'],65536);self.assertEqual(payload['reasoning'],{'effort':'high','exclude':False})
            self.assertEqual(payload['provider']['only'],['relace']);self.assertFalse(payload['provider']['allow_fallbacks'])
            self.assertNotIn('top_k',payload);self.assertNotIn('top_p',payload)
            self.assertEqual(payload['provider']['max_price'],{'prompt':.15,'completion':.10,'request':0})
            row=self.ledger.lookup(NAMESPACE+':b01.r01.a01.A_DOCUMENT.native')
            self.assertEqual(row['reserved_usd'],.0323104)
            self.assertEqual(p._preflight['input_limits'][MODEL],INPUT_LIMITS)
            p.adopt_preflight(p._preflight)

    def test_free_preflight_creates_no_request_and_rejects_old_bound_metadata(self):
        p=self.provider([])
        with self.ledger._connect()as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM requests').fetchone()[0],0)
        old=deepcopy(p._preflight);old['input_limits'][MODEL]['publish_document']['image_blocks']=3
        with self.assertRaises(IntegrityError):p.adopt_preflight(old)
        self.assertEqual(self.transport.posts_exact,[])

    def test_amended_metadata_requires_output_capacity_context_prices_and_profile(self):
        for mutation in ('output','context','prompt_price','completion_price'):
            with self.subTest(mutation=mutation):
                p=self.provider([])
                endpoint=self.transport.chat_endpoints[MODEL]['data']['endpoints'][0]
                if mutation=='output':endpoint['max_completion_tokens']=65535
                elif mutation=='context':endpoint['context_length']=MIN_CONTEXT_TOKENS-1
                elif mutation=='prompt_price':endpoint['pricing']['prompt']='0.000000150001'
                elif mutation=='completion_price':endpoint['pricing']['completion']='0.000000100001'
                p._get_cache.clear()
                with self.assertRaises(PreflightError):p.preflight(minimum_remaining=.1)
                self.assertEqual(self.transport.posts_exact,[])
        p=self.provider([])
        self.assertEqual(MAX_INPUT_RESERVE_TOKENS,114688);self.assertEqual(MIN_CONTEXT_TOKENS,180224)
        self.assertEqual(p._preflight['reservation']['amendment_profile'],AMENDMENT_PROFILE)
        for mutation in ('old_profile','old_sampling','old_prompt_cap','old_completion_cap','missing_amendment','expensive_endpoint'):
            old=deepcopy(p._preflight)
            if mutation=='old_profile':old['reservation']['profiles']['publish_document']['output_tokens']=16384
            elif mutation=='old_sampling':old['sampling']['max_output_tokens']=16384
            elif mutation=='old_prompt_cap':old['reservation']['hard_prompt_max_price_per_million']='0.10'
            elif mutation=='old_completion_cap':old['reservation']['hard_completion_max_price_per_million']='0.05'
            elif mutation=='missing_amendment':old['reservation'].pop('amendment_profile')
            else:
                old['chat_metadata'][MODEL]['endpoint']['pricing']['completion']='0.000000100001'
                old['chat_metadata'][MODEL]['pricing']=deepcopy(old['chat_metadata'][MODEL]['endpoint']['pricing'])
            with self.subTest(cached_mutation=mutation),self.assertRaises(IntegrityError):p.adopt_preflight(old)

    def test_expensive_catalog_with_valid_pinned_route_passes_and_preserves_original_quote(self):
        p=self.provider([])
        row=next(row for row in self.transport.models['data']if row['id']==MODEL)
        original=deepcopy(row)
        chat=p._preflight['chat_metadata'][MODEL]
        self.assertEqual(chat['catalog_advertised_pricing'],row['pricing'])
        self.assertEqual(chat['catalog_advertised_pricing']['completion'],'0.0000005')
        self.assertEqual(chat['pricing'],chat['endpoint']['pricing'])
        self.assertEqual(chat['pricing']['prompt'],'0.0000000502')
        self.assertEqual(chat['pricing_validation_basis'],'pinned_provider_only_no_fallback')
        direct=Provider._chat_metadata(row,MODEL,self.transport.chat_endpoints[MODEL])
        self.assertEqual(row,original)
        self.assertEqual(direct['catalog_advertised_pricing'],original['pricing'])
        p.adopt_preflight(p._preflight)
        self.assertEqual(self.transport.posts_exact,[])

    def test_cheap_catalog_cannot_hide_expensive_pinned_route(self):
        for field,too_expensive in(('prompt','0.000000150001'),('completion','0.000000100001')):
            with self.subTest(field=field):
                p=self.provider([])
                row=next(row for row in self.transport.models['data']if row['id']==MODEL)
                row['pricing']={'prompt':'0.000000001','completion':'0.000000001'}
                self.transport.chat_endpoints[MODEL]['data']['endpoints'][0]['pricing'][field]=too_expensive
                p._get_cache.clear()
                with self.assertRaises(PreflightError):p.preflight(minimum_remaining=.1)
                self.assertEqual(self.transport.posts_exact,[])

    def test_relace_native_metadata_needs_no_unused_json_mode_and_reports_unknown_precision(self):
        p=self.provider([]);chat=p._preflight['chat_metadata'][MODEL]
        self.assertEqual(chat['endpoint']['tag'],'relace');self.assertEqual(chat['endpoint']['provider_name'],'Relace')
        self.assertNotIn('response_format',chat['endpoint']['supported_parameters'])
        self.assertNotIn('structured_outputs',chat['endpoint']['supported_parameters'])
        self.assertFalse(chat['structured_schema'])
        self.assertEqual(chat['strict_tool_schema_enforcement'],'not_advertised_native_schema_locally_validated')
        self.assertEqual(chat['native_metadata_validation'],'actual_outgoing_tool_parameters_only')
        self.assertEqual(chat['precision_status'],'unreported');self.assertFalse(chat['precision_verified'])
        self.assertEqual(chat['endpoint_quantization_reported'],'unknown')
        self.assertEqual(chat['actual_tool_choice'],'auto');self.assertFalse(chat['tool_call_guaranteed_by_metadata'])
        self.assertEqual(chat['tool_choice_support'],{'none':True,'auto':True,'required':False,'function':False})
        payload=actor_payload(messages(),tools())
        self.assertNotIn('response_format',payload);self.assertEqual(payload['tools'],tools())
        self.assertTrue(payload['tools'][0]['function']['strict'])
        self.assertEqual(payload['tool_choice'],'auto')
        p.adopt_preflight(p._preflight)
        for field,value in(('structured_schema',True),('precision_status','fp4'),('precision_verified',True)):
            forged=deepcopy(p._preflight);forged['chat_metadata'][MODEL][field]=value
            with self.subTest(field=field),self.assertRaises(IntegrityError):p.adopt_preflight(forged)
        self.assertEqual(self.transport.posts_exact,[])

    def test_auto_choice_value_support_is_required_not_just_parameter_name(self):
        for support in(None,{}, {'auto':False}, {'auto':'true'}):
            p=self.provider([])
            endpoint=self.transport.chat_endpoints[MODEL]['data']['endpoints'][0]
            endpoint['supports_tool_choice']=support;p._get_cache.clear()
            with self.subTest(support=support),self.assertRaises(PreflightError):p.preflight(minimum_remaining=.1)
            self.assertEqual(self.transport.posts_exact,[])
        for choice in('required',{'type':'function','function':{'name':'publish_document'}}):
            payload=wire_payload();payload['tool_choice']=choice
            with self.subTest(choice=choice):
                with self.assertRaises(IntegrityError):inspect_payload(payload)
                with self.assertRaises(BudgetError):validate_native_wire(payload,RESERVES['chat'])

    def test_saved_actual_relace_endpoint_passes_native_validator_without_get_or_fabricated_json_support(self):
        source=Path(__file__).resolve().parents[2]/'runs/main/triad_document_carrier_v16_live/driver/FREE_TOOL_CHOICE_DIAGNOSTIC.json'
        if not source.exists():self.skipTest('Actual saved route metadata is absent outside this study workspace')
        diagnostic=read(source)
        p=self.provider([])
        row=next(row for row in self.transport.models['data']if row['id']==MODEL)
        actual=diagnostic['endpoint']
        result=Provider._chat_metadata(row,MODEL,{'data':{'id':MODEL,'endpoints':[actual]}})
        self.assertEqual(result['endpoint'],actual)
        self.assertEqual(result['endpoint']['context_length'],1048576)
        self.assertEqual(result['endpoint']['max_completion_tokens'],131072)
        self.assertEqual(result['pricing']['prompt'],'0.0000000502')
        self.assertEqual(result['pricing']['completion'],'0.0000000709')
        self.assertFalse(result['structured_schema']);self.assertFalse(result['precision_verified'])
        self.assertEqual(self.transport.posts_exact,[])

    def test_each_actual_native_parameter_high_reasoning_and_unknown_precision_is_required(self):
        for mutation in [*sorted(REQUIRED_NATIVE_PARAMETERS),'precision','effort','model_identity','provider_identity','status']:
            with self.subTest(mutation=mutation):
                p=self.provider([])
                endpoint=self.transport.chat_endpoints[MODEL]['data']['endpoints'][0]
                if mutation in REQUIRED_NATIVE_PARAMETERS:endpoint['supported_parameters'].remove(mutation)
                elif mutation=='precision':endpoint['quantization']='fp4'
                elif mutation=='effort':next(row for row in self.transport.models['data']if row['id']==MODEL)['reasoning']['supported_efforts']=['medium']
                elif mutation=='model_identity':endpoint['model_id']='different/model'
                elif mutation=='provider_identity':endpoint['provider_name']='OpenInference'
                else:endpoint['status']=-1
                p._get_cache.clear()
                with self.assertRaises(PreflightError):p.preflight(minimum_remaining=.1)
                self.assertEqual(self.transport.posts_exact,[])

    def test_original_openinference_binding_and_file_are_immutable_during_explicit_relace_repair(self):
        old={'models':{'actor':MODEL,'image':IMAGE_MODEL},'actor_slug':'original-snapshot',
             'actor_provider':'open-inference/fp4','actor_quantization':'fp4','image_provider':'alibaba'}
        self.ledger.bind('model_configuration',canonical(old))
        with self.ledger._connect()as db:db.execute('INSERT INTO settings VALUES (?,?)',(NAMESPACE+'_model_configuration',canonical(old)))
        old_file=self.out/'evidence/glm_model_binding.json';put(old_file,old);before=old_file.read_bytes()
        p=self.provider([])
        self.assertEqual(old_file.read_bytes(),before)
        binding=read(self.out/'evidence'/MODEL_BINDING_FILENAME)
        self.assertEqual(binding['actor_provider'],'relace');self.assertEqual(binding['actor_quantization'],'unreported')
        self.assertEqual(binding['actor_quantization_reported'],'unknown');self.assertFalse(binding['actor_precision_verified'])
        with self.ledger._connect()as db:
            values={row['key']:row['value']for row in db.execute('SELECT key,value FROM settings')}
        self.assertEqual(values['model_configuration'],canonical(old))
        self.assertEqual(values[NAMESPACE+'_model_configuration'],canonical(old))
        self.assertEqual(values[MODEL_BINDING_KEY],canonical(binding))
        self.ledger.bind(MODEL_BINDING_KEY,canonical(binding))
        changed=deepcopy(binding);changed['actor_slug']='a-different-snapshot'
        with self.assertRaises(BudgetError):self.ledger.bind(MODEL_BINDING_KEY,canonical(changed))
        for bad_key in('model_configuration_relace_untracked','secret'):
            with self.assertRaises(BudgetError):self.ledger.bind(bad_key,canonical(binding))
        with self.ledger._connect()as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM requests').fetchone()[0],0)

    def test_old_route_wire_or_cached_metadata_cannot_silently_substitute_for_relace(self):
        payload=wire_payload();payload['provider']['only']=['open-inference/fp4']
        with self.assertRaises(IntegrityError):inspect_payload(payload)
        with self.assertRaises(BudgetError):validate_native_wire(payload,RESERVES['chat'])
        p=self.provider([]);old=deepcopy(p._preflight)
        old['chat_metadata'][MODEL]['endpoint'].update(tag='open-inference/fp4',provider_name='OpenInference',quantization='fp4')
        with self.assertRaises(IntegrityError):p.adopt_preflight(old)
        self.assertEqual(self.transport.posts_exact,[])

    def test_returned_different_provider_is_preserved_and_halts_instead_of_falling_back(self):
        with lock(self.out):
            p=self.provider([{'title':'OFFLINE fixture'}])
            self.transport.envelope_overrides['provider']='OpenInference'
            with self.assertRaises(Fault)as caught:p.native('b01.r01.a01.WRONGROUTE.native',messages(),tools())
            self.assertTrue(caught.exception.permanent);self.assertEqual(caught.exception.code,'actor_identity_hold')
            self.assertEqual(len(self.transport.posts_exact),1)
            row=self.ledger.lookup(NAMESPACE+':b01.r01.a01.WRONGROUTE.native')
            self.assertEqual(row['status'],'completed');self.assertEqual(row['response']['provider'],'OpenInference')

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
            for i,(name,reserve)in enumerate([('publish_document','.0110112'),('record_vote','.0208416'),('record_log','.0110112')]):
                with self.assertRaises(BudgetError):g.execute(f'b01.r01.a01.BAD{i}.native','chat',MODEL,wire_payload(name),Decimal(reserve),lambda *_:None)
            wrong=wire_payload('record_log');wrong['max_tokens']=16384
            with self.assertRaises(BudgetError):g.execute('b01.r01.a01.BADMAX.native','chat',MODEL,wrong,Decimal('.0208416'),lambda *_:None)
            relaxed=wire_payload();relaxed['provider']['max_price']['prompt']=.2
            with self.assertRaises(BudgetError):g.execute('b01.r01.a01.BADPRICE.native','chat',MODEL,relaxed,RESERVES['chat'],lambda *_:None)
        with self.ledger._connect()as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM requests').fetchone()[0],0)

    def test_initial_judgment_profile_has_smaller_exact_text_bound_and_no_images(self):
        p=self.provider([]);payload=wire_payload('record_initial_judgment');used=inspect_payload(payload)[0]
        payload['messages'][1]['content']+='a'*(49152-used)
        self.assertEqual(inspect_payload(payload),(49152,0))
        payload['messages'][1]['content']+='a'
        with self.assertRaises(IntegrityError):inspect_payload(payload)
        mm=messages();mm.append({'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(png()).decode()}}]})
        with self.assertRaises(IntegrityError):p.native('b01.r01.a01.INITIAL.native',mm,tools('record_initial_judgment'))
        wire=wire_payload('record_initial_judgment')
        wire['messages'].append({'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/webp;base64,OFFLINE'}}]})
        with self.assertRaises(BudgetError):validate_native_wire(wire,Decimal('.0110112'))
        self.assertEqual(self.transport.posts_exact,[])

    def test_funding_checks_actual_wire_input_bound_and_typed_pins_before_reserving(self):
        g=Guard(self.path,self.out,'b01.r01.a01',lambda:None,ledger=self.ledger)
        oversized=wire_payload('record_log');oversized['messages'][1]['content']='x'*81921
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
        payload['messages'][1]['content']+='a'*(81920-used)
        self.assertEqual(inspect_payload(payload)[0],81920)
        payload['messages'][1]['content']+='a'
        with self.assertRaises(IntegrityError):inspect_payload(payload)
        payload=actor_payload(messages(),tools());payload['messages'][1]['content']='é'*40960
        with self.assertRaises(IntegrityError):inspect_payload(payload)

    def test_quote_heavy_encoded_context_fits_expanded_profile_without_changing_content(self):
        source={'reports':[{'public_text':'"'*4400+'s'*1600}for _ in range(4)],
                'memory':['"'*350 for _ in range(11)],'private_plan':'"'*1000}
        encoded=canonical(source)
        self.assertGreater(len(encoded.encode()),49152)
        for name in ('publish_document','record_vote','record_log'):
            with self.subTest(profile=name):
                mm=messages();mm[1]['content']=encoded
                payload=actor_payload(mm,tools(name))
                used,images=inspect_payload(payload)
                self.assertEqual(payload['messages'][1]['content'],encoded)
                self.assertEqual(json.loads(payload['messages'][1]['content']),source)
                self.assertEqual(images,0);self.assertLessEqual(used,81920)
                validate_native_wire(payload,Decimal(NATIVE_PROFILES[name]['reserve_usd']))
        payload=actor_payload(messages(),tools('record_initial_judgment'))
        payload['messages'][1]['content']=canonical({**source,'reports':source['reports'][:2]})
        self.assertLessEqual(inspect_payload(payload)[0],49152)
        stale=deepcopy(self.provider([])._preflight)
        stale['input_limits'][MODEL]['publish_document']['text_and_schema_utf8_bytes']=49152
        with self.assertRaises(IntegrityError):self.provider([]).adopt_preflight(stale)

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

    def test_auto_content_json_exact_object_or_fence_preserves_raw_and_bad_values_without_fake_calls(self):
        cases=[(' \n{"title":"Quoted é"}\t',{'title':'Quoted é'},'entire_json_object'),
               ('```json\n{"title":17}\n```',{'title':17},'exact_json_fence'),
               ('```\n{"title":null}\n```',{'title':None},'exact_json_fence')]
        with lock(self.out):
            for i,(content,arguments,form)in enumerate(cases):
                p=self.provider([{}],attempt_id='b01.r01.a04');identifier=f'b01.r01.a04.CONTENT{i}.native'
                self.transport.envelope_overrides['choices']=[{'finish_reason':'stop','message':{'role':'assistant','content':content}}]
                result=p.native(identifier,messages(),tools())
                again=p.native(identifier,messages(),tools())
                self.assertEqual(result['status'],'parsed');self.assertEqual(result['arguments'],arguments)
                self.assertEqual(result['action'],'publish_document');self.assertEqual(result['argument_origin'],'content_json')
                self.assertEqual(result['content_json_form'],form);self.assertNotIn('tool_calls',result['native_message'])
                self.assertEqual(result['native_message']['content'],content)
                self.assertEqual(Path(result['content_json_source']['path']).read_bytes(),content.encode())
                self.assertEqual(result['content_json_source']['sha256'],sha(content.encode()))
                self.assertTrue(again['cached']);self.assertEqual(len(self.transport.posts_exact),1)

    def test_content_json_no_narrative_multiple_objects_selection_refusal_or_value_repair(self):
        invalid=['{"title":"x","extra":1}','{"action":"publish_document","arguments":{"title":"x"}}',
                 '{"title":"first"}\n{"title":"second"}','Here is JSON: {"title":"x"}',
                 '{"title":"x"}\nTrailing prose','[{"title":"x"}]',
                 '```json\n{"title":"x"}\n```\n```json\n{"title":"later"}\n```',
                 '{"title":"first","title":"later"}']
        with lock(self.out):
            for i,content in enumerate(invalid):
                p=self.provider([{}],attempt_id='b01.r01.a04');self.transport.envelope_overrides['choices']=[{'finish_reason':'stop','message':{'role':'assistant','content':content}}]
                result=p.native(f'b01.r01.a04.BADCONTENT{i}.native',messages(),tools())
                self.assertEqual(result['status'],'model_invalid');self.assertEqual(result['native_message']['content'],content)
                self.assertEqual(len(self.transport.posts_exact),1)
            for i,message in enumerate([
                {'role':'assistant','content':'{"title":"x"}','refusal':'I refuse.'},
                {'role':'assistant','content':'{"title":"content"}','tool_calls':[{'id':'first','type':'function','function':{'name':'not_supplied','arguments':'{}'}}]}]):
                p=self.provider([{}],attempt_id='b01.r01.a04');self.transport.envelope_overrides['choices']=[{'finish_reason':'stop','message':message}]
                result=p.native(f'b01.r01.a04.NOPREFER{i}.native',messages(),tools())
                self.assertEqual(result['status'],'model_invalid');self.assertNotEqual(result['argument_origin'],'content_json')
                self.assertEqual(result['native_message'],message)

    def test_capped_incomplete_content_is_technical_but_multiple_complete_objects_are_behavioral(self):
        cases=[(65536,'{"title":',True),(65535,'{"title":',False),
               (65536,'```json\n{"title":',True),
               (65536,'{"title":"first"}\n{"title":"second"}',False),
               (65536,'```json\n{"title":"first"}\n{"title":"second"}',False),
               (65536,'{"unexpected":"complete wrong keys"}',False)]
        with lock(self.out):
            for i,(used,content,technical)in enumerate(cases):
                p=self.provider([{}],attempt_id='b01.r01.a04');identifier=f'b01.r01.a04.CAPCONTENT{i}.native'
                self.transport.envelope_overrides.update(usage={'cost':.0001,'completion_tokens':used,'is_byok':False},
                    choices=[{'finish_reason':'length'if used==65536 else'stop','message':{'role':'assistant','content':content}}])
                if technical:
                    with self.assertRaises(Fault)as caught:p.native(identifier,messages(),tools())
                    self.assertEqual(caught.exception.code,'native_output_cap_truncation')
                else:self.assertEqual(p.native(identifier,messages(),tools())['status'],'model_invalid')
                self.assertEqual(len(self.transport.posts_exact),1)

    def test_publication_deadline_and_socket_timeout_match_3600_other_calls_600(self):
        p=self.provider([])
        from . import provider as provider_module
        for name in NATIVE_PROFILES:
            payload=wire_payload(name);raw=canonical(payload).encode();expected=3600 if name=='publish_document'else 600
            self.assertEqual(paid_deadline_seconds('/chat/completions',raw),expected)
            with patch.object(provider_module,'deadline')as timeout,patch.object(p,'_http',return_value={'offline':True}):
                p._post_exact(raw,{'endpoint':'/chat/completions','request_id':'b01.r01.a04.TIMEOUT.'+name})
                timeout.assert_called_once_with(expected)
        self.assertEqual(paid_deadline_seconds('/images',b'{}'),600)
        p._transport=None
        with patch.object(p,'_opener')as opener:
            opener.open.return_value.__enter__.return_value.read.return_value=b'{"offline":true}'
            p._http('POST','/chat/completions',canonical(wire_payload()).encode())
            self.assertEqual(opener.open.call_args.kwargs['timeout'],3600)
            p._http('POST','/chat/completions',canonical(wire_payload('record_vote')).encode())
            self.assertEqual(opener.open.call_args.kwargs['timeout'],600)
        self.assertEqual(p._preflight['transport_policy'],TRANSPORT_POLICY)

    def test_exact_cap_malformed_primary_is_archived_technical_fault_without_repost(self):
        with lock(self.out):
            p=self.provider([{'title':'OFFLINE fixture'}])
            identifier='b01.r01.a01.A_DOCUMENT.native'
            arguments='{"title":"OFFLINE primary truncated at actual output cap'
            self.transport.envelope_overrides.update(
                usage={'cost':.0001,'completion_tokens':65536,'completion_tokens_details':{'reasoning_tokens':64000},'is_byok':False},
                choices=[{'finish_reason':'tool_calls','message':{'role':'assistant','content':None,'tool_calls':[{'id':'actual-first','type':'function','function':{'name':'publish_document','arguments':arguments}}]}}])
            for _ in range(2):
                with self.assertRaises(Fault)as caught:p.native(identifier,messages(),tools())
                self.assertEqual(caught.exception.code,'native_output_cap_truncation');self.assertFalse(caught.exception.permanent)
            self.assertEqual(len(self.transport.posts_exact),1)
            result=read(self.out/'evidence/native_results'/(sha(identifier.encode())+'.json'))
            marker=read(self.out/'evidence/native_truncations'/(sha(identifier.encode())+'.json'))
            self.assertEqual(result['status'],'technical_output_cap_truncation')
            self.assertEqual(result['native_message']['tool_calls'][0]['function']['arguments'],arguments)
            self.assertTrue(marker['technical']);self.assertFalse(marker['behavioral_evidence'])
            self.assertEqual(marker['actual_completion_tokens'],65536)
            self.assertEqual(self.ledger.lookup(NAMESPACE+':'+identifier)['status'],'completed')

    def test_complete_valid_primary_at_cap_is_preserved_even_with_length_finish_reason(self):
        with lock(self.out):
            for i,reason in enumerate(('tool_calls','length')):
                p=self.provider([{'title':'OFFLINE fixture'}])
                self.transport.envelope_overrides.update(usage={'cost':.0001,'completion_tokens':65536,'is_byok':False})
                result=p.native(f'b01.r01.a01.CAPVALID{i}.native',messages(),tools())if reason=='tool_calls'else None
                if reason=='length':
                    self.transport.envelope_overrides['choices']=[{'finish_reason':'length','message':{'role':'assistant','content':None,'tool_calls':[{'id':'valid-primary','type':'function','function':{'name':'publish_document','arguments':'{"title":"OFFLINE complete"}'}}]}}]
                    result=p.native(f'b01.r01.a01.CAPVALID{i}.native',messages(),tools())
                self.assertEqual(result['status'],'parsed')
                self.assertEqual(len(self.transport.posts_exact),1)
            self.assertFalse((self.out/'evidence/native_truncations').exists())

    def test_empty_primary_at_cap_is_technical_but_below_cap_refusal_and_text_remain_behavioral(self):
        cases=[(65536,{'role':'assistant','content':None},True),
               (65536,{'role':'assistant','content':'','tool_calls':[]},True),
               (65535,{'role':'assistant','content':None,'tool_calls':[]},False),
               (65536,{'role':'assistant','content':None,'refusal':'I refuse this task.'},False),
               (65536,{'role':'assistant','content':'I will answer outside the required tool.'},False),
               (65536,{'role':'assistant','content':'   '},False)]
        with lock(self.out):
            for i,(used,message,technical)in enumerate(cases):
                with self.subTest(case=i):
                    p=self.provider([{'title':'OFFLINE fixture'}])
                    identifier=f'b01.r01.a01.EMPTY{i}.native'
                    self.transport.envelope_overrides.update(usage={'cost':.0001,'completion_tokens':used,'is_byok':False},
                        choices=[{'finish_reason':'tool_calls','message':message}])
                    if technical:
                        with self.assertRaises(Fault)as caught:p.native(identifier,messages(),tools())
                        self.assertEqual(caught.exception.code,'native_output_cap_truncation')
                        marker=read(self.out/'evidence/native_truncations'/(sha(identifier.encode())+'.json'))
                        self.assertEqual(marker['primary_failure'],'absent_primary_output')
                    else:
                        result=p.native(identifier,messages(),tools())
                        self.assertEqual(result['status'],'model_invalid')
                        self.assertFalse((self.out/'evidence/native_truncations'/(sha(identifier.encode())+'.json')).exists())
                    self.assertEqual(len(self.transport.posts_exact),1)
                    self.assertEqual(self.ledger.lookup(NAMESPACE+':'+identifier)['status'],'completed')

    def test_below_cap_malformed_and_at_cap_semantic_invalid_remain_behavioral(self):
        cases=[(65535,'publish_document','{"title":"unfinished'),
               (16384,'publish_document','{"title":"old cap is below amended cap'),
               (65536,'not_supplied','{}'),(65536,'publish_document','[]'),
               (65536,'not_supplied','{"not_the_primary":')]
        with lock(self.out):
            for i,(used,name,arguments)in enumerate(cases):
                p=self.provider([{'title':'OFFLINE fixture'}])
                self.transport.envelope_overrides.update(usage={'cost':.0001,'completion_tokens':used,'is_byok':False},
                    choices=[{'finish_reason':'tool_calls','message':{'role':'assistant','content':None,'tool_calls':[{'id':'first','type':'function','function':{'name':name,'arguments':arguments}}]}}])
                result=p.native(f'b01.r01.a01.INVALID{i}.native',messages(),tools())
                self.assertEqual(result['status'],'model_invalid');self.assertEqual(len(self.transport.posts_exact),1)
            self.assertFalse((self.out/'evidence/native_truncations').exists())

    def test_amendment_never_reuses_or_overwrites_an_old_paid_identity(self):
        identifier='b01.r01.a01.OLD.native';physical=NAMESPACE+':'+identifier
        old_wire=wire_payload();old_wire['max_tokens']=16384
        old_wire['provider']['max_price']={'prompt':.15,'completion':.5,'request':0}
        digest=request_digest({'endpoint':'/chat/completions','wire_body_sha256':sha(canonical(old_wire).encode())})
        base=BudgetLedger(self.path,40);base.reserve(physical,'chat',MODEL,digest,Decimal('.0314912'))
        base.complete(physical,.0001,{'old_raw_response':'preserved'}, {'cost_usd':.0001})
        old_native=self.out/'evidence/native_results'/(sha(identifier.encode())+'.json')
        put(old_native,{'status':'model_invalid','old_original_record':True});before=old_native.read_bytes()
        old_row=self.ledger.lookup(physical)
        with lock(self.out):
            p=self.provider([])
            with self.assertRaises(Fault):p.native(identifier,messages(),tools())
        self.assertEqual(self.transport.posts_exact,[])
        self.assertEqual(self.ledger.lookup(physical),old_row);self.assertEqual(old_native.read_bytes(),before)

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
        row=self.ledger.lookup(NAMESPACE+':b05.r12.a03.A_DOCUMENT.native');self.assertIsNone(row['actual_usd']);self.assertEqual(row['reserved_usd'],.0323104)

    def test_attempt_ids_lock_attempt_boundary_and_fixed_reservation(self):
        for bid in range(1,6):
            for rnd in range(1,13):Guard(self.path,self.out,f'b{bid:02}.r{rnd:02}.a03',lambda:None,ledger=self.ledger)
        for bad in ['b00.r01.a01','b06.r01.a01','b01.r13.a01','b01.r01.a05','r01.a01']:
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
            self.assertEqual(row['reserved_usd'],.0323104);self.assertIsNone(row['actual_usd'])
            self.ledger.assert_ready()
            with lock(self.out/'arms/b02'):
                g=Guard(self.path,self.out,'b02.r01.a01',lambda:None,ledger=self.ledger)
                def post(body,binding):return {'response':{'fixture':True},'receipt':{**binding,'cost_usd':.001}}
                g.execute('b02.r01.a01.A.native','chat',MODEL,wire_payload(),RESERVES['chat'],post)
            still=self.ledger.lookup(NAMESPACE+':b01.r01.a01.A.native');self.assertEqual(still['status'],'pending');self.assertEqual(still['reserved_usd'],.0323104)
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
        self.assertEqual(self.ledger.lookup(rid)['reserved_usd'],.0323104)

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
