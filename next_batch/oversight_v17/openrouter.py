"""Pinned OpenRouter transport; no retries, no secret serialization, raw receipts."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import signal
import time
import urllib.request

from .finance import canonical, FundingHold
from .images import message_budget, validate_png

MODEL = 'z-ai/glm-5.3-flash'
ROUTE = 'open-inference/fp4'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@contextmanager
def deadline(seconds):
    prior = signal.getsignal(signal.SIGALRM)
    timer = signal.getitimer(signal.ITIMER_REAL)
    def expired(signum, frame):
        raise TimeoutError('Absolute paid request deadline')
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, *timer)
        signal.signal(signal.SIGALRM, prior)


def payload(messages, max_tokens=32768, tool=None):
    if type(max_tokens) is not int or not 1 <= max_tokens <= 65536:
        raise FundingHold('Positive bounded output limit required')
    result = {'model': MODEL, 'messages': messages, 'max_tokens': max_tokens,
              'temperature': 1.0, 'reasoning': {'effort': 'high', 'exclude': False},
              'stream': False, 'provider': {'only': [ROUTE], 'allow_fallbacks': False,
              'require_parameters': True, 'max_price': {'prompt': .15, 'completion': .20, 'request': 0}}}
    if tool is not None:
        result.update(tools=[tool], tool_choice={'type': 'function', 'function': {'name': tool['function']['name']}})
    return result


class Client:
    def __init__(self, ledger, out, transport=None):
        self.ledger = ledger
        self.out = Path(out)
        self.transport = transport

    def chat(self, request_id, messages, *, kind='actor', max_tokens=32768, tool=None):
        if self.transport is None and not os.environ.get('OPENROUTER_API_KEY'):
            raise FundingHold('Runtime OpenRouter secret missing')
        if not isinstance(messages, list) or not messages:
            raise FundingHold('Explicit bounded messages required')
        body = payload(messages, max_tokens, tool)
        text, images = message_budget(messages, [tool] if tool else [])
        wire = canonical(body).encode()
        reserve = (text + images * 8192) * .15e-6 + 2 * max_tokens * .20e-6 + .002
        return self._post('/chat/completions', request_id, wire, kind, reserve,
                          {MODEL, 'z-ai/glm-5.3-flash-20260826'}, {'OpenInference'})

    def image(self, request_id, prompt):
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode()) > 4096:
            raise FundingHold('Bounded nonempty figure brief required')
        body = {'model': 'qwen/qwen-image-2.1-turbo', 'prompt': prompt, 'n': 1,
                'resolution': '1K', 'aspect_ratio': '1:1', 'output_format': 'png',
                'provider': {'only': ['alibaba'], 'allow_fallbacks': False}}
        data, receipt = self._post('/images', request_id, canonical(body).encode(), 'image', .028,
            {'qwen/qwen-image-2.1-turbo', 'qwen-image-2.1-turbo'}, {'Alibaba Cloud Int.', 'alibaba'})
        import base64
        if len(data.get('data', [])) != 1:
            raise ValueError('Exactly one actual generated image required; charge retained')
        raw = base64.b64decode(data['data'][0]['b64_json'], validate=True)
        validate_png(raw)
        return raw, receipt

    def _post(self, endpoint, request_id, wire, kind, reserve, models, providers, *, receipt_context=None):
        if self.transport is None and not os.environ.get('OPENROUTER_API_KEY'):
            raise FundingHold('Runtime OpenRouter secret missing')
        digest = self.ledger.reserve(request_id, kind, wire, reserve)
        folder = self.out / hashlib.sha256(request_id.encode()).hexdigest()
        folder.mkdir(parents=True, exist_ok=False)
        (folder / 'request.json').write_bytes(wire)
        try:
            if self.transport is None:
                key = os.environ.get('OPENROUTER_API_KEY')
                if not key:
                    raise FundingHold('Runtime OpenRouter secret missing')
                headers = {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}
                if endpoint == '/responses':
                    headers['X-OpenRouter-Metadata'] = 'enabled'
                req = urllib.request.Request('https://openrouter.ai/api/v1' + endpoint, data=wire,
                    headers=headers, method='POST')
                with deadline(1200):
                    with urllib.request.build_opener(NoRedirect()).open(req, timeout=1200) as response:
                        raw = response.read(32 * 1024 * 1024 + 1)
            else:
                raw = self.transport(wire)
            (folder / 'raw_response.json').write_bytes(raw)
            if len(raw) > 32 * 1024 * 1024:
                raise ValueError('Oversized provider response')
            data = json.loads(raw)
            metadata = data.get('openrouter_metadata') or {}
            candidates = [value for value in [data.get('usage', {}).get('cost'), metadata.get('cost'),
                (metadata.get('usage') or {}).get('cost')] if value is not None]
            if candidates and any(value != candidates[0] for value in candidates):
                raise ValueError('Conflicting billed cost fields; reservation retained')
            cost = candidates[0] if candidates else None
            if cost is None or type(cost) not in (int, float) or data.get('usage', {}).get('is_byok') or metadata.get('is_byok'):
                raise ValueError('Missing monetary usage or unexpected BYOK')
            self.ledger.complete(request_id, cost)
            # A billed response remains billed even if model output is invalid.
            provider = data.get('provider') or metadata.get('provider_name') or metadata.get('provider')
            if isinstance(provider, dict):
                provider = provider.get('name')
            receipt = {'request_id': request_id, 'wire_sha256': digest,
                'response_sha256': hashlib.sha256(raw).hexdigest(), 'cost_usd': cost,
                'response_id': data.get('id'), 'returned_model': data.get('model'),
                'returned_provider': provider, 'kind': kind, 'at_unix': time.time(),
                'endpoint': endpoint, 'usage': data.get('usage'), 'context': receipt_context,
                'meaning': 'Development call, not a blind experimental reviewer' if kind == 'research_development' else 'First provider response'}
            (folder / 'receipt.json').write_text(canonical(receipt) + '\n')
            if (not isinstance(data.get('model'),str) or data['model'] not in models
                    or not isinstance(provider,str) or provider not in providers):
                self.ledger.hold_settled(request_id)
                raise FundingHold('Billed response differs from the pinned role route')
            return data, receipt
        except BaseException as error:
            self.ledger.uncertain(request_id)
            (folder / 'failure.json').write_text(canonical({'request_id': request_id,
                'error_type': type(error).__name__, 'automatic_post_retry': False}) + '\n')
            raise
