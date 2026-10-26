"""Explicit offline verification, free preflight and review-pausing execution."""
import argparse
import json
from pathlib import Path
import time

from experiments.pressure_interview_v1.budget_guard import readonly_budget_snapshot
from visual_collusion.budget import BudgetError
from visual_collusion.providers import ProviderError
from . import design as d, review, auxiliary
from .backend import Backend
from .controls import Controls
from .funding import AGGREGATE, PREFIXES, Ledger, Guard
from .provider import Provider, Fault
from .runner import Runner, DEFAULT_OUT
from .storage import canonical, sha, put, read, lock, IntegrityError


def funding_status(ledger):
    with ledger._connect() as db:
        rows=db.execute('SELECT request_id,COALESCE(actual_units,reserved_units) AS units FROM requests').fetchall()
    program=sum(r['units'] for r in rows if r['request_id'].startswith(PREFIXES))/1e9
    aggregate=sum(r['units'] for r in rows)/1e9
    return {'combined_v10_v11_committed_usd':program,'aggregate_committed_usd':aggregate,
            'combined_cap_usd':24,'aggregate_cap_usd':40,'cash_cap_usd':50,'Codex_excluded':True}


def actual_backend(runner,ledger,context):
    guard=Guard(AGGREGATE,runner.out,context['attempt_id'],runner.verify,ledger=ledger)
    provider=Provider(guard,runner.out/'provider_evidence')
    candidates=list((runner.out/'preflights').glob('*.json'))+list((runner.out/'provider_preflights').glob('*.json'))
    adopted=False
    for path in sorted(candidates,key=lambda p:p.stat().st_mtime,reverse=True):
        record=read(path)
        if record.get('freeze_file_sha256')!=sha((runner.out/'freeze.json').read_bytes()):continue
        try:provider.adopt_preflight(record['provider']);adopted=True;break
        except (IntegrityError,KeyError):continue
    if not adopted:
        report=provider.preflight(minimum_remaining=.060384)
        record={'provider':report,'freeze_file_sha256':sha((runner.out/'freeze.json').read_bytes())}
        put(runner.out/'provider_preflights'/(sha(canonical(record).encode())+'.json'),record)
    return Backend(provider,runner.out/'provider_evidence')


def preflight(runner,ledger):
    runner.verify();context,_=runner.current()
    if context is None:context={'attempt_id':'ctrl.r04.original.a01'}
    guard=Guard(AGGREGATE,runner.out,context['attempt_id'],runner.verify,ledger=ledger)
    provider=Provider(guard,runner.out/'provider_evidence')
    start=readonly_budget_snapshot(AGGREGATE)
    status=funding_status(ledger)
    normal=float(d.CONFIG['budget']['normal_reservation_ceiling_usd'])
    if status['combined_v10_v11_committed_usd']+normal>24 or status['aggregate_committed_usd']+normal>40:
        raise IntegrityError('Initial full normal-operation reservation does not fit remaining caps')
    result=provider.preflight(minimum_remaining=normal)
    end=readonly_budget_snapshot(AGGREGATE)
    if start['request_count']!=end['request_count']:raise IntegrityError('Free preflight unexpectedly created paid requests')
    record={'status':'passed','paid_calls_made':0,'request_count_before':start['request_count'],
            'request_count_after':end['request_count'],'funding':status,
            'required_normal_reservation_usd':normal,'provider':result,
            'freeze_file_sha256':sha((runner.out/'freeze.json').read_bytes()),'checked_at_unix':time.time()}
    put(runner.out/'preflights'/(sha(canonical(record).encode())+'.json'),record)
    return {k:v for k,v in record.items() if k!='provider'}


