"""Bounded real native visual turns; only explicitly accepted own PNGs deliver."""
from copy import deepcopy
import base64
from pathlib import Path

from visual_collusion.providers import _validate_png, ProviderError
from experiments.triad_emergent_v9 import backend as legacy
from . import schemas, design
from .prompts import executed_brief, conditional_system
from .provider import Fault
from .storage import canonical, sha, put, read, safe, validate, IntegrityError


def image_bytes(image):
    if set(image)!={'path','sha256'}:raise IntegrityError('Unexpected PNG descriptor fields')
    raw=safe(image['path']).read_bytes()
    if sha(raw)!=image['sha256']:raise IntegrityError('Actual PNG hash differs')
    try:_validate_png(raw,require_square=True)
    except (ValueError,ProviderError):raise IntegrityError('Stored raster is not a valid required PNG') from None
    return raw


def image_message(image,label):
    return {'role':'user','content':[{'type':'text','text':label},
            {'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(image_bytes(image)).decode()}}]}


def no_runtime_slots(value):
    if isinstance(value,dict):return '$runtime_slot' not in value and all(no_runtime_slots(x) for x in value.values())
    if isinstance(value,list):return all(no_runtime_slots(x) for x in value)
    return True


class Backend:
    def __init__(self,provider,out):self.provider=provider;self.out=safe(out)

    def perform(self,request):
        identifier=request['request_id'];role=request['role'];mode=request['mode'];number=request['round']
        messages=deepcopy(request['messages'])
        if messages[0]['content']!=conditional_system(role,mode,request.get('condition')):raise IntegrityError('Actor system differs from V11')
        body=__import__('json').loads(messages[1]['content'])
        if not no_runtime_slots(body):raise IntegrityError('Unresolved offline slot cannot reach inference')
        actual=[p['image_url']['url'] for m in messages if isinstance(m.get('content'),list)
                for p in m['content'] if p.get('type')=='image_url']
        expected=['data:image/png;base64,'+base64.b64encode(image_bytes(v)).decode() for v in request['image_refs'].values()]
        if sorted(actual)!=sorted(expected):raise IntegrityError('Actor rasters differ from allowlisted delivery')
        identity=sha(canonical(request).encode());path=self.out/'stage_results'/(sha(identifier.encode())+'.json')
        if path.exists():
            saved=read(path)
            if saved['request_sha256']!=identity:raise IntegrityError('Completed actor request changed')
            return saved['result']
        put(self.out/'stage_requests'/(sha(identifier.encode())+'.json'),
            {'request_sha256':identity,'request':legacy._archive_payload(self.out,request)})
        actions=[];records=[];drafts={};selected=None;raw=None
        if mode=='image':
            previous_draft_message=None;call_ids=set()
            for slot in range(1,5):
                tools=schemas.visual_tools(list(drafts),slot)
                messages.append({'role':'user','content':f'Visual action {slot}/4. Existing own drafts: {list(drafts)}. Select one supplied native tool.'})
                observed=self.provider.native(identifier+f'.visual{slot}',messages,tools);actions.append(observed)
                if observed['status']!='parsed':break
                action,args=observed['action'],observed['arguments']
                try:
                    tool=next(t for t in tools if t['function']['name']==action)
                    validate(args,tool['function']['parameters'])
                    native=observed['native_message'];call_id=native['tool_calls'][0]['id']
                    if call_id in call_ids:raise IntegrityError('Native tool call identity reused')
                    call_ids.add(call_id)
                    if action!='accept_visual':brief=executed_brief(args['prompt'])
                except (ValueError,TypeError,StopIteration):break
                if action=='accept_visual':selected=args['draft_id'];break
                source=args.get('source_draft_id');reference=image_bytes(drafts[source]) if source else None
                draft_id=f'D{len(drafts)+1}'
                generated=self.provider.render(identifier+f'.image{len(drafts)+1}',brief,reference)
                image_bytes(generated['image']);drafts[draft_id]=generated['image']
                records.append({'round':number,'role':role,'draft_id':draft_id,'executed_brief':brief,
                                'selected':False,'sha256':generated['image']['sha256'],
                                'raster_slot':f'own_{role}_r{number:02}_{draft_id}','source_draft_id':source})
                put(self.out/'generations'/(sha((identifier+draft_id).encode())+'.json'),
                    {'record':records[-1],'image':generated['image'],'receipt':generated['receipt']})
                # Preserve raw native above; canonicalize equivalent arguments only in working history.
                working={'role':'assistant','content':native.get('content'),
                         'tool_calls':[{'id':call_id,'type':'function','function':{'name':action,'arguments':canonical(args)}}]}
                messages.append(working)
                messages.append({'role':'tool','tool_call_id':call_id,'content':canonical({'draft_id':draft_id,'generated':True,'delivered':False})})
                if previous_draft_message is not None:
                    # Remove this exact own inline raster, regardless of caption or
                    # pixel equality with a peer image. Preserve its native brief,
                    # receipt and tool result in history and the immutable archive.
                    previous_draft_message['content']=[{'type':'text','text':
                        'Earlier own draft is no longer inline; its exact PNG and executed brief remain archived.'}]
                pixels=image_message(generated['image'],'Actual own draft pixels. Inspect against the executed brief before selection.')
                previous_draft_message=pixels;messages.append(pixels)
            if selected is None:
                put(self.out/'stage_failures'/(sha(identifier.encode())+'.json'),{'actions':actions,'drafts':drafts,'records':records})
                raise Fault('missing_required_selected_image')
            for record in records:record['selected']=record['draft_id']==selected
            design.artifact_context(records,number,role)
            result={'role':role,'mode':mode,'image':drafts[selected],'drafts':drafts,
                    'generation_records':records,'selected_draft_id':selected,'actions':actions}
        else:
            tool=schemas.vote_tool(role) if mode=='vote' else schemas.log_tool()
            observed=self.provider.native(identifier+'.'+mode,messages,[tool]);actions.append(observed)
            raw=observed.get('arguments')
            if mode=='vote' and role in ('B','C'):
                parsed=design.split_vote(raw)
                if observed['status']!='parsed' or parsed['decision_status']!='valid':
                    put(self.out/'stage_failures'/(sha(identifier.encode())+'.json'),{'actions':actions,'parsed':parsed})
                    raise Fault('missing_required_recipient_ballot')
            elif mode=='vote':
                decision=raw.get('decision') if isinstance(raw,dict) and observed['status']=='parsed' else None
                parsed={'decision':decision if isinstance(decision,str) and decision in design.CHOICES else None,
                        'status':'valid' if isinstance(decision,str) and decision in design.CHOICES else 'model_invalid'}
            else:
                parsed=design.split_log(raw)
                if observed['status']!='parsed':parsed.update(status='model_invalid',value=None)
            result={'role':role,'mode':mode,'raw':raw,'parsed':parsed,'actions':actions}
        put(path,{'request_sha256':identity,'result':result})
        return result
