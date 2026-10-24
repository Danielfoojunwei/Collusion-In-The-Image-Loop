"""Single-POST v10 funding inside the original aggregate, preserving old losses."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import time

from visual_collusion.budget import BudgetError, PendingPaidRequest, canonical, _units, request_digest
from experiments.pressure_interview_v1.budget_guard import SharedBudgetGuard, readonly_budget_snapshot, _wire_bytes
from experiments.triad_v9_checkpoint_restart04.funding import FundingLedger as PriorLedger
from experiments.triad_emergent_v9.engine import save_once

ROOT=Path(__file__).resolve().parents[2]
AGGREGATE=ROOT/'runs/main/budget.sqlite3'
PRIOR_OUT=ROOT/'runs/main/triad_emergent_v9_live'
NAMESPACE='triad_required_images_v10r1'
CAP_UNITS=24_000_000_000
MODELS={'chat':'z-ai/glm-5.3-flash','image':'qwen/qwen-image-2.1-turbo'}
OLD_V10_NAMESPACE='triad_committed_sender_v10'
OLD_V10_OUT=ROOT/'runs/main/triad_committed_sender_v10_live'


class OperatorStop(BaseException):
    """Cancellation is never a retryable provider failure."""


def physical_namespace(out):
    out=safe(out)
    if out.parent.name=='episodes':
        if re.fullmatch(r'e0[1-3]',out.name) is None:
            raise BudgetError('At most three whole-episode attempts are authorized')
        return NAMESPACE+':'+out.name+':'
    return NAMESPACE+':'


def valid_attempt_id(value):
    if not isinstance(value,str):return False
    if re.fullmatch(r'r(?:0[1-9]|1[0-2])\.a0[1-3]',value):return True
    match=re.fullmatch(r'ctrl\.r(04|08|12)\.(original|alternate|neutralA|neutralB|renderer|overt|honest)\.a0[1-3]',value)
    return bool(match and (match[2] not in {'overt','honest'} or match[1]=='04'))


def sha(value):return hashlib.sha256(value).hexdigest()


def safe(path):
    path=Path(path).absolute()
    if '..' in path.parts or any(p.is_symlink() for p in (path,*path.parents)):
        raise BudgetError('Funding evidence cannot follow a symlink or escape a path')
    return path


def read(path):return json.loads(safe(path).read_text())


def ticks(pid):
    try:return Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[19]
    except FileNotFoundError:return None


def owns_lock(out):
    path=safe(Path(out)/'producer.lock')
    if not path.exists():return False
    stat=path.stat()
    for line in Path('/proc/locks').read_text().splitlines():
        f=line.split()
        if len(f)>=6 and f[1:4]==['FLOCK','ADVISORY','WRITE'] and f[4]==str(os.getpid()):
            major,minor,inode=f[5].split(':')
            if (int(major,16),int(minor,16),int(inode))==(os.major(stat.st_dev),os.minor(stat.st_dev),stat.st_ino):return True
    return False


def checked_record(path):
    value=read(path)
    if not isinstance(value,dict) or value.get('record_hash')!=sha(canonical({k:v for k,v in value.items() if k!='record_hash'}).encode()):
        raise BudgetError('Durable attempt record checksum differs')
    return value


class FundingLedger(PriorLedger):
    def __init__(self,path,*,run_root,prior_out=PRIOR_OUT):
        self.run_root=safe(run_root)
        super().__init__(path,40,global_out=prior_out)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            key=NAMESPACE+'_namespace_cap_units'
            old=db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
            if old is not None and old[0]!=str(CAP_UNITS):raise BudgetError('V10 namespace cap changed')
            db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)',(key,str(CAP_UNITS)))

    def _sealed(self,db,row):
        if row['request_id'].startswith(OLD_V10_NAMESPACE+':'):
            from experiments.triad_committed_sender_v10.funding import FundingLedger as OldV10
            prior=object.__new__(OldV10);prior.run_root=OLD_V10_OUT
            return OldV10._sealed(prior,db,row)
        if not row['request_id'].startswith(NAMESPACE+':'):
            return super()._sealed(db,row)
        if row['status'] not in {'pending','uncertain'}:return False
        owner_root=self.run_root
        rest=row['request_id'][len(NAMESPACE)+1:]
        if re.match(r'e0[1-3]:',rest):
            if self.run_root.parent.name!='episodes':return False
            owner_root=safe(self.run_root.parent/rest.split(':',1)[0])
        path=owner_root/'funding_seals'/(sha(row['request_id'].encode())+'.json')
        if not path.exists():return False
        proof=checked_record(path)
        marker=safe(proof.get('abandonment_path',''))
        if not marker.is_relative_to(owner_root) or sha(marker.read_bytes())!=proof.get('abandonment_sha256'):return False
        abandoned=checked_record(marker)
        if abandoned.get('status')!='abandoned_technical' or abandoned.get('fault',{}).get('technical') is not True:return False
        if proof.get('row_sha256')!=sha(canonical(dict(row)).encode()):return False
        owner_path=safe(proof['owner_path'])
        if not owner_path.is_relative_to(owner_root) or sha(owner_path.read_bytes())!=proof['owner_sha256']:return False
        owner=checked_record(owner_path)
        if owner['request_id']!=row['request_id'] or owner['attempt_id']!=abandoned.get('attempt_id'):return False
        finished=owner_path.with_name(owner_path.name.replace('.started.json','.finished.json'))
        if finished.exists():
            ended=checked_record(finished)
            if ended.get('owner_sha256')!=sha(owner_path.read_bytes()):return False
        elif ticks(owner['producer_pid'])==owner['producer_start_ticks']:
            return False
        return proof.get('full_unknown_reservation_retained') is True

    def reserve(self,request_id,kind,model,request_hash,reserve_usd):
        prefix=NAMESPACE+':'
        if (not isinstance(request_id,str) or not request_id.startswith(prefix)
                or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,180}',request_id)
                or kind not in MODELS or model!=MODELS[kind]
                or not isinstance(request_hash,str) or not re.fullmatch('[a-f0-9]{64}',request_hash)):
            raise BudgetError('Paid identity, exact hash, model or namespace differs')
        amount=_units(reserve_usd)
        if amount<=0:raise BudgetError('Positive conservative reserve required')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM requests WHERE request_id=?',(request_id,)).fetchone()
            if old is not None:
                if any(old[k]!=v for k,v in [('kind',kind),('model',model),('request_hash',request_hash),('reserved_units',amount)]):
                    raise BudgetError('Paid identity reused for different bytes or reserve')
                if old['status']=='completed':return self._record(old)
                raise PendingPaidRequest('Existing unresolved paid identity is never reposted')
            self._ready(db)
            cap=db.execute('SELECT value FROM settings WHERE key=?',(NAMESPACE+'_namespace_cap_units',)).fetchone()
            if cap is None or cap[0]!=str(CAP_UNITS):raise BudgetError('Namespace cap binding changed')
            subset=db.execute('SELECT COALESCE(SUM(COALESCE(actual_units,reserved_units)),0) FROM requests WHERE substr(request_id,1,?)=?',(len(prefix),prefix)).fetchone()[0]
            prior_prefix=OLD_V10_NAMESPACE+':'
            subset+=db.execute('SELECT COALESCE(SUM(COALESCE(actual_units,reserved_units)),0) FROM requests WHERE substr(request_id,1,?)=?',(len(prior_prefix),prior_prefix)).fetchone()[0]
            aggregate=db.execute('SELECT COALESCE(SUM(COALESCE(actual_units,reserved_units)),0) FROM requests').fetchone()[0]
            if subset+amount>CAP_UNITS or aggregate+amount>self.cap_units:
                raise BudgetError('Original aggregate or namespace cannot cover next paid reserve')
            db.execute("INSERT INTO requests(request_id,kind,model,request_hash,reserved_units,status,created) VALUES (?,?,?,?,?,'pending',?)",(request_id,kind,model,request_hash,amount,time.time()))
            return self._record(db.execute('SELECT * FROM requests WHERE request_id=?',(request_id,)).fetchone())

    def namespace_summary(self):
        with self._connect() as db:
            rows=db.execute('SELECT request_id,status,actual_units,reserved_units FROM requests WHERE request_id LIKE ?',(NAMESPACE+':%',)).fetchall()
            prior=db.execute('SELECT actual_units,reserved_units FROM requests WHERE substr(request_id,1,?)=?',(len(OLD_V10_NAMESPACE)+1,OLD_V10_NAMESPACE+':')).fetchall()
        known=sum(r['actual_units'] or 0 for r in rows);unknown=sum(r['reserved_units'] for r in rows if r['actual_units'] is None)
        previous=sum(r['actual_units'] if r['actual_units'] is not None else r['reserved_units'] for r in prior)
        return {'namespace':NAMESPACE,'cap_usd':24,'known_usd':known/1e9,'unresolved_reserved_usd':unknown/1e9,'committed_usd':(known+unknown+previous)/1e9,'prior_failed_v10_committed_usd':previous/1e9,'request_count':len(rows),'episode_request_count':sum(r['request_id'].startswith(physical_namespace(self.run_root)) for r in rows),'new_allowance':False}


class Guard(SharedBudgetGuard):
    def __init__(self,path,*,run_root,evidence_dir,ledger,active_prefix=None,before_dispatch=None):
        self.run_root=safe(run_root);self.evidence_dir=safe(evidence_dir)
        if active_prefix is not None and not valid_attempt_id(active_prefix):
            raise BudgetError('Only a preregistered main/control attempt may fund calls')
        self.active_prefix=active_prefix;self.before_dispatch=before_dispatch
        super().__init__(path,NAMESPACE,ledger=ledger)

    def _request_id(self,local_request_id):
        return physical_namespace(self.run_root)+local_request_id

    def execute(self,local_request_id,kind,model,wire_payload,reserve_usd,post):
        if (self.run_root/'operator_stop.requested.json').exists():raise OperatorStop()
        if self.active_prefix is None or not local_request_id.startswith(self.active_prefix+'.'):
            raise BudgetError('No current attempt-qualified paid authorization')
        if not owns_lock(self.run_root):raise BudgetError('The current process does not own the producer lock')
        if not callable(self.before_dispatch):raise BudgetError('A live source/attempt check is required')
        self.before_dispatch()
        self.ledger.assert_ready()
        body=_wire_bytes(wire_payload,model);physical=self._request_id(local_request_id)
        owner_path=self.run_root/'paid_operations'/(sha(physical.encode())+'.started.json')
        is_new=self.ledger.lookup(physical) is None
        if is_new:
            endpoint='/chat/completions' if kind=='chat' else '/images'
            owner={'attempt_id':self.active_prefix,'request_id':physical,
                'request_hash':request_digest({'endpoint':endpoint,'wire_body_sha256':sha(body)}),
                'wire_sha256':sha(body),'reserved_units':_units(reserve_usd),
                'producer_pid':os.getpid(),'producer_start_ticks':ticks(os.getpid())}
            # Before reservation: a crash in the reserve-to-callback gap still
            # has durable exact-wire ownership and retains the full reserve.
            save_once(owner_path,{**owner,'record_hash':sha(canonical(owner).encode())})
        def observed_post(actual,binding):
            self.before_dispatch()
            if binding['request_hash']!=owner['request_hash'] or actual!=body:
                raise BudgetError('Callback bytes differ from durable pre-reservation intent')
            entered={'owner_sha256':sha(owner_path.read_bytes()),'callback_entered':True}
            save_once(owner_path.with_name(owner_path.name.replace('.started.json','.entered.json')),
                      {**entered,'record_hash':sha(canonical(entered).encode())})
            try:
                return post(actual,deepcopy(binding))
            except (KeyboardInterrupt,SystemExit,OperatorStop):
                stop_record={'status':'operator_stop_requested','automatic_restart_forbidden':True,
                    'costs_and_unknown_reserves_retained':True}
                save_once(self.run_root/'operator_stop.requested.json',
                    {**stop_record,'record_hash':sha(canonical(stop_record).encode())})
                raise
        try:
            return super().execute(local_request_id,kind,model,body,reserve_usd,observed_post)
        finally:
            if is_new:
                done={'owner_sha256':sha(owner_path.read_bytes()),'execute_returned_or_raised':True}
                save_once(owner_path.with_name(owner_path.name.replace('.started.json','.finished.json')),
                          {**done,'record_hash':sha(canonical(done).encode())})
            if (self.run_root/'operator_stop.requested.json').exists():
                raise OperatorStop()


def create_guard(aggregate_path,*,run_root,evidence_dir,active_prefix=None,before_dispatch=None,ledger=None):
    path=safe(aggregate_path)
    readonly_budget_snapshot(path)
    if ledger is None:
        if path.resolve()!=AGGREGATE.resolve():raise BudgetError('Production must use original aggregate ledger')
        ledger=FundingLedger(path,run_root=run_root)
    return Guard(path,run_root=run_root,evidence_dir=evidence_dir,ledger=ledger,
        active_prefix=active_prefix,before_dispatch=before_dispatch)


def seal_abandoned_attempt(run_root,attempt_id,abandonment_path,*,ledger):
    out=safe(run_root);marker=safe(abandonment_path)
    if not owns_lock(out):raise BudgetError('Sealing requires the exclusive producer lock')
    if not valid_attempt_id(attempt_id) or not marker.is_relative_to(out):
        raise BudgetError('Invalid bounded attempt abandonment')
    abandoned=checked_record(marker)
    if (abandoned.get('status')!='abandoned_technical' or abandoned.get('attempt_id')!=attempt_id
            or abandoned.get('fault',{}).get('technical') is not True):raise BudgetError('Typed technical abandonment required')
    prefix=physical_namespace(out)+attempt_id+'.'
    with ledger._connect() as db:
        rows=db.execute("SELECT * FROM requests WHERE status IN ('pending','uncertain') AND substr(request_id,1,?)=?",(len(prefix),prefix)).fetchall()
    for row in rows:
        owner_path=out/'paid_operations'/(sha(row['request_id'].encode())+'.started.json')
        owner=checked_record(owner_path)
        if owner['attempt_id']!=attempt_id or owner['request_hash']!=row['request_hash'] or owner['reserved_units']!=row['reserved_units']:
            raise BudgetError('Abandoned request lacks exact original callback ownership')
        finished=owner_path.with_name(owner_path.name.replace('.started.json','.finished.json'))
        if finished.exists():
            if checked_record(finished).get('owner_sha256')!=sha(owner_path.read_bytes()):raise BudgetError('Callback finish binding changed')
        elif ticks(owner['producer_pid'])==owner['producer_start_ticks']:
            raise PendingPaidRequest('Original paid callback may still be live; no new attempt')
        proof={'attempt_id':attempt_id,'row_sha256':sha(canonical(dict(row)).encode()),
            'abandonment_path':str(marker),'abandonment_sha256':sha(marker.read_bytes()),
            'owner_path':str(owner_path),'owner_sha256':sha(owner_path.read_bytes()),
            'full_unknown_reservation_retained':True}
        save_once(out/'funding_seals'/(sha(row['request_id'].encode())+'.json'),{**proof,'record_hash':sha(canonical(proof).encode())})
    return {'sealed_unknown_rows':len(rows),'released_usd':0,'reserve_retained':True}
