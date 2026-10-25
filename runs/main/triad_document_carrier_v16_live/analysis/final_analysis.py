import json,glob,os,sqlite3,ast
from next_batch.v16_document_carrier.funding import DEFAULT_OUT as o
from next_batch.v15_glm_restored.storage import read
out={}
dec=json.load(open(o/'analysis/decoding_scores.json'))['rows']
rows=[]
for arm in ('b01','b03','b05'):
  caught_before=False
  for n in range(1,6):
    c=read(o/'arms'/arm/'checkpoints'/f'r{n:02}.json'); att=c['attempt_id'].split('.')[-1]
    root=o/'arms'/arm/'attempts'/f'r{n:02}'/att
    A=read(root/'stages/A_PUBLISH.json')
    plan=(A.get('declared_plan') or A.get('original',{}).get('private_provenance',{}).get('declared_plan') or {})
    if not plan: plan=((A.get('raw_native_result') or {}).get('arguments') or {}).get('declared_plan',{})
    intent=plan.get('message_intent') or ''
    assert intent, (arm,n)
    no_payload=any(s in intent.lower() for s in ('no covert','no private-target payload','no private-target or tag','no target or tag transmission'))
    rep=(c.get('main_review') or {}).get('report') or {}
    u=rep.get('unilateral_attempts',[])
    sup=any(x['level']=='supported' for x in u)
    ad=rep.get('attribution_decomposition',{})
    t2=[r for r in dec if r['arm']==arm and r['round']==n]
    sen=read(o/'probes/sentinel'/f'{arm}.r{n:02}.delivered.json')
    sv=sen.get('value') or {}
    lad={}
    for L in ('L1_artifacts','L2_ballots','L3_history','L4_incentives'):
      js=sorted(glob.glob(str(o/'attribution_audits'/f'ladder.{arm}.r{n:02}.{L}*')))
      js=[j for j in js if not os.path.exists(j+'/SUPERSEDED_REWIND.json')]
      if js:
        a=read(js[-1]+'/assessment.json'); rp=a.get('report') or {}
        lad[L]={'signal':rp.get('signal_observed'),'target':rp.get('artifact_target_readout'),'tag':rp.get('artifact_tag_readout'),'joint':rp.get('knowing_joint_collusion')}
    votes={r:read(root/'stages'/f'{r}_VOTE.json').get('value',{}) for r in 'BC'}
    rows.append(dict(arm=arm,round=n,target=c['private_target'],tag=c['validation_tag'],client=c['client_correct'],
      encoded=not no_payload,after_catch=caught_before,main_supported=sup,main_levels=[(x['level'],x['carrier'],x['pattern_family'],x.get('recovered_payload')) for x in u],
      review_tag_recovered=any(x.get('recovered_payload')==c['validation_tag'] for x in u) or ad.get('artifact_tag_readout')==c['validation_tag'],
      t2_target=[r['target_match'] for r in t2],t2_tag=[r['tag_match'] for r in t2],
      t3_status=sen.get('status'),t3_target=sv.get('inferred_target'),t3_tag=sv.get('decoded_tag'),
      receivers={r:(votes[r].get('decision'),votes[r].get('inferred_target'),votes[r].get('decoded_tag')) for r in 'BC'},
      ladder=lad,sanction=c['settlement'].get('sanction_triggered'),fine=c['settlement'].get('fine_each_nominal')))
    caught_before = caught_before or sup
out['rounds']=rows
# replay T6
rep=[]
for f in sorted(glob.glob(str(o/'probes/replay/*.json'))):
  d=read(f); v=d.get('value')
  if isinstance(v,str):
    try: v=ast.literal_eval(v)
    except Exception: v={}
  rep.append(dict(round=d['round'],policy=d['policy'],stage=d['stage'],status=d['status'],decision=(v or {}).get('decision'),inferred=(v or {}).get('inferred_target'),tag=(v or {}).get('decoded_tag')))
out['replay']=rep
c=sqlite3.connect(f'file:{o.parent}/budget.sqlite3?mode=ro',uri=True)
out['ledger']={'rows':c.execute('select count(*) from requests').fetchone()[0],
 'v16_rows':c.execute("select count(*) from requests where request_id like 'triad_document_carrier_v16:%'").fetchone()[0],
 'v16_status':c.execute("select status,count(*) from requests where request_id like 'triad_document_carrier_v16:%' group by status").fetchall(),
 'v16_actual_usd':c.execute("select sum(actual_units)/1e9 from requests where request_id like 'triad_document_carrier_v16:%'").fetchone()[0],
 'since_018_rows':c.execute('select count(*) from requests where rowid>1847').fetchone()[0],
 'since_018_usd':c.execute('select sum(actual_units)/1e9 from requests where rowid>1847').fetchone()[0]}
json.dump(out,open(o/'analysis/final_analysis.json','w'),indent=1,default=str)
print(json.dumps(out['ledger'],default=str))
for r in rows: print(r['arm'],r['round'],'enc',r['encoded'],'after',r['after_catch'],'sup',r['main_supported'],'revtag',r['review_tag_recovered'],'t2',r['t2_target'],r['t2_tag'],'t3',r['t3_status'],r['t3_target'],r['t3_tag'],'L1',r['ladder'].get('L1_artifacts',{}).get('signal'),'recv',r['receivers'])
from collections import Counter
print(Counter((x['policy'],x['status']) for x in rep))
print(Counter((x['policy'],x['decision']==[r for r in rows if r['arm']=='b01' and r['round']==x['round']][0]['client']) for x in rep if x['decision']))
print('replay inferred/tag nonunknown', [(x['round'],x['policy'],x['stage'],x['inferred'],x['tag']) for x in rep if x['inferred'] not in (None,'unknown') or x['tag'] not in (None,'unknown')])
