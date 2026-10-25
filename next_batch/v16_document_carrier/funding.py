"""V16 single-POST funding in the original aggregate, including all prior losses."""
from pathlib import Path
from decimal import Decimal
import os
import re
import time

from visual_collusion.budget import BudgetError, PendingPaidRequest, _units, request_digest
from experiments.pressure_interview_v1.budget_guard import SharedBudgetGuard, _wire_bytes
from experiments.triad_output_room_v10r3.funding import owns_lock, ticks
from next_batch.v15_glm_restored.funding import Ledger as PriorLedger, PREFIXES as PRIOR_PREFIXES
from next_batch.v15_glm_restored.storage import safe, put, put_bytes, read, sha, canonical, parse, IntegrityError

ROOT=Path(__file__).resolve().parents[2]
AGGREGATE=ROOT/'runs/main/budget.sqlite3'
OLD_V15_OUT=ROOT/'runs/main/triad_glm_restored_v15_live'
DEFAULT_OUT=ROOT/'runs/main/triad_document_carrier_v16_live'
NAMESPACE='triad_document_carrier_v16'
PREFIXES=PRIOR_PREFIXES+(NAMESPACE+':',)
MODELS={'chat':'z-ai/glm-5.3-flash','image':'qwen/qwen-image-2.1-turbo'}
ACTOR_PROVIDER_TAG='relace'
ACTOR_PROVIDER_NAME='Relace'
MODEL_BINDING_KEY='model_configuration_v16_relace_native'
RESERVES={'chat':Decimal('0.0323104'),'image':Decimal('0.028')}
NATIVE_PROFILES={
 'publish_document':{'text_and_schema_utf8_bytes':81920,'image_blocks':4,'output_tokens':65536,'reserve_usd':'0.0323104'},
 'record_vote':{'text_and_schema_utf8_bytes':81920,'image_blocks':4,'output_tokens':12288,'reserve_usd':'0.0216608'},
 'record_log':{'text_and_schema_utf8_bytes':81920,'image_blocks':4,'output_tokens':8192,'reserve_usd':'0.0208416'},
 'record_initial_judgment':{'text_and_schema_utf8_bytes':49152,'image_blocks':0,'output_tokens':8192,'reserve_usd':'0.0110112'},
 # Amendment 006 same-model sentinel probe: limits and reserve identical to record_vote.
 'record_sentinel_decode':{'text_and_schema_utf8_bytes':81920,'image_blocks':4,'output_tokens':12288,'reserve_usd':'0.0216608'},
}
CHAT_RESERVES=frozenset(Decimal(p['reserve_usd'])for p in NATIVE_PROFILES.values())
NORMAL_NATIVE_CALLS=600
NORMAL_IMAGE_CALLS=180
NORMAL_RESERVATION_USD=Decimal('18.528')
ATTEMPT_PATTERN=r'b0[1-5]\.r(?:0[1-9]|1[0-2])\.a0[1-4]'


def profile_for_tools(tools):
    if not isinstance(tools,list)or len(tools)!=1:
        raise BudgetError('Exactly one known native function profile is required')
    try:name=tools[0]['function']['name']
    except (KeyError,TypeError):raise BudgetError('Native function profile missing')from None
    if name not in NATIVE_PROFILES:raise BudgetError('Unknown native function profile')
    return name,dict(NATIVE_PROFILES[name])


def validate_native_wire(payload,reserve_usd):
    """Funding checks the actual immutable wire, not a caller's cost label.

    Input images on the network are losslessWebP. Logical PNG dimensional and
    pixel integrity checks remain in Provider before exact-wire reconstruction.
    """
    if not isinstance(payload,dict):raise BudgetError('Native wire object required')
    name,profile=profile_for_tools(payload.get('tools'))
    expected_provider={'only':[ACTOR_PROVIDER_TAG],'allow_fallbacks':False,'require_parameters':True,
                       'max_price':{'prompt':.15,'completion':.10,'request':0}}
    expected_keys={'model','messages','tools','tool_choice','max_tokens','stream','temperature','reasoning','provider'}
    if (set(payload)!=expected_keys or payload.get('model')!=MODELS['chat']
            or payload.get('max_tokens')!=profile['output_tokens']or payload.get('stream')is not False
            or payload.get('temperature')!=1.0 or payload.get('reasoning')!={'effort':'high','exclude':False}
            or payload.get('provider')!=expected_provider
            or payload.get('tool_choice')!='auto'
            or _units(reserve_usd)!=_units(profile['reserve_usd'])):
        raise BudgetError('Native wire/profile/reservation differs')
    messages=payload.get('messages');text=0;images=0
    if not isinstance(messages,list)or not 2<=len(messages)<=64 or messages[0].get('role')!='system':
        raise BudgetError('Native wire history bounds differ')
    for message in messages:
        if not isinstance(message,dict):raise BudgetError('Native wire message differs')
        content=message.get('content')
        if isinstance(content,str):text+=len(content.encode())
        elif isinstance(content,list):
            for part in content:
                if not isinstance(part,dict):raise BudgetError('Native wire content differs')
                if part.get('type')=='text'and isinstance(part.get('text'),str):text+=len(part['text'].encode())
                elif part.get('type')=='image_url':
                    try:url=part['image_url']['url']
                    except (KeyError,TypeError):raise BudgetError('Native wire image URL missing')from None
                    if not isinstance(url,str)or not url.startswith(('data:image/webp;base64,','data:image/png;base64,')):
                        raise BudgetError('Only actual inline image wire data is permitted')
                    images+=1
                else:raise BudgetError('Native wire content kind differs')
        elif content is not None:raise BudgetError('Native wire content differs')
        if message.get('tool_calls'):text+=len(canonical(message['tool_calls']).encode())
    text+=len(canonical(payload['tools']).encode())
    if text>profile['text_and_schema_utf8_bytes']or images>profile['image_blocks']:
        raise BudgetError('Native wire input exceeds funded function profile')
    return name,profile


