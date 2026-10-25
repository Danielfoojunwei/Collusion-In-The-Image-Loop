"""Chronological per-arm transcripts of every actor's reasoning, declared fields, reviews and settlements."""
import json, glob, sys, textwrap
OUT = sys.argv[1]
F = json.load(open('../../../presentations/v16_report_src/v16_facts.json'))
NR = {}
for f in glob.glob('provider_evidence/native_results/*.json'):
    b = json.load(open(f)); b = b.get('body', b) or {}
    pi = ((b.get('arguments') or {}).get('declared_plan') or {}).get('message_intent')
    if pi: NR[pi] = b

def J(o): return json.dumps(o, ensure_ascii=False, indent=1)
def stage(arm, n, att, st):
    fs = sorted(glob.glob(f'arms/{arm}/attempts/r{n:02}/{att}/stages/{st}*.json'))
    if not fs: return None, None
    b = json.load(open(fs[-1])); return fs[-1], b.get('body', b)

for arm in ('b01', 'b03', 'b05'):
    out = []
    for r in [x for x in F['rows'] if x['arm'] == arm]:
        n, att = r['round'], r['attempt'].split('.')[-1]
        out.append(f"\n\n{'=' * 100}\n##### {arm} ROUND {n} · {r['work_order']} · client-correct {r['client']} · A's private target {r['target']} · tag {r['tag']} · attempt {att}\n{'=' * 100}")
        for st in ('B_INITIAL', 'C_INITIAL'):
            f, b = stage(arm, n, att, st)
            rr = b['raw_native_result']
            out.append(f"\n----- {st} [{f}] status={b.get('status')}\n[REASONING]\n{rr['native_message'].get('reasoning') or ''}\n[RECORDED FIELDS]\n{J(rr['arguments'])}")
        f, b = stage(arm, n, att, 'A_PUBLISH')
        nr = NR.get(b['declared_plan'].get('message_intent'), {})
        out.append(f"\n----- A_PUBLISH [{f}]\n[REASONING]\n{(nr.get('native_message') or {}).get('reasoning') or '(none stored)'}\n[DECLARED PLAN]\n{J(b['declared_plan'])}\n[DELIVERED TEXT SEEN BY B AND C]\n{b['delivered']['public_text']}")
        for st in ('B_PUBLISH',):
            f, b = stage(arm, n, att, st)
            out.append(f"\n----- {st} [{f}]\n[DECLARED PLAN]\n{J(b['declared_plan'])}\n[EXECUTIVE SUMMARY]\n{b['authored_document']['executive_summary']}")
        for st in ('B_VOTE', 'C_VOTE'):
            f, b = stage(arm, n, att, st)
            rr = b['raw_native_result']
            out.append(f"\n----- {st} [{f}] status={b.get('status')}\n[REASONING]\n{rr['native_message'].get('reasoning') or ''}\n[RECORDED FIELDS]\n{J(rr['arguments'])}")
        f, b = stage(arm, n, att, 'MAIN_REVIEW')
        out.append(f"\n----- MAIN REVIEW (sent in full to A, B and C) [{f}]\n{J(b.get('report'))}\n[SETTLEMENT] {J(r['settlement'])}")
        for st in ('A_LOG', 'B_LOG'):
            f, b = stage(arm, n, att, st)
            rr = b['raw_native_result']
            out.append(f"\n----- {st} [{f}] status={b.get('status')}\n[REASONING]\n{rr['native_message'].get('reasoning') or ''}\n[RECORDED FIELDS]\n{J(rr['arguments'])}")
        f, b = stage(arm, n, att, 'C_PUBLISH')
        out.append(f"\n----- C_PUBLISH (return document for next round's A) [{f}]\n[DECLARED PLAN]\n{J(b['declared_plan'])}\n[EXECUTIVE SUMMARY]\n{b['authored_document']['executive_summary']}")
        f, b = stage(arm, n, att, 'RETURN_REVIEW')
        out.append(f"\n----- RETURN REVIEW [{f}]\n{J((b or {}).get('report'))}")
        f, b = stage(arm, n, att, 'C_LOG')
        rr = b['raw_native_result']
        out.append(f"\n----- C_LOG [{f}] status={b.get('status')}\n[REASONING]\n{rr['native_message'].get('reasoning') or ''}\n[RECORDED FIELDS]\n{J(rr['arguments'])}")
    p = f'{OUT}/{arm}_chronology.txt'
    open(p, 'w').write('\n'.join(out))
    print(p, len('\n'.join(out)))
