"""V16 document-carrier calls: fixed models, bounded inputs and one funded POST.

Reuses the proven exact-wire image parser/transport without changing V15 sources.
"""
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
from next_batch.v15_glm_restored.storage import canonical, parse, sha, put, put_bytes, IntegrityError
from .funding import NATIVE_PROFILES,profile_for_tools,NORMAL_NATIVE_CALLS,NORMAL_RESERVATION_USD
MODEL='z-ai/glm-5.3-flash'
IMAGE_MODEL='qwen/qwen-image-2.1-turbo'
MAX_TEXT_BYTES=49152
MAX_IMAGE_BLOCKS=4
MAX_OUTPUT_TOKENS=65536
MAX_INPUT_RESERVE_TOKENS=MAX_TEXT_BYTES+MAX_IMAGE_BLOCKS*VISION_RESERVE_TOKENS
MIN_CONTEXT_TOKENS=MAX_INPUT_RESERVE_TOKENS+MAX_OUTPUT_TOKENS
NATIVE_RESERVE_USD=Decimal('0.0167456')
PROMPT_PRICE_CEILING=Decimal('0.00000010')
COMPLETION_PRICE_CEILING=Decimal('0.00000005')
AMENDMENT_PROFILE='v16_output_room_65536_price_caps_0.10_0.05'
INPUT_LIMITS={name:{k:v for k,v in p.items()if k!='reserve_usd'}for name,p in NATIVE_PROFILES.items()}
PROFILE_MAP=deepcopy(NATIVE_PROFILES)


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
    try:name,profile=profile_for_tools(tools)
    except BudgetError as failure:raise IntegrityError(str(failure))from None
    return {'model':MODEL,'messages':deepcopy(messages),'tools':deepcopy(tools),
            'tool_choice':{'type':'function','function':{'name':name}},
            'max_tokens':profile['output_tokens'],'stream':False,'temperature':1.0,
            'reasoning':{'effort':'high','exclude':False},
            'provider':{'only':['open-inference/fp4'],'allow_fallbacks':False,'require_parameters':True,
                        'max_price':{'prompt':0.10,'completion':0.05,'request':0}}}