def _pid_owns_lock(root,pid):
    """Authenticate a different producer's flock without claiming its lock."""
    path=safe(Path(root)/'producer.lock')
    if not path.is_file():return False
    stat=path.stat()
    for line in Path('/proc/locks').read_text().splitlines():
        fields=line.split()
        if len(fields)>=6 and fields[1:4]==['FLOCK','ADVISORY','WRITE'] and fields[4]==str(pid):
            major,minor,inode=fields[5].split(':')
            if (int(major,16),int(minor,16),int(inode))==(os.major(stat.st_dev),os.minor(stat.st_dev),stat.st_ino):return True
    return False


class Ledger(PriorLedger):
    def __init__(self,path,run_root):
        self.v16_root=safe(run_root)
        # Keep V15's own root for its unknown-charge ownership proofs. Earlier
        # namespace proof routing remains delegated to its immutable classes.
        super().__init__(path,OLD_V15_OUT)

    def bind(self,key,value):
        """A new explicit route binding never overwrites the original route."""
        if key!=MODEL_BINDING_KEY:return super().bind(key,value)
        if not isinstance(value,str):raise BudgetError('Invalid Relace model binding')
        try:binding=parse(value)
        except (ValueError,TypeError,IntegrityError):raise BudgetError('Invalid Relace model binding')from None
        if(not isinstance(binding,dict)or binding.get('models')!={'actor':MODELS['chat'],'image':MODELS['image']}
                or binding.get('actor_provider')!=ACTOR_PROVIDER_TAG or binding.get('actor_quantization')!='unreported'
                or binding.get('actor_precision_verified')is not False or binding.get('image_provider')!='alibaba'):
            raise BudgetError('Relace binding differs from explicit native provider repair')
        with self._connect()as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
            if old is not None and old[0]!=value:raise BudgetError('Immutable Relace model binding changed')
            db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)',(key,value))

    def _sealed(self,db,row):
        if not row['request_id'].startswith(NAMESPACE+':'):
            return super()._sealed(db,row)
        if row['status'] not in ('pending','uncertain'):return False
        path=self.v16_root/'funding_seals'/(sha(row['request_id'].encode())+'.json')
        if not path.exists():return False
        proof=read(path)
        marker=safe(proof['abandonment_path'],self.v16_root)
        owner=safe(proof['owner_path'],self.v16_root)
        if (sha(marker.read_bytes())!=proof['abandonment_sha256']
                or sha(owner.read_bytes())!=proof['owner_sha256']):return False
        abandoned,started=read(marker),read(owner)
        finished=owner.with_suffix('.finished.json')
        exited=finished.exists() and read(finished).get('owner_sha256')==sha(owner.read_bytes())
        if not exited and ticks(started['pid'])==started['start_ticks']:return False
        return (abandoned.get('status')=='abandoned_technical' and abandoned.get('technical') is True
                and started['attempt_id']==abandoned['attempt_id']
                and re.fullmatch(ATTEMPT_PATTERN,started['attempt_id']) is not None
                and started['request_id']==row['request_id']
                and proof['row_sha256']==sha(canonical(dict(row)).encode())
                and proof.get('full_unknown_reservation_retained') is True)

    def _live_pending(self,row):
        """Only another arm's provably executing guard can be funded concurrently.

        A live guard intent is recorded before reserve and its callback entry is
        recorded separately. Neither marker claims that an HTTP response exists.
        Dead, exited, uncertain or unowned operations remain blocking.
        """
        if row['status']!='pending' or not row['request_id'].startswith(NAMESPACE+':'):return False
        try:
            owner=self.v16_root/'paid_operations'/(sha(row['request_id'].encode())+'.json')
            if not owner.is_file() or owner.with_suffix('.finished.json').exists():return False
            started=read(owner);attempt=started['attempt_id'];pid=started['pid']
            if (re.fullmatch(ATTEMPT_PATTERN,attempt) is None or type(pid) is not int
                    or pid<=1 or pid==os.getpid() or ticks(pid)!=started['start_ticks']
                    or started['request_id']!=row['request_id']
                    or not row['request_id'].startswith(NAMESPACE+':'+attempt+'.')):return False
            endpoint='/chat/completions' if row['kind']=='chat' else '/images'
            if (row['kind'] not in MODELS or row['model']!=MODELS[row['kind']]
                    or row['request_hash']!=request_digest({'endpoint':endpoint,'wire_body_sha256':started['wire_sha256']})):return False
            entered=read(owner.with_suffix('.inflight.json'))
            if entered!={'owner_sha256':sha(owner.read_bytes()),'guard_execute_inflight':True}:return False
            arm=self.v16_root/'arms'/attempt.split('.')[0]
            return _pid_owns_lock(arm,pid) or _pid_owns_lock(self.v16_root,pid)
        except (KeyError,TypeError,ValueError,OSError):return False

    def _ready(self,connection):
        halt=connection.execute("SELECT value FROM settings WHERE key='halt_reason'").fetchone()
        if halt:raise BudgetError(halt[0])
        for row in connection.execute("SELECT * FROM requests WHERE status IN ('pending','uncertain')"):
            if not self._sealed(connection,row) and not self._live_pending(row):
                raise PendingPaidRequest('An unsealed unresolved paid intent blocks further paid work')

    def reserve(self,request_id,kind,model,request_hash,reserve_usd):
        pattern=re.escape(NAMESPACE)+':'+ATTEMPT_PATTERN+r'\.[A-Za-z0-9_.:-]{1,90}'
        if (not isinstance(request_id,str) or re.fullmatch(pattern,request_id) is None
                or kind not in MODELS or model!=MODELS[kind]
                or not isinstance(request_hash,str) or re.fullmatch(r'[0-9a-f]{64}',request_hash) is None):
            raise BudgetError('V16 exact paid identity/model differs')
        amount=_units(reserve_usd)
        if(kind=='chat'and amount not in{_units(x)for x in CHAT_RESERVES})or(kind=='image'and amount!=_units(RESERVES['image'])):
            raise BudgetError('V16 conservative reservation differs')
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
            aggregate=sum(r['units'] for r in rows)
            combined=sum(r['units'] for r in rows if r['request_id'].startswith(PREFIXES))
            if aggregate+amount>40_000_000_000 or combined+amount>24_000_000_000:
                raise BudgetError('Original aggregate or combined program cap exhausted')
            db.execute("INSERT INTO requests(request_id,kind,model,request_hash,reserved_units,status,created) VALUES (?,?,?,?,?,'pending',?)",
                       (request_id,kind,model,request_hash,amount,time.time()))
            return self._record(db.execute('SELECT * FROM requests WHERE request_id=?',(request_id,)).fetchone())


