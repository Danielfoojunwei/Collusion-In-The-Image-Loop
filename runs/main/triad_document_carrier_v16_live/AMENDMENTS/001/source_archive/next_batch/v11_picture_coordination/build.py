"""Render a reviewable candidate and run offline checks; never start inference."""
from pathlib import Path
import datetime as dt
import hashlib
import json
import sqlite3
import subprocess
import sys

from . import design as d, schemas
from .policy import POLICY
from .prompts import actor_system, REVIEWER, C_FORMAT_REVIEWER

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]


def dump(name,value):
    (HERE/name).write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False)+'\n')


def main():
    old=ROOT/'runs/main/triad_ascii_records_v10r2_live/episodes/e01/continuation_v10r5/freeze.json'
    frozen=json.loads(old.read_text())['source_files']
    for relative,expected in frozen.items():
        if hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()!=expected:
            raise RuntimeError('An earlier frozen source changed')
    with sqlite3.connect(f'file:{ROOT}/runs/main/budget.sqlite3?mode=ro',uri=True) as con:
        con.row_factory=sqlite3.Row
        rows=con.execute('SELECT request_id,actual_units,reserved_units FROM requests').fetchall()
    prefixes=('triad_committed_sender_v10:','triad_required_images_v10r1:',
              'triad_ascii_records_v10r2:','triad_output_room_v10r3:','triad_picture_coordination_v11:')
    aggregate=sum(r['actual_units'] if r['actual_units'] is not None else r['reserved_units'] for r in rows)
    program=sum(r['actual_units'] if r['actual_units'] is not None else r['reserved_units'] for r in rows if r['request_id'].startswith(prefixes))
    budget=d.budget_check(str(program/1e9),str(aggregate/1e9))
    if not budget['fits_existing_program_and_aggregate_caps']:
        raise RuntimeError('The normal prospective allowance does not fit the existing envelope')
    dump('config.json',d.CONFIG);dump('POLICY.json',POLICY.public())
    dump('RESEARCHER_ONLY_CASES.json',{'not_actor_context':True,'seed':d.CONFIG['seed'],'rounds':d.cases()})
    rendered='\n\n'.join(f'=== {role}.{mode} ===\n{actor_system(role,mode)}'
                            for role in d.ROLES for mode in ('image','vote','log'))
    rendered+='\n\n=== CODEX REVIEWER ===\n'+REVIEWER+'\n\n=== C RETURN COMPLIANCE REVIEWER ===\n'+C_FORMAT_REVIEWER+'\n'
    (HERE/'SYSTEM_PROMPTS.txt').write_text(rendered)
    templates=[]
    for case in (d.cases()[0],d.cases()[1],d.cases()[-1]):
        for role in d.ROLES:
            for mode in ('image','vote','log'):
                post=mode=='log' or role=='C' and mode=='image'
                options={'locked_ballots':{'$runtime_slot':'actual locked ballots'},
                         'actual_review':{'$runtime_slot':'actual genuine first Codex report'}} if post else {}
                if role=='C' and mode=='log':
                    options.update(final_C_compliance={'$runtime_slot':'actual C image compliance result'},
                                   calculated_settlement={'$runtime_slot':'authoritative calculated settlement'})
                templates.append({'round':case['round'],'role':role,'mode':mode,
                                  **d.message_template(case,role,mode,**options)})
    dump('USER_MESSAGE_TEMPLATES.json',{'researcher_only':True,'offline_only':True,'dispatchable':False,'templates':templates})
    dump('TOOL_SCHEMAS.json',{'votes':{r:schemas.vote_tool(r) for r in d.ROLES},'log':schemas.log_tool(),
                            'first_visual_action':schemas.visual_tools([],1),
                            'reviewer':schemas.review_schema(),'C_format':schemas.c_format_schema(),
                            'review_raw_and_canonical_utf8_limit':6000,'C_format_raw_and_canonical_utf8_limit':1200})
    dump('BUDGET_CHECK.json',{'prior_program_committed_usd':program/1e9,
                            'prior_aggregate_committed_usd':aggregate/1e9,**budget})
    with (HERE/'OFFLINE_TESTS.log').open('wb') as stream:
        result=subprocess.run([sys.executable,'-m','unittest','next_batch.v11_picture_coordination.test_design',
                               'next_batch.v11_picture_coordination.test_runtime','-v'],
                              cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,timeout=180)
    import re
    matches=re.findall(rb'Ran (\d+) tests? in ',(HERE/'OFFLINE_TESTS.log').read_bytes())
    count=int(matches[-1]) if matches else 0
    before={k:hashlib.sha256((ROOT/k).read_bytes()).hexdigest() for k in frozen}
    assert before==frozen
    files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(HERE.iterdir())
           if p.is_file() and p.name!='VALIDATION.json'}
    validation={'status':'passed' if result.returncode==0 and count else 'failed',
                'tests_passed':count if result.returncode==0 else 0,
                'evidence_origin':'offline_workflow_and_exact_wire_tests_not_model_results','paid_calls':0,
                'live_adapter_integrated':True,'live_ready':False,
                'live_readiness_note':'Requires immutable source freeze and current free production preflight; real product reviews remain external first-answer jobs.',
                'previous_frozen_source_files_unchanged':len(frozen),
                'source_and_exports_sha256':files,'budget':budget,
                'checked_at_utc':dt.datetime.now(dt.timezone.utc).isoformat()}
    dump('VALIDATION.json',validation)
    print(json.dumps({k:v for k,v in validation.items() if k!='source_and_exports_sha256'},indent=2))
    raise SystemExit(0 if validation['status']=='passed' else 2)


if __name__=='__main__':main()
