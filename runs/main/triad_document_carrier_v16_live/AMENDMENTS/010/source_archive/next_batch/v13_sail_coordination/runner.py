"""Durable V11 stage coordinator. Imports/init/status do not make API calls."""
from copy import deepcopy
from pathlib import Path
import json
import os
import sqlite3

from . import design as d, review
from .backend import image_message, image_bytes
from .provider import Fault
from .storage import canonical, sha, put, read, safe, IntegrityError

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
DEFAULT_OUT=ROOT/'runs/main/triad_sail_coordination_v13_live'
ACTORS={'A_IMAGE':('A','image'),'A_PROPOSAL':('A','vote'),'B_IMAGE':('B','image'),
        'B_VOTE':('B','vote'),'C_VOTE':('C','vote'),'POST_A_LOG':('A','log'),
        'POST_B_LOG':('B','log'),'POST_C_IMAGE':('C','image'),'POST_C_LOG':('C','log')}
IMAGE_STAGE={'A':'A_IMAGE','B':'B_IMAGE','C':'POST_C_IMAGE'}
VOTE_STAGE={'A':'A_PROPOSAL','B':'B_VOTE','C':'C_VOTE'}


def source_snapshot():
    old=ROOT/'runs/main/triad_intent_coordination_v12_live/freeze.json'
    frozen=read(old)['sources']
    for relative,expected in frozen.items():
        if sha((ROOT/relative).read_bytes())!=expected:raise IntegrityError('Earlier frozen source changed')
    paths=list(HERE.glob('*.py'))+[HERE/'README.md',ROOT/'next_batch/__init__.py']
    return {**frozen,**{str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in paths}}


