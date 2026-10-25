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

from visual_collusion.budget import BudgetError, PendingPaidRequest
from visual_collusion.providers import ProviderError, PreflightError, ResponseValidationError, VISION_RESERVE_TOKENS
from experiments.triad_emergent_v9 import backend as legacy
from next_batch.v15_glm_restored.storage import canonical, parse, sha, put, put_bytes, IntegrityError
from .funding import NATIVE_PROFILES,profile_for_tools,NORMAL_NATIVE_CALLS,NORMAL_RESERVATION_USD,ACTOR_PROVIDER_TAG,ACTOR_PROVIDER_NAME,ACTOR_QUANTIZATION,MODEL_BINDING_KEY
MODEL='z-ai/glm-5.3-flash'
IMAGE_MODEL='qwen/qwen-image-2.1-turbo'
MAX_TEXT_BYTES=81920
MAX_IMAGE_BLOCKS=4
MAX_OUTPUT_TOKENS=65536
MAX_INPUT_RESERVE_TOKENS=MAX_TEXT_BYTES+MAX_IMAGE_BLOCKS*VISION_RESERVE_TOKENS
MIN_CONTEXT_TOKENS=MAX_INPUT_RESERVE_TOKENS+MAX_OUTPUT_TOKENS
NATIVE_RESERVE_USD=Decimal('0.0323104')
PROMPT_PRICE_CEILING=Decimal('0.00000015')
COMPLETION_PRICE_CEILING=Decimal('0.00000020')
AMENDMENT_PROFILE='v16_openinference_fp4_forced_function_65536_input_81920_initial_49152_prices_0.15_0.20'
# Amendment008: space every new paid POST (actor, Qwen, probe) after a Relace HTTP429.
# Amendment019 (user instruction, no HTTP429 since amendment011): 45s -> 5s; arms run in parallel.
PAID_POST_SPACING_SECONDS=5
MODEL_BINDING_FILENAME='glm_model_binding_openinference_fp4_forced.json'
REQUIRED_NATIVE_PARAMETERS=frozenset(('tools','tool_choice','max_tokens','temperature','reasoning'))
INPUT_LIMITS={name:{k:v for k,v in p.items()if k!='reserve_usd'}for name,p in NATIVE_PROFILES.items()}
PROFILE_MAP=deepcopy(NATIVE_PROFILES)
TRANSPORT_POLICY={'tool_choice':'forced_named_function','functions_per_turn':1,
    'content_json_normalization':'one_entire_JSON_object_or_exact_JSON_fence_with_exact_argument_keys_no_value_repair',
    'absolute_post_deadline_seconds':{'publish_document':3600,'other_calls':600},
    'socket_post_timeout_matches_absolute_deadline':True}


class MalformedBody(ValueError):
    """Amendment016: an HTTP 200 body that is not one JSON document; keeps the bytes for diagnosis."""
    def __init__(self,raw):self.partial=raw;super().__init__('Malformed response body')


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


def wire_tool(function):
    """Amendment018: the outgoing copy of a native function never lets the provider cut model text.

    OpenInference's constrained decoder enforces string maxLength by truncating the
    value mid-word (observed throughout R1). The wire copy therefore states each
    limit in the field description and omits the keyword; the full local schema,
    including every quota, still validates the raw output, so an over-limit value
    is recorded as the model's own invalid field rather than silently shortened.
    Idempotent: a wire copy maps to itself.
    """
    def strip(node):
        if isinstance(node, list):
            return [strip(item) for item in node]
        if not isinstance(node, dict):
            return node
        result = {key: strip(value) for key, value in node.items() if key != 'maxLength'}
        if node.get('type') == 'string' and 'maxLength' in node:
            limit = (f'Hard limit: {node["maxLength"]} characters (UTF-8 bytes). Text is never cut off; '
                     'a longer value is recorded as an invalid field.')
            result['description'] = (node['description'] + ' ' + limit) if node.get('description') else limit
        return result
    return strip(function)


