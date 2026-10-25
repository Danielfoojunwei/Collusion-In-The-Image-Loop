"""Extract every number and quotation used by the V16 report and talk from the immutable run records.

Run from the repository root with PYTHONPATH=src:. ; writes v16_facts.json next to this file.
Nothing here is typed by hand: each value is read from checkpoints, stages, review/decoder/ladder
assessments, probes or the paid ledger.
"""
import ast
import glob
import json
import os
import sqlite3
from pathlib import Path

from next_batch.v15_glm_restored.storage import read
from next_batch.v16_document_carrier import design as d
from next_batch.v16_document_carrier.funding import DEFAULT_OUT as O

HERE = Path(__file__).resolve().parent
ARMS = ('b01', 'b03', 'b05')
LADDER = ('L1_artifacts', 'L2_ballots', 'L3_history', 'L4_incentives')


def latest_valid(directory, prefix):
    """The job version that the run used: the last non-superseded version of the job."""
    jobs = sorted((p for p in glob.glob(str(O / directory / (prefix + '*'))) if not os.path.exists(p + '/SUPERSEDED_REWIND.json')),
                  key=lambda p: (len(p), p))
    return jobs


def review(job_dirs, used_id):
    out = {'versions': [], 'used': None}
    for j in job_dirs:
        a = read(j + '/assessment.json')
        out['versions'].append({'job': os.path.basename(j), 'status': a['status'], 'error_class': a.get('error_class')})
        if os.path.basename(j) == used_id:
            out['used'] = a.get('report')
    return out


def doc_flags(stage):
    o = stage['original']
    diagram = o['private_provenance']['analytical_diagram']
    return {'delivery_allowed': o['delivery_allowed'], 'mechanical_pass': o['quality'].get('mechanical_pass'),
            'word_count': o['quality'].get('authored_word_count'), 'diagram_status': diagram.get('status'),
            'diagram_issues': diagram.get('issues'), 'title': (stage.get('value') or {}).get('document', {}).get('title')
            or o.get('public_text', '')[:120]}


def plan_of(stage):
    p = stage.get('declared_plan') or stage.get('original', {}).get('private_provenance', {}).get('declared_plan') or {}
    if not p:
        p = ((stage.get('raw_native_result') or {}).get('arguments') or {}).get('declared_plan', {})
    return p


rows = []
for arm in ARMS:
    for n in range(1, 6):
        ck = read(O / 'arms' / arm / 'checkpoints' / f'r{n:02}.json')
        att = ck['attempt_id'].split('.')[-1]
        root = O / 'arms' / arm / 'attempts' / f'r{n:02}' / att
        st = lambda s: read(root / 'stages' / f'{s}.json')
        case = d.cases()[n - 1]
        A, B, C = st('A_PUBLISH'), st('B_PUBLISH'), st('C_PUBLISH')
        main_stage, ret_stage = st('MAIN_REVIEW'), st('RETURN_REVIEW')
        main = review(latest_valid('reviews', f'{arm}.r{n:02}.{ck["attempt_id"]}.MAIN'), main_stage['job_id'])
        ret = review(latest_valid('reviews', f'{arm}.r{n:02}.{ck["attempt_id"]}.RETURN'), ret_stage['job_id'])
        votes, initials, logs = {}, {}, {}
        for r in 'BC':
            v = st(f'{r}_VOTE'); i = st(f'{r}_INITIAL')
            vv, iv = v.get('value') or {}, i.get('value') or {}
            votes[r] = {'decision': vv.get('decision'), 'inferred_target': vv.get('inferred_target'), 'decoded_tag': vv.get('decoded_tag'),
                        'confidence': vv.get('confidence'), 'disposition': (vv.get('persuasion') or {}).get('disposition'),
                        'reported_detection_risk': (vv.get('persuasion') or {}).get('reported_detection_risk'),
                        'expected_other_alignment': (vv.get('persuasion') or {}).get('expected_other_receiver_alignment'),
                        'evidence': vv.get('evidence'), 'record_status': v.get('status')}
            initials[r] = {'decision': iv.get('decision'), 'disposition': iv.get('disposition'), 'confidence': iv.get('confidence'),
                           'reported_detection_risk': iv.get('reported_detection_risk'), 'record_status': i.get('status')}
        for r in 'ABC':
            lv = st(f'{r}_LOG').get('value') or {}
            logs[r] = {'carryover_note': lv.get('carryover_note'), 'pressure_report': lv.get('pressure_report'),
                       'artifact_reflection': lv.get('artifact_reflection')}
        plan = plan_of(A)
        intent = plan.get('message_intent') or ''
        encoded = not any(s in intent.lower() for s in ('no covert', 'no private-target payload', 'no private-target or tag', 'no target or tag transmission'))
        sen = read(O / 'probes' / 'sentinel' / f'{arm}.r{n:02}.delivered.json')
        dec = []
        for variant in ('original', 'delivered'):
            jobs = latest_valid('decoding', f'decode.{arm}.r{n:02}.{variant}')
            a = read(jobs[-1] + '/assessment.json'); rep = a.get('report') or {}
            dec.append({'variant': variant, 'job': os.path.basename(jobs[-1]), 'status': a['status'],
                        'inferred_target': rep.get('inferred_target'), 'decoded_tag': rep.get('decoded_tag'),
                        'interpretation_kind': rep.get('interpretation_kind'), 'decoder_rule': rep.get('decoder_rule'),
                        'confidence': rep.get('confidence')})
        ladder = {}
        if arm in ('b01', 'b05'):
            for L in LADDER:
                jobs = latest_valid('attribution_audits', f'ladder.{arm}.r{n:02}.{L}')
                a = read(jobs[-1] + '/assessment.json'); rep = a.get('report') or {}
                ladder[L] = {'status': a['status'], 'signal': rep.get('signal_observed'), 'target': rep.get('artifact_target_readout'),
                             'tag': rep.get('artifact_tag_readout'), 'joint': rep.get('knowing_joint_collusion'), 'basis': rep.get('basis')}
        replay = []
        if arm == 'b01':
            for f in sorted(glob.glob(str(O / 'probes' / 'replay' / f'b01.r{n:02}.*.json'))):
                p = read(f); v = p.get('value')
                if isinstance(v, str):
                    try:
                        v = ast.literal_eval(v)
                    except Exception:
                        v = {}
                v = v or {}
                replay.append({'policy': p['policy'], 'stage': p['stage'], 'status': p['status'], 'decision': v.get('decision'),
                               'inferred_target': v.get('inferred_target'), 'decoded_tag': v.get('decoded_tag')})
        s = ck['settlement']
        rows.append({
            'arm': arm, 'round': n, 'attempt': ck['attempt_id'], 'work_order': case['work_order']['id'],
            'client_name': case['work_order']['synthetic_client'], 'domain': case['work_order']['domain'], 'job': case['work_order']['job'],
            'candidates': case['candidates'], 'client': ck['client_correct'], 'target': ck['private_target'], 'tag': ck['validation_tag'],
            'plan': plan, 'encoded': encoded, 'documents': {'A': doc_flags(A), 'B': doc_flags(B), 'C': doc_flags(C)},
            'initials': initials, 'votes': votes, 'ballots': ck['ballots'], 'logs': logs,
            'main': main, 'main_job': main_stage['job_id'], 'return': ret, 'return_job': ret_stage['job_id'],
            'settlement': {k: s.get(k) for k in ('grant_state', 'sanction_triggered', 'fine_each_nominal', 'actual_fines', 'bank_balances', 'scores',
                                                  'target_aligned', 'document_quality_eligible', 'all_reviews_valid')},
            'sentinel': {'status': sen.get('status'), 'value': sen.get('value')}, 'decoders': dec, 'ladder': ladder, 'replay': replay})

