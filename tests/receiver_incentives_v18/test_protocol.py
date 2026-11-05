from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from next_batch.oversight_v17.finance import FundingHold, units, canonical
from next_batch.oversight_v17.documents import render_document
from next_batch.receiver_incentives_v18.design import COMMON, ROLE, TOOL, prompt, parse_response, schedule
from next_batch.receiver_incentives_v18.engine import messages
from next_batch.receiver_incentives_v18.funding import StudyLedger
from next_batch.receiver_incentives_v18.provider import Provider


def png():
    b=io.BytesIO();Image.new('RGB',(1024,1024),'white').save(b,format='PNG');return b.getvalue()


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.task={'candidates':[],'policy':{},'work_order':{'deliverables':{'C':'old timing'}}}
    def tearDown(self):self.temp.cleanup()
    def ledger(self,cap=1):
        ledger=StudyLedger(self.root/'budget.sqlite')
        ledger.create({'aggregate_units':units(20),'program_units':units(15),
            'period_remaining_units':units(10),'historical_rows':2091},cap)
        return ledger

    def test_only_one_reward_field_changes_at_randomization(self):
        for role in ['B','C']:
            s0,u0=prompt(role,self.task,'P4',0,[],1);s1,u1=prompt(role,self.task,'P4',4000,[],1)
            self.assertEqual(s0,s1)
            self.assertEqual([k for k in u0 if u0[k]!=u1[k]],['own_correctness_reward'])
            self.assertIn('deliverables',self.task['work_order'])

    def test_replication_pairing_orders_and_audits_are_fixed(self):
        groups=schedule()
        self.assertEqual(len(groups),12);self.assertEqual(sum(g['correctness_reward']==4000 for g in groups),6)
        self.assertEqual(sum(g['audited_case'] is not None for g in groups),6)
        orders=set()
        for pair in sorted(set(g['pair_id'] for g in groups)):
            both=[g for g in groups if g['pair_id']==pair]
            self.assertEqual(both[0]['case_order'],both[1]['case_order'])
            self.assertEqual(both[0]['audited_case'],both[1]['audited_case'])
            orders.add(tuple(both[0]['case_order']))
        self.assertEqual(len(orders),6)

    def test_C_receives_actual_B_pages_without_private_fields(self):
        case={'task':self.task,'target':'P4','pages':[png(),png()]}
        record=messages('C',case,0,[],1,[png()])
        self.assertEqual(sum(p.get('type')=='image_url' for p in record[1]['content']),3)
        self.assertNotIn('private_interpretation',canonical(record))

    def test_invalid_auxiliary_memory_does_not_erase_valid_ballot(self):
        fields={'choice':'P4','interpretation':'x','rationale':'x','public_report':'Useful actual work. '*25,
                'figure_brief':'Task diagram','memory_note':'x'*701}
        result=parse_response({'choices':[{'message':{'tool_calls':[{'function':{
            'name':'record_verifier_decision','arguments':canonical(fields)}}]}}]},'B')
        self.assertEqual(result['choice'],'P4');self.assertFalse(result['memory_available'])
        self.assertFalse(result['valid'])

    def test_model_invalid_output_is_missing_without_reroll(self):
        result=parse_response({'choices':[{'message':{'content':'I will not provide the required record.'}}]},'B')
        self.assertIsNone(result['choice'])

    def test_parallel_pending_reservations_are_fully_counted(self):
        ledger=self.ledger(.10)
        def reserve(n):
            try:ledger.reserve('fixture:'+str(n),'actor',b'fixture',.06);return True
            except FundingHold:return False
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(reserve,[1,2]))
        self.assertEqual(sum(results),1);self.assertEqual(ledger.summary()['new_committed_usd'],.06)

    def test_uncertain_cost_blocks_even_with_other_pending_requests(self):
        ledger=self.ledger()
        ledger.reserve('a','actor',b'a',.01);ledger.reserve('b','image',b'b',.028)
        ledger.uncertain('a')
        with self.assertRaises(FundingHold):ledger.reserve('c','actor',b'c',.01)

    def test_technical_restart_limit_is_append_only(self):
        ledger=self.ledger()
        self.assertTrue(ledger.claim_technical_restart('fault1'))
        self.assertTrue(ledger.claim_technical_restart('fault2'))
        self.assertFalse(ledger.claim_technical_restart('fault3'))

    def test_image_API_missing_identity_is_logged_as_requested_only(self):
        import base64
        raw=canonical({'created':1,'data':[{'b64_json':base64.b64encode(png()).decode()}],
                       'usage':{'cost':.0184,'is_byok':False}}).encode()
        image,receipt=Provider(self.ledger(),self.root/'calls',lambda wire:raw).image('fixture-image','A useful diagram')
        self.assertEqual(image,png());self.assertIsNone(receipt['returned_model'])
        self.assertEqual(receipt['identity_evidence'],'pinned_request_and_free_metadata_only')

    def test_actor_November_price_and_reasoning_are_frozen(self):
        seen=[]
        raw=canonical({'model':'z-ai/glm-5.3-flash','provider':'OpenInference','usage':{'cost':.001}}).encode()
        Provider(self.ledger(),self.root/'calls',lambda wire:seen.append(json.loads(wire)) or raw).chat(
            'fixture-actor',[{'role':'system','content':'fixture'},{'role':'user','content':'fixture'}],tool=TOOL)
        self.assertEqual(seen[0]['provider']['max_price']['completion'],.25)
        self.assertEqual(seen[0]['temperature'],1.)
        self.assertEqual(seen[0]['reasoning']['effort'],'high')
        self.assertEqual(seen[0]['max_tokens'],8192)

    def test_frozen_B_body_envelope_fits_two_readable_pages(self):
        pages,_=render_document('Independent verification memo','Source-grounded verification. '*50,png())
        self.assertLessEqual(len(pages),2)

    def test_tag_and_document_quality_do_not_gate_shared_grant(self):
        self.assertIn('does not require an arbitrary tag',COMMON)
        self.assertIn('not canceled by document-quality',COMMON)


if __name__=='__main__':unittest.main()
