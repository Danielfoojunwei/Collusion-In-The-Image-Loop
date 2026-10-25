"""Measure how much of each page image the actor route actually ingested.

Rebuilds every B/C INITIAL and VOTE request exactly as the runner sent it (offline, no network), takes the
text-only characters-per-token rate from the INITIAL calls (which carry no images), and attributes the
billed prompt tokens of each VOTE call beyond its text to its page images. Run from the bind-mounted
workspace root with PYTHONPATH=src:. ; writes img_tokens.json next to this file.
"""
import json
from pathlib import Path

from next_batch.v16_document_carrier import runner as R

OUT = Path('runs/main/triad_document_carrier_v16_live')
HERE = Path(__file__).resolve().parent
run = R.Runner(str(OUT))
F = json.load(open(HERE / 'v16_facts.json'))
calls = []
for r in F['rows']:
    root = OUT / f"arms/{r['arm']}/attempts/r{r['round']:02}/{r['attempt'].split('.')[-1]}"
    ctx = json.load(open(root / 'context.json'))['body']
    for stage in ('B_INITIAL', 'C_INITIAL', 'B_VOTE', 'C_VOTE'):
        req = run.actor_request(ctx, root, stage)
        text = sum(len(m['content']) if isinstance(m['content'], str) else
                   sum(len(x.get('text', '')) for x in m['content'] if x.get('type') == 'text') for m in req['messages'])
        images = sum(1 for m in req['messages'] if isinstance(m['content'], list) for x in m['content'] if x.get('type') == 'image_url')
        st = json.load(open(sorted(root.glob(f'stages/{stage}*.json'))[-1]))
        st = st.get('body', st)
        calls.append({'arm': r['arm'], 'round': r['round'], 'stage': stage, 'text_chars': text, 'images': images,
                      'prompt_tokens': st['raw_native_result']['receipt']['usage']['prompt_tokens']})
init = [c for c in calls if c['images'] == 0]
rate = sum(c['text_chars'] for c in init) / sum(c['prompt_tokens'] for c in init)
per_image = sorted((c['prompt_tokens'] - c['text_chars'] / rate) / c['images'] for c in calls if c['images'])
res = {'chars_per_token_text_only': rate, 'n_image_calls': len(per_image), 'images_per_call': 4,
       'extra_tokens_per_image': {'min': per_image[0], 'median': per_image[len(per_image) // 2], 'max': per_image[-1]}, 'calls': calls}
json.dump(res, open(HERE / 'img_tokens.json', 'w'), indent=1)
print(res['chars_per_token_text_only'], res['extra_tokens_per_image'])
