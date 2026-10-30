"""Read-only original ledger + hash-checked committed V16 deltas, never reset."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess

from .events import IntegrityError

PREFIXES = ('triad_committed_sender_v10:', 'triad_required_images_v10r1:',
    'triad_ascii_records_v10r2:', 'triad_output_room_v10r3:', 'triad_picture_coordination_v11:',
    'triad_intent_coordination_v12:', 'triad_sail_coordination_v13:', 'triad_meta_coordination_v14:',
    'triad_glm_restored_v15:', 'triad_document_carrier_v16:')


def reconcile(original_database, repository):
    with sqlite3.connect(Path(original_database).resolve().as_uri() + '?mode=ro&immutable=1', uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = {r['request_id']: dict(r) for r in db.execute('SELECT request_id,actual_units,reserved_units,status FROM requests')}
    if len(rows) != 1780:
        raise IntegrityError('Expected the original 1780-row baseline; use a separately audited recovery for another ledger')
    names = subprocess.check_output(['git','ls-tree','-r','--name-only','HEAD',
        'runs/main/triad_document_carrier_v16_live/ledger_deltas'],cwd=repository,text=True).splitlines()
    deltas = []
    for name in names:
        if not name.endswith('.json'):
            continue
        value = json.loads(subprocess.check_output(['git','show','HEAD:'+name],cwd=repository))
        body = value['body']
        encodings = [ensure for ensure in [True,False] if hashlib.sha256(json.dumps(body['rows'],sort_keys=True,
            separators=(',',':'),ensure_ascii=ensure).encode()).hexdigest() == body['rows_sha256']]
        if not encodings:
            raise IntegrityError('Historical delta rows do not match their recorded digest')
        deltas.append((body['recorded_at_unix'],name,body,encodings[0]))
    proofs = []
    for _,name,body,encoding in sorted(deltas):
        for row in body['rows']:
            rows[row['request_id']] = {k:row[k] for k in ['request_id','actual_units','reserved_units','status']}
        proofs.append({'path':name,'rows':len(body['rows']),'rows_now':body['rows_now'],
                       'rows_digest_verified':True,'digest_ensure_ascii':encoding})
    if len(rows) != 2090:
        raise IntegrityError('Historical coverage differs from audited2090; do not silently discard liabilities')
    cost = lambda r:r['actual_units'] if r['actual_units'] is not None else r['reserved_units']
    return {'rows':len(rows), 'aggregate_committed_usd':sum(cost(r) for r in rows.values())/1e9,
        'program_committed_usd':sum(cost(r) for r in rows.values() if r['request_id'].startswith(PREFIXES))/1e9,
        'uncertain_reserve_usd':sum(r['reserved_units'] for r in rows.values() if r['actual_units'] is None)/1e9,
        'settled_usd':sum(r['actual_units'] or 0 for r in rows.values())/1e9,
        'source':'Read-only1780-row original ledger plus every committed V16 ledger delta through2090',
        'delta_proofs':proofs}


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--original-database',required=True)
    parser.add_argument('--repository',default='.'); parser.add_argument('--output',required=True)
    args=parser.parse_args()
    Path(args.output).write_text(json.dumps(reconcile(args.original_database,args.repository),indent=2)+'\n')
