"""Offline validation, free metadata preflight, and durable review-pausing producers."""
import argparse,json,signal,time,sys,subprocess
from pathlib import Path
from next_batch.v15_glm_restored.storage import canonical,put,read,sha,lock,IntegrityError
from visual_collusion.budget import BudgetError,PendingPaidRequest
from visual_collusion.providers import ProviderError
from experiments.pressure_interview_v1.budget_guard import readonly_budget_snapshot
from .runner import Runner,arm_list,budget_status
from .funding import DEFAULT_OUT,AGGREGATE,Ledger,Guard,NORMAL_RESERVATION_USD
from .provider import Provider,Fault
from . import review


def main(argv=None):
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('command',choices=('init','status','verify','preflight','advance','launch-review','ingest-review','report'))
 p.add_argument('--out',type=Path,default=DEFAULT_OUT);p.add_argument('--arm');p.add_argument('--through-round',type=int,default=12);p.add_argument('--maximum-actor-stages',type=int)
 p.add_argument('--job-id');p.add_argument('--agent-id');p.add_argument('--raw-file',type=Path);p.add_argument('--actual-model')
 args=p.parse_args(argv);runner=Runner(args.out)
 try:
  if args.command=='init':
   with lock(runner.out):result=runner.init()
  elif args.command=='status':result=runner.status(args.arm)
  elif args.command=='verify':runner.verify(args.arm);result={'status':'verified','new_paid_calls':0,'run':str(runner.out)}
  elif args.command in ('launch-review','ingest-review'):
   if not args.job_id or not args.agent_id:raise IntegrityError('Actual job and fresh agent IDs required')
   runner.verify()
   if args.command=='launch-review':result=review.launch(runner.out,args.job_id,args.agent_id,args.actual_model)
   else:
    if args.raw_file is None:raise IntegrityError('First raw output file required')
    result=review.ingest(runner.out,args.job_id,args.raw_file.read_text(),args.agent_id)
    result={k:result[k]for k in ('status','raw_sha256','agent_id','packet_sha256')}
  elif args.command=='preflight':
   with lock(runner.out):
    runner.verify();before=readonly_budget_snapshot(AGGREGATE);ledger=Ledger(AGGREGATE,runner.out);budget=budget_status(ledger);normal=float(NORMAL_RESERVATION_USD)
    if budget['aggregate_committed_usd']+normal>40 or budget['combined_program_committed_usd']+normal>24:raise IntegrityError('Entire normal60-round reservation does not fit unchanged caps')
    guard=Guard(AGGREGATE,runner.out,'b01.r01.a01',runner.verify,ledger=ledger);provider=Provider(guard,runner.out/'provider_evidence');metadata=provider.preflight(minimum_remaining=normal)
    after=readonly_budget_snapshot(AGGREGATE)
    if after['request_count']!=before['request_count']:raise IntegrityError('Free preflight unexpectedly spent inference money')
    record={'status':'passed','new_paid_calls':0,'funding':budget,'normal60_round_reservation_usd':normal,'provider':metadata,'freeze_sha256':runner.freeze_info()['sha256'],'checked_at_unix':time.time()}
    put(runner.out/'preflights'/(sha(canonical(record).encode())+'.json'),record)
    result={k:v for k,v in record.items()if k!='provider'}
  elif args.command=='advance':
   if args.arm is None:raise IntegrityError('Choose one fixed independent arm producer')
   current_freeze=runner.freeze_info()['sha256']
   if not any(read(path).get('status')=='passed'and read(path).get('freeze_sha256')==current_freeze for path in(runner.out/'preflights').glob('*.json')):raise IntegrityError('Entire study free preflight for current freeze required')
   def stopped(signum,frame):
    put(runner.out/'STOP_REQUESTED.json',{'reason':'operator_signal','automatic_restart_forbidden':True});raise SystemExit(130)
   signal.signal(signal.SIGTERM,stopped)
   with lock(runner.out/'arms'/args.arm):result=runner.advance(args.arm,args.through_round,args.maximum_actor_stages)
  else:
   from .report import build_report
   runner.verify();result=build_report(runner)
  print(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False),flush=True)
 except PendingPaidRequest:
  print(json.dumps({'status':'funding_barrier','code':'An active or recovering paid identity gates new work; no request reposted.'}));return 3
 except (IntegrityError,Fault,BudgetError,ProviderError,ValueError)as failure:
  print(json.dumps({'status':'held','error_class':type(failure).__name__,'code':failure.code if isinstance(failure,Fault)else str(failure)}));return 2
 return 0

if __name__=='__main__':raise SystemExit(main())
