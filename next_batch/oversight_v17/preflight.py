"""Free authenticated metadata only. Never serialize authorization headers."""
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.request

from .finance import FundingHold, canonical
from .openrouter import MODEL, NoRedirect


def get(path):
    key = os.environ.get('OPENROUTER_API_KEY')
    if not key:
        raise FundingHold('Runtime OpenRouter secret missing')
    request = urllib.request.Request('https://openrouter.ai/api/v1' + path,
        headers={'Authorization': 'Bearer ' + key}, method='GET')
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
        raw = response.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise FundingHold('Oversized metadata')
    return json.loads(raw)


def inspect(key, routes):
    data = key.get('data', {})
    if data.get('is_free_tier') or data.get('is_management_key') or data.get('limit_remaining') is None:
        raise FundingHold('Funded inference key metadata required')
    matches = [e for e in routes.get('data', {}).get('endpoints', [])
               if e.get('tag') == 'open-inference/fp4']
    if len(matches) != 1:
        raise FundingHold('Pinned route not uniquely available')
    endpoint = matches[0]
    if endpoint.get('status') != 0:
        raise FundingHold('Pinned route is not currently available')
    prices = endpoint.get('pricing', {})
    required = {'tools', 'tool_choice', 'temperature', 'max_tokens', 'reasoning'}
    if not required.issubset(endpoint.get('supported_parameters', [])):
        raise FundingHold('Pinned route lacks required parameters')
    if float(prices.get('prompt', 'inf')) > .15e-6 or float(prices.get('completion', 'inf')) > .20e-6:
        raise FundingHold('Pinned route exceeds price ceiling')
    if not endpoint.get('supports_tool_choice', {}).get('function'):
        raise FundingHold('Forced named tool is unsupported')
    return {k: data.get(k) for k in ['limit', 'limit_remaining', 'limit_reset', 'usage', 'is_free_tier', 'is_management_key']}


def run(destination):
    key = get('/auth/key')
    routes = get('/models/' + MODEL + '/endpoints')
    account = inspect(key, routes)
    record = {'at_unix': time.time(), 'account': account, 'actor_routes': routes,
              'model': MODEL, 'paid_requests': 0}
    # Key labels and identifiers are unnecessary for the reproducibility record.
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical(record) + '\n')
    return account, hashlib.sha256(path.read_bytes()).hexdigest()
