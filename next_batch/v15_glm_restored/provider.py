"""Pinned OpenRouter native calls and PNG rendering, funded once per exact wire."""
from copy import deepcopy
from decimal import Decimal
import base64
import http.client
import json
import os
import re
import signal
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from unittest.mock import patch

from visual_collusion.budget import BudgetError, PendingPaidRequest
from visual_collusion.providers import ProviderError, PreflightError, ResponseValidationError, VISION_RESERVE_TOKENS
from visual_collusion.providers import CHAT_PROFILES, OpenRouterProvider
from experiments.triad_emergent_v9 import backend as legacy
from .storage import canonical, parse, sha, put, put_bytes, IntegrityError
from .design import CONFIG
from . import schemas

MODEL=CONFIG['models']['actors']; IMAGE_MODEL=CONFIG['models']['image']


class Fault(RuntimeError):
    def __init__(self,code,*,permanent=False):self.code=code;self.permanent=permanent;super().__init__(code)


@contextmanager
def deadline(seconds):
    """Absolute deadline in addition to socket inactivity; never retry a POST."""
    previous=signal.getsignal(signal.SIGALRM);old=signal.getitimer(signal.ITIMER_REAL)
    def expired(*_):raise TimeoutError('Absolute request deadline')
    signal.signal(signal.SIGALRM,expired);signal.setitimer(signal.ITIMER_REAL,seconds)
    try:yield
    finally:
        signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,previous)
        if old[0]:signal.setitimer(signal.ITIMER_REAL,*old)


def actor_payload(messages,tools):
    names=[t['function']['name'] for t in tools]
    if not names or len(names)!=len(set(names)):raise IntegrityError('Invalid native tools')
    return {'model':MODEL,'messages':deepcopy(messages),'tools':deepcopy(tools),
            'tool_choice':{'type':'function','function':{'name':names[0]}} if len(names)==1 else 'required',
            'max_tokens':16384,'stream':False,'temperature':1.0,
            'reasoning':{'effort':'high','exclude':False},
            'provider':{'only':['open-inference/fp4'],'allow_fallbacks':False,'require_parameters':True,
                        'max_price':{'prompt':0.15,'completion':0.5,'request':0}}}


def inspect_payload(payload):
    if payload!=actor_payload(payload.get('messages',[]),payload.get('tools',[])):
        raise IntegrityError('Actual outgoing actor fields differ from V11')
    messages=payload['messages'];text=0;images=0
    if not 2<=len(messages)<=64 or messages[0].get('role')!='system':raise IntegrityError('Invalid bounded history')
    for m in messages:
        content=m.get('content')
        if isinstance(content,str):text+=len(content.encode())
        elif isinstance(content,list):
            for part in content:
                if part.get('type')=='text':text+=len(part['text'].encode())
                elif part.get('type')=='image_url':
                    url=part['image_url']['url']
                    if not url.startswith('data:image/png;base64,'):raise IntegrityError('Only actual PNG data URLs permitted')
                    from visual_collusion.providers import _validate_png
                    _validate_png(base64.b64decode(url.split(',',1)[1],validate=True),require_square=True)
                    images+=1
                else:raise IntegrityError('Unexpected content block')
        elif content is not None:raise IntegrityError('Unexpected message content')
        if m.get('tool_calls'):text+=len(canonical(m['tool_calls']).encode())
    text+=len(canonical(payload['tools']).encode())
    if images>3 or text>131072 or text+images*VISION_RESERVE_TOKENS>=210000:
        raise IntegrityError('Context exceeds conservative funded bounds; no truncation')
    return text,images


