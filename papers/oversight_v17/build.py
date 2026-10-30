#!/usr/bin/env python3
"""Offline manuscript build and consistency audit. Never imports experiment code."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

import fitz

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def check_design(plan, source):
    design = plan['design']
    counts = plan['planning_counts']
    multi = design['multi_turn']
    core = (len(design['study1']['cells']) + len(design['study3']['policies'])) * design['cohorts_per_cell_planning'] * design['rounds_per_cohort']
    actor = multi['cycles_per_issue_max'] * sum(multi['actor_model_decisions_per_cycle'].values()) + multi['overhead_actor_calls']
    checks = {
        'paid_execution_disabled': design['paid_execution_enabled'] is False,
        'swe_contract_bound_to_proposal': 'SWE_BENCH_SETUP.md' in design['task_contract'],
        'no_fabricated_instance_ids': design['benchmark']['instance_ids'] == [],
        'hidden_references_not_visible': design['benchmark']['gold_and_test_patch_actor_visible'] is False,
        'planning_core_count': core == counts['core_issue_episodes'] == 1600,
        'planning_actor_decisions': actor == multi['total_actor_decisions_per_issue_upper'] == 246,
        'core_actor_arithmetic': core * actor == counts['core_actor_decisions_upper'] == 393600,
        'core_review_arithmetic': core * multi['monitor_reviews_per_issue_upper'] == counts['core_live_monitor_reviews_upper'] == 6400,
        'core_image_arithmetic': core * multi['figure_generations_per_document_issue_upper'] == counts['core_image_calls_upper'] == 11200,
        'no_unpriced_cost_claim': counts['current_total_cost_usd'] is None,
        'research_protocol_status': 'No new SWE-bench experiments' in source,
        'no_placeholder_results': not re.search(r'\b(?:INSERT RESULTS|TODO RESULTS|RESULTS HERE)\b', source),
        'explicit_participation_distinction': 'not automatically knowing collusion' in source,
        'delayed_hidden_outcomes': 'escrowed until cohort completion' in source,
        'four_research_questions': all('\\RQ{' + str(i) + '}' in source for i in range(1, 5)),
        'planning_numbers_in_source': all(x in source for x in ['393,600', '11,200', '6,400', '960']),
    }
    bad = [key for key, value in checks.items() if not value]
    if bad:
        raise ValueError('Manuscript/design mismatch: ' + ', '.join(bad))
    return checks


def build():
    plan_path = ROOT / 'research_plans/oversight_v17/STUDY_PLAN.json'
    tex = HERE / 'manuscript.tex'
    source = tex.read_text()
    checks = check_design(json.loads(plan_path.read_text()), source)
    if shutil.which('pdflatex') is None:
        raise RuntimeError('pdflatex and the documented TeX packages are required')
    for _ in range(2):
        run = subprocess.run(['pdflatex', '-interaction=nonstopmode', '-halt-on-error', tex.name],
                             cwd=HERE, text=True, capture_output=True, timeout=120)
        if run.returncode:
            raise RuntimeError(run.stdout[-5000:] + run.stderr[-1000:])
    log = (HERE / 'manuscript.log').read_text()
    if re.search(r'Overfull \\[hv]box|undefined references|Citation .* undefined|Reference .* undefined', log):
        raise RuntimeError('Layout or reference issue remains; inspect manuscript.log')
    pdf = HERE / 'manuscript.pdf'
    with fitz.open(pdf) as document:
        texts = [page.get_text() for page in document]
        checks['every_page_has_status_footer'] = all('Prospective design; no new empirical results' in text for text in texts)
        checks['no_blank_pages'] = all(len(text.strip()) > 100 for text in texts)
        all_text = '\n'.join(texts)
        checks['four_studies_rendered'] = all('Study ' + str(i) + ':' in all_text for i in range(1, 5))
        checks['references_rendered'] = 'SWE-Bench Pro V2: A Cleaner' in all_text
        # Text block bounds detect content protruding outside a PDF page. Inline
        # math may have special glyph boxes but still stays inside page bounds.
        checks['text_blocks_inside_pages'] = all(
            block[0] >= 0 and block[1] >= 0 and block[2] <= page.rect.width + .5 and block[3] <= page.rect.height + .5
            for page in document for block in page.get_text('blocks'))
        pages = len(document)
    if not all(checks.values()):
        raise RuntimeError('Rendered PDF validation failed: ' + repr(checks))
    (HERE / 'manuscript.txt').write_text(all_text)
    record = {
        'status': 'passed', 'artifact_type': 'prospective_research_protocol',
        'date': '2026-10-30', 'pages': pages, 'checks': checks,
        'manuscript_source_sha256': hashlib.sha256(tex.read_bytes()).hexdigest(),
        'study_plan_sha256': hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        'pdf_sha256': hashlib.sha256(pdf.read_bytes()).hexdigest(),
        'experimental_instances_executed': 0, 'new_paid_inference_calls': 0,
        'scope': 'Writing artifact, arithmetic, references and layout only; not harness validation.',
    }
    (HERE / 'BUILD_VALIDATION.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({'status': 'passed', 'pages': pages, 'checks': len(checks), 'pdf': str(pdf), 'experimental_instances_executed': 0}, indent=2))


if __name__ == '__main__':
    build()
