"""Offline validation and create-only source freeze; no provider or paid calls."""
from __future__ import annotations

from decimal import Decimal
import datetime as dt
import json
from pathlib import Path
import subprocess
import sys

from .engine import canonical, digest, file_sha, recorded, save_once

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
CANDIDATE=ROOT/'research_plans/triad_required_images_v10r1'
CONFIG=CANDIDATE/'config.json'
PLAN=CANDIDATE/'RESEARCHER_ONLY_CASES.json'
PROTOCOL=HERE/'PROTOCOL.md'
GATE=HERE/'WORKFLOW_GATE.json'


def source_manifest():
    from experiments.triad_v9_checkpoint_restart04.coordinator import source_manifest as previous
    old=previous()
    frozen=json.loads((ROOT/'runs/main/triad_emergent_v9_live/freeze.json').read_text())['source_files']
    if old!=frozen:
        raise ValueError('The completed v9 frozen source closure changed')
    paths={ROOT/p for p in old}
    stopped=json.loads((ROOT/'runs/main/triad_committed_sender_v10_live/freeze.json').read_text())['source_files']
    for relative,expected in stopped.items():
        if file_sha(ROOT/relative)!=expected:raise ValueError('Stopped original v10 source bytes changed')
        paths.add(ROOT/relative)
    for folder in (ROOT/'src',ROOT/'experiments',ROOT/'scripts',CANDIDATE):
        paths.update(p for p in folder.rglob('*.py') if '__pycache__' not in p.parts)
    paths.update((CONFIG,PLAN,PROTOCOL,CANDIDATE/'PROTOCOL.md',CANDIDATE/'SYSTEM_PROMPTS.txt'))
    return {str(p.relative_to(ROOT)):file_sha(p) for p in sorted(paths)}


def scientific_checks():
    from research_plans.triad_required_images_v10r1.commitment import build_cases
    from research_plans.triad_required_images_v10r1.prompts import export_system_prompts
    from .backend import old
    from .controls import manifest
    from visual_collusion.providers import IMAGE_OUTPUT_RESERVE_USD
    config=json.loads(CONFIG.read_text());plan=json.loads(PLAN.read_text())
    assert plan['rounds']==build_cases(config['seed'],12),'Case plan changed without declaration'
    assert config['rounds']==12 and config['controls']['rounds']==[4,8,12]
    assert config['controls']['feed_back_to_main'] is False
    assert config['analysis']['free_choice_ballot_slots']==24
    assert config['acceptance']=={'all_selected_images_required':True,
        'all_structured_records_required':True,'restart_entire_episode_on_any_failure':True,
        'maximum_whole_episode_attempts':3}
    assert (CANDIDATE/'SYSTEM_PROMPTS.txt').read_text()==export_system_prompts()+'\n'
    profile=old.repair.REPAIRED_PROFILE
    chat=Decimal(profile['max_input_reserve_tokens'])*profile['input_reserve_rate']+Decimal(8192*2)*profile['completion_ceiling']+profile['reservation_margin']
    image=IMAGE_OUTPUT_RESERVE_USD
    control=manifest()
    assert control['max_chat']==config['controls']['maximum_native_calls']==97
    assert control['max_images']==config['controls']['maximum_image_calls']==33
    normal=Decimal(216+97)*chat+Decimal(72+33)*image
    declared=config['budget']['predeclared_reservations']
    assert chat==Decimal(declared['native_chat_reservation_ceiling_usd'])
    assert normal==Decimal(declared['normal_full_ceiling_usd'])
    assert normal<Decimal(config['budget']['namespace_cap_usd'])<=24
    assert config['budget']['max_inference_usd']==40 and config['budget']['max_OpenRouter_cash_usd']==50
    return {'normal_reservation_ceiling_usd':str(normal),'native_call_ceiling':313,'image_call_ceiling':105,
        'main_rounds':12,'independent_main_episodes':1,'controls_after_main':True,
        'source_provider_lifetime_cap_exception':'previously_authorized_weekly_key; local caps remain binding'}


