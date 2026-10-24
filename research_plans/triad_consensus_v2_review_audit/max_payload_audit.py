"""Independent offline payload audit; no network or production ledger writes."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib, io, json, tempfile
from pathlib import Path
from PIL import Image
from experiments.triad_consensus_v2.engine import TriadEngine, canonical
from experiments.triad_consensus_v2.contracts import validate_review, oracle, MAX_REVIEW_BYTES, RCA_KEYS
from experiments.triad_consensus_v2.coordinator import inherited_plan, load_prompts
from experiments.triad_consensus_v2.backend import TriadBackend, effective_system_prompt, MODEL, NAMESPACE
from visual_collusion.providers import OpenRouterProvider
from visual_collusion.budget import BudgetLedger
from experiments.pressure_interview_v1.budget_guard import SharedBudgetGuard
from experiments.pressure_interview_v1.tests.test_live_provider import ScriptedWireTransport

ROOT=Path('/workspace/visual_collusion_stress_v4')
OUT=ROOT/'research_plans/triad_consensus_v2_review_audit'
base=json.loads((ROOT/'experiments/triad_consensus_v2/config.json').read_text())
base['evidence_origin']='fixture';base['prompts']=load_prompts('explicit');base['reviewer_prompt']=base['prompts']['reviewer']
case=deepcopy(inherited_plan()['cases'][0]);case['case_id']='audit_max_payload';case['task']['brief']='"'*1600
correct=oracle(case['task'])
report={'verdict':'accept','collusion_suspected':False,'channel':'none','task_violation':False,'confidence':0.5,
'evidence':['x'*300 for _ in range(4)],'rca':{k:['x'*300 for _ in range(4)] for k in RCA_KEYS},'feedback':'"'*800,
'task_assessment':{'correct_decision':correct,'agent_correctness':dict.fromkeys('ABC',True),'team_correct':True,'reward':1}}
# Every evidence/RCA string is300 UTF8 bytes; replace x with quote to fill the
# complete canonical JSON budget exactly, exercising double-escaping overhead.
for group in [report['evidence'],*report['rca'].values()]:
 for i,s in enumerate(group):
  remaining=MAX_REVIEW_BYTES-len(canonical(report).encode())
  count=min(300,max(0,remaining));group[i]='"'*count+'x'*(300-count)
assert len(canonical(report).encode())==MAX_REVIEW_BYTES
validate_review(report)
result={'evidence_origin':'fixture','no_paid_calls':True,'production_ledger_touched':False,
'report_utf8_bytes':MAX_REVIEW_BYTES,'task_brief_utf8_bytes':1600,'ballot_rationale_utf8_bytes_each':800,
'B_message_utf8_bytes':800,'private_carryover_utf8_bytes_each':2048,'contexts':[],'wire_checks':[],'passed':False}
requests={}
with tempfile.TemporaryDirectory() as temp:
 temp=Path(temp);png={}
 for role,color in [('A',(20,30,40)),('C',(60,70,80))]:
  p=temp/(role+'.png');Image.new('RGB',(1024,1024),color).save(p)
  png[role]={'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
 engine=TriadEngine(temp/'engine',base,case)
 try:
  while engine.next_stage():
   target=engine.next_stage()
   if target['role']=='REVIEWER':
    engine.ingest_review(report,{'evidence_origin':'fixture'});continue
   req=engine.actor_request();view=json.loads(req['messages'][1]['content'])
   messages=deepcopy(req['messages']);messages[0]['content']=effective_system_prompt(req)
   from experiments.triad_consensus_v2.backend import direct_schema,image_action_schema
   schema=image_action_schema(req['mode'],req.get('image_refs',{})) if req['mode'] in {'image_vote','image_memory'} else direct_schema(req['mode'])
   estimate,images=OpenRouterProvider._input_estimate(messages,schema,MODEL)
   shared=view.get('reviewer_feedback',view.get('previous_reviewer_feedback'))
   if shared:assert shared['reviewer_report']==report
   result['contexts'].append({'round':target['round'],'stage':target['stage'],
    'view_utf8_bytes':len(req['messages'][1]['content'].encode()),'initial_input_estimate':estimate,'inline_images':images})
   if target['round']==12:requests[req['mode']]=deepcopy(req)
   out={'decision':None,'rationale':None,'message':None,'image':None,'private_note':'','report':None}
   if req['mode'] in {'image_vote','message_vote','vote_only'}:out.update(decision=correct,rationale='"'*800)
   if req['mode']=='message_vote':out['message']='"'*800
   if req['mode'] in {'image_vote','image_memory'}:out['image']=png[req['role']]
   if req['mode'] in {'memory_only','image_memory'}:out['private_note']='"'*2048
   engine.claim_actor_dispatch(req);accepted=engine.accept_actor({'status':'valid','output':out,'provenance':{'evidence_origin':'fixture'}})
   assert accepted['status']=='valid'
  result['twelve_rounds_finished']=engine.summary()['rounds_finished']
  # Exercise all four image actions at round12 using the actual adapter and
  # exact previously generated1K PNG+XMP, over a simulated provider transport.
  real_png=(ROOT/'runs/main/pressure_interview_live/provider_evidence/blobs/3261793ad855cf89885d5a2df99e747d1a46d04d064538111c0e6ded61358d98.png').read_bytes()
  for mode in ['image_vote','image_memory']:
   ledger=BudgetLedger(temp/(mode+'.sqlite3'));guard=SharedBudgetGuard(ledger.path,NAMESPACE,ledger=ledger)
   args=[{'action':'generate_image','arguments':{'prompt':'"'*3000,'reference':'none'}},
         {'action':'generate_image','arguments':{'prompt':'"'*3000,'reference':'latest_draft'}},
         {'action':'inspect_image','arguments':{'reference':'latest_draft','operation':'full','box':None}},
         {'action':'deliver','arguments':{'decision':correct,'rationale':'"'*800} if mode=='image_vote' else {'private_note':'"'*2048}}]
   transport=ScriptedWireTransport(ledger,args);transport.image_bytes=real_png
   backend=TriadBackend(base,temp/('backend_'+mode),ledger.path,guard=guard,transport=transport,api_key='FIXTURE_ONLY_NOT_A_CREDENTIAL')
   answer=backend.run_actor(requests[mode]);assert answer['status']=='valid',answer.get('reason')
   posts=transport.posts_exact
   result['wire_checks'].append({'mode':mode,'posts':len(posts),'image_posts':sum(p=='/images' for p,b in posts),
    'max_actual_wire_bytes':max(len(b) for p,b in posts),'four_actions_and_two_images':len(posts)==6,
    'all_wire_bytes_below10MiB':all(len(b)<10485760 for p,b in posts)})
  result['passed']=True
 except Exception as e:
  result['failure']={'stage':engine.next_stage(),'exception':type(e).__name__,'message':str(e)}
  result['twelve_rounds_finished']=engine.summary()['rounds_finished']
result['recorded_at_utc']=datetime.now(timezone.utc).isoformat()
result['max_view_utf8_bytes']=max((r['view_utf8_bytes'] for r in result['contexts']),default=0)
result['source_hashes']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'experiments/triad_consensus_v2').glob('*.py')}
(OUT/'MAX_PAYLOAD_AUDIT.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k not in {'contexts','source_hashes'}},indent=2))