def audit(runner):
    runner.verify();commits=runner.commits();rows=[]
    for c in commits:
        case=d.cases()[c['round']-1]
        rows.append({'round':c['round'],'attempt':c['attempt'],'target':case['private_target'],'correct':case['oracle'],
                     'ballots':c['ballots'],'A_binding_is_assigned':True,
                     'free_recipient_correct':sum(c['ballots'][r]==case['oracle'] for r in ('B','C')),
                     'free_recipient_target':sum(c['ballots'][r]==case['private_target'] for r in ('B','C')),
                     'grant_state':c['settlement']['grant_state'],'scores':c['settlement']['scores']})
    manifest=runner.out/'AUXILIARY_MANIFEST.json';assessments=[]
    if manifest.exists():
        for job in read(manifest)['jobs']:
            path=runner.out/'reviews'/job['job_id']/'assessment.json'
            assessments.append({'job_id':job['job_id'],'status':read(path)['status'] if path.exists() else 'not_run'})
    return {'main':runner.status(),'rounds':rows,'controls':Controls(runner).status(),
            'auxiliary_assessments':assessments,'unknown_or_invalid_preserved':True,
            'claimed_covert_success':False,'interpretation':'Artifact/control review required; agreement alone is insufficient.'}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('init','status','preflight','advance','workflow','controls','auxiliary','launch-review','ingest-review','audit','verify'))
    parser.add_argument('--out',type=Path,default=DEFAULT_OUT)
    parser.add_argument('--through-round',type=int,default=12)
    parser.add_argument('--maximum-actor-stages',type=int)
    parser.add_argument('--job-id');parser.add_argument('--agent-id');parser.add_argument('--actual-model')
    parser.add_argument('--raw-file',type=Path)
    args=parser.parse_args(argv)
    if args.command in ('launch-review','ingest-review') and (not args.job_id or not args.agent_id):
        parser.error('--job-id and --agent-id are required for review operations')
    if args.command=='verify':
        from .build import main as build
        return build()
    runner=Runner(args.out)
    try:
        with lock(runner.out):
            if args.command=='init':result=runner.init()
            elif args.command=='status':result=runner.status()
            elif args.command=='audit':result=audit(runner)
            elif args.command=='auxiliary':result={'jobs':auxiliary.prepare(runner),'paid_calls':0}
            elif args.command=='launch-review':
                runner.verify();review.launch(runner.out,args.job_id,args.agent_id,args.actual_model)
                result={'status':'launch_recorded','job_id':args.job_id}
            elif args.command=='ingest-review':
                runner.verify()
                if args.raw_file is None:raise IntegrityError('Actual first raw answer file required')
                assessment=review.ingest(runner.out,args.job_id,args.raw_file.read_text(),args.agent_id)
                result={'status':assessment['status'],'job_id':args.job_id,'raw_sha256':assessment['raw_sha256']}
            else:
                runner.verify();ledger=Ledger(AGGREGATE,runner.out)
                if args.command=='preflight':result=preflight(runner,ledger)
                else:
                    # Every production advance requires a recorded free launch preflight.
                    files=list((runner.out/'preflights').glob('*.json')) if (runner.out/'preflights').exists() else []
                    if not files:raise IntegrityError('Run the free launch preflight first')
                    for record_path in files:
                        if read(record_path)['freeze_file_sha256']!=sha((runner.out/'freeze.json').read_bytes()):
                            raise IntegrityError('Launch preflight belongs to a different source freeze')
                    backends={}
                    def factory(context):
                        key=context['attempt_id']
                        if key not in backends:backends[key]=actual_backend(runner,ledger,context)
                        return backends[key]
                    if args.command in ('advance','workflow'):
                        result=runner.advance(factory,through_round=args.through_round,
                                              maximum_actor_stages=args.maximum_actor_stages,ledger=ledger)
                    if args.command=='controls' or args.command=='workflow' and result.get('status')=='main_complete':
                        result=Controls(runner).advance(factory,ledger)
                        if args.command=='workflow':
                            jobs=auxiliary.prepare(runner)
                            pending=[j for j in jobs if not (runner.out/'reviews'/j['job_id']/'assessment.json').exists()]
                            result={'status':'awaiting_auxiliary_reviews' if pending else 'pipeline_complete',
                                    'pending_jobs':pending,'controls':result}
        print(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False))
    except (IntegrityError,Fault,ValueError,BudgetError,ProviderError) as failure:
        # A safe bounded class/code; never serialize a transport request or credential.
        print(json.dumps({'status':'held','error_class':type(failure).__name__,
                          'code':failure.code if isinstance(failure,Fault) else str(failure)}))
        raise SystemExit(2)


if __name__=='__main__':main()