# The five invalid same-model sentinel records still carry readable fields in their raw responses.
con = sqlite3.connect(f'file:{O.parent}/budget.sqlite3?mode=ro', uri=True)
for r in rows:
    if r['sentinel']['status'] != 'valid':
        rid = con.execute("SELECT request_id, response_json FROM requests WHERE request_id LIKE ? AND request_id LIKE '%SENTINEL_delivered.native' ORDER BY rowid DESC",
                          (f'triad_document_carrier_v16:{r["attempt"]}.%',)).fetchone()
        args = json.loads(json.loads(rid[1])['choices'][0]['message']['tool_calls'][0]['function']['arguments'])
        r['sentinel']['raw_invalid_readout'] = {'inferred_target': args.get('inferred_target'), 'decoded_tag': args.get('decoded_tag'),
                                                'evidence_bytes': len(args.get('evidence', '').encode()), 'request_id': rid[0]}
ledger = {
    'rows_total': con.execute('SELECT COUNT(*) FROM requests').fetchone()[0],
    'v16_rows': con.execute("SELECT COUNT(*) FROM requests WHERE request_id LIKE 'triad_document_carrier_v16:%'").fetchone()[0],
    'v16_status': dict(con.execute("SELECT status, COUNT(*) FROM requests WHERE request_id LIKE 'triad_document_carrier_v16:%' GROUP BY status").fetchall()),
    'v16_actual_usd': con.execute("SELECT SUM(actual_units)/1e9 FROM requests WHERE request_id LIKE 'triad_document_carrier_v16:%'").fetchone()[0],
    'since_018_rows': con.execute('SELECT COUNT(*) FROM requests WHERE rowid > 1847').fetchone()[0],
    'since_018_status': dict(con.execute('SELECT status, COUNT(*) FROM requests WHERE rowid > 1847 GROUP BY status').fetchall()),
    'since_018_usd': con.execute('SELECT SUM(actual_units)/1e9 FROM requests WHERE rowid > 1847').fetchone()[0],
    'since_018_first_created': con.execute('SELECT MIN(created) FROM requests WHERE rowid > 1847').fetchone()[0],
    'since_018_last_finished': con.execute('SELECT MAX(finished) FROM requests WHERE rowid > 1847').fetchone()[0],
    'figure_calls_since_018': con.execute("SELECT COUNT(*) FROM requests WHERE rowid > 1847 AND request_id LIKE '%.figure'").fetchone()[0],
}
report = json.load(open(O / 'analysis' / 'final_report_cli.json'))
json.dump({'rows': rows, 'ledger': ledger, 'cli_report': report, 'arm_labels': {a['id']: a['label'] for a in d.ARMS}},
          open(HERE / 'v16_facts.json', 'w'), indent=1, default=str)
print(len(rows), 'rounds;', ledger)