class Provider(legacy.Provider):
    """Reuse checked free model metadata and image parser; override paid policy."""
    def __init__(self,guard,evidence_dir,*,transport=None,api_key=None):
        super().__init__(os.environ.get('OPENROUTER_API_KEY') if api_key is None else api_key,
                         guard,evidence_dir,transport=transport,timeout=120)
        self._get_cache={};self.last_status=None

    def _http(self,method,path,body=None):
        if self._transport is not None:return self._transport(method,path,body)
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):return None
        request=urllib.request.Request(self.BASE_URL+path,data=body,method=method,
                    headers={'Authorization':'Bearer '+self._key,'Content-Type':'application/json'})
        opener=self._opener or urllib.request.build_opener(NoRedirect())
        with opener.open(request,timeout=120 if method=='POST' else 15) as response:
            raw=response.read(64*1024*1024+1)
        if len(raw)>64*1024*1024:raise Fault('response_size_limit',permanent=True)
        return parse(raw.decode())

    def _request(self,method,path,payload=None):
        if method!='GET' or not path.startswith('/') or any(c in path for c in ('..','?','#')):
            raise Fault('invalid_free_endpoint',permanent=True)
        cached=self._get_cache.get(path)
        if cached and time.monotonic()-cached[0]<300:return deepcopy(cached[1])
        started=time.monotonic()
        with deadline(45):
            for attempt in range(3):
                if time.monotonic()-started>=45:raise Fault('free_absolute_deadline')
                try:
                    result=self._http('GET',path)
                    if not isinstance(result,dict) or 'error' in result:raise Fault('free_error_envelope')
                    # Only successful JSON envelopes are cached, never failures.
                    self._get_cache[path]=(time.monotonic(),deepcopy(result));return result
                except urllib.error.HTTPError as error:
                    if error.code not in (408,409,425,429) and not 500<=error.code<=599:
                        raise Fault('free_http_policy_hold',permanent=True) from None
                    if attempt==2:raise Fault('free_http_unavailable') from None
                except (urllib.error.URLError,TimeoutError,OSError,http.client.HTTPException,ValueError,UnicodeError):
                    if attempt==2 or time.monotonic()-started>=45:raise Fault('free_metadata_unavailable') from None
        raise Fault('free_metadata_unavailable')

    def preflight(self, models=None, *, max_inference_usd=40,
                  required_remaining_usd=None, minimum_remaining=None):
        """Free GETs only, checking only the models actually used by V9."""
        if models is not None and models != {"actor": MODEL, "image": IMAGE_MODEL}:
            raise PreflightError("V9 model pins differ from the frozen configuration")
        if Decimal(str(max_inference_usd)) != Decimal("40"):
            raise PreflightError("The original aggregate inference cap must remain40")
        self._preflight = None
        self.ledger.assert_ready()
        required = minimum_remaining if minimum_remaining is not None else required_remaining_usd
        key_status = self._key_check(0.05 if required is None else required)
        catalog = self._request("GET", "/models")
        rows = [r for r in catalog.get("data", []) if isinstance(r, dict) and r.get("id") == MODEL]
        if len(rows) != 1:
            raise PreflightError("The pinned actor model is not uniquely available")
        endpoints = self._request("GET", "/models/" + MODEL + "/endpoints")
        with legacy.repair.repaired_route():
            chat = self._chat_metadata(rows[0], MODEL, endpoints)
        images = self._request("GET", "/images/models")
        image_rows = [r for r in images.get("data", [])
                      if isinstance(r, dict) and r.get("id") == IMAGE_MODEL]
        if len(image_rows) != 1:
            raise PreflightError("The pinned image model is not uniquely available")
        endpoint = self._request("GET", "/images/models/" + IMAGE_MODEL + "/endpoints")
        if endpoint.get("id") != IMAGE_MODEL:
            raise PreflightError("Image endpoint model identity mismatch")
        image_endpoint = self._image_metadata(image_rows[0], endpoint)
        binding = {"models": {"actor": MODEL, "image": IMAGE_MODEL},
                   "actor_slug": chat["canonical_slug"],
                   "actor_provider": chat["endpoint"]["tag"],
                   "actor_quantization": chat["endpoint"].get("quantization"),
                   "image_provider": "alibaba"}
        self.ledger.bind('model_configuration',canonical(binding))
        put(self.evidence_dir/'glm_model_binding.json',binding)
        report = {"status": "ready", "checked_at_unix": time.time(),
            "paid_requests_made": 0, "models": binding["models"],
            "chat_metadata": {MODEL: chat}, "image_endpoint": image_endpoint,
            "key": key_status, "local_budget": self.ledger.summary(),
            "sampling": {"visual_temperature": 1.0,
                         "vote_temperature": 1.0,
                         "log_temperature": 1.0,
                         "reasoning_effort": "high", "max_output_tokens": 16384,
                         "temperature_maximum_basis": "OpenRouter documented API range 0.0–2.0",
                         "temperature_documentation": 'https://openrouter.ai/docs/api/reference/parameters',
                         "provider_numeric_range_published": False,
                         "provider_sampling_effect_verified": False},
            "unused_model_checks": [],
            "input_limits": {MODEL: {"text_and_schema_utf8_bytes": 131072,
                "image_blocks": 3, "output_tokens": 16384}},
            "cash_limit_note": "Original40 inference/50cash limits remain; no new allowance."}
        self._models, self._image_endpoint = {MODEL: chat}, image_endpoint
        self._preflight_time, self._preflight = time.monotonic(), deepcopy(report)
        return report

    def adopt_preflight(self,result):
        age=time.time()-result.get('checked_at_unix',0)
        sampling=result.get('sampling',{});endpoint=result.get('chat_metadata',{}).get(MODEL,{}).get('endpoint',{})
        if (result.get('status')!='ready' or not 0<=age<300 or result.get('paid_requests_made')!=0
                or result.get('models')!={'actor':MODEL,'image':IMAGE_MODEL}
                or sampling.get('max_output_tokens')!=16384 or sampling.get('reasoning_effort')!='high'
                or any(sampling.get(k)!=1.0 for k in ('visual_temperature','vote_temperature','log_temperature'))
                or endpoint.get('tag')!='open-inference/fp4' or endpoint.get('quantization')!='fp4'
                or int(endpoint.get('max_completion_tokens',0))<16384 or int(endpoint.get('context_length',0))<226384):
            raise IntegrityError('Cached free metadata is stale or incompatible')
        self.ledger.assert_ready();self._models=deepcopy(result['chat_metadata'])
        self._image_endpoint=deepcopy(result['image_endpoint']);self._preflight=deepcopy(result)
        self._preflight_time=time.monotonic()-age
        return result

    @staticmethod
    def _chat_metadata(row,model,endpoints):
        profile={**legacy.repair.REPAIRED_PROFILE,'provider_tag':'open-inference/fp4','provider_name':'OpenInference','quantization':'fp4'}
        with patch.dict(CHAT_PROFILES,{MODEL:profile}):
            result=OpenRouterProvider._chat_metadata(row,model,endpoints)
        endpoint=result['endpoint']
        if (int(endpoint.get('max_completion_tokens',0))<16384
                or any(int(x.get('context_length',0))<226384 for x in (row,endpoint))):
            raise PreflightError('Pinned endpoint cannot cover V11 input/output reservation')
        parameters=set(row.get('supported_parameters',())) & set(endpoint.get('supported_parameters',()))
        if not {'tools','tool_choice','temperature'}<=parameters:
            raise PreflightError('Pinned endpoint lacks required native tools or temperature metadata')
        return result

    def _post_exact(self,body,binding):
        self.last_status=None
        try:
            with deadline(600):result=self._http('POST',binding['endpoint'],body)
            if not isinstance(result,dict):raise Fault('paid_non_object_envelope')
            put(self.evidence_dir/'observed_responses'/(sha(binding['request_id'].encode())+'.json'),
                {'binding':binding,'response':result})
            return result
        except urllib.error.HTTPError as error:
            self.last_status=error.code
            # Retain bounded router diagnostics without headers, request bodies or credentials.
            diagnostic={}
            try:
                envelope=parse(error.read(16385).decode('utf-8'))
                item=envelope.get('error',{}) if isinstance(envelope,dict) else {}
                for key in ('code','message'):
                    value=item.get(key)
                    if isinstance(value,(str,int)):
                        value=str(value)
                        if isinstance(self._key,str) and self._key:value=value.replace(self._key,'[REDACTED]')
                        value=re.sub(r'(?i)bearer\s+[^\s]+|sk-[A-Za-z0-9_-]+','[REDACTED]',value)
                        diagnostic[key]=value[:2000]
            except (ValueError,UnicodeError,OSError,IntegrityError):
                diagnostic={'status':'error_body_unavailable_or_invalid'}
            put(self.evidence_dir/'transport_failures'/(sha(binding['request_id'].encode())+'.json'),
                {'request_id':binding['request_id'],'failure_class':'HTTPError','http_status':error.code,
                 'router_diagnostic':diagnostic,'request_not_reposted':True})
            raise Fault('paid_http_failure',permanent=error.code not in (408,409,425,429) and not 500<=error.code<=599) from None
        except (urllib.error.URLError,TimeoutError,OSError,http.client.HTTPException,ValueError,UnicodeError) as error:
            put(self.evidence_dir/'transport_failures'/(sha(binding['request_id'].encode())+'.json'),
                {'request_id':binding['request_id'],'failure_class':type(error).__name__,
                 'request_not_reposted':True})
            raise Fault('paid_transport_or_json_failure') from None

    def _paid(self,request_id,kind,endpoint,payload,reserve_usd):
        if kind=='chat':
            inspect_payload(payload)
            if Decimal(str(reserve_usd))!=Decimal('0.060384'):raise IntegrityError('Native reserve changed')
        if self._preflight is None:raise Fault('missing_successful_preflight',permanent=True)
        self.guard.before_dispatch()
        key_status=None
        if self.ledger.lookup(request_id) is None:
            self.ledger.assert_ready()
            if time.monotonic()-self._preflight_time>300:self.preflight(minimum_remaining=reserve_usd)
            try:key_status=self._key_check(reserve_usd)
            except (PreflightError,ProviderError):raise Fault('key_funding_policy_hold',permanent=True) from None
        wire,mapping=legacy.codec.prepare_wire(self.evidence_dir,request_id,endpoint,payload)
        if legacy.codec.verify_wire(self.evidence_dir,endpoint,payload,mapping)!=wire:
            raise IntegrityError('PNG/wire reconstruction differs')
        put(self.evidence_dir/'transport_requests'/(sha(request_id.encode())+'.json'),{
            'request_id':request_id,'endpoint':endpoint,'logical_payload':legacy._archive_payload(self.evidence_dir,payload),
            'mapping':mapping})
        def post(body,binding):
            if sha(body)!=mapping['wire_body_sha256']:raise IntegrityError('Funded wire bytes differ')
            response=self._post_exact(body,binding)
            usage=response.get('usage')
            receipt={**binding,'response_id':response.get('id'),'returned_model':response.get('model'),
                     'provider':response.get('provider'),'usage':usage,
                     'cost_usd':usage.get('cost') if isinstance(usage,dict) else None,
                     'reserved_usd':float(reserve_usd),
                     'key_remaining_before_usd':key_status.get('remaining_usd') if key_status else None,
                     'received_at_unix':time.time()}
            return {'response':response,'receipt':receipt}
        try:observed=self.guard.execute(request_id,kind,payload['model'],canonical(wire).encode(),reserve_usd,post)
        except PendingPaidRequest:
            permanent=self.last_status is not None and self.last_status not in (408,409,425,429) and not 500<=self.last_status<=599
            raise Fault('paid_outcome_unresolved',permanent=permanent) from None
        except BudgetError:raise Fault('funding_hold',permanent=True) from None
        response,receipt=observed['response'],observed['receipt']
        if Decimal(str(receipt['cost_usd']))>Decimal(str(reserve_usd)) or receipt.get('usage',{}).get('is_byok') is True:
            self.ledger.halt('V11 cost or BYOK integrity hold');raise Fault('cost_or_byok_hold',permanent=True)
        if response.get('error') is not None:raise Fault('paid_error_envelope')
        return response,receipt,observed['cached']

    def native(self,identifier,messages,tools):
        payload=actor_payload(messages,tools);inspect_payload(payload)
        with legacy.repair.repaired_route():
            response,receipt,cached=self._paid(identifier,'chat','/chat/completions',payload,Decimal('0.060384'))
        returned=response.get('model');allowed={MODEL,MODEL.split('/',1)[1]}
        slug=self._models.get(MODEL,{}).get('canonical_slug')
        if isinstance(slug,str):allowed.update((slug,slug.split('/',1)[-1]))
        if returned not in allowed or response.get('provider') not in (None,'OpenInference','open-inference/fp4'):
            self.ledger.halt('V11 returned actor identity mismatch');raise Fault('actor_identity_hold',permanent=True)
        native=None;arguments=None;action=None;status='model_invalid';ignored=0
        try:
            choices=response['choices']
            if len(choices)!=1 or choices[0]['finish_reason'] not in ('tool_calls','stop'):raise ValueError()
            native=choices[0]['message'];calls=native['tool_calls']
            if native.get('refusal') or not isinstance(calls,list) or not calls:raise ValueError()
            # The API may return parallel calls despite the one-operation turn.
            # Preserve the complete response; process ONLY its first command.
            # Never search later commands for a more favorable/valid operation.
            ignored=len(calls)-1
            call=calls[0]
            if call['type']!='function' or not isinstance(call['id'],str) or not call['id']:raise ValueError()
            action=call['function']['name'];arguments=parse(call['function']['arguments'])
            if action not in [t['function']['name'] for t in tools] or not isinstance(arguments,dict):raise ValueError()
            status='parsed'
        except (KeyError,TypeError,ValueError,IntegrityError):pass
        result={'status':status,'action':action,'arguments':arguments,'native_message':native,
                'receipt':receipt,'cached':cached,'ignored_extra_native_calls':ignored,
                'secondary_protocol_status':'model_invalid_multiple_calls' if ignored else 'valid'}
        put(self.evidence_dir/'native_results'/(sha(identifier.encode())+'.json'),
            {k:v for k,v in result.items() if k!='cached'})
        return result

    def render(self,identifier,brief,reference=None):
        try:
            with legacy.repair.repaired_route():result=self.image(identifier,brief,input_reference=reference)
        except ResponseValidationError:raise Fault('missing_valid_required_png') from None
        except ProviderError:raise Fault('image_identity_or_parser_hold',permanent=True) from None
        raw=result['png_bytes'];path=self.evidence_dir/'blobs'/(sha(raw)+'.png')
        put_bytes(path,raw)
        return {'image':{'path':str(path),'sha256':sha(raw)},'receipt':result['receipt']}