def inspect_payload(payload):
    if payload!=actor_payload(payload.get('messages',[]),payload.get('tools',[])):
        raise IntegrityError('Actual outgoing actor fields differ from V16')
    _,profile=profile_for_tools(payload['tools'])
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
    if images>profile['image_blocks']or text>profile['text_and_schema_utf8_bytes']:
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
        """Free GETs only, checking only the models actually used by V16."""
        if models is not None and models != {"actor": MODEL, "image": IMAGE_MODEL}:
            raise PreflightError("V16 model pins differ from the frozen configuration")
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
                         "reasoning_effort": "high", "max_output_tokens": MAX_OUTPUT_TOKENS,
                         "profiles":deepcopy(PROFILE_MAP),
                         "temperature_maximum_basis": "OpenRouter documented API range 0.0–2.0",
                         "temperature_documentation": 'https://openrouter.ai/docs/api/reference/parameters',
                         "provider_numeric_range_published": False,
                         "provider_sampling_effect_verified": False},
            "unused_model_checks": [],
            "input_limits": {MODEL: dict(INPUT_LIMITS)},
            "reservation": {"profiles":deepcopy(PROFILE_MAP),
                "formula":"(text_bound+image_blocks*8192)*0.00000010+2*output_tokens*0.00000005+0.002",
                "hard_prompt_max_price_per_million":"0.10","hard_completion_max_price_per_million":"0.05",
                "amendment_profile":AMENDMENT_PROFILE,
                "normal_native_calls":NORMAL_NATIVE_CALLS,"normal_study_reservation_usd":str(NORMAL_RESERVATION_USD)},
            "cash_limit_note": "Original40 inference/50cash limits remain; no new allowance."}
        self._models, self._image_endpoint = {MODEL: chat}, image_endpoint
        self._preflight_time, self._preflight = time.monotonic(), deepcopy(report)
        return report

    def adopt_preflight(self,result):
        age=time.time()-result.get('checked_at_unix',0)
        sampling=result.get('sampling',{});endpoint=result.get('chat_metadata',{}).get(MODEL,{}).get('endpoint',{})
        if (result.get('status')!='ready' or not 0<=age<300 or result.get('paid_requests_made')!=0
                or result.get('models')!={'actor':MODEL,'image':IMAGE_MODEL}
                or result.get('input_limits',{}).get(MODEL)!=INPUT_LIMITS
                or result.get('reservation',{}).get('profiles')!=PROFILE_MAP
                or sampling.get('profiles')!=PROFILE_MAP
                or result.get('reservation',{}).get('normal_native_calls')!=NORMAL_NATIVE_CALLS
                or result.get('reservation',{}).get('normal_study_reservation_usd')!=str(NORMAL_RESERVATION_USD)
                or result.get('reservation',{}).get('amendment_profile')!=AMENDMENT_PROFILE
                or result.get('reservation',{}).get('hard_prompt_max_price_per_million')!='0.10'
                or result.get('reservation',{}).get('hard_completion_max_price_per_million')!='0.05'
                or sampling.get('max_output_tokens')!=MAX_OUTPUT_TOKENS or sampling.get('reasoning_effort')!='high'
                or any(sampling.get(k)!=1.0 for k in ('visual_temperature','vote_temperature','log_temperature'))
                or endpoint.get('tag')!='open-inference/fp4' or endpoint.get('quantization')!='fp4'
                or int(endpoint.get('max_completion_tokens',0))<MAX_OUTPUT_TOKENS or int(endpoint.get('context_length',0))<MIN_CONTEXT_TOKENS):
            raise IntegrityError('Cached free metadata is stale or incompatible')
        try:
            self._check_cached_prices(result['chat_metadata'][MODEL]['pricing'])
            self._check_cached_prices(endpoint['pricing'])
        except (KeyError,TypeError,ValueError,PreflightError):
            raise IntegrityError('Cached free prices are stale or incompatible')from None
        self.ledger.assert_ready();self._models=deepcopy(result['chat_metadata'])
        self._image_endpoint=deepcopy(result['image_endpoint']);self._preflight=deepcopy(result)
        self._preflight_time=time.monotonic()-age
        return result

    @staticmethod
    def _check_cached_prices(prices):
        for name,ceiling in (('prompt',PROMPT_PRICE_CEILING),('completion',COMPLETION_PRICE_CEILING),
                              ('input_cache_read',PROMPT_PRICE_CEILING),('input_cache_write',PROMPT_PRICE_CEILING),
                              ('input_cache_write_1h',PROMPT_PRICE_CEILING),('request',Decimal(0)),('image',Decimal(0))):
            if name not in prices:
                if name in ('prompt','completion'):raise PreflightError('Missing amended native pricing')
                continue
            try:amount=Decimal(str(prices[name]))
            except Exception:raise PreflightError('Invalid amended native pricing')from None
            if not amount.is_finite()or amount<0 or amount>ceiling:
                raise PreflightError('Native pricing exceeds amended hard ceiling')
        for tier in prices.get('overrides',[]):
            try:threshold=Decimal(str(tier['min_prompt_tokens']))
            except Exception:raise PreflightError('Invalid amended native price tier')from None
            if not threshold.is_finite()or threshold<MAX_INPUT_RESERVE_TOKENS:
                raise PreflightError('Unexpected amended native price tier')

    @staticmethod
    def _chat_metadata(row,model,endpoints):
        profile={**legacy.repair.REPAIRED_PROFILE,'provider_tag':'open-inference/fp4','provider_name':'OpenInference','quantization':'fp4',
                 'prompt_ceiling':PROMPT_PRICE_CEILING,'completion_ceiling':COMPLETION_PRICE_CEILING,
                 'cache_price_ceiling':PROMPT_PRICE_CEILING,'max_input_reserve_tokens':MAX_INPUT_RESERVE_TOKENS,
                 'max_output_tokens':MAX_OUTPUT_TOKENS}
        with patch.dict(CHAT_PROFILES,{MODEL:profile}):
            result=OpenRouterProvider._chat_metadata(row,model,endpoints)
        endpoint=result['endpoint']
        if (int(endpoint.get('max_completion_tokens',0))<MAX_OUTPUT_TOKENS
                or any(int(x.get('context_length',0))<MIN_CONTEXT_TOKENS for x in (row,endpoint))):
            raise PreflightError('Pinned endpoint cannot cover V16 input/output reservation')
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
            _,profile=profile_for_tools(payload['tools'])
            if Decimal(str(reserve_usd))!=Decimal(profile['reserve_usd']):raise IntegrityError('Native reserve changed')
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
            # Another arm may acquire an unresolved intent between our ready
            # check and the atomic reserve. No own row means no own POST ever
            # began; let the coordinator pause this identity without rerolling.
            if self.ledger.lookup(request_id) is None:raise
            permanent=self.last_status is not None and self.last_status not in (408,409,425,429) and not 500<=self.last_status<=599
            raise Fault('paid_outcome_unresolved',permanent=permanent) from None
        except BudgetError:raise Fault('funding_hold',permanent=True) from None
        response,receipt=observed['response'],observed['receipt']
        if Decimal(str(receipt['cost_usd']))>Decimal(str(reserve_usd)) or receipt.get('usage',{}).get('is_byok') is True:
            self.ledger.halt('V16 cost or BYOK integrity hold');raise Fault('cost_or_byok_hold',permanent=True)
        if response.get('error') is not None:raise Fault('paid_error_envelope')
        return response,receipt,observed['cached']

    def native(self,identifier,messages,tools):
        payload=actor_payload(messages,tools);inspect_payload(payload)
        with legacy.repair.repaired_route():
            response,receipt,cached=self._paid(identifier,'chat','/chat/completions',payload,Decimal(profile_for_tools(tools)[1]['reserve_usd']))
        returned=response.get('model');allowed={MODEL,MODEL.split('/',1)[1]}
        slug=self._models.get(MODEL,{}).get('canonical_slug')
        if isinstance(slug,str):allowed.update((slug,slug.split('/',1)[-1]))
        if returned not in allowed or response.get('provider') not in (None,'OpenInference','open-inference/fp4'):
            self.ledger.halt('V16 returned actor identity mismatch');raise Fault('actor_identity_hold',permanent=True)
        completion_tokens=(response.get('usage')or{}).get('completion_tokens')
        at_cap=type(completion_tokens)in(int,float)and completion_tokens==payload['max_tokens']
        native=None;arguments=None;action=None;status='model_invalid';ignored=0;malformed_arguments=False
        try:
            choices=response['choices']
            if len(choices)!=1 or choices[0]['finish_reason'] not in (('tool_calls','stop','length')if at_cap else('tool_calls','stop')):raise ValueError()
            native=choices[0]['message'];calls=native['tool_calls']
            if native.get('refusal') or not isinstance(calls,list) or not calls:raise ValueError()
            # The API may return parallel calls despite the one-operation turn.
            # Preserve the complete response; process ONLY its first command.
            # Never search later commands for a more favorable/valid operation.
            ignored=len(calls)-1
            call=calls[0]
            if call['type']!='function' or not isinstance(call['id'],str) or not call['id']:raise ValueError()
            action=call['function']['name']
            try:arguments=parse(call['function']['arguments'])
            except json.JSONDecodeError:
                malformed_arguments=action in [t['function']['name']for t in tools];raise
            if action not in [t['function']['name'] for t in tools] or not isinstance(arguments,dict):raise ValueError()
            status='parsed'
        except (KeyError,TypeError,ValueError,IntegrityError):pass
        result={'status':status,'action':action,'arguments':arguments,'native_message':native,
                'receipt':receipt,'cached':cached,'ignored_extra_native_calls':ignored,
                'secondary_protocol_status':'model_invalid_multiple_calls' if ignored else 'valid'}
        empty_primary=(isinstance(native,dict)and not native.get('refusal')
                       and native.get('content')in(None,'',[])and native.get('tool_calls')in(None,[]))
        if at_cap and(malformed_arguments or empty_primary):
            result.update(status='technical_output_cap_truncation',technical=True,
                          requested_max_tokens=payload['max_tokens'],actual_completion_tokens=completion_tokens,
                          finish_reason=response['choices'][0].get('finish_reason'),
                          primary_failure='absent_primary_output'if empty_primary else'incomplete_primary_arguments',
                          classification_basis='Required primary output has absent or incomplete arguments and the actual completion allowance is exhausted; no behavioral inference.')
        put(self.evidence_dir/'native_results'/(sha(identifier.encode())+'.json'),
            {k:v for k,v in result.items() if k!='cached'})
        if result['status']=='technical_output_cap_truncation':
            put(self.evidence_dir/'native_truncations'/(sha(identifier.encode())+'.json'),
                {'request_id':identifier,'response_sha256':sha(canonical(response).encode()),
                 'native_result_sha256':sha((self.evidence_dir/'native_results'/(sha(identifier.encode())+'.json')).read_bytes()),
                 'technical':True,'fault_code':'native_output_cap_truncation','behavioral_evidence':False,
                 'requested_max_tokens':payload['max_tokens'],'actual_completion_tokens':completion_tokens,
                 'primary_failure':result['primary_failure'],
                 'finish_reason':response['choices'][0].get('finish_reason'),'original_paid_identity_must_not_be_reposted':True})
            raise Fault('native_output_cap_truncation')
        return result

    def render(self,identifier,brief,reference=None):
        try:
            with legacy.repair.repaired_route():result=self.image(identifier,brief,input_reference=reference)
        except ResponseValidationError:raise Fault('missing_valid_required_png') from None
        except ProviderError:raise Fault('image_identity_or_parser_hold',permanent=True) from None
        raw=result['png_bytes'];path=self.evidence_dir/'blobs'/(sha(raw)+'.png')
        put_bytes(path,raw)
        return {'image':{'path':str(path),'sha256':sha(raw)},'receipt':result['receipt']}
