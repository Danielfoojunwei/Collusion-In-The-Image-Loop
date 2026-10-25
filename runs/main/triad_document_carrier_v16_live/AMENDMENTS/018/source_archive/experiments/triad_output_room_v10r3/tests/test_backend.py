"""Actual native-wire and image paths on temporary ledgers; never real API calls."""
from copy import deepcopy
import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image
from visual_collusion.budget import BudgetLedger
from visual_collusion.providers import image_content
from experiments.pressure_interview_v1.budget_guard import SharedBudgetGuard
from experiments.triad_repaired_v8.tests.test_backend import NativeWireTransport, RawAnswer
from experiments.triad_output_room_v10r3.backend import Backend, BackendError, TechnicalFault, NAMESPACE, price_ceiling


def png(color):
    out=io.BytesIO();Image.new('RGB',(1024,1024),color).save(out,'PNG');return out.getvalue()


class WireTransport(NativeWireTransport):
    def __init__(self, ledger, actions):
        super().__init__(ledger,actions)
        self.key.update(limit=40,limit_remaining=40,limit_reset=None)
        self.generated=[];self.invalid_png=False;self.reasoning_usage=None

    def __call__(self, method, path, body):
        result=super().__call__(method,path,body)
        if method=='POST' and path=='/chat/completions':
            payload=json.loads(body);choice=payload.get('tool_choice')
            if isinstance(choice,dict):
                expected=choice.get('function',{}).get('name')
                for call in result.get('choices',[{}])[0].get('message',{}).get('tool_calls') or []:
                    try:args=json.loads(call['function']['arguments'])
                    except (ValueError,TypeError):continue
                    if isinstance(args,dict) and set(args)=={'action','arguments'} and args['action']==expected:
                        call['function']['arguments']=json.dumps(args['arguments'],ensure_ascii=False)
        if method=='POST' and path=='/chat/completions' and self.reasoning_usage is not None:
            reason,total=self.reasoning_usage
            result['usage'].update(completion_tokens=total,completion_tokens_details={'reasoning_tokens':reason})
        if method=='POST' and path=='/images':
            raw=png((len(self.generated)*70,40,100))
            self.generated.append(raw)
            result['data'][0]['b64_json']=base64.b64encode(b'bad png' if self.invalid_png else raw).decode()
        return result


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.ledger=BudgetLedger(self.root/'budget.sqlite3')
        self.config=json.loads((Path(__file__).resolve().parents[3]/'research_plans/triad_output_room_v10r3/config.json').read_text())

    def tearDown(self):self.tmp.cleanup()

    def backend(self, actions):
        guard=SharedBudgetGuard(self.ledger.path,NAMESPACE,ledger=self.ledger)
        self.transport=WireTransport(self.ledger,actions)
        return Backend(self.config,self.root/'run',self.ledger.path,guard=guard,
            transport=self.transport,api_key='OFFLINE_FIXTURE_NOT_A_SECRET')

    def request(self,role='A',mode='image',refs=None):
        result={'request_id':'r01.a01.'+role+'.'+mode,'round':1,'role':role,'mode':mode,
            'messages':[{'role':'system','content':'Use the current synthetic task.'},{'role':'user','content':'Current task and private role view.'}],
            'image_refs':refs or {},'orchestrator_only':'NEVER_IN_MODEL_CONTEXT'}
        for handle,d in (refs or {}).items():
            result['messages'].append({'role':'user','content':[{'type':'text','text':handle},image_content(Path(d['path']).read_bytes())]})
        return result

    @staticmethod
    def act(name,**args):return {'action':name,'arguments':args}

    def chats(self):return [json.loads(raw) for endpoint,raw in self.transport.posts_exact if endpoint=='/chat/completions']

    def descriptor(self,name,color):
        path=self.root/(name+'.png');raw=png(color);path.write_bytes(raw)
        return {'path':str(path),'sha256':hashlib.sha256(raw).hexdigest()}

    def test_reasoning8192_plus_required_output_fits_new_ceiling_and_real_receipts(self):
        backend=self.backend([self.act('create_visual',prompt='Synthetic image brief.'),self.act('accept_visual',draft_id='D1')])
        self.transport.reasoning_usage=(8192,8492)
        result=backend.perform(self.request())
        self.assertEqual(result['component_status']['image'],'delivered')
        self.assertEqual(backend.provider._preflight['input_limits'][self.config['models']['A']]['output_tokens'],16384)
        for chat in self.chats():
            self.assertEqual(chat['max_tokens'],16384)
            self.assertEqual(chat['temperature'],1.0)
            self.assertEqual(chat['reasoning']['effort'],'high')
        chats=[r for r in result['receipts'] if r['kind']=='chat']
        self.assertEqual(chats[0]['usage']['completion_tokens'],8492)
        self.assertEqual(chats[0]['usage']['completion_tokens_details']['reasoning_tokens'],8192)
        self.assertGreater(chats[0]['reserved_usd'],16384*0.0000005)

    def test_length_exhaustion_still_preserved_without_invented_image_or_retry(self):
        backend=self.backend([RawAnswer({'role':'assistant','content':None},finish_reason='length')])
        self.transport.reasoning_usage=(16384,16384)
        result=backend.perform(self.request())
        self.assertEqual(result['component_status']['image'],'model_invalid')
        self.assertIsNone(result['output']['image'])
        self.assertEqual(len(self.transport.posts_exact),1)
        self.assertEqual(result['receipts'][0]['usage']['completion_tokens'],16384)

    def test_free_preflight_rejects_endpoint_too_small_for_new_ceiling(self):
        backend=self.backend([])
        for group in self.transport.chat_endpoints.values():
            for endpoint in group['data']['endpoints']:endpoint['max_completion_tokens']=8192
        from visual_collusion.providers import PreflightError
        with self.assertRaises(PreflightError):backend.preflight()
        self.assertEqual(len(self.transport.posts_exact),0)

    def test_two_fresh_images_and_explicit_first_selection_are_actual_and_cached(self):
        backend=self.backend([self.act('create_visual',prompt='First independent idea.'),
            self.act('create_visual',prompt='Entirely different second idea.'),self.act('accept_visual',draft_id='D1')])
        req=self.request();result=backend.perform(req)
        self.assertEqual(result['component_status']['image'],'delivered')
        self.assertEqual(Path(result['output']['image']['path']).read_bytes(),self.transport.generated[0])
        self.assertNotEqual(self.transport.generated[0],self.transport.generated[1])
        self.assertEqual([a['action'] for a in result['actions']],['create_visual','create_visual','accept_visual'])
        self.assertEqual(result['actions'][2]['selected_draft_id'],'D1')
        self.assertEqual(result['actions'][1]['generation_attempt'],2)
        self.assertEqual(backend.perform(req),result)
        self.assertEqual(len(self.transport.posts_exact),5)
        for field in ('visual_temperature','vote_temperature','log_temperature','actual_v10_temperature'):
            self.assertEqual(backend.provider._preflight['sampling'][field],1.0)
        for chat in self.chats():
            self.assertEqual(chat['temperature'],1.0)
            self.assertEqual(chat['reasoning'],{'effort':'high','exclude':False})
            self.assertEqual(chat['max_tokens'],16384)
            self.assertEqual(chat['provider']['max_price'],price_ceiling())
            self.assertFalse(chat['provider']['allow_fallbacks'])
            self.assertNotIn('NEVER_IN_MODEL_CONTEXT',json.dumps(chat))
            self.assertIn('Mechanical output budget',chat['messages'][0]['content'])
        image_records=[json.loads(f.read_text()) for f in (backend.evidence_dir/'image_calls').glob('*.json')]
        self.assertEqual([r['reference_sha256'] for r in image_records],[None,None])

    def test_revision_uses_named_original_own_draft(self):
        backend=self.backend([self.act('create_visual',prompt='First.'),
            self.act('revise_visual',source_draft_id='D1',prompt='Revision.'),self.act('accept_visual',draft_id='D2')])
        result=backend.perform(self.request())
        self.assertEqual(result['component_status']['image'],'delivered')
        records=[json.loads(f.read_text()) for f in (backend.evidence_dir/'image_calls').glob('*.json')]
        refs=[r['reference_sha256'] for r in records if r['reference_sha256']]
        self.assertEqual(refs,[hashlib.sha256(self.transport.generated[0]).hexdigest()])

    def test_B_valid_ballot_survives_bad_interpretation_without_reroll(self):
        backend=self.backend([{'decision':'P3','inferred_target':'unsupported','confidence':.5,'visual_basis':'Observed pixels.'}])
        result=backend.perform(self.request('B','vote'))
        self.assertEqual(result['output']['decision'],'P3')
        self.assertEqual(result['component_status']['decision'],'valid')
        self.assertEqual(result['component_status']['interpretation'],'model_invalid')
        self.assertIsNone(result['output']['private_interpretation'])
        self.assertEqual(len(self.transport.posts_exact),1)

    def test_ASCII_basis_contract_and_raw_nonASCII_preserve_actual_vote(self):
        for index,(basis,expected) in enumerate([('x'*500,'valid'),('解'*166+'ab','model_invalid'),('解'*167,'model_invalid')]):
            backend=self.backend([{'decision':'P3','inferred_target':'P2','confidence':.5,'visual_basis':basis}])
            request=self.request('B','vote');request['request_id']+=f'.u{index}'
            result=backend.perform(request)
            self.assertEqual(result['component_status']['decision'],'valid')
            self.assertEqual(result['component_status']['interpretation'],expected)
            self.assertEqual(result['output']['decision'],'P3')
            self.assertEqual(result['output']['raw_proposal']['visual_basis'],basis)
            self.assertEqual(len(self.transport.posts_exact),1)
            if expected=='model_invalid':self.assertIsNone(result['output']['private_interpretation'])
            tool=self.chats()[0]['tools'][0]['function']['parameters']['properties']['visual_basis']
            self.assertIn('UTF8 bytes',tool['description'])

    def test_ASCII_log_limits_and_nonASCII_rejection_without_truncation(self):
        rows=[('x'*400,'x'*800,'valid'),('x'*399+'≠','x','model_invalid'),('é'*200,'解'*266+'ab','model_invalid'),('é'*201,'x','model_invalid'),
              ('x','解'*267,'model_invalid')]
        for index,(note,reflection,expected) in enumerate(rows):
            backend=self.backend([{'carryover_note':note,'image_reflection':reflection}])
            request=self.request('A','log');request['request_id']+=f'.u{index}'
            result=backend.perform(request)
            self.assertEqual(result['component_status']['log'],expected)
            self.assertEqual(result['output']['raw_proposal'],{'carryover_note':note,'image_reflection':reflection})
            self.assertEqual(len(self.transport.posts_exact),1)
            if expected=='valid':
                self.assertEqual(result['output']['carryover_note'],note)
                self.assertEqual(result['output']['image_reflection'],reflection)
            else:self.assertEqual(result['output']['carryover_note'],'')
            import re
            props=self.chats()[0]['tools'][0]['function']['parameters']['properties']
            for key,limit in (('carryover_note',400),('image_reflection',800)):
                self.assertEqual(props[key]['maxLength'],limit)
                self.assertIsNotNone(re.fullmatch(props[key]['pattern'],'x'*limit))
                self.assertIsNone(re.fullmatch(props[key]['pattern'],'x'*(limit-1)+'≠'))

    def test_maximum_unicode_briefs_preserve_raw_calls_but_normalize_equivalent_history(self):
        brief='🧪'*3000
        originals=[]
        actions=[]
        for index in (1,2):
            argument_string=json.dumps({'prompt':brief},ensure_ascii=True,indent=16)
            originals.append(argument_string)
            actions.append(RawAnswer({'role':'assistant','content':None,'tool_calls':[{
                'index':0,'id':f'escaped-{index}','type':'function',
                'function':{'name':'create_visual','arguments':argument_string}}]}))
        actions.append(self.act('accept_visual',draft_id='D1'))
        backend=self.backend(actions);request=self.request()
        request['messages'][1]['content']='x'*90000
        result=backend.perform(request)
        self.assertEqual(result['component_status']['image'],'delivered')
        self.assertEqual(len(self.transport.posts_exact),5)
        for index,action in enumerate(result['actions'][:2]):
            normalization=action['working_history_argument_normalization']
            self.assertGreater(normalization['original_arguments_utf8_bytes'],normalization['canonical_arguments_utf8_bytes'])
            self.assertTrue(normalization['decoded_arguments_unchanged'])
            self.assertEqual(action['arguments']['prompt'],brief)
            saved=json.loads((backend.evidence_dir/'calls'/(hashlib.sha256(action['request_id'].encode()).hexdigest()+'.json')).read_text())
            self.assertEqual(saved['native_message']['tool_calls'][0]['function']['arguments'],originals[index])
        for chat in self.chats()[1:]:
            for message in chat['messages']:
                for call in message.get('tool_calls',[]):
                    self.assertEqual(json.loads(call['function']['arguments']),{'prompt':brief})
                    self.assertLess(len(call['function']['arguments'].encode()),13000)

    def test_A_proposal_is_not_assigned_or_rewritten_by_backend(self):
        backend=self.backend([{'decision':'P4'}]);result=backend.perform(self.request('A','vote'))
        self.assertEqual(result['output']['decision'],'P4')
        self.assertEqual(result['output']['raw_proposal'],{'decision':'P4'})
        self.assertNotIn('binding_choice',result['output'])
        self.assertEqual(len(self.transport.posts_exact),1)

    def test_malformed_visual_brief_consumes_one_slot_and_terminates_without_image(self):
        backend=self.backend([self.act('create_visual',prompt='x'*3001)])
        result=backend.perform(self.request())
        self.assertEqual(result['component_status']['image'],'model_invalid')
        self.assertEqual(len(result['actions']),1)
        self.assertEqual(len(self.transport.posts_exact),1)
        self.assertEqual(self.transport.generated,[])

    def test_native_refusal_is_retained_without_a_replacement_sample(self):
        backend=self.backend([RawAnswer({'role':'assistant','content':None,'refusal':'No vote.'})])
        result=backend.perform(self.request('C','vote'))
        self.assertEqual(result['component_status']['decision'],'refused')
        self.assertEqual(len(self.transport.posts_exact),1)
        self.assertIsNone(result['output']['decision'])

    def test_decline_tool_is_not_advertised_and_cannot_complete_required_delivery(self):
        backend=self.backend([self.act('decline_visual',reason='I choose no image for this turn.')])
        result=backend.perform(self.request())
        self.assertEqual(result['status'],'model_invalid')
        self.assertEqual(result['component_status']['image'],'model_invalid')
        self.assertIsNone(result['output']['image'])
        self.assertEqual(len(self.transport.posts_exact),1)
        self.assertTrue(all(t['function']['name']!='decline_visual' for t in self.chats()[0]['tools']))

    def test_unusable_provider_PNG_is_typed_technical_fault_not_actor_failure(self):
        backend=self.backend([self.act('create_visual',prompt='An actual generation.')]);self.transport.invalid_png=True
        with self.assertRaises(TechnicalFault) as caught:backend.perform(self.request())
        self.assertEqual(caught.exception.fault,'invalid_provider_png')
        self.assertEqual(len(self.transport.posts_exact),2)
        self.assertEqual(self.ledger.summary()['completed_count'],2)

    def test_C_return_can_reinspect_two_peers_plus_latest_own_draft(self):
        a=self.descriptor('a',(1,2,3));b=self.descriptor('b',(8,9,10))
        backend=self.backend([self.act('create_visual',prompt='Return image.'),self.act('accept_visual',draft_id='D1')])
        result=backend.perform(self.request('C','image',{'current_A':a,'current_B':b}))
        self.assertEqual(result['component_status']['image'],'delivered')
        counts=[sum(p.get('type')=='image_url' for m in chat['messages'] if isinstance(m.get('content'),list) for p in m['content']) for chat in self.chats()]
        self.assertEqual(counts,[2,3])

    def test_descriptor_and_route_leaks_fail_before_any_paid_dispatch(self):
        image=self.descriptor('peer',(20,30,40));backend=self.backend([])
        bad=self.request('B','image',{'previous_C':image})
        with self.assertRaises(BackendError):backend.perform(bad)
        bad=self.request('B','image',{'current_A':image});bad['image_refs']['current_A']['sha256']='0'*64
        with self.assertRaises(ValueError):backend.perform(bad)
        self.assertEqual(self.transport.posts_exact,[])


if __name__=='__main__':unittest.main()
