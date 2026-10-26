"""One aggregate ledger, bounded V10+V11 subset, no released unknown charges."""
from pathlib import Path
import os
import re
import time

from visual_collusion.budget import BudgetError, PendingPaidRequest, _units, request_digest
from experiments.pressure_interview_v1.budget_guard import SharedBudgetGuard, _wire_bytes
from experiments.triad_output_room_v10r3.funding import FundingLedger as EarlierLedger, owns_lock, ticks
from .storage import safe, put, read, sha, canonical, IntegrityError

ROOT=Path(__file__).resolve().parents[2]
AGGREGATE=ROOT/'runs/main/budget.sqlite3'
NAMESPACE='triad_meta_coordination_v14'
PREFIXES=('triad_committed_sender_v10:','triad_required_images_v10r1:',
          'triad_ascii_records_v10r2:','triad_output_room_v10r3:',
          'triad_picture_coordination_v11:','triad_intent_coordination_v12:','triad_sail_coordination_v13:',NAMESPACE+':')
MODELS={'chat':'meta/muse-spark-1.3-contributor','image':'qwen/qwen-image-2.1-turbo'}


class Ledger(EarlierLedger):
    def __init__(self,path,run_root):
        self.v11_root=safe(run_root)
        # Earlier unknown charges must be checked against their original owner.
        # The V10r3 continuation used its new namespace in the retained ASCII
        # episode directory. Its original seal/owner proof lives there.
        super().__init__(path,run_root=ROOT/'runs/main/triad_ascii_records_v10r2_live/episodes/e01')

    def _sealed(self, db, row):
        if row['request_id'].startswith('triad_sail_coordination_v13:'):
            from next_batch.v13_sail_coordination.funding import Ledger as V13Ledger
            prior=object.__new__(V13Ledger)
            prior.v11_root=ROOT/'runs/main/triad_sail_coordination_v13_live'
            return V13Ledger._sealed(prior,db,row)
        if row['request_id'].startswith('triad_intent_coordination_v12:'):
            from next_batch.v12_intent_coordination.funding import Ledger as V12Ledger
            prior=object.__new__(V12Ledger)
            prior.v11_root=ROOT/'runs/main/triad_intent_coordination_v12_live'
            return V12Ledger._sealed(prior,db,row)
        if row['request_id'].startswith('triad_picture_coordination_v11:'):
            from next_batch.v11_picture_coordination.funding import Ledger as V11Ledger
            prior=object.__new__(V11Ledger)
            prior.v11_root=ROOT/'runs/main/triad_picture_coordination_v11_live'
            return V11Ledger._sealed(prior,db,row)
        if not row['request_id'].startswith(NAMESPACE+':'):return super()._sealed(db,row)
        if row['status'] not in ('pending','uncertain'):return False
        path=self.v11_root/'funding_seals'/(sha(row['request_id'].encode())+'.json')
        if not path.exists():return False
        proof=read(path)
        marker=safe(proof['abandonment_path'],self.v11_root)
        owner=safe(proof['owner_path'],self.v11_root)
        if sha(marker.read_bytes())!=proof['abandonment_sha256'] or sha(owner.read_bytes())!=proof['owner_sha256']:return False
        abandoned,started=read(marker),read(owner)
        finished=owner.with_suffix('.finished.json')
        exited=finished.exists() and read(finished).get('owner_sha256')==sha(owner.read_bytes())
        if not exited and ticks(started['pid'])==started['start_ticks']:return False
        return (abandoned.get('status')=='abandoned_technical' and abandoned.get('technical') is True
                and started['attempt_id']==abandoned['attempt_id']
                and started['request_id']==row['request_id']
                and proof['row_sha256']==sha(canonical(dict(row)).encode())
                and proof.get('full_unknown_reservation_retained') is True)

    def reserve(self,request_id,kind,model,request_hash,reserve_usd):
        if (not re.fullmatch(re.escape(NAMESPACE)+r':[A-Za-z0-9_.:-]{1,110}',request_id)
                or kind not in MODELS or model!=MODELS[kind]
                or not re.fullmatch(r'[0-9a-f]{64}',request_hash)):
            raise BudgetError('V11 exact paid identity/model differs')
        amount=_units(reserve_usd)
        if amount<=0:raise BudgetError('Positive reserve required')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM requests WHERE request_id=?',(request_id,)).fetchone()
            if old is not None:
                if any(old[k]!=v for k,v in [('kind',kind),('model',model),('request_hash',request_hash),('reserved_units',amount)]):
                    raise BudgetError('Existing paid identity changed')
                if old['status']=='completed':return self._record(old)
                raise PendingPaidRequest('Unresolved identity cannot be reposted')
            self._ready(db)
            rows=db.execute('SELECT request_id,COALESCE(actual_units,reserved_units) AS units FROM requests').fetchall()
            total=sum(r['units'] for r in rows)
            subset=sum(r['units'] for r in rows if r['request_id'].startswith(PREFIXES))
            if total+amount>40_000_000_000 or subset+amount>24_000_000_000:
                raise BudgetError('Original aggregate or combined V10/V11 cap exhausted')
            db.execute("INSERT INTO requests(request_id,kind,model,request_hash,reserved_units,status,created) VALUES (?,?,?,?,?,'pending',?)",
                       (request_id,kind,model,request_hash,amount,time.time()))
            return self._record(db.execute('SELECT * FROM requests WHERE request_id=?',(request_id,)).fetchone())


