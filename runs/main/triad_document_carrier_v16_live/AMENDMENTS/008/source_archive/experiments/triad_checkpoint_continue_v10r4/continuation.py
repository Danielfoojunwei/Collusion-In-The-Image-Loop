"""Continue at R5 without rewriting the immutable R1--R4 history.

The original engine verifies its own config, requests, images and checkpoint
chain. A separately frozen amendment governs only subsequent provider calls.
An exact old abort marker is superseded; no general integrity guard is waived.
"""
from pathlib import Path
import argparse
import datetime as dt
import json
import signal

from experiments.triad_ascii_records_v10r2 import coordinator as prior
from experiments.triad_ascii_records_v10r2 import funding as prior_funding
from experiments.triad_output_room_v10r3 import backend as output_room
from experiments.triad_output_room_v10r3 import funding
from experiments.triad_output_room_v10r3.coordinator import Coordinator as OutputCoordinator
from experiments.triad_ascii_records_v10r2.engine import Engine, canonical, digest, file_sha, read_record, recorded, save_once, safe_path

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'runs/main/triad_ascii_records_v10r2_live/episodes/e01'
BASELINE=ROOT/'runs/main/triad_output_room_v10r3_live/episodes/e01'
HERE=Path(__file__).resolve().parent
AMENDMENT=OUT/'continuation_v10r4'
MANIFEST=AMENDMENT/'freeze.json'
GATE=HERE/'WORKFLOW_GATE.json'


def sources():
    frozen=json.loads((BASELINE/'freeze.json').read_text())['source_files']
    for relative, expected in frozen.items():
        if file_sha(ROOT/relative)!=expected:
            raise prior.CoordinatorError('prior_frozen_source_bytes_changed')
    paths={ROOT/p for p in frozen}
    paths.update(p for folder in ('src','experiments','scripts') for p in (ROOT/folder).rglob('*.py') if '__pycache__' not in p.parts)
    return {str(p.relative_to(ROOT)):file_sha(p) for p in sorted(paths)}


class Backend(output_room.Backend):
    """Translate typed faults into the original coordinator's exception type."""
    def perform(self, request):
        try:
            return super().perform(request)
        except output_room.TechnicalFault as error:
            raise prior.TechnicalFault(error.fault,error.request_id,permanent=error.permanent) from None


class Coordinator(prior.Coordinator):
    serve=OutputCoordinator.serve

    def __init__(self, out=OUT, **kwargs):
        super().__init__(out,**kwargs)
        if self.out!=OUT:
            raise prior.CoordinatorError('exact_user_selected_checkpoint_required')

    def verify_freeze(self):
        self._require_lock()
        manifest=read_record(MANIFEST)
        if manifest['source_files']!=sources():
            raise prior.CoordinatorError('continuation_sources_changed')
        for name,path in [('original_freeze',self.out/'freeze.json'),('original_config',self.config_path),
                          ('original_plan',self.plan_path),('backend_config',BASELINE/'config.json'),
                          ('superseded_abort',self.out/'EPISODE_ABORTED.json'),('gate',GATE)]:
            if file_sha(path)!=manifest[name+'_sha256']:
                raise prior.CoordinatorError('continuation_bound_input_changed')
        gate=json.loads(GATE.read_text())
        if gate['status']!='passed' or gate['source_files']!=manifest['source_files']:
            raise prior.CoordinatorError('continuation_exact_offline_gate_required')
        for relative,expected in manifest['preserved_prefix'].items():
            if file_sha(self.out/relative)!=expected:
                raise prior.CoordinatorError('preserved_checkpoint_prefix_changed')
        sha=file_sha(MANIFEST)
        if self._freeze_sha256 is not None and self._freeze_sha256!=sha:
            raise prior.CoordinatorError('continuation_manifest_changed')
        self._freeze_sha256=sha
        return {'status':'verified','freeze_sha256':sha,'source_files':len(manifest['source_files'])}

    def init(self):
        self._require_lock();self.verify_freeze()
        if self.engine is None:
            self.engine=Engine(self.out,json.loads(self.config_path.read_text()),json.loads(self.plan_path.read_text()))
        if self.engine.status()['committed_rounds']<4:
            raise prior.CoordinatorError('preserved_four_round_checkpoint_missing')
        if self.ledger is None:
            self.ledger=funding.FundingLedger(self.aggregate_path,run_root=self.out)
        save_once(AMENDMENT/'coordinator_contract.json',recorded({'freeze_sha256':self._freeze_sha256,
            'resume_after_round':4,'next_round':5,'retain_prior_actor_memory':True,'old_abort_superseded_by_user':True,
            'original_engine_config_unchanged':True,'subsequent_total_output_tokens':16384,
            'temperature':1.0,'reasoning_effort':'high','mixed_configuration_episode':True}))
        return self.status()

    def _assert_no_holds(self):
        if (self.out/'operator_stop.requested.json').exists() or (AMENDMENT/'STOPPED.json').exists():
            raise prior.CoordinatorError('continuation_stopped')
        if any((self.out/'coordinator_holds').glob('*.json')):
            raise prior.CoordinatorError('durable_integrity_hold')
        manifest=read_record(MANIFEST)
        if file_sha(self.out/'EPISODE_ABORTED.json')!=manifest['superseded_abort_sha256']:
            raise prior.CoordinatorError('unexpected_abort_marker')

    def _get_backend(self, attempt_id=None):
        if self.backend is None or self._backend_attempt!=attempt_id:
            self.guard=funding.create_guard(self.aggregate_path,run_root=self.out,
                evidence_dir=self.out/'provider_evidence',active_prefix=attempt_id,ledger=self.ledger,
                before_dispatch=lambda:self._before_dispatch(attempt_id))
            config=json.loads((BASELINE/'config.json').read_text())
            self.backend=Backend(config,self.out,self.aggregate_path,guard=self.guard,preflight=False)
            self._backend_attempt=attempt_id;self._preflight_ok=False
        return self.backend

    def preflight(self):
        self._ready();self._assert_no_holds();self._seal_abandonments();self.ledger.assert_ready()
        active=self.engine.status()['active_attempt']
        report=self._get_backend(active['attempt_id'] if active else None).preflight()
        minimum=21.840192
        if report['key']['remaining_usd']<minimum:
            raise prior.CoordinatorError('continuation_provider_bound_insufficient')
        path=AMENDMENT/'launch_preflight.json'
        if not path.exists():
            save_once(path,recorded({'freeze_sha256':self._freeze_sha256,'minimum_remaining_usd':minimum,
                'remaining_usd':report['key']['remaining_usd'],'preflight_report_sha256':digest(report),
                'paid_calls_made_by_preflight':0}))
        self._check_launch_preflight();self._preflight_ok=True
        return {'status':'ready','paid_requests_made':0,'provider':report,'budget':self._budget_status()}

    def _check_launch_preflight(self):
        value=read_record(AMENDMENT/'launch_preflight.json')
        if value['freeze_sha256']!=self._freeze_sha256 or value['minimum_remaining_usd']!=21.840192:
            raise prior.CoordinatorError('continuation_launch_binding_differs')

    def _seal_abandonments(self):
        results=[]
        for path in sorted((self.out/'attempts').glob('r*/a*/abandonment.json')):
            marker=read_record(path)
            for module in (prior_funding,funding):
                results.append(module.seal_abandoned_attempt(self.out,marker['attempt_id'],path,ledger=self.ledger))
        return results

    def _abandon(self, fault):
        abandoned=self.engine.abandon_current(fault)
        sealed=funding.seal_abandoned_attempt(self.out,abandoned['attempt_id'],abandoned['path'],ledger=self.ledger)
        self.backend=self.guard=None;self._backend_attempt=None;self._preflight_ok=False
        return {'attempt_id':abandoned['attempt_id'],'funding':sealed}

    def _abort_episode(self, request, code, result=None):
        if result is not None:
            save_once(self.out/'failed_required_outputs'/(request['request_id']+'.json'),recorded({
                'request_id':request['request_id'],'result':result,'accepted_as_main_observation':False}))
        value={'status':'continuation_stopped','code':code,'round':request.get('round'),
            'stage':request.get('stage'),'request_id':request.get('request_id'),
            'previous_checkpoints_preserved':True,'automatic_further_paid_retry':False}
        save_once(AMENDMENT/'STOPPED.json',recorded(value))
        return value

    def status(self):
        result=super().status()
        return {**result,'execution_amendment':'triad_checkpoint_continue_v10r4',
            'resumed_after_round':4,'original_temperature':1.0,'continuation_temperature':1.0,
            'original_output_tokens':8192,'continuation_output_tokens':16384,
            'mixed_configuration_episode':True}


