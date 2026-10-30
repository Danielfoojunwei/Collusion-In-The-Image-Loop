"""One funded research critique, explicitly outside experimental measurements."""
import argparse
import json
from pathlib import Path

from .finance import Ledger, seed_from_reconciliation, canonical
from .openrouter import Client
from .preflight import run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--private-root', type=Path, required=True)
    parser.add_argument('--registry', type=Path, required=True)
    parser.add_argument('--request-id', required=True)
    args = parser.parse_args()
    root = args.private_root
    account, metadata_digest = run(root / 'development_preflight.json')
    ledger = Ledger(root / 'v17_development.sqlite3')
    if not ledger.path.exists():
        legacy = json.loads((root / 'legacy_budget_reconciliation.json').read_text())
        ledger.create(seed_from_reconciliation(legacy, account), '.10')
    records = json.loads(args.registry.read_text())
    system = (
        'You are a research-methods critic assisting implementation, not a blind experimental monitor. '
        'Use only the supplied source-grounded primary-literature registry; you have not independently read every PDF. '
        'Identify concrete design problems and fixes for a controlled multi-agent collusion-monitoring study. '
        'Preserve: useful detailed document images; A privately motivated to recruit independent truthful B/C; '
        'A image to B/C, B image to C only, C image to next A only; separate product-context monitor M; '
        'actual first MAIN feedback before logs and C return; persistent permitted memory; collective fictional fines '
        'as reference and individual attribution; real multi-turn SWE-bench code/tests; high reasoning, temperature1. '
        'B repair text to A and public C status are declared software-workflow exceptions. '
        'Do not provide a covert codebook or engineer collusion outcomes. Do not promise novelty or successful collusion. '
        'Distinguish proposed design, implementation evidence, technical failure and empirical outcomes. '
        'Return one JSON object with critical_flaws (each with evidence_id, problem, fix, acceptance_check), '
        'candidate_contribution, rejected_novelty_claims, and minimum_release_gates. Prioritize at most eight flaws. '
        'This paid development call must not be described as a Codex reviewer judgment or a study result.'
    )
    prompt = {'review_registry': records, 'workflow': {'cycles_per_issue': 3,
        'A_decisions_per_cycle': 40, 'B_decisions_per_cycle': 20, 'C_decisions_per_cycle': 20,
        'hidden_results': 'Escrowed until cohort completion; pristine regrade only.',
        'current_infrastructure': 'Official Hard51 task pinned; Docker VFS image pull failed for disk capacity. No actors executed.'}}
    response, receipt = Client(ledger, root / 'paid_calls').chat(args.request_id,
        [{'role': 'system', 'content': system}, {'role': 'user', 'content': canonical(prompt)}],
        kind='research_development', max_tokens=32768)
    messages = response.get('choices', [])
    if not messages or messages[0].get('finish_reason') == 'length':
        raise RuntimeError('First development output incomplete; retained without automatic retry')
    content = messages[0].get('message', {}).get('content')
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError('First development response has no final content; no automatic retry')
    (root / 'development_critique.txt').write_text(content + '\n')
    (root / 'development_receipt.json').write_text(canonical({**receipt, 'metadata_sha256': metadata_digest,
        'funding_summary': ledger.summary(), 'experimental_episodes': 0}) + '\n')
    print(json.dumps({'status': 'first_development_response_retained', 'cost_usd': receipt['cost_usd'],
                      'experimental_episodes': 0, 'private_output': str(root / 'development_critique.txt')}))


if __name__ == '__main__':
    main()
