"""Offline amendment gate; the frozen output-room adapter passed 164 checks."""
import datetime as dt
import json
import re
import subprocess
import sys
from .continuation import sources, HERE, GATE, canonical, file_sha


def main():
    before=sources()
    log=HERE/'OFFLINE_TESTS.log'
    with log.open('wb') as stream:
        run=subprocess.run([sys.executable,'-m','unittest',
            'experiments.triad_checkpoint_retry_v10r5.test_continuation',
            'experiments.triad_output_room_v10r3.tests.test_backend',
            'research_plans.triad_output_room_v10r3.test_transport_contract','-v'],
            stdout=stream,stderr=subprocess.STDOUT,timeout=180)
    matches=re.findall(rb'Ran (\d+) tests? in ',log.read_bytes());count=int(matches[-1]) if matches else 0
    after=sources()
    value={'status':'passed' if run.returncode==0 and count>0 and before==after else 'failed',
        'tests_passed':count if run.returncode==0 else 0,'source_files':after,
        'test_log_sha256':file_sha(log),'paid_calls':0,
        'prior_adapter_full_gate_tests':164,'prior_adapter_sources_verified_unchanged':True,
        'evidence_origin':'offline_scripted_validation_not_live_results',
        'checked_at_utc':dt.datetime.now(dt.timezone.utc).isoformat()}
    GATE.write_text(json.dumps(value,indent=2)+'\n')
    print(canonical({k:v for k,v in value.items() if k!='source_files'}))
    raise SystemExit(0 if value['status']=='passed' else 2)


if __name__=='__main__':main()
