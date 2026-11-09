from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from next_batch.oversight_v17.events import Journal, IntegrityError
from next_batch.oversight_v17.finance import Ledger, FundingHold, canonical, units
from next_batch.oversight_v17.openrouter import Client
from next_batch.oversight_v17.monitor import public_packet
from next_batch.oversight_v17.reviewer import (MODEL, profile, validate_binding, EvidenceBundle,
    initial_input, request_payload, reservation, run_review, project_packet, valid_returned_model)


class ReviewerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.journal=Journal(self.root/'journal'); self.bundle=EvidenceBundle(self.journal)
        self.packet=public_packet({'task_id':'fixture-only','instruction':'Repair the fixture.'},[],[],'fixture patch',[])[0]
        self.report={'finding':'no_supported_violation','supported_participants':[],
            'categories':['no_supported_violation'],'evidence':[], 'uncertainty':['Fixture, not empirical evidence.'],
            'repair_requirements':[]}
        output=io.BytesIO();Image.new('RGB',(1024,1024),'white').save(output,format='PNG');self.png=output.getvalue()

    def tearDown(self): self.temp.cleanup()

    def ledger(self,cap=20):
        ledger=Ledger(self.root/'ledger.sqlite')
        ledger.create({'aggregate_units':0,'program_units':0,'period_remaining_units':units(30),'historical_rows':0},cap)
        return ledger

    def response(self,output=None,**changes):
        result={'id':'FIXTURE-API-RESPONSE','model':MODEL,'status':'completed',
            'openrouter_metadata':{'provider_name':'OpenAI','cost':.01},
            'usage':{'input_tokens':100,'output_tokens':500,'output_tokens_details':{'reasoning_tokens':400}},
            'reasoning':{'effort':'max','mode':'pro'},
            'output':output if output is not None else [{'type':'message','content':[{'type':'output_text','text':canonical(self.report)}]}]}
        result.update(changes);return canonical(result).encode()

    def test_primary_binding_is_frontier_vision_at_actual_highest_effort(self):
        config=profile()
        self.assertTrue(validate_binding(config['metadata_evidence']['model'],config['metadata_evidence']['endpoint']))
        model=deepcopy(config['metadata_evidence']['model']);model['reasoning']['supported_efforts'].remove('max')
        with self.assertRaises(FundingHold): validate_binding(model,config['metadata_evidence']['endpoint'])
        self.assertTrue(valid_returned_model('gpt-6-astra-2026-09-03',{'mode':'pro','effort':'max'}))
        self.assertFalse(valid_returned_model('gpt-6-astra-2026-09-03',{'mode':'standard','effort':'max'}))

    def test_responses_wire_uses_max_pro_and_no_unsupported_sampling(self):
        wire=request_payload([{'role':'user','content':'fixture'}])
        self.assertEqual(wire['reasoning']['effort'],'max');self.assertEqual(wire['reasoning']['mode'],'pro')
        self.assertEqual(wire['max_output_tokens'],128000)
        self.assertNotIn('temperature',wire);self.assertNotIn('previous_response_id',wire)
        self.assertFalse(wire['store']);self.assertFalse(wire['provider']['allow_fallbacks'])

    def test_all_current_images_attached_beyond_actor_four_image_limit(self):
        for n in range(6): self.bundle.add('delivered_image',self.png,label='fixture-page-'+str(n))
        items,shown=initial_input(self.packet,self.bundle)
        count=sum(p.get('type')=='input_image' for p in items[0]['content'])
        self.assertEqual(count,6);self.assertEqual(len(shown),6)

    def test_private_objects_and_unavailable_ids_are_rejected(self):
        with self.assertRaises(IntegrityError): self.bundle.add('generator_brief',b'private',label='private')
        with self.assertRaises(IntegrityError): self.bundle.read(['../../secrets'],'full_public')
        packet=deepcopy(self.packet)
        packet['history']=[{'public_packet_sha256':'a'*64,
            'public_documents':[{'generator_brief':'PRIVATE'}],'locked_actions':[]}]
        with self.assertRaises(IntegrityError):initial_input(packet,self.bundle)

    def test_restricted_simulation_actually_removes_actions_and_source(self):
        source=self.bundle.add('source_file',b'PUBLIC-SOURCE',label='base/source.py')
        projected=project_packet(self.packet,'artifact_only')
        self.assertEqual(projected['patch'],'');self.assertEqual(projected['actions'],[])
        with self.assertRaises(IntegrityError): self.bundle.read([source],'artifact_only')

    def test_no_history_condition_removes_both_packet_and_catalog_history(self):
        self.packet['history']=[{'public_packet_sha256':'a'*64,'public_documents':[],'locked_actions':[]}]
        self.bundle.add('public_history',b'PRIOR-PUBLIC',label='prior issue',historical=True)
        items,_=initial_input(self.packet,self.bundle,'no_history')
        self.assertNotIn('PRIOR-PUBLIC',canonical(items));self.assertEqual(project_packet(self.packet,'no_history')['history'],[])
        self.assertEqual(self.bundle.catalog('no_history'),[])

    def test_required_image_reference_without_pixels_blocks_before_any_call(self):
        self.packet['documents']=[{'sender':'A','kind':'work','image':self.journal.put(self.png,'png')}]
        with self.assertRaises(IntegrityError): initial_input(self.packet,self.bundle)

    def test_frontier_reasoning_is_not_billed_at_actor_prices(self):
        cost=reservation(request_payload([{'role':'user','content':'fixture'}]))
        self.assertGreater(float(cost),6.4)
        calls=[]
        client=Client(self.ledger(.10),self.root/'calls',lambda wire:calls.append(wire))
        with self.assertRaises(FundingHold): run_review(client,self.journal,'fixture',self.packet,self.bundle)
        self.assertEqual(calls,[])

    def test_context_overflow_never_silently_truncates(self):
        with self.assertRaises(FundingHold):
            reservation(request_payload([{'role':'user','content':'x'*530000}]))

    def test_first_review_retains_response_cost_and_packet_provenance(self):
        client=Client(self.ledger(),self.root/'calls',lambda wire:self.response())
        result=run_review(client,self.journal,'fixture-review',self.packet,self.bundle)
        self.assertEqual(result['source'],'openrouter_frontier_model')
        self.assertEqual(self.journal.get(result['raw_report']).decode(),canonical(self.report))
        receipt=json.loads(self.journal.get(result['receipt']))
        self.assertEqual(receipt['cost_usd'],.01);self.assertEqual(receipt['endpoint'],'/responses')
        self.assertEqual(receipt['context']['packet_sha256'],result['packet']['sha256'])

    def test_readonly_retrieval_preserves_previous_response_and_actual_source(self):
        ident=self.bundle.add('source_file',b'OBSERVED-PUBLIC-SOURCE',label='base/source.py')
        seen=[]
        first=self.response(output=[{'type':'function_call','name':'read_evidence','call_id':'fixture-call',
            'arguments':canonical({'ids':[ident]})}])
        def transport(wire):
            seen.append(json.loads(wire))
            return first if len(seen)==1 else self.response(id='FIXTURE-API-SECOND')
        client=Client(self.ledger(),self.root/'calls',transport)
        result=run_review(client,self.journal,'fixture-review',self.packet,self.bundle)
        self.assertEqual(len(seen),2);self.assertIn('OBSERVED-PUBLIC-SOURCE',canonical(seen[1]['input']))
        self.assertIn(ident,result['presented_evidence_ids'])
        self.assertEqual(seen[1]['reasoning']['effort'],'max')

    def test_incomplete_first_response_is_retained_without_retry(self):
        seen=[]
        client=Client(self.ledger(),self.root/'calls',lambda wire:seen.append(wire) or self.response(status='incomplete'))
        with self.assertRaises(IntegrityError): run_review(client,self.journal,'fixture-review',self.packet,self.bundle)
        self.assertEqual(len(seen),1);self.assertEqual(client.ledger.summary()['new_committed_usd'],.01)

    def test_contradictory_reasoning_blocks_and_retains_the_bill(self):
        client=Client(self.ledger(),self.root/'calls',lambda wire:self.response(reasoning={'effort':'high','mode':'standard'}))
        with self.assertRaises(IntegrityError): run_review(client,self.journal,'fixture-review',self.packet,self.bundle)
        self.assertEqual(client.ledger.summary()['calls'][0]['status'],'route_hold')


if __name__=='__main__': unittest.main()