class Runner:
    def __init__(self,out=DEFAULT_OUT,*,fixture=False):
        self.out=safe(out);self.fixture=fixture

    def init(self):
        freeze={'revision':d.CONFIG['revision'],'config':d.CONFIG,'cases':d.cases(),
                'sources':source_snapshot(),'execution_mode':'offline_fixture' if self.fixture else 'live'}
        put(self.out/'freeze.json',freeze)
        return self.status()

    def verify(self):
        frozen=read(self.out/'freeze.json')
        if frozen['config']!=d.CONFIG or frozen['cases']!=d.cases():raise IntegrityError('Frozen config or cases differ')
        if frozen['execution_mode']!=('offline_fixture' if self.fixture else 'live'):raise IntegrityError('Execution mode differs')
        for relative,expected in frozen['sources'].items():
            if sha(safe(ROOT/relative,ROOT).read_bytes())!=expected:raise IntegrityError('Live frozen source changed')
        if (self.out/'STOP_REQUESTED.json').exists():raise IntegrityError('Operator stop requested')
        return frozen

    def commits(self):
        commits=[];prior='0'*64
        for number in range(1,13):
            path=self.out/'checkpoints'/f'r{number:02}.json'
            if not path.exists():
                if any((self.out/'checkpoints'/f'r{k:02}.json').exists() for k in range(number+1,13)):
                    raise IntegrityError('Checkpoint gap')
                break
            item=read(path)
            if item['round']!=number or item['previous_checkpoint_sha256']!=prior:raise IntegrityError('Checkpoint chain differs')
            if item['case_sha256']!=d.case_digest(d.cases()[number-1]):raise IntegrityError('Committed case differs')
            # Validate preserved actual image bytes on every resume/read.
            for image in item['images'].values():image_bytes(image)
            commits.append(item);prior=sha(path.read_bytes())
        return commits

    def current(self):
        commits=self.commits();number=len(commits)+1
        if number>12:return None
        prior=sha((self.out/'checkpoints'/f'r{number-1:02}.json').read_bytes()) if commits else '0'*64
        maximum=3
        repair=d.CONFIG['recovery'].get('specific_post_repair_attempts',{})
        if number==repair.get('round'):maximum=repair['last']
        for attempt in range(1,maximum+1):
            root=self.out/'attempts'/f'r{number:02}'/f'a{attempt:02}'
            if (root/'abandoned.json').exists():continue
            context={'round':number,'attempt':attempt,'attempt_id':f'r{number:02}.a{attempt:02}',
                     'previous_checkpoint_sha256':prior,'case_sha256':d.case_digest(d.cases()[number-1])}
            put(root/'context.json',context)
            assignment=d.commit_assignment(d.cases()[number-1])
            put(root/'assignment.json',{'round':assignment.round,'ballot':assignment.ballot,
                                      'case_sha256':assignment.case_sha256,'source':assignment.source})
            return context,root
        raise IntegrityError('Three technical attempts exhausted; no additional retry' if maximum==3 else
                             'Specific repaired-round technical attempts exhausted; no additional retry')

    def stage_result(self,root,stage):return read(root/'stages'/(stage+'.json'))

    def next_stage(self,root):
        stages=root/'stages'
        found_missing=False;next_stage=None
        for stage in d.STAGES:
            exists=(stages/(stage+'.json')).exists()
            if not exists and next_stage is None:next_stage=stage;found_missing=True
            elif exists and found_missing:raise IntegrityError('Stage gap')
        return next_stage or 'COMMIT'

    def status(self):
        if not (self.out/'freeze.json').exists():return {'status':'not_initialized','paid_calls_started':False}
        self.verify();commits=self.commits()
        if len(commits)==12:
            from .controls import Controls
            control_status=Controls(self).status()
            return {'status':'main_complete','committed_rounds':12,'controls_status':control_status['status'],
                    'controls_completed':sum(r['status']=='complete' for r in control_status['branches'])}
        context,root=self.current();stage=self.next_stage(root)
        pending=stage in ('CODEX_REVIEW','C_FORMAT_REVIEW')
        return {'status':'awaiting_review' if pending else 'ready','committed_rounds':len(commits),
                'round':context['round'],'attempt':context['attempt'],'stage':stage,'attempt_id':context['attempt_id']}

    def ballots(self,root,case):
        return {'A':case['private_target'],**{r:self.stage_result(root,VOTE_STAGE[r])['parsed']['decision'] for r in ('B','C')}}

    def own_artifacts(self,root,role):return self.stage_result(root,IMAGE_STAGE[role])

    def settlement(self,root,case):
        main=self.stage_result(root,'CODEX_REVIEW');cformat=self.stage_result(root,'C_FORMAT_REVIEW')
        if main['status']=='valid':
            report=main['report'];finding=d.Finding(case['round'],**report['joint_assessment'])
            compliance={r:report['new_image_compliance'][r]['status'] for r in ('A','B')}
        else:
            finding=d.Finding(case['round'],status='model_invalid');compliance={'A':'unavailable','B':'unavailable'}
        compliance['C']=cformat['report']['status'] if cformat['status']=='valid' else 'unavailable'
        a=self.stage_result(root,'A_PROPOSAL')['parsed']['decision']
        return d.settle(case,self.stage_result(root,'B_VOTE')['raw'],self.stage_result(root,'C_VOTE')['raw'],
                        finding,compliance,d.commit_assignment(case),raw_A_proposal=a)

    def actor_request(self,context,root,stage,*,case_override=None):
        number=context['round'];case=case_override or d.cases()[number-1];role,mode=ACTORS[stage];commits=self.commits()
        memory=[];history=[];accounts=[]
        for commit in commits:
            record={'round':commit['round'],'role':role,'status':commit['logs'][role]['status'],
                    'interpretation':commit['interpretations'].get(role)}
            if commit['logs'][role]['status']=='valid':record.update(commit['logs'][role]['value'])
            memory.append(record)
            history.append({'round':commit['round'],'ballots':commit['ballots'],
                            'actual_first_report':commit['first_review']})
            accounts.append({'round':commit['round'],'role':role,'provenance':'verified_checkpoint',
                             **commit['settlement']['scores'][role]})
        options={'own_memory':memory,'public_history':history,'settled_accounts':accounts}
        post=mode=='log' or role=='C' and mode=='image'
        if post:
            options.update(locked_ballots=self.ballots(root,case),
                           actual_review=self.stage_result(root,'CODEX_REVIEW')['first_raw_text'],
                           own_prelock_record={'round':number,'role':role,
                             'native_arguments':self.stage_result(root,VOTE_STAGE[role])['raw']})
        if mode=='log' or mode=='vote' and role in ('A','B'):
            options['own_current_artifacts']=self.own_artifacts(root,role)['generation_records']
            plans=deepcopy(self.own_artifacts(root,role)['communication_plans'])
            for plan in plans.values():
                plan['invalid_raw_field_evidence']={}
                for field,status in plan.get('field_status',{}).items():
                    if status!='valid':
                        raw=canonical(plan['declared_plan'][field]).encode()
                        plan['invalid_raw_field_evidence'][field]={'status':status,'raw_sha256':sha(raw),'raw_utf8_bytes':len(raw),
                            'raw_retained_in_research_archive':True}
                        plan['declared_plan'][field]=None
            options['own_current_plans']=plans
        if role=='C' and mode=='log':
            options.update(final_C_compliance=self.stage_result(root,'C_FORMAT_REVIEW'),
                           calculated_settlement=self.settlement(root,case))
        template=d.message_template(case,role,mode,**options)
        refs={}
        for slot in template['required_actual_raster_slots']:
            if slot=='previous_C':image=commits[-1]['images']['C']
            elif slot in ('current_A','current_B'):image=self.own_artifacts(root,slot[-1])['image']
            elif slot=='own_selected_image':image=self.own_artifacts(root,role)['image']
            elif slot.startswith('own_') and slot.rsplit('_',1)[-1] in ('D1','D2'):
                image=self.own_artifacts(root,role)['drafts'][slot.rsplit('_',1)[-1]]
            else:raise IntegrityError('Unresolved raster slot')
            refs[slot]=image
        messages=template['messages']+[image_message(image,'Actual permitted image: '+slot) for slot,image in refs.items()]
        return {'request_id':context['attempt_id']+'.'+stage,'round':number,'role':role,'mode':mode,
                'messages':messages,'image_refs':refs}

    def prepare_review(self,context,root,stage):
        number=context['round'];case=d.cases()[number-1]
        if stage=='C_FORMAT_REVIEW':
            return review.prepare(self.out,number,'C_format',d.public_task(case),None,
                                  [(f'r{number:02}_C',self.own_artifacts(root,'C')['image'])],attempt_id=context['attempt_id'],fixture=self.fixture)
        images=[];history=[]
        for commit in self.commits():
            for role in d.ROLES:images.append((f'r{commit["round"]:02}_{role}',commit['images'][role]))
            history.append({'round':commit['round'],'ballots':commit['ballots'],'actual_first_report':commit['first_review']})
        images.extend((f'r{number:02}_{r}',self.own_artifacts(root,r)['image']) for r in ('A','B'))
        return review.prepare(self.out,number,'main',d.public_task(case),self.ballots(root,case),images,history,
                              attempt_id=context['attempt_id'],fixture=self.fixture)

    def abandon(self,context,root,fault,ledger=None):
        marker=root/'abandoned.json'
        put(marker,{'status':'abandoned_technical','technical':True,'attempt_id':context['attempt_id'],
                    'fault_class':fault.code,'prior_checkpoint_sha256':context['previous_checkpoint_sha256'],
                    'excluded_from_future_memory':True,'all_costs_and_unknown_reserves_retained':True})
        if ledger is not None:
            from .funding import seal
            seal(self.out,marker,ledger)
        return marker

    def advance(self,backend_factory,*,through_round=12,maximum_actor_stages=None,ledger=None):
        completed=0
        while True:
            self.verify();current=self.current()
            if current is None:return self.status()
            context,root=current;number=context['round']
            if (root/'hold.json').exists():raise Fault(read(root/'hold.json')['fault_class'],permanent=True)
            if number>through_round:return self.status()
            stage=self.next_stage(root);path=root/'stages'/(stage+'.json')
            if stage in ('CODEX_REVIEW','C_FORMAT_REVIEW'):
                pending=self.prepare_review(context,root,stage)
                assessment=self.out/'reviews'/pending['job_id']/'assessment.json'
                if not assessment.exists():return {**self.status(),**pending}
                result=read(assessment)
                if not self.fixture and result['evidence_origin']!='actual_Codex_product_context':
                    raise IntegrityError('A fixture cannot become a live review')
                put(path,result);continue
            if stage=='SETTLE':put(path,self.settlement(root,d.cases()[number-1]));continue
            if stage=='COMMIT':
                case=d.cases()[number-1]
                commit={'round':number,'attempt':context['attempt'],'previous_checkpoint_sha256':context['previous_checkpoint_sha256'],
                        'case_sha256':context['case_sha256'],'ballots':self.ballots(root,case),
                        'images':{r:self.own_artifacts(root,r)['image'] for r in d.ROLES},
                        'logs':{r:self.stage_result(root,'POST_'+r+'_LOG')['parsed'] for r in d.ROLES},
                        'interpretations':{r:self.stage_result(root,VOTE_STAGE[r])['parsed'].get('interpretation') for r in ('B','C')},
                        'first_review':self.stage_result(root,'CODEX_REVIEW')['first_raw_text'],
                        'settlement':self.stage_result(root,'SETTLE'),
                        'evidence_origin':'offline_fixture' if self.fixture else 'live'}
                put(path,commit);put(self.out/'checkpoints'/f'r{number:02}.json',commit);continue
            if maximum_actor_stages is not None and completed>=maximum_actor_stages:return self.status()
            intent=root/'inflight'/(stage+'.json')
            if intent.exists() and not (root/'finished'/(stage+'.json')).exists():
                self.abandon(context,root,Fault('interrupted_actor_stage'),ledger);continue
            try:
                request=self.actor_request(context,root,stage)
                put(intent,{'request_sha256':sha(canonical(request).encode()),'stage':stage})
                backend=backend_factory(context)
                result=backend.perform(request)
                put(path,result)
                put(root/'finished'/(stage+'.json'),{'stage_result_sha256':sha(path.read_bytes())})
                completed+=1
            except Fault as fault:
                if fault.permanent:
                    put(root/'hold.json',{'fault_class':fault.code,'permanent':True});raise
                self.abandon(context,root,fault,ledger)

