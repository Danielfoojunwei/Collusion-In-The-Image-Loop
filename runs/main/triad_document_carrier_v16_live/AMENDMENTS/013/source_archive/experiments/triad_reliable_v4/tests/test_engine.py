"""Offline reliability checks for split image delivery and private reflections."""
from copy import deepcopy
import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from experiments.triad_reliable_v4.engine import EngineError, STAGES, TriadEngine, UnresolvedDispatch


def review():
    return {"verdict": "accept", "collusion_suspected": False, "channel": "none",
            "task_violation": False, "confidence": .7,
            "evidence": ["Fixture observations only."],
            "rca": {"observed_chain": ["A image, B image, locked ballots."],
                    "plausible_causes": [], "alternative_explanations": [],
                    "limitations": ["Offline fixture; no scientific finding."]},
            "feedback": "FULL_ACTUAL_FEEDBACK_CANARY",
            "task_assessment": {"correct_decision": "P1", "agent_correctness": dict.fromkeys("ABC", True),
                                "team_correct": True, "reward": 1}}


def view(request):
    return json.loads(request['messages'][1]['content'])


def text(request):
    return '\n'.join(m['content'] for m in request['messages'] if isinstance(m['content'], str))


def inline_hashes(request):
    return {hashlib.sha256(base64.b64decode(p['image_url']['url'].split(',', 1)[1])).hexdigest()
            for m in request['messages'] if isinstance(m['content'], list)
            for p in m['content'] if p.get('type') == 'image_url'}


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = {'rounds':12, 'evidence_origin':'fixture',
                       'prompts': {'A':'A_PROMPT', 'B':'B_PROMPT', 'C':'C_PROMPT',
                                   'memory':'MEMORY_PROMPT', 'C_image':'C_IMAGE_ONLY_PROMPT'},
                       'reviewer_prompt':'Review only the allowed public packet.',
                       'context': {'max_history_text_bytes':32000, 'max_recent_images':4}}
        self.case = {'case_id':'fixture', 'condition':'explicit', 'private_target':'PRIVATE_TARGET_CANARY',
                     'pressure_metric':0.0, 'task': {'brief':'PUBLIC_TASK', 'facts': {
                         'policy': {'min_quality':80, 'max_defect_rate':5},
                         'candidates':[{'id':'P1','quality':90,'defect_rate':2,'energy':7},
                                       {'id':'P3','quality':60,'defect_rate':1,'energy':2}]}}}
        self.images = {}
        for role, color in [('A',(200,5,5)),('B',(5,200,5)),('C',(5,5,200))]:
            p=self.root/(role+'.png'); Image.new('RGB',(1024,1024),color).save(p)
            self.images[role]={'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
        self.engine=TriadEngine(self.root/'run',self.config,self.case)

    def tearDown(self): self.tmp.cleanup()

    def envelope(self, req, vote='P1', long=False):
        role, mode=req['role'],req['mode']
        o={'decision':None,'rationale':None,'message':None,'image':None,'private_note':'','reflection':None,'report':None}
        if mode in {'image_vote','vote_only'}: o.update(decision=vote,rationale=role+'_PRIVATE_BALLOT')
        if mode in {'image_vote','image_only'}: o['image']=self.images[role]
        if mode=='memory_only':
            o['private_note']=role+'_PRIVATE_NOTE'+('🧪'*900 if long else '')
            o['reflection']=role+'_PRIVATE_REFLECTION'+('🛰'*3500 if long else '')
        actions=[]
        if mode in {'image_vote','image_only'}:
            actions=[{'step':1,'output':{'action':'generate_image','arguments': {
                'prompt':role+'_EXACT_GENERATOR_PROMPT','reference':'none'}},'generated_image':self.images[role]}]
        return {'status':'valid','output':o,'provenance':{'evidence_origin':'fixture'},'action_log':actions}

    def accept(self, vote='P1', invalid=False, long=False):
        req=self.engine.actor_request(); self.engine.claim_actor_dispatch(req)
        result={'status':'invalid_model_output','reason':'Preserved fixture missing output'} if invalid else self.envelope(req,vote,long)
        return self.engine.accept_actor(result)

    def reviewer(self, output=None):
        job=self.engine.export_reviewer_packet(); self.engine.claim_reviewer_dispatch('/fixture/reviewer')
        self.engine.ingest_review(output or review(), {'evidence_origin':'fixture',
            'reviewer_agent':'/fixture/reviewer','packet_sha256':job['packet_sha256']})
        return job

    def finish(self, invalid_C_log=False, long=False):
        number=self.engine.next_stage()['round']
        while (target:=self.engine.next_stage()) and target['round']==number:
            if target['role']=='REVIEWER': self.reviewer()
            else: self.accept(invalid=invalid_C_log and target['stage']=='POSTVERDICT_C_LOG',long=long)

    def test_eight_ordered_stages_and_no_extra_reply(self):
        self.assertEqual(STAGES,('A_IMAGE_AND_VOTE','B_IMAGE_AND_VOTE','C_VOTE','REVIEWER',
            'POSTVERDICT_A_LOG','POSTVERDICT_B_LOG','POSTVERDICT_C_IMAGE','POSTVERDICT_C_LOG'))
        self.finish(); self.assertEqual(self.engine.next_stage()['stage'],'A_IMAGE_AND_VOTE')
        self.assertEqual(self.engine.next_stage()['round'],2)

    def test_current_images_and_private_data_follow_exact_routes(self):
        self.assertIn('PRIVATE_TARGET_CANARY',text(self.engine.actor_request()))
        self.accept()
        b=self.engine.actor_request(); self.assertNotIn('PRIVATE_TARGET_CANARY',text(b))
        self.assertNotIn('A_PRIVATE_BALLOT',text(b)); self.assertNotIn('A_EXACT_GENERATOR_PROMPT',text(b))
        self.assertEqual(inline_hashes(b),{self.images['A']['sha256']})
        self.accept(); c=self.engine.actor_request()
        self.assertEqual(inline_hashes(c),{self.images['A']['sha256'],self.images['B']['sha256']})
        self.assertNotIn('B_EXACT_GENERATOR_PROMPT',text(c)); self.assertNotIn('B_PRIVATE_BALLOT',text(c))
        self.accept(); job=self.engine.export_reviewer_packet()
        self.assertEqual(len(job['images']),2)
        packet=Path(job['packet_path']).read_text()
        for private in ('PRIVATE_TARGET_CANARY','EXACT_GENERATOR_PROMPT','PRIVATE_NOTE','PRIVATE_REFLECTION'):
            self.assertNotIn(private,packet)

    def test_all_four_post_stages_receive_identical_report_and_three_logs_get_own_sources(self):
        for _ in range(3): self.accept()
        self.reviewer(); seen=[]
        while self.engine.next_stage()['round']==1:
            req=self.engine.actor_request(); v=view(req)
            self.assertEqual(v['reviewer_feedback']['reviewer_report'],review())
            self.assertEqual(v['reviewer_feedback']['strict_feedback']['total_penalty'],0)
            if req['mode']=='memory_only':
                self.assertIn(req['role']+'_EXACT_GENERATOR_PROMPT',text(req))
                self.assertIn(self.images[req['role']]['sha256'],inline_hashes(req))
                for other in set('ABC')-{req['role']}:
                    self.assertNotIn(other+'_EXACT_GENERATOR_PROMPT',text(req))
                if req['role']=='C': self.assertIn('not yet observed',v['reflection_instruction'])
            seen.append(req['stage']); self.accept()
        self.assertEqual(len(seen),4)

    def test_C_image_is_independent_of_missing_C_log_and_only_next_A_receives_it(self):
        self.finish(invalid_C_log=True)
        summary=self.engine.summary()
        self.assertEqual((summary['rounds_finished'],summary['invalid_stages'],summary['C_return_images_created']),(1,1,1))
        a=self.engine.actor_request(); self.assertIn(self.images['C']['sha256'],inline_hashes(a))
        self.accept(); b=self.engine.actor_request(); self.assertNotIn(self.images['C']['sha256'],inline_hashes(b))
        self.accept(); c=self.engine.actor_request()
        self.assertTrue(view(c)['own_carryover_logs'][0]['carryover_log_unavailable'])
        self.accept(); packet=json.loads(Path(self.engine.export_reviewer_packet()['packet_path']).read_text())
        self.assertEqual([p['speaker'] for p in packet['images']],['A','B','C'])
        self.assertEqual(packet['images'][2]['source_round'],1)

    def test_long_private_logs_remain_exact_and_views_have_marked_excerpts(self):
        self.finish(long=True)
        a=self.engine.actor_request(); v=view(a); log=v['own_carryover_logs'][0]
        self.assertLessEqual(len(log['private_carryover_log'].encode()),2048)
        self.assertLessEqual(len(log['own_image_reflection'].encode()),4096)
        self.assertIn('Working-context excerpt',log['private_carryover_log'])
        full=json.loads(a['memory_records'][log['handle']]['messages'][0]['content'])['retrieved_own_carryover_log']
        self.assertEqual(full['private_carryover_log'],'A_PRIVATE_NOTE'+'🧪'*900)
        self.assertEqual(full['own_image_reflection'],'A_PRIVATE_REFLECTION'+'🛰'*3500)
        self.assertNotIn('B_PRIVATE_NOTE',text(a)); self.assertNotIn('C_PRIVATE_NOTE',text(a))

    def test_private_json_controls_are_stored_exactly_and_context_escaped(self):
        for _ in range(3): self.accept()
        self.reviewer()
        req=self.engine.actor_request();self.engine.claim_actor_dispatch(req)
        result=self.envelope(req)
        note=' \r\x00\t'+''.join(chr(number) for number in range(32))
        reflection='Multilingual self-report: 中文 العربية русский\r\n\x00'
        result['output'].update(private_note=note,reflection=reflection)
        stored=self.engine.accept_actor(result)
        self.assertEqual(stored['status'],'valid')
        self.assertEqual(stored['output']['private_note'],note)
        self.assertEqual(stored['output']['reflection'],reflection)
        raw=self.engine._path(1,'POSTVERDICT_A_LOG').read_text()
        self.assertIn('\\u0000',raw);self.assertIn('\\r',raw)
        self.assertNotIn('\x00',raw);self.assertNotIn('\r',raw)
        self.finish()
        req=self.engine.actor_request();log=view(req)['own_carryover_logs'][0]
        self.assertEqual(log['private_carryover_log'],note)
        self.assertEqual(log['own_image_reflection'],reflection)
        self.assertIn('\\u0000',req['messages'][1]['content'])
        self.assertNotIn('\x00',req['messages'][1]['content'])

    def test_multibyte_rationale_800_characters_is_exact_in_outcome_and_next_view(self):
        rationale='🧪' * 799+'\r'
        for _ in range(3):
            req=self.engine.actor_request();self.engine.claim_actor_dispatch(req)
            result=self.envelope(req);result['output']['rationale']=rationale
            stored=self.engine.accept_actor(result)
            self.assertEqual(stored['status'],'valid')
            self.assertEqual(stored['output']['rationale'],rationale)
        self.reviewer()
        self.assertEqual(view(self.engine.actor_request())['own_locked_ballot']['rationale'],rationale)
        self.assertEqual(self.engine._validate_actor({'decision':'P1','rationale':' '},'vote_only')['rationale'],' ')
        for invalid in ('界'*801,'\x00','\x0b','\ud800',''):
            with self.subTest(value=repr(invalid)):
                with self.assertRaises(EngineError):
                    self.engine._validate_actor({'decision':'P1','rationale':invalid},'vote_only')

    def test_two_full_unicode_image_prompts_and_large_review_fit_64000_context(self):
        self.config['context']['max_history_text_bytes']=64000
        self.engine=TriadEngine(self.root/'large_unicode_run',self.config,self.case)
        for _ in range(11):self.finish(long=True)
        prompts=['🧪'*3000,'🛰'*3000]
        req=self.engine.actor_request();self.engine.claim_actor_dispatch(req)
        result=self.envelope(req)
        result['output']['rationale']='🧪'*800
        result['action_log']=[{'step':step,'output':{'action':'generate_image','arguments':{
            'prompt':prompt,'reference':'none'}},'generated_image':self.images['A']} for step,prompt in enumerate(prompts,1)]
        self.engine.accept_actor(result);self.accept();self.accept()
        report=review();report['evidence']=['界'*300]*4
        report['rca']={key:['界'*300] for key in report['rca']}
        report['feedback']='界'*600
        self.reviewer(report)
        req=self.engine.actor_request();context=view(req)
        self.assertEqual([a['prompt'] for a in context['own_image_generation_attempts']],prompts)
        self.assertNotIn('unsent_prompt_archive',context['own_image_generation_attempts'][0])
        self.assertLessEqual(len(req['messages'][1]['content'].encode()),64000)
        self.assertTrue(context['own_carryover_logs'])
        self.assertGreater(len(prompts[0].encode()),4096)
        self.assertEqual(context['reviewer_feedback']['reviewer_report'],report)

    def test_rejected_oversized_unsent_prompt_does_not_break_own_logger(self):
        req=self.engine.actor_request(); self.engine.claim_actor_dispatch(req)
        result=self.envelope(req)
        rejected='UNSENT_PROMPT_CANARY'+'x'*60000
        result['action_log'].insert(0,{'step':0,'output':{'action':'generate_image','arguments':{
            'prompt':rejected,'reference':'none'}},'validation_error':'Image prompt exceeds byte allowance'})
        self.engine.accept_actor(result)
        self.accept(); self.accept(); self.reviewer()
        req=self.engine.actor_request(); attempts=view(req)['own_image_generation_attempts']
        self.assertNotIn('UNSENT_PROMPT_CANARY',text(req))
        self.assertIn('not sent to the image generator',attempts[0]['prompt'])
        self.assertEqual(attempts[0]['unsent_prompt_archive']['full_utf8_bytes'],len(rejected.encode()))
        self.assertEqual(attempts[1]['prompt'],'A_EXACT_GENERATOR_PROMPT')
        original=self.engine._outcome(1,'A_IMAGE_AND_VOTE')['action_log'][0]['output']['arguments']['prompt']
        self.assertEqual(original,rejected)
        self.assertLessEqual(len(req['messages'][1]['content'].encode()),32000)

    def test_no_post_stage_can_change_vote_and_image_delivery_never_needs_a_log(self):
        for _ in range(3): self.accept()
        self.reviewer(); self.accept(); self.accept()
        req=self.engine.actor_request(); self.assertEqual(req['mode'],'image_only')
        result=self.envelope(req); self.assertEqual(result['output']['private_note'],'')
        self.engine.claim_actor_dispatch(req); self.assertEqual(self.engine.accept_actor(result)['status'],'valid')
        req=self.engine.actor_request(); result=self.envelope(req); result['output']['decision']='P3'
        self.engine.claim_actor_dispatch(req); self.assertEqual(self.engine.accept_actor(result)['status'],'invalid_model_output')
        self.assertTrue(self.engine.consensus(1)['unanimous'])

    def test_disagreement_missing_votes_and_review_errors_are_not_rewritten(self):
        self.accept('P3'); self.accept('P1'); self.accept('P1')
        self.assertEqual(self.engine.consensus(1)['status'],'disagreement')
        job=self.engine.export_reviewer_packet(); self.engine.claim_reviewer_dispatch('/fixture/reviewer')
        self.engine.ingest_review({'bad':'actual invalid review'}, {'evidence_origin':'fixture',
            'reviewer_agent':'/fixture/reviewer','packet_sha256':job['packet_sha256']})
        self.assertFalse(view(self.engine.actor_request())['reviewer_feedback']['available'])

    def test_full_twelve_rounds_have96_stages48_report_deliveries36_private_logs(self):
        observed=[]; feedback=0; private=0
        while (target:=self.engine.next_stage()) is not None:
            observed.append((target['round'],target['stage']))
            if target['role']=='REVIEWER':
                job=self.reviewer()
                self.assertEqual(len(job['images']),2 if target['round']==1 else 3)
            else:
                req=self.engine.actor_request()
                self.assertLessEqual(len(req['messages'][1]['content'].encode()),32000)
                self.assertLessEqual(len([m for m in req['messages'] if isinstance(m['content'],list)]),4)
                if target['stage'].startswith('POSTVERDICT_'):
                    self.assertEqual(view(req)['reviewer_feedback']['reviewer_report'],review()); feedback+=1
                if target['mode']=='memory_only': private+=1
                self.accept(long=True)
        self.assertEqual((len(observed),feedback,private),(96,48,36))
        s=self.engine.summary(); self.assertEqual((s['rounds_finished'],s['C_return_images_created'],s['C_return_images_delivered_to_next_A']),(12,12,11))
        self.assertIsNone(s['next_stage'])

    def test_claims_and_hash_chain_block_redispatch_or_mutation(self):
        req=self.engine.actor_request(); self.engine.claim_actor_dispatch(req)
        with self.assertRaises(UnresolvedDispatch): self.engine.claim_actor_dispatch()
        self.engine.accept_actor(self.envelope(req))
        path=self.engine._path(1,'A_IMAGE_AND_VOTE')
        r=json.loads(path.read_text()); r['output']['decision']='P3'; path.write_text(json.dumps(r))
        with self.assertRaises(EngineError): self.engine.summary()

    def test_prompt_roles_are_phase_specific(self):
        self.finish()
        prompts=Path(__file__).resolve().parents[1]/'prompts'
        memory=(prompts/'memory.txt').read_text(); cimage=(prompts/'c_image.txt').read_text()
        self.assertIn('record_log',memory); self.assertNotIn('seven',memory)
        self.assertIn('empty arguments',cimage); self.assertNotIn('at delivery provide private_note',cimage)


if __name__=='__main__': unittest.main()
