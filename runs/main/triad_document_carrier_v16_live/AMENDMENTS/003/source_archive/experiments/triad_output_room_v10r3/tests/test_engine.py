"""Scripted OFFLINE fixtures only: no paid producer or actual reviewer evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image
from research_plans.triad_output_room_v10r3.commitment import build_cases
from experiments.triad_output_room_v10r3.engine import (
    Engine, EngineError, PendingOperation, STAGES, canonical, file_sha, read_record,
)
from experiments.triad_output_room_v10r3 import reviewer


def mock_result(request,out,plan,*,long=False,invalid=False,bad_inference=False):
    """Clearly scripted data, never a model observation or live finding."""
    role,mode,number=request['role'],request['mode'],request['round']
    case=plan[number-1]
    output={'image':None,'decision':None,'raw_proposal':None,'private_interpretation':None,
            'carryover_note':'','image_reflection':''}
    components=dict.fromkeys(('image','decision','interpretation','log'),'not_applicable')
    actions=[]
    if mode=='image':
        components['image']='model_invalid' if invalid else 'delivered'
        if not invalid:
            path=Path(out)/'fixture_pixels'/f"{request['request_id']}.png"
            path.parent.mkdir(parents=True,exist_ok=True)
            Image.new('RGB',(1024,1024),(number,ord(role),127)).save(path)
            output['image']={'path':str(path),'sha256':file_sha(path)}
            for index in range(2 if long else 1):
                actions.append({'phase':'visual','action':'create_visual','slot':index+1,
                    'arguments':{'prompt':'🌀'*3000 if long else 'SCRIPTED OFFLINE FIXTURE'},
                    'generation_attempt':index+1,'draft_id':f'D{index+1}',
                    'generated_image':deepcopy(output['image'])})
    elif mode=='vote':
        components['decision']='model_invalid' if invalid else 'valid'
        if role in 'BC':components['interpretation']='model_invalid' if invalid or bad_inference else 'valid'
        if not invalid:
            decision=case['oracle'] if role=='A' else case['private_target']
            output['decision']=decision
            raw={'decision':decision}
            if role in 'BC':
                raw.update(inferred_target=case['private_target'],confidence=.75,
                           visual_basis='x'*500 if long else 'Scripted offline fixture, not an observed decoder.')
                if bad_inference:raw['confidence']='invalid'
                else:output['private_interpretation']={k:v for k,v in raw.items() if k!='decision'}
            output['raw_proposal']=raw
    else:
        components['log']='model_invalid' if invalid else 'valid'
        if not invalid:
            output.update(carryover_note='x'*400 if long else f'PRIVATE {role} note for round {number}',
                          image_reflection='x'*800 if long else 'SCRIPTED OFFLINE FIXTURE reflection.')
    good=any(v in {'valid','delivered'} for v in components.values())
    return {'status':'partial' if bad_inference and good else 'valid' if good else 'model_invalid',
            'output':output,'component_status':components,'actions':actions,'receipts':[]}


def commit_actor(engine,*,invalid=False,bad_inference=False,long=False):
    request=engine.actor_request()
    engine.begin_actor_request(request)
    result=mock_result(request,engine.out,engine.plan,invalid=invalid,bad_inference=bad_inference,long=long)
    engine.commit_actor_result(request,result)
    return request,result


def review_fixture(engine,*,invalid=False,long=False):
    """Exercise the actual bridge with labeled synthetic unit-test provenance."""
    packet_data=engine.review_packet_data()
    job=reviewer.prepare_packet(engine.out,packet_data)
    reviewer.record_launch(engine.out,job['job_id'],'/root/offline_fixture_reviewer')
    packet=json.loads(Path(job['packet_path']).read_text())
    report={'verdict':'revise','task_assessment':reviewer.task_assessment(packet),
        'unilateral_attempts':[],
        'image_factual_observations':[{'image_file':r['file'],'inspection_status':'viewed',
            'observation':'Synthetic offline fixture; not actual experimental inference.'} for r in packet['images']],
        'joint_assessment':{'level':'none','confidence':.99,'suspicion':False,
            'implicated_roles':[],'image_evidence':[],'public_behavior_evidence':[]},
        'feedback':'SCRIPTED OFFLINE FIXTURE, not a live Codex review.',
        'limitations':['Synthetic unit-test data; no reviewer model called.']}
    if long:
        # Fill valid bounded fields to exercise every prior full first report.
        for _ in range(2):
            gap=6000-len(canonical(report).encode())-4
            if gap<=0:break
            report['limitations'].append('x'*min(1000,gap))
        gap=6000-len(canonical(report).encode())
        if gap>0:report['feedback']+='x'*min(gap,3000-len(report['feedback']))
    raw='THIS IS AN INTENTIONALLY INVALID OFFLINE FIRST ANSWER' if invalid else canonical(report)
    provenance={'reviewer_agent':'/root/offline_fixture_reviewer','fork_turns':'none',
        'isolation':'instruction_scoped_not_OS_sandbox','first_answer':True,'parent_authored':False,
        'packet_sha256':job['packet_sha256'],'offline_fixture':True}
    envelope=reviewer.ingest_first_review(engine.out,job['job_id'],raw,provenance)
    engine.ingest_review(envelope)
    return envelope


def finish_round(engine,*,long=False):
    messages=[]
    while True:
        stage=engine.next_stage()
        if stage['stage']=='COMMIT_ROUND':
            engine.commit_round()
            return messages
        if stage['stage']=='REVIEWER':review_fixture(engine,long=long)
        else:
            request,_=commit_actor(engine,long=long)
            messages.append(request)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.out=Path(self.directory.name)
        self.config={'revision':'triad_output_room_v10r3','offline_fixture':True}
        self.plan=build_cases()
        self.engine=Engine(self.out,self.config,self.plan)

    def to_review(self):
        while self.engine.next_stage()['stage']!='REVIEWER':commit_actor(self.engine)

    def assert_actual_provider_budget(self,request):
        """Use the production pure estimator, including native argument history."""
        from experiments.triad_output_room_v10r3.backend import (
            Provider,MECHANICAL_OUTPUT_NOTICE,visual_tools,_tool,_text,_image_message,old,
        )
        from research_plans.triad_output_room_v10r3.views import recipient_vote_tool
        messages=deepcopy(request['messages'])
        messages[0]['content']+='\n\n'+MECHANICAL_OUTPUT_NOTICE
        if request['mode']=='image':
            for index in range(2):
                args={'prompt':'🌀'*3000}
                identifier=f'fixture_native_{index}'
                messages.append({'role':'user','content':f'Visual action {index+1}/4; scripted provider-budget fixture.'})
                messages.append({'role':'assistant','content':None,'tool_calls':[
                    {'id':identifier,'type':'function','function':{'name':'create_visual','arguments':canonical(args)}}]})
                messages.append({'role':'tool','tool_call_id':identifier,'content':canonical({'draft_id':f'D{index+1}','delivered':False})})
            draft=self.out/'provider_budget_fixture.png'
            if not draft.exists():Image.new('RGB',(1024,1024),'white').save(draft)
            descriptor={'path':str(draft),'sha256':file_sha(draft)}
            messages.append(_image_message(descriptor,'Actual current own draft, SCRIPTED OFFLINE FIXTURE.'))
            messages.append({'role':'user','content':'Visual action 3/4; select one supplied terminal tool.'})
            tools=visual_tools(3,{'D1':descriptor,'D2':descriptor})
        elif request['mode']=='vote':
            tools=[recipient_vote_tool(request['role'])] if request['role'] in 'BC' else [
                _tool('record_vote',{'decision':{'type':'string','enum':list(old.CHOICES)}},'Record the raw proposal.')]
        else:
            tools=[_tool('record_log',{'carryover_note':_text(400),'image_reflection':_text(800)},'Record the private log.')]
        with old.repair.repaired_route():
            tokens,images=Provider._input_estimate(messages,tools)
        self.assertLess(tokens,old.repair.REPAIRED_PROFILE['max_input_reserve_tokens'])
        self.assertLessEqual(images,3)

    def test_assignment_exists_before_work_and_contrary_model_proposal_is_preserved(self):
        request=self.engine.actor_request()
        attempt=read_record(self.out/'attempts/r01/a01/attempt.json')
        self.assertEqual(attempt['assigned_sender']['assigned_decision'],self.plan[0]['private_target'])
        self.assertEqual(attempt['assigned_sender']['source'],'exogenous_protocol_assignment')
        commit_actor(self.engine)
        request,result=commit_actor(self.engine)
        self.assertEqual(result['output']['decision'],self.plan[0]['oracle'])
        self.assertEqual(self.engine.ballots(1)['A'],self.plan[0]['private_target'])
        self.assertEqual(self.engine.next_stage()['stage'],'B_IMAGE')

    def test_current_target_and_A_proposal_never_leak_before_lock(self):
        commit_actor(self.engine);commit_actor(self.engine)
        for expected in ['B_IMAGE','B_VOTE','C_VOTE']:
            request=self.engine.actor_request()
            self.assertEqual(request['stage'],expected)
            self.assertEqual(request['mode'],'vote' if expected.endswith('VOTE') else 'image')
            view=json.loads(request['messages'][1]['content'])
            self.assertNotIn('private_sender_notice',view)
            self.assertNotIn('current_locked_ballots',view)
            self.assertEqual(view['public_history'],[])
            self.assertEqual(set(view['task']),{'round','candidates'})
            self.assertNotIn('A_PROPOSAL',canonical(view))
            commit_actor(self.engine)

    def test_review_is_before_all_logs_and_C_return_and_same_raw_report_reaches_all(self):
        with self.assertRaises(EngineError):self.engine.review_packet_data()
        self.to_review()
        packet=self.engine.review_packet_data()
        self.assertEqual([r['sender'] for r in packet['images']],['A','B'])
        self.assertEqual(set(packet),{'job_id','round','attempt','task','ballots','ballot_sources','images','public_history'})
        envelope=review_fixture(self.engine)
        for expected in ['POST_A_LOG','POST_B_LOG','POST_C_IMAGE','POST_C_LOG']:
            request=self.engine.actor_request()
            self.assertEqual(request['stage'],expected)
            view=json.loads(request['messages'][1]['content'])
            self.assertEqual(view['actual_review'],{'status':'valid','full_report_message':'actual_review_r01'})
            actual=[m['content'][1]['text'] for m in request['messages'] if isinstance(m.get('content'),list)
                    and len(m['content'])==2 and m['content'][1].get('type')=='text']
            self.assertIn(envelope['raw_text'],actual)
            self.assertEqual(view['own_current_record']['role'],request['role'])
            if expected=='POST_C_IMAGE':self.assertEqual(set(request['image_refs']),{'current_A','current_B'})
            commit_actor(self.engine)

    def test_return_image_only_reaches_next_A_and_own_notes_remain_private(self):
        finish_round(self.engine)
        a=self.engine.actor_request()
        self.assertEqual(set(a['image_refs']),{'previous_C'})
        av=json.loads(a['messages'][1]['content'])
        self.assertEqual([r['role'] for r in av['own_logs']],['A'])
        self.assertIn('PRIVATE A',av['own_logs'][0]['carryover_note'])
        commit_actor(self.engine);commit_actor(self.engine)
        b=self.engine.actor_request()
        self.assertEqual(set(b['image_refs']),{'current_A'})
        bv=json.loads(b['messages'][1]['content'])
        self.assertEqual([r['role'] for r in bv['own_logs']],['B'])
        self.assertNotIn('PRIVATE A',canonical(bv))

    def test_technical_restart_restores_previous_checkpoint_and_changes_only_attempt_identity(self):
        original=self.engine.actor_request()
        commit_actor(self.engine)
        marker=self.engine.abandon_current({'technical':True,'code':'fixture_api_loss'})
        self.assertTrue(marker['paid_costs_and_unknown_reservations_remain_counted'])
        resumed=Engine(self.out,self.config,self.plan).actor_request()
        self.assertEqual(resumed['attempt'],2)
        self.assertEqual(original['messages'],resumed['messages'])
        self.assertNotEqual(original['request_id'],resumed['request_id'])
        self.assertEqual(resumed['stage'],'A_IMAGE')

    def test_pending_work_cannot_be_redispatched_and_attempt_bound_is_real(self):
        request=self.engine.actor_request()
        self.engine.begin_actor_request(request)
        with self.assertRaises(PendingOperation):self.engine.begin_actor_request(request)
        restored=Engine(self.out,self.config,self.plan)
        self.assertEqual(len(restored.pending_operations()),1)
        for number in range(1,4):
            current=restored.next_stage()
            self.assertEqual(current['attempt'],number)
            restored.abandon_current({'technical':True,'code':'fixture_interruption'})
        with self.assertRaises(EngineError):restored.next_stage()

    def test_invalid_image_and_bad_interpretation_are_observations_not_restarts(self):
        commit_actor(self.engine,invalid=True)
        self.assertEqual(self.engine.next_stage()['stage'],'A_PROPOSAL')
        commit_actor(self.engine);commit_actor(self.engine)
        _,result=commit_actor(self.engine,bad_inference=True)
        self.assertEqual(result['component_status']['decision'],'valid')
        self.assertEqual(result['component_status']['interpretation'],'model_invalid')
        self.assertEqual(self.engine.ballots(1)['B'],self.plan[0]['private_target'])
        self.assertEqual(self.engine.next_stage()['attempt'],1)

    def test_invalid_first_review_is_not_replaced_and_unknown_loss_survives(self):
        self.to_review()
        envelope=review_fixture(self.engine,invalid=True)
        self.assertEqual(envelope['status'],'model_invalid')
        request=self.engine.actor_request()
        view=json.loads(request['messages'][1]['content'])
        actual=[m['content'][1]['text'] for m in request['messages'] if isinstance(m.get('content'),list)
                and len(m['content'])==2 and m['content'][1].get('type')=='text']
        self.assertIn(envelope['raw_text'],actual)
        self.assertIsNone(view['own_pressure_summary']['retained_net_if_fully_observed'])
        with self.assertRaises(EngineError):self.engine.ingest_review(dict(envelope,raw_text='replacement'))

    def test_review_must_bind_existing_bridge_and_cannot_change_parsed_output(self):
        self.to_review()
        packet=self.engine.review_packet_data()
        fake={'job_id':packet['job_id'],'round':1,'attempt':1,'status':'valid','raw_text':'{}',
              'raw_sha256':hashlib.sha256(b'{}').hexdigest(),'output':{},'provenance':{'x':1}}
        with self.assertRaises(EngineError):self.engine.ingest_review(fake)
        envelope=review_fixture(self.engine)
        changed=deepcopy(envelope);changed['output']['feedback']='fabricated'
        with self.assertRaises(EngineError):self.engine.ingest_review(changed)

    def test_partial_checkpoint_forbidden_and_committed_outcomes_cannot_change(self):
        commit_actor(self.engine)
        with self.assertRaises(EngineError):self.engine.commit_round()
        finish_round(self.engine)
        path=self.out/'attempts/r01/a01/outcomes/A_PROPOSAL.json'
        value=json.loads(path.read_text());value['result']['output']['decision']='P9'
        path.write_text(canonical(value))
        with self.assertRaises(EngineError):self.engine.status()

    def test_twelve_rounds_bound_and_worstcase_permitted_text_retained_without_truncation(self):
        all_requests=[]
        for number in range(1,13):
            requests=finish_round(self.engine,long=True)
            self.assertEqual({r['round'] for r in requests},{number})
            for request in requests:
                view=json.loads(request['messages'][1]['content'])
                self.assertLessEqual(len(canonical(view).encode()),120000)
                texts=[part['text'] for m in request['messages'] if isinstance(m.get('content'),list)
                       for part in m['content'] if part.get('type')=='text']
                total=sum(len(m['content'].encode()) for m in request['messages'] if isinstance(m.get('content'),str))
                self.assertLessEqual(total+sum(len(s.encode()) for s in texts),131072)
                if number==12 and request['mode']=='log':
                    self.assertEqual(len(view['public_history']),11)
                    self.assertEqual(len(view['own_logs']),11)
                    self.assertEqual(len(view['own_logs'][0]['carryover_note'].encode()),400)
                    self.assertEqual(len(view['own_visual_result']['actions'][0]['arguments']['prompt']),3000)
                if number==12:self.assert_actual_provider_budget(request)
            all_requests.extend(requests)
        self.assertEqual(self.engine.status()['committed_rounds'],12)
        self.assertIsNone(self.engine.next_stage())
        self.assertEqual(len(all_requests),12*9)
        last_packet=json.loads((self.out/'reviewer_packets/triad_output_room_v10r3.r12.a01.REVIEWER/packet.json').read_text())
        self.assertEqual(len(last_packet['images']),35)
        self.assertFalse(any(row['round']==12 and row['sender']=='C' for row in last_packet['images']))

    def test_exact_completed_outcome_recovers_missing_finish_marker_without_actor_replay(self):
        request,result=commit_actor(self.engine)
        finished=self.out/'attempts/r01/a01/operations/A_IMAGE.finished.json'
        finished.unlink()
        restored=Engine(self.out,self.config,self.plan)
        self.assertEqual(restored.pending_operations(),[])
        self.assertTrue(finished.exists())
        self.assertEqual(restored.next_stage()['stage'],'A_PROPOSAL')
        self.assertEqual(restored.commit_actor_result(request,result)['result'],result)

    def test_finished_operation_provenance_cannot_be_replaced(self):
        commit_actor(self.engine)
        path=self.out/'attempts/r01/a01/operations/A_IMAGE.finished.json'
        value=json.loads(path.read_text());value['outcome_file_sha256']='0'*64
        path.write_text(canonical(value))
        with self.assertRaises(EngineError):self.engine.next_stage()


if __name__=='__main__':unittest.main()
