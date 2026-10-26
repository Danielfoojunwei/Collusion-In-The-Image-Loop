"""Create-only whole-experiment restart identities; no inference dispatch."""
from pathlib import Path
import json

from .coordinator import Coordinator,producer_lock
from .engine import EngineError,canonical,file_sha,read_record,recorded,safe_path,save_once


def next_episode(root):
    root=safe_path(root);root.mkdir(parents=True,exist_ok=True)
    with producer_lock(root):
        if (root/'ACCEPTED_EPISODE.json').exists():raise EngineError('A completed episode cannot be replaced')
        existing=sorted((root/'episodes').glob('e*'))
        if [p.name for p in existing]!=[f'e{i:02d}' for i in range(1,len(existing)+1)]:
            raise EngineError('Whole-episode identities must be contiguous')
        if len(existing)>=3:raise EngineError('Three whole-episode attempts exhausted; no unbounded retry')
        prior=None
        if existing:
            last=existing[-1]
            with producer_lock(last):
                markers=[p for p in (last/'EPISODE_ABORTED.json',last/'operator_stop.requested.json') if p.exists()]
                if not markers:raise EngineError('The previous episode has not been explicitly aborted')
                prior={'episode':last.name,'markers':{str(p):file_sha(p) for p in markers},
                       'freeze_sha256':file_sha(last/'freeze.json')}
                from .funding import FundingLedger,AGGREGATE,seal_abandoned_attempt
                from .engine import Engine
                ledger=FundingLedger(AGGREGATE,run_root=last)
                engine=Engine(last,json.loads((last/'config.json').read_text()),json.loads((last/'plan.json').read_text()))
                if engine.status()['active_attempt'] is not None:
                    stopped=engine.abandon_current({'technical':True,'code':'user_required_whole_episode_restart',
                        'failure_origin':'required_output_or_operator_stop; not evidence of collusion',
                        'partial_prefix_is_excluded_from_accepted_episode':True})
                    seal_abandoned_attempt(last,stopped['attempt_id'],stopped['path'],ledger=ledger)
                ledger.assert_ready()
        out=root/'episodes'/f'e{len(existing)+1:02d}'
        from .gate import prepare_freeze
        freeze=prepare_freeze(out)
        save_once(out/'WHOLE_EPISODE_ATTEMPT.json',recorded({'episode':out.name,
            'restart_from_round':1,'fresh_actor_memory':True,'prior_failed_episode':prior,
            'failed_attempts_and_charges_retained':True,'acceptance_is_conditional_on_usable_outputs':True}))
        return {'episode':out.name,'out':str(out),'freeze':freeze}


def accept_episode(out):
    """Only a complete image-bearing episode is accepted, never padded slots."""
    out=safe_path(out)
    if out.parent.name!='episodes':raise EngineError('An explicit whole-episode directory is required')
    c=Coordinator(out)
    with c.locked():
        c.init();c._assert_no_holds()
        if c.engine.status()['committed_rounds']!=12:raise EngineError('All twelve rounds must commit')
        selected=[]
        for n in range(1,13):
            for role in 'ABC':
                im=c.engine._image(n,role)
                if im is None:raise EngineError('Missing image cannot count in accepted twelve rounds')
                selected.append({'round':n,'role':role,'sha256':im['sha256'],'path':im['path']})
        commits={f'r{n:02d}':file_sha(out/'checkpoints'/f'r{n:02d}.commit.json') for n in range(1,13)}
        value={'status':'accepted','episode':out.name,'out':str(out),'rounds':12,
            'selected_main_images':36,'images':selected,'checkpoint_sha256':commits,
            'freeze_sha256':file_sha(out/'freeze.json'),'failed_episode_history_retained':True,
            'claim':'Twelve completed rounds conditional on valid required outputs, not twelve independent trials'}
        save_once(out.parent.parent/'ACCEPTED_EPISODE.json',recorded(value))
        return value


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('command',choices=('next','accept'));p.add_argument('path')
    a=p.parse_args();print(canonical(next_episode(a.path) if a.command=='next' else accept_episode(a.path)))
