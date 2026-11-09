"""Recover the first completed audit using independently verified route metadata.

Post-collection parser repair only: no paid dispatch, no changed evidence, no
replacement output, and no mutation of the original request/response/receipt.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time

from next_batch.oversight_v17.finance import canonical, units
from next_batch.oversight_v17.events import IntegrityError
from next_batch.oversight_v17.reviewer import PRO_RESPONSE_MODELS, valid_returned_model
from .audit import packet
from .engine import check_freeze, write_json
from .funding import StudyLedger


def recover(private_root):
    root=Path(private_root);freeze=check_freeze(root)
    identity=f"v18:{freeze['run_id']}:post-cohort-audit"
    folder=root/'paid_calls'/hashlib.sha256(identity.encode()).hexdigest()
    request=json.loads((folder/'request.json').read_text())
    receipt=json.loads((folder/'receipt.json').read_text())
    raw=(folder/'raw_response.json').read_bytes();response=json.loads(raw)
    lookup=json.loads((root/'audit_generation_lookup.json').read_text())['data']
    selected=[x for x in response.get('openrouter_metadata',{}).get('endpoints',{}).get('available',[]) if x.get('selected') is True]
    if receipt['response_sha256']!=hashlib.sha256(raw).hexdigest() or receipt['wire_sha256']!=hashlib.sha256((folder/'request.json').read_bytes()).hexdigest():
        raise IntegrityError('First response/request differs from immutable receipt')
    if request['provider']['only']!=['openai'] or request['provider']['allow_fallbacks'] is not False:
        raise IntegrityError('Original direct-route binding differs')
    if len(selected)!=1 or selected[0].get('provider')!='OpenAI' or selected[0].get('model') not in PRO_RESPONSE_MODELS:
        raise IntegrityError('Nested selected route is not the frozen frontier provider/model')
    if lookup.get('id')!=receipt['response_id'] or lookup.get('provider_name')!='OpenAI' or lookup.get('model') not in PRO_RESPONSE_MODELS or units(lookup['total_cost'])!=units(receipt['cost_usd']):
        raise IntegrityError('Independent generation lookup contradicts first receipt')
    if response.get('status')!='completed' or not valid_returned_model(response.get('model'),response.get('reasoning') or {}):
        raise IntegrityError('First audit incomplete or model/mode differs')
    for name,value in [('effort','max'),('mode','pro'),('context','current_turn')]:
        if response.get('reasoning',{}).get(name)!=value:raise IntegrityError('Actual highest-reasoning echo differs')
    contents,public,mapping=packet(root)
    if hashlib.sha256(canonical(public).encode()).hexdigest()!=receipt['context']['public_packet_sha256']:
        raise IntegrityError('Evidence changed since original audit dispatch')
    text=''.join(part['text'] for item in response.get('output',[]) if item.get('type')=='message'
        for part in item.get('content',[]) if part.get('type')=='output_text')
    audit=json.loads(text)
    if set(audit)!={'audits','overall_inspection_gaps'} or len(audit['audits'])!=6 or {a['audit_id'] for a in audit['audits']}!=set(mapping):
        raise IntegrityError('First audit lacks the six fixed trials')
    for item in audit['audits']:
        if item['finding']=='supported_joint' and set(item['participants'])!={'B','C'}:
            raise IntegrityError('Joint finding does not identify both receivers')
    proof={'request_id':identity,'no_new_paid_call':True,'unchanged_actual_cost_usd':receipt['cost_usd'],
        'original_receipt_provider_field':receipt['returned_provider'],'verified_provider':'OpenAI',
        'selected_endpoint':selected[0],'generation_lookup':{k:lookup.get(k) for k in ['id','model','provider_name','total_cost','finish_reason']},
        'actual_reasoning_echo':response['reasoning'],'response_sha256':receipt['response_sha256'],
        'request_sha256':receipt['wire_sha256'],'generation_lookup_sha256':hashlib.sha256((root/'audit_generation_lookup.json').read_bytes()).hexdigest(),
        'original_failure':'Top-level provider missing; nested selected-provider metadata not parsed.',
        'repair':'Validate nested selected endpoint and independent generation lookup; parse the same first completed response.',
        'at_unix':time.time()}
    ledger=StudyLedger(root/'budget.sqlite3')
    with ledger.transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS identity_reconciliations (request_id TEXT PRIMARY KEY, old_status TEXT NOT NULL, new_status TEXT NOT NULL, proof_json TEXT NOT NULL, created REAL NOT NULL)')
        row=db.execute('SELECT * FROM calls WHERE request_id=?',(identity,)).fetchone()
        if row['actual']!=units(receipt['cost_usd']):raise IntegrityError('Settled financial liability changed')
        prior=db.execute('SELECT * FROM identity_reconciliations WHERE request_id=?',(identity,)).fetchone()
        if not prior:
            if row['status']!='route_hold':raise IntegrityError('Expected original settled identity hold')
            db.execute('INSERT INTO identity_reconciliations VALUES (?,?,?,?,?)',(identity,'route_hold','completed',canonical(proof),time.time()))
            db.execute("UPDATE calls SET status='completed' WHERE request_id=?",(identity,))
        else:proof=json.loads(prior['proof_json'])
    write_json(root/'audit_identity_reconciliation.json',proof)
    (root/'first_audit_output.txt').write_text(text)
    write_json(root/'audit_result.private.json',{'audit':audit,'mapping':mapping,'receipt':receipt,
        'identity_reconciliation':proof,'public_trials':public,'image_count':sum(x['type']=='input_image' for x in contents),
        'timing':'after all cohort ballots locked; original first response recovered without resampling'})
    for group in freeze['assignment']:
        relevant=[a for a in audit['audits'] if mapping[a['audit_id']]['cohort_id']==group['cohort_id']]
        write_json(root/'cohorts'/group['cohort_id']/'delivered_after_cohort_audit.json',{'actual_first_report':relevant,'review_available_only_after_trials':True})
    return {'first_audit_recovered':True,'new_paid_calls':0,'actual_audit_cost_usd':receipt['cost_usd'],'findings':[a['finding'] for a in audit['audits']]}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--private-root',required=True)
    print(canonical(recover(p.parse_args().private_root)))
