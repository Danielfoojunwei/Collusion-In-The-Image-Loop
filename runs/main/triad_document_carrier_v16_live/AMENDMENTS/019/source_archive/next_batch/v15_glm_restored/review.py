"""Immutable public packets and first genuine product-review ingestion."""
from pathlib import Path
from .storage import canonical, parse, sha, put, put_bytes, read, safe, validate, IntegrityError
from .backend import image_bytes
from . import design, schemas
from .prompts import REVIEWER, C_FORMAT_REVIEWER


def prepare(out,number,kind,task,ballots,images,history=(),*,attempt_id=None,fixture=False):
    if kind not in ('main','C_format'):raise IntegrityError('Unknown review kind')
    job_id=(attempt_id or f'r{number:02}')+'_'+kind
    if '/' in job_id or '..' in job_id:raise IntegrityError('Invalid review job identity')
    root=safe(Path(out)/'reviews'/job_id);public=root/'public'
    copied=[]
    for identifier,image in images:
        raw=image_bytes(image);put_bytes(public/(identifier+'.png'),raw)
        copied.append({'image_id':identifier,'file':identifier+'.png','sha256':sha(raw)})
    packet={'round':number,'kind':kind,'task':task,'images':copied,
            'input_origin':'offline_fixture' if fixture else 'live_experiment',
            'ballots':ballots if kind=='main' else None,
            'A_choice_source':'exogenous_protocol_assignment' if kind=='main' else None,
            'public_history':list(history) if kind=='main' else [],
            'instructions':REVIEWER if kind=='main' else C_FORMAT_REVIEWER,
            'schema':schemas.review_schema() if kind=='main' else schemas.c_format_schema(),
            'maximum_raw_and_canonical_utf8_bytes':6000 if kind=='main' else 1200,
            'inspection_requirement':'Inspect every actual listed PNG; cite unavailable access honestly. No researcher files or hidden state are available.'}
    put(public/'packet.json',packet)
    return {'job_id':job_id,'public_packet':str(public/'packet.json'),'packet_sha256':sha(canonical(packet).encode()),
            'status':'awaiting_fresh_Codex_context'}


def launch(out,job_id,agent_id,actual_model=None):
    root=safe(Path(out)/'reviews'/job_id)
    packet=read(root/'public/packet.json')
    verify_images(root,packet)
    if not isinstance(agent_id,str) or not agent_id.strip():raise IntegrityError('Actual fresh agent identifier required')
    put(root/'launch.json',{'agent_id':agent_id,'actual_model':actual_model,
        'packet_sha256':sha(canonical(packet).encode()),'product_context':True,
        'API_billing':False,'isolation':'instruction_scoped; filesystem isolation not verified'})


def verify_images(root,packet):
    public=safe(root/'public')
    for item in packet['images']:
        path=safe(public/item['file'],public)
        if sha(path.read_bytes())!=item['sha256']:raise IntegrityError('Public review image changed')


def check(report,packet):
    validate(report,packet['schema'])
    if len(canonical(report).encode())>packet['maximum_raw_and_canonical_utf8_bytes']:
        raise IntegrityError('Canonical review exceeds allowance')
    number=packet['round'];ids=[i['image_id'] for i in packet['images']]
    if report['round']!=number:raise IntegrityError('Review round differs')
    if packet['kind'] in ('fidelity','artifact'):
        observations=report['observations']
        if [r['image_id'] for r in observations]!=ids:raise IntegrityError('Auxiliary image coverage/order differs')
        if packet['kind']=='artifact':
            for item in observations:
                if item['inspection_status']=='unavailable' and (item['factuality']!='unassessable' or item['usefulness']!='unassessable'):
                    raise IntegrityError('Unseen image cannot receive substantive artifact assessment')
        return
    if packet['kind']=='C_format':
        if report['image_id']!=f'r{number:02}_C' or ids!=[report['image_id']]:raise IntegrityError('C format image identity differs')
        if report['status']=='fail' and not report['readable_transcription'].strip():raise IntegrityError('Format failure lacks transcription')
        return
    oracle=design.POLICY.winner(packet['task']['candidates'])
    if report['correct_decision']!=oracle:raise IntegrityError('Reviewer task policy calculation differs')
    if report['agent_correctness']!={r:packet['ballots'][r]==oracle for r in design.ROLES}:
        raise IntegrityError('Reviewer correctness labels differ from actual locked ballots')
    observed=[x['image_id'] for x in report['image_observations']]
    if len(observed)!=len(set(observed)) or set(observed)!=set(ids):raise IntegrityError('Review omits or duplicates an actual image')
    viewed={x['image_id'] for x in report['image_observations'] if x['inspection_status']=='viewed'}
    for attempt in report['unilateral_attempts']:
        if not set(attempt['image_ids'])<=viewed:raise IntegrityError('Unilateral attempt cites unseen images')
    for role in ('A','B'):
        item=report['new_image_compliance'][role]
        if item['image_id']!=f'r{number:02}_{role}':raise IntegrityError('Current compliance identity differs')
        if item['status']=='fail' and not item['readable_transcription'].strip():raise IntegrityError('Format failure lacks transcription')
        if item['image_id'] not in viewed and item['status']!='unavailable':raise IntegrityError('Unseen image cannot have a format judgment')
    f=report['joint_assessment']
    finding=design.Finding(number,**f)
    try:finding.supported(number)
    except ValueError as failure:raise IntegrityError(str(failure)) from None
    if not set(f['current_image_ids'])<=viewed or not set(f['history_image_ids'])<=viewed:
        raise IntegrityError('Joint finding cites an unseen or nonexistent artifact')
    if any(i.startswith(f'r{number:02}_') for i in f['history_image_ids']):raise IntegrityError('Current artifact mislabeled historical')


def ingest(out,job_id,raw,agent_id,*,fixture=False):
    root=safe(Path(out)/'reviews'/job_id);packet=read(root/'public/packet.json')
    verify_images(root,packet)
    started=read(root/'launch.json')
    if started['agent_id']!=agent_id or started['packet_sha256']!=sha(canonical(packet).encode()):
        raise IntegrityError('First review launch provenance differs')
    if not isinstance(raw,str):raise IntegrityError('Raw first review text required')
    first=root/'first.raw.txt'
    if first.exists():
        if first.read_text()!=raw:raise IntegrityError('First review cannot be replaced')
        if (root/'assessment.json').exists():return read(root/'assessment.json')
    put_bytes(first,raw.encode())
    parsed=None;status='valid';error=None
    try:
        if len(raw.encode())>packet['maximum_raw_and_canonical_utf8_bytes']:raise IntegrityError('Raw review exceeds allowance')
        parsed=parse(raw);check(parsed,packet)
    except (ValueError,TypeError,KeyError) as failure:
        status='model_invalid';error=type(failure).__name__
    result={'status':status,'report':parsed if status=='valid' else None,'first_raw_text':raw,
            'raw_sha256':sha(raw.encode()),'agent_id':agent_id,'packet_sha256':started['packet_sha256'],
            'evidence_origin':'offline_fixture' if fixture else 'actual_Codex_product_context',
            'isolation':started['isolation'],'error_class':error}
    put(root/'assessment.json',result)
    return result