class Guard(SharedBudgetGuard):
    def __init__(self,path,run_root,attempt_id,before_dispatch,*,ledger=None):
        self.run_root=safe(run_root);self.attempt_id=attempt_id;self.before_dispatch=before_dispatch
        normal=re.fullmatch(r'(r(?:0[1-9]|1[0-2])|ctrl\.r(?:04|08|12)\.(?:original|alternate|neutralA|neutralB|renderer|overt|honest))\.a0[1-3]',attempt_id)
        if not normal:
            raise BudgetError('Invalid funded attempt')
        if ledger is None:
            if safe(path)!=AGGREGATE:raise BudgetError('Production must use original ledger')
            ledger=Ledger(path,run_root)
        super().__init__(path,NAMESPACE,ledger=ledger)

    def execute(self,local_id,kind,model,wire_payload,reserve_usd,post):
        if not owns_lock(self.run_root):raise BudgetError('Exclusive producer lock required')
        if not local_id.startswith(self.attempt_id+'.'):raise BudgetError('Call escapes current attempt')
        self.before_dispatch()
        body=_wire_bytes(wire_payload,model);physical=self._request_id(local_id)
        owner=self.run_root/'paid_operations'/(sha(physical.encode())+'.json')
        if self.ledger.lookup(physical) is None:
            put(owner,{'attempt_id':self.attempt_id,'request_id':physical,'wire_sha256':sha(body),
                       'pid':os.getpid(),'start_ticks':ticks(os.getpid())})
        def observed(actual,binding):
            self.before_dispatch()
            started=read(owner)
            if actual!=body or started['wire_sha256']!=sha(actual):raise BudgetError('Paid callback bytes changed')
            try:return post(actual,binding)
            except (KeyboardInterrupt,SystemExit):
                put(self.run_root/'STOP_REQUESTED.json',{'reason':'operator_interrupt','automatic_restart_forbidden':True})
                raise
        try:return super().execute(local_id,kind,model,body,reserve_usd,observed)
        except (KeyboardInterrupt,SystemExit):
            put(self.run_root/'STOP_REQUESTED.json',{'reason':'operator_interrupt','automatic_restart_forbidden':True})
            raise
        finally:
            if owner.exists():put(owner.with_suffix('.finished.json'),{'owner_sha256':sha(owner.read_bytes())})
            if (self.run_root/'STOP_REQUESTED.json').exists():raise IntegrityError('Operator stop; automatic restart forbidden')


def seal(out,marker,ledger):
    out=safe(out);marker=safe(marker,out)
    if not owns_lock(out):raise IntegrityError('Exclusive lock required to seal abandoned charges')
    abandoned=read(marker)
    if abandoned.get('status')!='abandoned_technical' or abandoned.get('technical') is not True:
        raise IntegrityError('Typed technical abandonment required')
    prefix=NAMESPACE+':'+abandoned['attempt_id']+'.'
    with ledger._connect() as db:
        rows=db.execute("SELECT * FROM requests WHERE status IN ('pending','uncertain') AND substr(request_id,1,?)=?",(len(prefix),prefix)).fetchall()
    for row in rows:
        owner=out/'paid_operations'/(sha(row['request_id'].encode())+'.json');started=read(owner)
        finished=owner.with_suffix('.finished.json')
        if not finished.exists() and ticks(started['pid'])==started['start_ticks']:
            raise PendingPaidRequest('Original paid callback may remain live')
        put(out/'funding_seals'/(sha(row['request_id'].encode())+'.json'),{
            'row_sha256':sha(canonical(dict(row)).encode()),'owner_path':str(owner),
            'owner_sha256':sha(owner.read_bytes()),'abandonment_path':str(marker),
            'abandonment_sha256':sha(marker.read_bytes()),'full_unknown_reservation_retained':True})
    return len(rows)