def _preserve_unfunded_owner(run_root,owner,physical,wire_sha256,ledger):
    """Retire only an unreserved dispatch preparation before safe rebinding.

    Every byte is copied into a permanent content-addressed archive first.
    Canonical paths may then identify the eventual paid owner. Previously
    reserved IDs are never moved, rebound or given another POST.
    """
    if not owner.exists():return
    if ledger.lookup(physical)is not None:raise BudgetError('Reserved ownership cannot be rebound')
    old=read(owner);old_hash=sha(owner.read_bytes())
    if old.get('request_id')!=physical or old.get('wire_sha256')!=wire_sha256:
        raise BudgetError('Unfunded identity differs from its original exact wire')
    if owner.with_suffix('.callback.json').exists():
        raise IntegrityError('Callback marker without a ledger row requires manual integrity review')
    finished=owner.with_suffix('.finished.json')
    if finished.exists():
        if read(finished).get('owner_sha256')!=old_hash:raise IntegrityError('Unfunded completion ownership differs')
    elif ticks(old.get('pid'))==old.get('start_ticks'):
        raise PendingPaidRequest('Unfunded dispatch owner may still be executing')
    archive=run_root/'unfunded_dispatches'/sha(physical.encode())/old_hash
    snapshots=[('owner.json',owner),('inflight.json',owner.with_suffix('.inflight.json')),
               ('finished.json',finished)]
    for name,path in snapshots:
        if path.exists():put_bytes(archive/name,path.read_bytes())
    archived=[{'file':name,'sha256':sha((archive/name).read_bytes())}for name,_ in snapshots if(archive/name).exists()]
    put(archive/'retirement.json',{'request_id':physical,'original_owner_sha256':old_hash,
        'wire_sha256':wire_sha256,'ledger_row_existed':False,'reason':'Unreserved guard returned or its process exited before any funded callback.',
        'preserved_records':archived,'canonical_owner_rebinding_allowed_only_while_unreserved':True})
    # Recheck after archiving; a charged intent must never lose its ownership.
    if ledger.lookup(physical)is not None:raise BudgetError('Reservation appeared; ownership cannot be rebound')
    # Remove markers before the owner. If interrupted, rerunning this operation
    # reuses the identical archived snapshots and finishes this local retirement.
    for name,path in snapshots[1:]+snapshots[:1]:
        if path.exists():
            if path.read_bytes()!=(archive/name).read_bytes():raise IntegrityError('Unfunded ownership changed during archival')
            path.unlink()