def freeze():
    with prior.producer_lock(OUT):
        gate=json.loads(GATE.read_text());files=sources()
        if gate['status']!='passed' or gate['source_files']!=files:
            raise prior.CoordinatorError('exact_gate_required')
        engine=Engine(OUT,json.loads((OUT/'config.json').read_text()),json.loads((OUT/'plan.json').read_text()))
        state=engine.status()
        if state['committed_rounds']!=4 or state['active_attempt'] is not None:
            raise prior.CoordinatorError('exact_four_round_checkpoint_required')
        if engine.pending_operations():raise prior.CoordinatorError('pending_old_operation')
        ledger=funding.FundingLedger(funding.AGGREGATE,run_root=OUT);ledger.assert_ready()
        if 24-ledger.namespace_summary()['committed_usd']<21.840192:
            raise prior.CoordinatorError('combined_cap_insufficient')
        prefix={}
        for n in range(1,5):
            checkpoint=OUT/'checkpoints'/f'r{n:02d}.commit.json'
            commit=read_record(checkpoint)
            prefix[str(checkpoint.relative_to(OUT))]=file_sha(checkpoint)
            for path in (OUT/'attempts'/f'r{n:02d}'/f"a{commit['attempt']:02d}").rglob('*'):
                if path.is_file():prefix[str(path.relative_to(OUT))]=file_sha(path)
        body={'revision':'triad_checkpoint_continue_v10r4','source_files':files,'preserved_prefix':prefix,
            'resume_after_round':4,'next_round':5,'user_instruction':'Restart from round 4 last checkpoint',
            'supersedes_prior_full_episode_restart_requirement':True,'created_at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
            'original_freeze_sha256':file_sha(OUT/'freeze.json'),'original_config_sha256':file_sha(OUT/'config.json'),
            'original_plan_sha256':file_sha(OUT/'plan.json'),'backend_config_sha256':file_sha(BASELINE/'config.json'),
            'superseded_abort_sha256':file_sha(OUT/'EPISODE_ABORTED.json'),'gate_sha256':file_sha(GATE),
            'mixed_configuration_episode':True,'no_old_records_rewritten':True,'new_allowance':False}
        save_once(MANIFEST,recorded(body))
        return {'status':'frozen','paid_calls':0,'preserved_rounds':4,'next_round':5,'freeze_sha256':file_sha(MANIFEST)}


def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=('freeze','serve','status'));args=p.parse_args()
    if args.command=='freeze':print(canonical(freeze()));return
    c=Coordinator()
    def stop(signum,frame):c.request_stop();raise funding.OperatorStop()
    signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
    if args.command=='serve':c.serve();return
    with c.locked():print(canonical(c.init()))


if __name__=='__main__':main()
