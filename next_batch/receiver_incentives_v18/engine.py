"""Fixed-stimulus receiver cohorts with genuine B image delivery and durable logs."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from next_batch.oversight_v17.documents import render_document
from next_batch.oversight_v17.events import Journal, IntegrityError
from next_batch.oversight_v17.finance import canonical, FundingHold
from .design import CASES, TOOL, prompt, parse_response, load_stimuli, schedule
from .funding import StudyLedger, bridge
from .provider import Provider, block

ROOT=Path(__file__).resolve().parents[2]


def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    with temporary.open('w') as file:
        file.write(canonical(value)+'\n');file.flush();os.fsync(file.fileno())
    temporary.replace(path)


def messages(role,case,reward,notes,number,b_pages=None):
    system,user=prompt(role,case['task'],case['target'],reward,notes,number)
    content=[{'type':'text','text':canonical(user)}]+[block(raw) for raw in case['pages']]
    if role=='C':
        if b_pages:
            content.append({'type':'text','text':'The following actual page images are B\'s independent verification memo.'})
            content.extend(block(raw) for raw in b_pages)
        else:content.append({'type':'text','text':'B\'s public document is unavailable. Do not invent its content.'})
    return [{'role':'system','content':system},{'role':'user','content':content}]


def check_freeze(private_root):
    root=Path(private_root);freeze=json.loads((root/'freeze.json').read_text())
    for name,digest in freeze['source_sha256'].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=digest:
            raise IntegrityError('Prospective source freeze differs: '+name)
    if hashlib.sha256((root/'stimuli/manifest.private.json').read_bytes()).hexdigest()!=freeze['stimulus_manifest_sha256']:
        raise IntegrityError('Historical source manifest changed')
    if schedule()!=freeze['assignment']:raise IntegrityError('Assignment differs from pre-outcome schedule')
    return freeze


def worker(private_root,cohort_id):
    root=Path(private_root);freeze=check_freeze(root)
    group=next(g for g in freeze['assignment'] if g['cohort_id']==cohort_id)
    stimuli=load_stimuli(root);folder=root/'cohorts'/cohort_id;folder.mkdir(parents=True,exist_ok=True)
    journal=Journal(folder);ledger=StudyLedger(root/'budget.sqlite3');provider=Provider(ledger,root/'paid_calls')
    checkpoint=folder/'checkpoint.json'
    state=json.loads(checkpoint.read_text()) if checkpoint.exists() else {'completed':[],'notes':{'B':[],'C':[]}}
    for number,case_id in enumerate(group['case_order'],1):
        if any(row['round']==number for row in state['completed']):continue
        case=stimuli[case_id]
        prior=deepcopy(state)
        for retry in range(2):
            earlier=[r for r in journal.read() if r['body']['kind']=='trial_started' and r['body']['data']['round']==number]
            attempt=len(earlier)+1
            prefix=f"v18:{freeze['run_id']}:{cohort_id}:r{number}:a{attempt}"
            journal.append('trial_started',{'round':number,'case_id':case_id,'attempt':attempt,
                'checkpoint_notes':deepcopy(prior['notes']),'source_page_sha256':[hashlib.sha256(x).hexdigest() for x in case['pages']]})
            try:
                response,b_receipt=provider.chat(prefix+':B',messages('B',case,group['correctness_reward'],prior['notes']['B'],number),tool=TOOL)
                b=parse_response(response,'B');b_ref=journal.put(canonical(response).encode(),'json')
                journal.append('first_B',{'response':b_ref,'parsed':b,'receipt':b_receipt})
                b_pages=[];render_meta=None;figure_ref=None
                if b.get('document_available'):
                    try:
                        figure,img_receipt=provider.image(prefix+':B-image',b['fields']['figure_brief'])
                        figure_ref=journal.put(figure,'png')
                        b_pages,render_meta=render_document('Independent verification memo',b['fields']['public_report'],figure)
                        if len(b_pages)>2:raise IntegrityError('B document exceeds frozen two-page delivery envelope')
                        journal.append('B_document',{'pages':[journal.put(raw,'png') for raw in b_pages],
                            'figure':figure_ref,'render':render_meta,'receipt':img_receipt})
                    except Exception as error:
                        journal.append('technical_image_fault',{'round':number,'attempt':attempt,'error_type':type(error).__name__,
                            'checkpoint_restored':True,'behavioral_retry':False})
                        state=deepcopy(prior)
                        if retry==0 and ledger.claim_technical_restart(prefix):continue
                        raise
                response,c_receipt=provider.chat(prefix+':C',messages('C',case,group['correctness_reward'],prior['notes']['C'],number,b_pages),tool=TOOL)
                c=parse_response(response,'C');c_ref=journal.put(canonical(response).encode(),'json')
                journal.append('first_C',{'response':c_ref,'parsed':c,'receipt':c_receipt})
                row={'pair_id':group['pair_id'],'arm':group['correctness_reward'],'cohort_id':cohort_id,
                    'round':number,'case_id':case_id,'target':case['target'],'client_best':case['client_best'],
                    'B_choice':b['choice'],'C_choice':c['choice'],
                    'B_result':b,'C_result':c,'B_response':b_ref,'C_response':c_ref,
                    'B_page_refs':[journal.put(raw,'png') for raw in b_pages],
                    'B_figure_ref':figure_ref,'B_render':render_meta,'audited':case_id==group['audited_case'],
                    'checkpoint_attempt':attempt}
                state=deepcopy(prior);state['completed'].append(row)
                for role,result in [('B',b),('C',c)]:
                    if result.get('fields') and result.get('memory_available'):
                        state['notes'][role].append(result['fields']['memory_note'])
                journal.append('trial_committed',row);write_json(checkpoint,state)
                print(canonical({'cohort':cohort_id,'trial':number,'status':'committed','B':b['choice'],'C':c['choice']}),flush=True)
                break
            except BaseException as error:
                journal.append('attempt_failed',{'round':number,'attempt':attempt,'error_type':type(error).__name__})
                raise
    write_json(folder/'completed.json',{'group':group,'records':state['completed']})


def collect(private_root):
    root=Path(private_root);plan=check_freeze(root)['assignment'];stimuli=load_stimuli(root);rows=[]
    for group in plan:
        path=root/'cohorts'/group['cohort_id']/'checkpoint.json'
        observed=json.loads(path.read_text())['completed'] if path.exists() else []
        by_round={r['round']:r for r in observed}
        for number,case_id in enumerate(group['case_order'],1):
            rows.append(by_round.get(number,{'pair_id':group['pair_id'],'arm':group['correctness_reward'],
                'cohort_id':group['cohort_id'],'round':number,'case_id':case_id,
                'target':stimuli[case_id]['target'],'client_best':stimuli[case_id]['client_best'],
                'B_choice':None,'C_choice':None,'technical_missing':True,'audited':case_id==group['audited_case']}))
    write_json(root/'all_records.private.json',rows)
    return rows


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--private-root',required=True,type=Path)
    parser.add_argument('--cohort');parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    if args.cohort:return worker(args.private_root,args.cohort)
    if not args.execute:raise SystemExit('Explicit --execute required for the frozen pilot')
    freeze=check_freeze(args.private_root);groups=freeze['assignment']
    # Two complete matched pairs per batch, preventing arm/time batching.
    pairs=sorted(set(g['pair_id'] for g in groups));failed=[]
    for start in range(0,len(pairs),2):
        selected=[g for g in groups if g['pair_id'] in pairs[start:start+2]]
        jobs=[]
        for group in selected:
            path=args.private_root/'worker_logs';path.mkdir(exist_ok=True)
            log=(path/(group['cohort_id']+'.log')).open('a')
            process=subprocess.Popen([sys.executable,'-m','next_batch.receiver_incentives_v18.engine',
                '--private-root',str(args.private_root),'--cohort',group['cohort_id']],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
            jobs.append((group,process,log))
        while any(process.poll() is None for _,process,_ in jobs):time.sleep(1)
        for group,process,log in jobs:
            log.close()
            if process.returncode:failed.append(group['cohort_id'])
            print(canonical({'cohort':group['cohort_id'],'exit_code':process.returncode}),flush=True)
        if failed:break
    rows=collect(args.private_root)
    print(canonical({'completed_trials':sum(not r.get('technical_missing') for r in rows),'planned_trials':36,'failed_cohorts':failed}),flush=True)
    if failed:raise SystemExit(1)


if __name__=='__main__':main()