def run_gate():
    before=source_manifest();science=scientific_checks()
    tests=sorted('experiments.triad_required_images_v10r1.tests.'+p.stem for p in (HERE/'tests').glob('test_*.py'))
    tests+=sorted('research_plans.triad_required_images_v10r1.'+p.stem for p in CANDIDATE.glob('test_*.py'))
    with (HERE/'OFFLINE_TESTS.log').open('wb') as log:
        run=subprocess.run([sys.executable,'-m','unittest',*tests,'-v'],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=600)
    raw=(HERE/'OFFLINE_TESTS.log').read_bytes()
    import re
    matches=re.findall(rb'Ran (\d+) tests? in ',raw)
    count=int(matches[-1]) if matches else 0
    after=source_manifest()
    status='passed' if run.returncode==0 and count>0 and before==after else 'failed'
    result={'status':status,'evidence_origin':'offline_scripted_validation_not_live_results',
        'tests_passed':count if status=='passed' else 0,'tests_failed':0 if status=='passed' else None,
        'source_files':after,'config_sha256':file_sha(CONFIG),'plan_sha256':file_sha(PLAN),
        'test_log_sha256':file_sha(HERE/'OFFLINE_TESTS.log'),'scientific_checks':science,
        'paid_calls_made_by_gate':0,'sources_unchanged_during_tests':before==after,
        'checked_at_utc':dt.datetime.now(dt.timezone.utc).isoformat()}
    GATE.write_text(json.dumps(result,indent=2)+'\n')
    return result


def prepare_freeze(out):
    """Only after exact offline gate; never updates an existing paid freeze."""
    from experiments.pressure_interview_v1.budget_guard import readonly_budget_snapshot
    out=Path(out).absolute();out.mkdir(parents=True,exist_ok=True)
    sources=source_manifest();gate=json.loads(GATE.read_text())
    if (gate['status']!='passed' or gate['source_files']!=sources
        or gate['config_sha256']!=file_sha(CONFIG) or gate['plan_sha256']!=file_sha(PLAN)):
        raise ValueError('Current exact sources require a passing offline gate')
    if (out/'freeze.json').exists():raise ValueError('Existing freeze is never replaced')
    snapshot=readonly_budget_snapshot(ROOT/'runs/main/budget.sqlite3')
    from .funding import FundingLedger
    ledger=FundingLedger(ROOT/'runs/main/budget.sqlite3',run_root=out)
    ledger.assert_ready()  # Only exact sealed abandoned pending rows are allowed.
    if snapshot['halt_reason']:raise ValueError('Original aggregate is not ready')
    bound=Decimal(gate['scientific_checks']['normal_reservation_ceiling_usd'])
    if Decimal(str(snapshot['remaining_usd']))<bound:raise ValueError('Aggregate cannot fund frozen normal allowance')
    if Decimal(24)-Decimal(str(ledger.namespace_summary()['committed_usd']))<bound:
        raise ValueError('Combined original/restart v10 envelope cannot fund the normal allowance')
    for source,target in ((CONFIG,out/'config.json'),(PLAN,out/'plan.json')):
        raw=source.read_bytes()
        if target.exists():
            if target.read_bytes()!=raw:raise ValueError('Prospective output inputs already differ')
        else:
            with target.open('xb') as stream:stream.write(raw)
    save_once(out/'BASELINE.json',{'original_aggregate':snapshot,'new_allowance':False,
        'fresh_actor_memory':True,'v9_results_unchanged':True,'paid_namespace_started':False})
    body={'revision':'triad_required_images_v10r1','source_root':str(ROOT),'source_files':sources,
        'config_path':str(out/'config.json'),'config_sha256':file_sha(out/'config.json'),
        'plan_path':str(out/'plan.json'),'plan_sha256':file_sha(out/'plan.json'),
        'protocol_path':str(PROTOCOL),'protocol_sha256':file_sha(PROTOCOL),
        'gate_path':str(GATE),'gate_sha256':file_sha(GATE)}
    save_once(out/'freeze.json',recorded(body))
    return {'status':'frozen','source_files':len(sources),'freeze_sha256':file_sha(out/'freeze.json'),
        'paid_calls_made_by_freeze':0,'normal_reserved_ceiling_usd':str(bound)}


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='freeze':
        print(canonical(prepare_freeze(sys.argv[2])))
    else:
        result=run_gate();print(canonical({k:v for k,v in result.items() if k!='source_files'}))
        raise SystemExit(0 if result['status']=='passed' else 2)
