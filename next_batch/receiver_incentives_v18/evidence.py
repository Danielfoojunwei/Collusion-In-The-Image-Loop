"""Post-collection public evidence, with private reasoning and notes excluded."""
import argparse
import hashlib
import json
from pathlib import Path

from next_batch.oversight_v17.events import Journal
from .engine import check_freeze, collect
from .design import load_stimuli
from .report import dump, natural


def export(private_root,output):
    root=Path(private_root);out=Path(output);freeze=check_freeze(root)
    if any(not (root/'cohorts'/g['cohort_id']/'completed.json').exists() for g in freeze['assignment']):
        raise RuntimeError('Evidence release requires all planned cohorts to finish')
    records=collect(root);sources=load_stimuli(root);assets=out/'all_documents';assets.mkdir(parents=True,exist_ok=True)
    source_public={};decisions=[];manifest=[];faults=[]
    def asset(name,raw,kind):
        (assets/name).write_bytes(raw)
        item={'path':'all_documents/'+name,'sha256':hashlib.sha256(raw).hexdigest(),'kind':kind}
        manifest.append(item);return item
    for ident,source in sources.items():
        source_public[ident]={'task':source['task'],'experimenter_disclosed_sponsor_preference':source['target'],
            'client_best':source['client_best'],'pages':[asset(f'{ident}_A_page{n}.png',raw,'actual_delivered_A_page')
                for n,raw in enumerate(source['pages'],1)]}
    for row in sorted(records,key=lambda r:(natural(r['pair_id']),r['arm'],r['round'])):
        journal=Journal(root/'cohorts'/row['cohort_id']);name=f"{row['cohort_id']}_r{row['round']}_{row['case_id']}"
        item={k:row[k] for k in ['pair_id','arm','cohort_id','round','case_id','target','client_best','B_choice','C_choice','audited','checkpoint_attempt']}
        for role in ['B','C']:
            parsed=row[role+'_result'];fields=parsed.get('fields') or {}
            item[role+'_public_report']=fields.get('public_report')
            item[role+'_validation']={'valid':parsed['valid'],'reason':parsed['reason'],
                'memory_available':parsed.get('memory_available'),'response_object':row[role+'_response']}
        item['B_delivered_pages']=[asset(f'{name}_B_page{n}.png',journal.get(ref),'actual_delivered_B_page')
            for n,ref in enumerate(row['B_page_refs'],1)]
        item['B_original_figure']=asset(f'{name}_B_original_figure.png',journal.get(row['B_figure_ref']),'original_generated_figure') if row['B_figure_ref'] else None
        item['B_render']=row['B_render'];decisions.append(item)
    for group in freeze['assignment']:
        for event in Journal(root/'cohorts'/group['cohort_id']).read():
            body=event['body'];kind=body['kind']
            if kind in {'trial_started','first_B','first_C','B_document','trial_committed'}:continue
            data=body['data']
            faults.append({'cohort_id':group['cohort_id'],'kind':kind,
                'data':{k:v for k,v in data.items() if k in ['round','attempt','error_type','classification','B_ballot_retained','checkpoint_restored','behavioral_retry']}})
    receipts=[]
    for path in sorted((root/'paid_calls').glob('*/receipt.json')):
        receipts.append(json.loads(path.read_text()))
    dump(out/'sources_public.json',source_public);dump(out/'decisions_public.json',decisions)
    dump(out/'assets_manifest.json',manifest);dump(out/'technical_events.json',faults)
    dump(out/'receipts_public.json',receipts)
    audit=root/'audit_result.private.json'
    if audit.exists():dump(out/'frontier_audit.json',json.loads(audit.read_text()))
    return {'public_trials':len(decisions),'assets':len(manifest),'technical_events':len(faults),'receipts':len(receipts)}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--private-root',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();print(json.dumps(export(a.private_root,a.output),indent=2))
