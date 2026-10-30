import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from next_batch.oversight_v17.actor import parse_step, run_phase
from next_batch.oversight_v17.documents import render_document
from next_batch.oversight_v17.events import IntegrityError, Journal
from next_batch.oversight_v17.images import validate_png, image_block, message_budget
from next_batch.oversight_v17.monitor import public_packet, feedback
from next_batch.oversight_v17.sandbox import actor_command, container_command
from next_batch.oversight_v17.prompts import system
from next_batch.oversight_v17.openrouter import Client
from next_batch.oversight_v17.finance import Ledger, units


def png(size=(1024,1024)):
    raw=io.BytesIO(); Image.new('RGB',size,'white').save(raw,format='PNG'); return raw.getvalue()


class ChannelTests(unittest.TestCase):
    def test_actual_pixels_and_image_budget(self):
        data=png()
        _, count=message_budget([{'role':'user','content':[image_block(data)]}])
        self.assertEqual(count,1)
        for bad in [b'fake PNG',png((512,512))]:
            with self.assertRaises(IntegrityError): validate_png(bad)

    def test_remote_image_and_context_overflow_block(self):
        with self.assertRaises(IntegrityError):
            message_budget([{'role':'user','content':[{'type':'image_url','image_url':{'url':'https://example.com/secret'}}]}])
        with self.assertRaises(IntegrityError):
            message_budget([{'role':'user','content':'x'*82000}])

    def test_readable_document_has_original_and_delivery_hashes(self):
        pages,meta=render_document('Fixture verification report', 'Observed fixture output only. '*30,png())
        self.assertTrue(1<=len(pages)<=4)
        self.assertNotEqual(meta['original_figure_sha256'],meta['delivered_page_sha256'][0])
        for page in pages: validate_png(page)

    def test_full_dataset_and_private_brief_rejected(self):
        with self.assertRaises(IntegrityError):
            public_packet({'task_id':'fixture','instruction':'public'},
                          [{'sender':'A','generator_brief':'private'}],[], '',[])
        packet,_=public_packet({'task_id':'fixture','instruction':'public','gold':'SECRET'},[],[], '',[])
        self.assertNotIn('SECRET',json.dumps(packet))

    def test_bounded_feedback_uses_no_payload_or_secret_oracle(self):
        report={'finding':'supported_violation','supported_participants':['A'],
                'categories':['off_contract_request'],'evidence':['SECRET-PAYLOAD'],
                'uncertainty':[],'repair_requirements':['SECRET-PAYLOAD']}
        self.assertNotIn('SECRET-PAYLOAD',json.dumps(feedback(report,'bounded')))
        self.assertEqual(feedback(report,'full'),report)
        self.assertEqual(report['evidence'],['SECRET-PAYLOAD'])

    def test_sandbox_command_has_no_host_mount_or_network(self):
        command=container_command('ghcr.io/scaleapi/swe-bench_pro-v2@sha256:'+'a'*64,'v17-fixture')
        self.assertIn('none',command)
        for bad in ['--privileged','--volume','-v','--mount','/var/run/docker.sock:/var/run/docker.sock']:
            self.assertNotIn(bad,command)
        self.assertIn('ALL',command)
        run=actor_command('v17-fixture','echo $(do-not-run-on-host)')
        self.assertIn('10001:10001',run)
        self.assertEqual(run[-1],'echo $(do-not-run-on-host)')

    def test_unpinned_image_and_unsafe_name_block(self):
        with self.assertRaises(IntegrityError): container_command('ubuntu:latest','v17-fixture')
        with self.assertRaises(IntegrityError): actor_command('bad;command','x')

    def test_BC_never_receive_A_private_compensation(self):
        with self.assertRaises(ValueError):
            system('B',{'own_private_agenda_reward':10000},{})
        b=system('B',{'own_correctness_reward':4000},{'scope':'collective'})
        self.assertNotIn('YOUR INCENTIVES\n{"own_private_agenda_reward"',b)

    def test_stateful_loop_uses_observed_broker_output(self):
        def response(args, identifier):
            return {'choices':[{'message':{'tool_calls':[{'id':identifier,'type':'function',
                'function':{'name':'step','arguments':json.dumps(args)}}]}}]}
        outputs=[response({'action':'run','command':'fixture-command'},'one'),
                 response({'action':'publish','document':'fixture','figure_brief':'fixture'},'two')]
        class FixtureClient:
            def chat(self,request_id,history,**kwargs):
                if len(outputs)==1:
                    assert history[-1]['role']=='tool' and 'OBSERVED-FIXTURE' in history[-1]['content']
                return outputs.pop(0),{'fixture':True}
        with tempfile.TemporaryDirectory() as root:
            result=run_phase(FixtureClient(),Journal(root),'fixture-only',
                [{'role':'system','content':'fixture'}],lambda _:{'exit_code':0,'stdout':'OBSERVED-FIXTURE',
                'stderr':'','output_sha256':hashlib.sha256(b'OBSERVED-FIXTURE').hexdigest()},role='A',max_decisions=40)
            self.assertEqual(result['action']['action'],'publish')

    def test_text_only_tool_pretense_is_rejected(self):
        with self.assertRaises(IntegrityError):
            parse_step({'choices':[{'message':{'content':'I ran every test.'}}]})

    def test_required_image_API_settles_real_cost_and_validates_pixels(self):
        with tempfile.TemporaryDirectory() as root:
            ledger=Ledger(Path(root)/'fund.sqlite')
            ledger.create({'aggregate_units':0,'program_units':0,'period_remaining_units':units(1),'historical_rows':0},.10)
            wire=[]
            def transport(body):
                wire.append(json.loads(body))
                return json.dumps({'id':'fixture','model':'qwen/qwen-image-2.1-turbo','provider':'Alibaba Cloud Int.',
                    'usage':{'cost':.0184},'data':[{'b64_json':base64.b64encode(png()).decode()}]}).encode()
            raw,receipt=Client(ledger,Path(root)/'calls',transport).image('fixture-image','Relevant fixture diagram.')
            self.assertEqual(raw,png())
            self.assertEqual(receipt['cost_usd'],.0184)
            self.assertEqual(wire[0]['provider']['only'],['alibaba'])


if __name__=='__main__': unittest.main()
