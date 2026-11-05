"""One prospective successor freeze; original freeze and allocation remain intact."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time

from next_batch.oversight_v17.finance import canonical, FundingHold
from .design import COMMON, ROLE, TOOL, schedule, PAIRS
from .funding import StudyLedger

ROOT=Path(__file__).resolve().parents[2]


def run(private_root):
    root=Path(private_root)
    if (root/'freeze_002.json').exists():raise FundingHold('Successor freeze already exists')
    ledger=StudyLedger(root/'budget.sqlite3');summary=ledger.summary()
    if any(c['kind'] in {'actor','monitor'} for c in summary['calls']):
        raise FundingHold('Amendment002 must precede all participant and audit calls')
    canary=json.loads((root/'delivery_check.json').read_text())
    if canary.get('passed') is not True:raise FundingHold('Original technical canary must pass')
    original=(root/'freeze.json').read_bytes();new=deepcopy(json.loads(original))
    new.update({'at_unix':time.time(),'parent_freeze_sha256':hashlib.sha256(original).hexdigest(),
        'amendment':2,'participant_calls_before_amendment':0,
        'amendment_reason':'Prospective budget-based replication increase and removal of endogenous document-failure rerolls; no participant outcomes observed.',
        'technical_canary_sha256':hashlib.sha256((root/'delivery_check.json').read_bytes()).hexdigest(),
        'assignment':schedule(),'cohorts_per_arm':PAIRS,'pairs':PAIRS,
        'receiver_calls':PAIRS*12,'B_image_calls':PAIRS*6,'audit_probability':1/10,
        'starting_bank':10000,'inference':'Exact1024 paired label swaps; missingness bounds; no vote-level independent CI'})
    source_files=list(sorted((ROOT/'next_batch/receiver_incentives_v18').glob('*.py')))+[
        ROOT/'next_batch/oversight_v17'/name for name in
        ['openrouter.py','finance.py','documents.py','events.py','reviewer.py','images.py']]
    new['source_sha256']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files}
    new['protocol_sha256']=hashlib.sha256((ROOT/'research_plans/receiver_incentives_v18/PROTOCOL.md').read_bytes()).hexdigest()
    new['prompt_sha256']={'common':hashlib.sha256(COMMON.encode()).hexdigest(),
        'roles':{k:hashlib.sha256(v.encode()).hexdigest() for k,v in ROLE.items()},
        'tool':hashlib.sha256(canonical(TOOL).encode()).hexdigest()}
    new['budget']={'new_allocation_usd':9.15,'funds_added_usd':0.,'receivers_upper_usd':1.4688,
        'images_upper_usd':1.68,'reviewer_upper_usd':5.41672,'canary_upper_usd':.0390112,
        'technical_reserve_usd':.5,'total_envelope_usd':9.1045312}
    if new['budget']['total_envelope_usd']>min(9.15,
        (24e9-summary['seed']['program_units'])/1e9,
        (40e9-summary['seed']['aggregate_units'])/1e9,
        summary['seed']['period_remaining_units']/1e9):
        raise FundingHold('Full amended envelope does not fit unchanged inherited caps')
    allocation=ledger.amend_allocation('9.15',new['amendment_reason'])
    new['allocation_amendment']=allocation
    new.pop('run_id',None)
    new['run_id']=hashlib.sha256(canonical(new).encode()).hexdigest()[:12]
    data=(canonical(new)+'\n').encode();(root/'freeze_002.json').write_bytes(data)
    pointer={'file':'freeze_002.json','sha256':hashlib.sha256(data).hexdigest()}
    (root/'ACTIVE_FREEZE.json').write_text(canonical(pointer)+'\n')
    public=ROOT/'research_plans/receiver_incentives_v18'
    (public/'FREEZE_002.json').write_bytes(data)
    (public/'ACTIVE_FREEZE.json').write_text(canonical(pointer)+'\n')
    return {'run_id':new['run_id'],'pairs':PAIRS,'participant_calls_before_amendment':0,
            'allocation_amendment':allocation,'freeze_sha256':pointer['sha256']}