class Guard(SharedBudgetGuard):
    def __init__(self,path,run_root,attempt_id,before_dispatch,*,ledger=None,lock_root=None):
        self.run_root=safe(run_root);self.attempt_id=attempt_id;self.before_dispatch=before_dispatch
        if not isinstance(attempt_id,str) or re.fullmatch(ATTEMPT_PATTERN,attempt_id) is None:
            raise BudgetError('Invalid V16 funded attempt')
        if not callable(before_dispatch):raise BudgetError('A live source/attempt check is required')
        arm_root=self.run_root/'arms'/attempt_id.split('.')[0]
        self.lock_root=safe(arm_root if lock_root is None else lock_root,self.run_root)
        if self.lock_root not in (self.run_root,arm_root):raise BudgetError('Producer lock differs from current arm')
        if ledger is None:
            if safe(path)!=AGGREGATE:raise BudgetError('Production must use original ledger')
            ledger=Ledger(path,run_root)
        super().__init__(path,NAMESPACE,ledger=ledger)

    def execute(self,local_id,kind,model,wire_payload,reserve_usd,post):
        if not (owns_lock(self.run_root) or owns_lock(self.lock_root)):
            raise BudgetError('Exclusive matching-arm or whole-run producer lock required')
        if not isinstance(local_id,str) or not local_id.startswith(self.attempt_id+'.'):
            raise BudgetError('Call escapes current V16 attempt')
        if (self.run_root/'STOP_REQUESTED.json').exists():
            raise IntegrityError('Operator stop; automatic restart forbidden')
        if kind not in MODELS or model!=MODELS[kind]:raise BudgetError('V16 paid kind/model differs')
        self.before_dispatch()
        body=_wire_bytes(wire_payload,model)
        if kind=='chat':validate_native_wire(parse(body.decode()),reserve_usd)
        elif _units(reserve_usd)!=_units(RESERVES['image']):raise BudgetError('V16 image reservation differs')
        physical=self._request_id(local_id)
        owner=self.run_root/'paid_operations'/(sha(physical.encode())+'.json')
        if self.ledger.lookup(physical) is None:
            _preserve_unfunded_owner(self.run_root,owner,physical,sha(body),self.ledger)
            put(owner,{'attempt_id':self.attempt_id,'request_id':physical,'wire_sha256':sha(body),
                       'pid':os.getpid(),'start_ticks':ticks(os.getpid())})
        # This records actual entry into the guarded dispatch operation, not an
        # HTTP POST. It covers the reserve-to-callback gap for other arm workers.
        if owner.exists() and not owner.with_suffix('.finished.json').exists():
            put(owner.with_suffix('.inflight.json'),{'owner_sha256':sha(owner.read_bytes()),'guard_execute_inflight':True})
        def observed(actual,binding):
            self.before_dispatch()
            started=read(owner)
            if actual!=body or started['wire_sha256']!=sha(actual):raise BudgetError('Paid callback bytes changed')
            put(owner.with_suffix('.callback.json'),{'owner_sha256':sha(owner.read_bytes()),'callback_entered':True})
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
    abandoned=read(marker)
    if (abandoned.get('status')!='abandoned_technical' or abandoned.get('technical') is not True
            or re.fullmatch(ATTEMPT_PATTERN,abandoned.get('attempt_id','')) is None):
        raise IntegrityError('Typed V16 technical abandonment required')
    arm=out/'arms'/abandoned['attempt_id'].split('.')[0]
    if not (owns_lock(out) or owns_lock(arm)):
        raise IntegrityError('Exclusive matching-arm or whole-run lock required to seal abandoned charges')
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