def actor_payload(messages,tools):
    try:name,profile=profile_for_tools(tools)
    except BudgetError as failure:raise IntegrityError(str(failure))from None
    # Amendment018: string limits travel as prose, never as a provider grammar that truncates text.
    return {'model':MODEL,'messages':deepcopy(messages),'tools':[wire_tool(t) for t in tools],
            'tool_choice':{'type':'function','function':{'name':name}},
            'max_tokens':profile['output_tokens'],'stream':False,'temperature':1.0,
            'reasoning':{'effort':'high','exclude':False},
            'provider':{'only':[ACTOR_PROVIDER_TAG],'allow_fallbacks':False,'require_parameters':True,
                        'max_price':{'prompt':0.15,'completion':0.20,'request':0}}}


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


def paid_deadline_seconds(endpoint,body):
    if endpoint!='/chat/completions':return 600
    payload=parse(body.decode())if isinstance(body,bytes)else body
    name,_=profile_for_tools(payload.get('tools'))
    return 3600 if name=='publish_document'else 600


def content_json_arguments(content):
    """Return one complete object without extracting commands or repairing values.

    The final flag marks only incomplete JSON candidates for exact-cap resource
    classification. Multiple objects or trailing prose stay behavioral failures.
    """
    if not isinstance(content,str):return None,False,None
    fence=re.fullmatch(r'\s*```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n```\s*',content)
    body=fence.group(1)if fence else content
    kind='exact_json_fence'if fence else'entire_json_object'
    try:value=parse(body)
    except json.JSONDecodeError as failure:
        starts_object=body.lstrip().startswith('{')
        opening=re.match(r'\s*```(?:json)?[ \t]*\r?\n(?=\s*\{)',content)if not fence else None
        unfinished_fence=opening is not None
        if opening:
            try:parse(content[opening.end():])
            except json.JSONDecodeError as inside:unfinished_fence=inside.msg!='Extra data'
            except (TypeError,ValueError,IntegrityError):unfinished_fence=False
        incomplete=(starts_object or unfinished_fence)and failure.msg!='Extra data'
        return None,incomplete,None
    except (TypeError,ValueError,IntegrityError):return None,False,None
    return (value,False,kind)if isinstance(value,dict)else(None,False,None)


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
        timeout=paid_deadline_seconds(path,body)if method=='POST'else 15
        with opener.open(request,timeout=timeout) as response:
            raw=response.read(64*1024*1024+1)
        if len(raw)>64*1024*1024:raise Fault('response_size_limit',permanent=True)
        try:return parse(raw.decode())
        except (ValueError,UnicodeError) as failure:raise MalformedBody(raw) from failure

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
                   "actor_quantization": ACTOR_QUANTIZATION,
                   "actor_quantization_reported": chat["endpoint"].get("quantization"),
                   "actor_precision_verified": False,
                   "image_provider": "alibaba"}
        # The reused parser's namespaced view accepts only its original binding
        # key. Use our exact aggregate ledger's narrowly allowed repair key.
        self.guard.ledger.bind(MODEL_BINDING_KEY,canonical(binding))
        put(self.evidence_dir/MODEL_BINDING_FILENAME,binding)
        report = {"status": "ready", "checked_at_unix": time.time(),
            "paid_requests_made": 0, "models": binding["models"],
            "chat_metadata": {MODEL: chat}, "image_endpoint": image_endpoint,
            "model_binding_setting_key":MODEL_BINDING_KEY,"model_binding_evidence_filename":MODEL_BINDING_FILENAME,
            "transport_policy":deepcopy(TRANSPORT_POLICY),
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
                "formula":"(text_bound+image_blocks*8192)*0.00000015+output_tokens*0.00000020+0.002",
                "hard_prompt_max_price_per_million":"0.15","hard_completion_max_price_per_million":"0.20",
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
                or result.get('reservation',{}).get('hard_prompt_max_price_per_million')!='0.15'
                or result.get('reservation',{}).get('hard_completion_max_price_per_million')!='0.20'
                or result.get('model_binding_setting_key')!=MODEL_BINDING_KEY
                or result.get('model_binding_evidence_filename')!=MODEL_BINDING_FILENAME
                or result.get('transport_policy')!=TRANSPORT_POLICY
                or sampling.get('max_output_tokens')!=MAX_OUTPUT_TOKENS or sampling.get('reasoning_effort')!='high'
                or any(sampling.get(k)!=1.0 for k in ('visual_temperature','vote_temperature','log_temperature'))
                or endpoint.get('tag')!=ACTOR_PROVIDER_TAG or endpoint.get('provider_name')!=ACTOR_PROVIDER_NAME
                or int(endpoint.get('max_completion_tokens',0))<MAX_OUTPUT_TOKENS or int(endpoint.get('context_length',0))<MIN_CONTEXT_TOKENS):
            raise IntegrityError('Cached free metadata is stale or incompatible')
        try:
            chat=result['chat_metadata'][MODEL]
            if(chat.get('pricing_validation_basis')!='pinned_provider_only_no_fallback'
                    or not isinstance(chat.get('catalog_advertised_pricing'),dict)
                    or chat.get('pricing')!=endpoint['pricing']):
                raise PreflightError('Cached pinned-route price provenance differs')
            reconstructed={'id':MODEL,'canonical_slug':chat['canonical_slug'],'context_length':chat['catalog_context_length'],
                'architecture':{'input_modalities':chat['input_modalities'],'output_modalities':chat['output_modalities']},
                'pricing':chat['catalog_advertised_pricing'],'supported_parameters':chat['catalog_supported_parameters'],
                'reasoning':{'supported_efforts':chat['reasoning_supported_efforts']}}
            if self._chat_metadata(reconstructed,MODEL,{'data':{'id':MODEL,'endpoints':[endpoint]}})!=chat:
                raise PreflightError('Cached native capabilities or precision provenance differ')
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
        """Validate parameters actually sent by native function turns, not JSON mode.

        Unused response_format/structured_outputs parameters are not required,
        and strict schema enforcement is not claimed from metadata. Actual
        primary JSON stays locally validated.
        """
        if not isinstance(row,dict)or row.get('id')!=model:
            raise PreflightError('Native model catalog identity differs')
        architecture=row.get('architecture',{})
        inputs=architecture.get('input_modalities',[]);outputs=architecture.get('output_modalities',[])
        if not {'text','image'}<=set(inputs)or'text'not in outputs:
            raise PreflightError('Pinned native model lacks advertised image input/text output')
        data=endpoints.get('data')
        if not isinstance(data,dict)or data.get('id')!=model:
            raise PreflightError('Free chat endpoint metadata has the wrong model')
        selected=[entry for entry in data.get('endpoints',[])if isinstance(entry,dict)and entry.get('tag')==ACTOR_PROVIDER_TAG]
        if len(selected)!=1:raise PreflightError('The exact pinned actor endpoint is not uniquely available')
        endpoint=selected[0]
        if(endpoint.get('provider_name')!=ACTOR_PROVIDER_NAME or endpoint.get('model_id')!=model
                or type(endpoint.get('status'))is not int or endpoint['status']!=0):
            raise PreflightError('Pinned native provider identity or status differs')
        precision=endpoint.get('quantization')
        if precision!=ACTOR_QUANTIZATION:
            raise PreflightError('Pinned native precision metadata differs from the amendment011 route')
        catalog_parameters=row.get('supported_parameters',[]);endpoint_parameters=endpoint.get('supported_parameters',[])
        if(not isinstance(catalog_parameters,list)or not isinstance(endpoint_parameters,list)
                or any(not isinstance(x,str)for x in catalog_parameters+endpoint_parameters)):
            raise PreflightError('Invalid native supported-parameter metadata')
        parameters=set(catalog_parameters)&set(endpoint_parameters)
        if not REQUIRED_NATIVE_PARAMETERS<=parameters:
            raise PreflightError('Pinned endpoint lacks an actual outgoing native parameter')
        choice_support=endpoint.get('supports_tool_choice')
        if not isinstance(choice_support,dict)or choice_support.get('function')is not True:
            raise PreflightError('Pinned endpoint does not explicitly support a forced named function')
        efforts=row.get('reasoning',{}).get('supported_efforts',[])
        if not isinstance(efforts,list)or'high'not in efforts:
            raise PreflightError('Pinned native model does not advertise required high reasoning')
        for source,field,minimum in((row,'context_length',MIN_CONTEXT_TOKENS),(endpoint,'context_length',MIN_CONTEXT_TOKENS),
                                     (endpoint,'max_completion_tokens',MAX_OUTPUT_TOKENS)):
            try:amount=Decimal(str(source.get(field)))
            except Exception:raise PreflightError('Missing native capacity metadata')from None
            if not amount.is_finite()or amount<minimum:raise PreflightError('Pinned native capacity is too small')
        if endpoint.get('max_prompt_tokens')is not None:
            try:amount=Decimal(str(endpoint['max_prompt_tokens']))
            except Exception:raise PreflightError('Invalid native prompt capacity')from None
            if not amount.is_finite()or amount<MAX_INPUT_RESERVE_TOKENS:raise PreflightError('Pinned native prompt capacity is too small')
        if not isinstance(row.get('canonical_slug'),str)or not row['canonical_slug']:
            raise PreflightError('Missing canonical native model snapshot')
        Provider._check_cached_prices(endpoint.get('pricing',{}))
        structured='structured_outputs'in parameters
        return {'id':model,'canonical_slug':row['canonical_slug'],'pricing':deepcopy(endpoint['pricing']),
            'catalog_advertised_pricing':deepcopy(row.get('pricing',{})),'pricing_validation_basis':'pinned_provider_only_no_fallback',
            'input_modalities':deepcopy(inputs),'output_modalities':deepcopy(outputs),'supported_parameters':sorted(parameters),
            'catalog_supported_parameters':deepcopy(catalog_parameters),'catalog_context_length':row['context_length'],
            'structured_schema':structured,'strict_tool_schema_enforcement':('advertised_structured_outputs'if structured else'not_advertised_native_schema_locally_validated'),
            'native_metadata_validation':'actual_outgoing_tool_parameters_only','endpoint':deepcopy(endpoint),
            'actual_tool_choice':'forced_named_function','tool_choice_support':deepcopy(choice_support),'tool_call_guaranteed_by_metadata':False,
            'reasoning_effort':'high','reasoning_supported_efforts':deepcopy(efforts),'expose_reasoning':True,
            'precision_status':'endpoint_metadata_reported','endpoint_quantization_reported':precision,'precision_verified':False,
            'vision_evidence':'Model metadata advertises image input; endpoint-native vision behavior is not live-tested by free preflight'}

    def _post_exact(self,body,binding):
        self.last_status=None
        try:
            seconds=paid_deadline_seconds(binding['endpoint'],body)
            with deadline(seconds):result=self._http('POST',binding['endpoint'],body)
            if not isinstance(result,dict):raise Fault('paid_non_object_envelope')
            put(self.evidence_dir/'observed_responses'/(sha(binding['request_id'].encode())+'.json'),
                {'binding':binding,'response':result,'absolute_post_deadline_seconds':seconds})
            return result
        except urllib.error.HTTPError as error:
            self.last_status=error.code
            # Retain bounded router diagnostics without headers, request bodies or credentials.
            diagnostic={}
            try:
                envelope=parse(error.read(16385).decode('utf-8'))
                item=envelope.get('error',{}) if isinstance(envelope,dict) else {}
                metadata=item.get('metadata') if isinstance(item.get('metadata'),dict) else {}
                for key,value in (('code',item.get('code')),('message',item.get('message')),
                                  ('provider_name',metadata.get('provider_name')),('upstream_raw',metadata.get('raw')),
                                  ('retry_after',error.headers.get('Retry-After') if error.headers else None)):
                    if isinstance(value,(dict,list)):value=canonical(value)
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
            record={'request_id':binding['request_id'],'failure_class':type(error).__name__,'request_not_reposted':True}
            partial=getattr(error,'partial',None)
            if isinstance(partial,bytes):
                # Amendment012: keep a bounded, redacted view of a truncated body for diagnosis.
                text=partial.decode('utf-8','replace')
                if isinstance(self._key,str) and self._key:text=text.replace(self._key,'[REDACTED]')
                text=re.sub(r'(?i)bearer\s+[^\s]+|sk-[A-Za-z0-9_-]+','[REDACTED]',text)
                generation=re.search(r'"id"\s*:\s*"(gen-[A-Za-z0-9_-]{1,120})"',text)
                record['partial_body']={'received_bytes':len(partial),'stripped_prefix':text.strip()[:4000],
                                        'stripped_suffix':text.strip()[-1500:],
                                        'generation_id':generation.group(1) if generation else None}
            put(self.evidence_dir/'transport_failures'/(sha(binding['request_id'].encode())+'.json'),record)
            raise Fault('paid_transport_or_json_failure') from None

    def _pace(self):
        """Wait until PAID_POST_SPACING_SECONDS after the previous new paid POST, across processes.

        The stamp is administrative pacing state, not evidence; it never changes a request.
        """
        stamp=self.evidence_dir/'pacing'/'last_paid_post.stamp'
        stamp.parent.mkdir(parents=True,exist_ok=True)
        if stamp.exists():
            remaining=stamp.stat().st_mtime+PAID_POST_SPACING_SECONDS-time.time()
            if remaining>0:time.sleep(remaining)
        stamp.touch()

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
                     'absolute_post_deadline_seconds':paid_deadline_seconds(endpoint,body),
                     'key_remaining_before_usd':key_status.get('remaining_usd') if key_status else None,
                     'received_at_unix':time.time()}
            return {'response':response,'receipt':receipt}
        if self.ledger.lookup(request_id) is None:self._pace()
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
        response,receipt,cached=self._paid(identifier,'chat','/chat/completions',payload,Decimal(profile_for_tools(tools)[1]['reserve_usd']))
        returned=response.get('model');allowed={MODEL,MODEL.split('/',1)[1]}
        slug=self._models.get(MODEL,{}).get('canonical_slug')
        if isinstance(slug,str):allowed.update((slug,slug.split('/',1)[-1]))
        if returned not in allowed or response.get('provider') not in (None,ACTOR_PROVIDER_NAME,ACTOR_PROVIDER_TAG):
            self.ledger.halt('V16 returned actor identity mismatch');raise Fault('actor_identity_hold',permanent=True)
        completion_tokens=(response.get('usage')or{}).get('completion_tokens')
        at_cap=type(completion_tokens)in(int,float)and completion_tokens==payload['max_tokens']
        native=None;arguments=None;action=None;status='model_invalid';ignored=0;malformed_arguments=False
        argument_origin='none';content_json_form=None
        try:
            choices=response['choices']
            if len(choices)!=1 or choices[0]['finish_reason'] not in (('tool_calls','stop','length')if at_cap else('tool_calls','stop')):raise ValueError()
            native=choices[0]['message'];calls=native.get('tool_calls')
            if native.get('refusal'):raise ValueError()
            if calls in(None,[]):
                arguments,malformed_arguments,content_json_form=content_json_arguments(native.get('content'))
                if arguments is None:raise ValueError()
                argument_origin='content_json'
                expected=set(tools[0]['function']['parameters'].get('properties',{}))
                if set(arguments)!=expected:raise ValueError()
                action=tools[0]['function']['name']
            else:
                if not isinstance(calls,list):raise ValueError()
                # Preserve every returned call; process only the first, never
                # prefer a later call or content wrapper over an invalid first.
                ignored=len(calls)-1;call=calls[0];argument_origin='native_tool_call'
                if call['type']!='function' or not isinstance(call['id'],str) or not call['id']:raise ValueError()
                action=call['function']['name']
                try:arguments=parse(call['function']['arguments'])
                except json.JSONDecodeError as failure:
                    malformed_arguments=action in [t['function']['name']for t in tools]and failure.msg!='Extra data';raise
                if action not in [t['function']['name']for t in tools]or not isinstance(arguments,dict):raise ValueError()
            status='parsed'
        except (KeyError,TypeError,ValueError,IntegrityError):pass
        result={'status':status,'action':action,'arguments':arguments,'native_message':native,
                'receipt':receipt,'cached':cached,'ignored_extra_native_calls':ignored,
                'argument_origin':argument_origin,'content_json_form':content_json_form,
                'secondary_protocol_status':'model_invalid_multiple_calls' if ignored else 'valid'}
        if isinstance(native,dict)and isinstance(native.get('content'),str)and(argument_origin=='content_json'or malformed_arguments):
            raw_content=native['content'].encode('utf-8')
            source=self.evidence_dir/'native_content'/(sha(identifier.encode())+'.txt')
            put_bytes(source,raw_content)
            result['content_json_source']={'path':str(source),'sha256':sha(raw_content),'utf8_bytes':len(raw_content)}
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
