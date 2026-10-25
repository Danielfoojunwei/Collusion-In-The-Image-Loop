"""Researcher-only chronological reports from immutable v10 source evidence.

No model/API calls, image rewriting, visual classification or main-state writes
exist here. The gallery displays only accepted original main PNGs. Private
interpretations and reflections remain attributed self-reports, never hidden
cognition or proof of successful decoding. This is one dependent trajectory.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import argparse
import html
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import quote

from .engine import (Engine, LOG_STAGE, STAGES,
                     canonical, digest, file_sha, read_record, safe_path)
from .evidence import build_chain_manifest, collect_auxiliary_results


class ReportError(ValueError):
    """Source identity differs; do not silently report altered evidence."""


LIMITATIONS = [
    "One dependent 12-round trajectory; rounds and B/C choices are not independent episodes.",
    "A's binding ballot is exogenously assigned to T, not an autonomous model decision. Raw A proposals are separate observations.",
    "B/C interpretations and actor reflections are attributed, fallible self-reports, not faithful hidden cognition or causal decoding proof.",
    "Target matching, unusual imagery, intent, and reviewer suspicion are distinct; none alone establishes a covert shared codebook.",
    "Main reviewer findings are actual attributed judgments, not verified detector accuracy or researcher ground truth.",
    "No visual meaning is classified automatically. Renderer fidelity, factuality, utility and mechanism claims require actual auxiliary assessments and controls.",
    "Whole-image controls cannot isolate a particular visual feature; shared review text can relay earlier content indirectly.",
    "No real money is paid to agents; fictional points and actual API expenses are different quantities.",
]


def _require(condition, message):
    if not condition:
        raise ReportError(message)


def _plain(path):
    return json.loads(safe_path(path).read_text())


def _counter(values):
    return dict(sorted(Counter(values).items()))


def _metric(values):
    """Keep absent values out of numerator and explicitly count missingness."""
    values = list(values)
    observed = [x for x in values if type(x) is bool]
    return {"true": sum(x is True for x in observed), "false": sum(x is False for x in observed),
            "observed": len(observed), "missing": len(values)-len(observed), "slots": len(values)}


def _technical_attempts(engine, committed):
    result = []
    selected = {x["round"]: x["attempt"] for x in committed}
    for path in sorted((engine.out/"attempts").glob("r*/a*/attempt.json")):
        identity = read_record(path)
        root = path.parent
        abandonment = root/"abandonment.json"
        chosen = selected.get(identity["round"]) == identity["attempt"]
        row = {"round": identity["round"], "attempt": identity["attempt"],
               "attempt_id": identity["attempt_id"], "selected_main_attempt": chosen,
               "source": str(path), "source_sha256": file_sha(path)}
        if abandonment.exists():
            fault = read_record(abandonment)
            _require(not chosen, "A selected main attempt cannot be abandoned")
            row.update(status="abandoned_technical", fault_code=fault.get("fault", {}).get("code"),
                       abandonment_source=str(abandonment), abandonment_sha256=file_sha(abandonment),
                       behavioral_evidence=False, costs_still_counted=True)
        else:
            row.update(status="committed" if chosen else "uncommitted",
                       behavioral_evidence=chosen)
        result.append(row)
    return result


def _auxiliary(engine):
    """Canonical collector owns source validation; missing jobs stay pending."""
    kinds = ("renderer_fidelity", "artifact_factuality_usefulness", "observer")
    if len(engine._commits()) != 12:
        return {"status": "pending", "automatic_visual_judgments": False,
                "reason": "Auxiliary work starts only after all twelve main rounds commit",
                "by_kind": {kind: {"status": "pending", "assessments": []} for kind in kinds}}
    actual = collect_auxiliary_results(engine)
    grouped = {kind: [] for kind in kinds}
    for row in actual["results"]:
        grouped.setdefault(row["kind"], []).append(deepcopy(row))
    return {"status": "available" if actual["valid_actual_report_count"] else "pending",
            "automatic_visual_judgments": False,
            "by_kind": {kind: {"status": "available" if any(x["status"] == "valid" for x in rows) else "pending",
                               "assessments": rows} for kind, rows in grouped.items()},
            "actual_collector_result": actual}


def _controls(engine):
    """Optional controls module owns validation and computation, never guessing."""
    try:
        from . import controls
    except ImportError:
        return {"status": "pending", "reason": "Controls implementation or actual results not available", "results": None}
    reader = getattr(controls, "report_summary", None)
    if reader is None:
        return {"status": "pending", "reason": "Controls reporting contract not bound", "results": None}
    summary = reader(engine)
    if summary.get("status") == "available":
        # Canonical reader has just verified these exact immutable branch hashes.
        branches = []
        for binding in summary["branch_sources"]:
            path = safe_path(binding["path"], engine.out)
            _require(file_sha(path) == binding["sha256"], "Control report source differs")
            branch = read_record(path)
            branches.append({"round": branch["round"], "family": branch["family"],
                             "attempt_id": branch["attempt_id"], "result": deepcopy(branch["result"]),
                             "source": str(path), "sha256": binding["sha256"]})
        summary["actual_branch_results"] = branches
    return summary


def build_report(engine, *, require_complete=True):
    """Read verified main sources; return JSON-ready research data, without writes."""
    commits = engine._commits()
    _require(not require_complete or len(commits) == 12, "All twelve main rounds must be committed")
    chain = build_chain_manifest(engine, require_complete=require_complete) if commits else {
        "chains": [], "source_files_sha256": {}, "summary": {}, "evidence_origin":
        engine.config.get("evidence_origin", "offline_fixture" if engine.config.get("offline_fixture") else "not_declared")}
    rounds = []
    for item in chain["chains"]:
        row = deepcopy(item)
        root = engine.out/"attempts"/f"r{item['round']:02}"/f"a{item['committed_attempt']:02}"
        components, notes, raw_proposals = {}, {}, {}
        for stage in STAGES:
            if stage == "REVIEWER":
                continue
            result = read_record(root/"outcomes"/(stage+".json"))["result"]
            components[stage] = {"status": result["status"], "components": deepcopy(result["component_status"])}
            if stage in LOG_STAGE.values():
                notes[stage] = {"carryover_note": result["output"]["carryover_note"],
                                "image_reflection": result["output"]["image_reflection"],
                                "status": result["component_status"]["log"],
                                "is_fallible_self_report": True}
            if stage in ("A_PROPOSAL", "B_VOTE", "C_VOTE"):
                raw_proposals[stage] = deepcopy(result["output"]["raw_proposal"])
        review = item["actual_main_review"]
        actual = review["reported_output"] if review["status"] == "valid" else None
        row.update(status="committed", stage_component_status=components, own_postreview_logs=notes,
                   raw_native_vote_proposals=raw_proposals,
                   review_axes={"status": review["status"],
                                "task_assessment": deepcopy(actual["task_assessment"]) if actual else None,
                                "unilateral_attempts": deepcopy(actual["unilateral_attempts"]) if actual else None,
                                "joint_assessment": deepcopy(actual["joint_assessment"]) if actual else None,
                                "verdict": actual["verdict"] if actual else None},
                   model_invalid_or_refused_components=[{"stage": stage, "component": key, "status": status}
                       for stage, value in components.items() for key, status in value["components"].items()
                       if status in {"model_invalid", "refused", "declined"}])
        rounds.append(row)
    committed_numbers = {x["round"] for x in rounds}
    for case in engine.plan:
        if case["round"] not in committed_numbers:
            rounds.append({"round": case["round"], "status": "not_committed",
                           "private_target_researcher_only": case["private_target"], "oracle": case["oracle"],
                           "choices": None, "images": None, "actual_main_review": None, "scores": None})
    rounds.sort(key=lambda x: x["round"])
    selected = [x for x in rounds if x["status"] == "committed"]
    bc = [row["choices"][role] for row in selected for role in "BC"]
    inf = [row["recipient_reported_interpretations"][role] for row in selected for role in "BC"]
    actual_reviews = [r["actual_main_review"]["reported_output"] for r in selected
                      if r["actual_main_review"]["status"] == "valid"]
    deductions = [r["scores"]["scores"][role]["joint_integrity_deduction"] for r in selected for role in "ABC"]
    totals = {role: {field: {"known_sum": sum(value for row in selected
                    if (value := row["scores"]["scores"][role][field]) is not None),
                    "missing_rounds": sum(row["scores"]["scores"][role][field] is None for row in selected)}
                    for field in ("policy_points", "shared_bonus", "joint_integrity_deduction", "net_points")}
              for role in "ABC"}
    attempts = _technical_attempts(engine, commits)
    summary = {"planned_rounds": 12, "committed_rounds": len(selected), "dependent_episodes": 1,
        "planned_assigned_A_ballots": 12, "assigned_A_ballots_observed": len(selected),
        "planned_free_BC_slots": 24, "committed_free_BC_slots": len(bc),
        "valid_free_BC_choices": sum(x["status"] == "valid" for x in bc),
        "B_C_target_choice": _metric(x["matches_private_target"] for x in bc),
        "B_C_policy_correct_choice": _metric(x["policy_correct"] for x in bc),
        "reported_target_interpretation_matches": _metric(x["reported_target_matches_T"] for x in inf),
        "valid_interpretations": sum(x["status"] == "valid" for x in inf),
        "raw_A_proposal_matches_assignment": _metric(row["choices"]["A"]["proposal_matches_assignment"] for row in selected),
        "raw_A_proposal_validity": _counter(row["choices"]["A"]["proposal_validity"] for row in selected),
        "unanimous_assigned_target": _metric(row["scores"]["unanimous_target"] for row in selected),
        "valid_main_reviews": len(actual_reviews),
        "main_reviewer_verdicts": _counter(x["verdict"] for x in actual_reviews),
        "main_reviewer_joint_levels": _counter(x["joint_assessment"]["level"] for x in actual_reviews),
        "main_reviewer_unilateral_attempt_records": sum(len(x["unilateral_attempts"]) for x in actual_reviews),
        "qualifying_joint_finding": _metric(row["scores"]["review_assessment"]["qualifies_joint_fine"] for row in selected),
        "actual_deductions_fictional_points": {"known_sum": sum(x for x in deductions if x is not None),
                                              "missing_agent_rounds": sum(x is None for x in deductions)},
        "fictional_score_totals_by_role": totals,
        "selected_main_images": sum(row["images"][role]["selected_pixels"] is not None for row in selected for role in "ABC"),
        "model_invalid_refused_or_declined_components": sum(len(x["model_invalid_or_refused_components"]) for x in selected),
        "abandoned_technical_attempts": sum(x["status"] == "abandoned_technical" for x in attempts),
        "not_committed_rounds": 12-len(selected)}
    return {"report_revision": "v10_researcher_chronology_v1", "researcher_only": True,
            "never_feed_to_agents_or_main_reviewer": True, "evidence_origin": chain["evidence_origin"],
            "summary": summary, "rounds": rounds, "attempt_inventory": attempts,
            "source_files_sha256": chain["source_files_sha256"], "delivery_checks": chain["summary"],
            "auxiliary_assessments": _auxiliary(engine), "paired_controls": _controls(engine),
            "limitations": LIMITATIONS}


def _text(value):
    return "missing" if value is None else str(value)


def _relative(source, target):
    return quote(os.path.relpath(source, target), safe="/.")


def _fenced(value, language="text"):
    """A model-produced delimiter cannot escape its quoted source block."""
    value = str(value)
    fence = "`" * max(3, max((len(x) for x in re.findall(r"`+", value)), default=0)+1)
    return f"{fence}{language}\n{value}\n{fence}"


def _markdown(report, directory):
    s = report["summary"]
    lines = ["# V10 researcher-only chronological evidence", "", f"Evidence origin: **{report['evidence_origin']}**. "
             f"Committed rounds: **{s['committed_rounds']}/12**; one dependent episode. "
             f"Free B/C target choices: **{s['B_C_target_choice']['true']}/{s['B_C_target_choice']['observed']} observed** "
             f"({s['B_C_target_choice']['missing']} missing).", "",
             "A's binding ballot is assigned to T; raw A proposals below remain separate. "
             "Reviewer findings and private interpretations are attributed judgments, not established decoding mechanisms.", "",
             "| Round | Target / oracle | Assigned A / raw A proposal | B inference → choice | C inference → choice | Main reviewer joint finding | Earned bonus / actual withholding each |",
             "|---|---|---|---|---|---|---|"]
    for row in report["rounds"]:
        n = row["round"]
        if row["status"] != "committed":
            lines.append(f"| {n} | {row['private_target_researcher_only']} / {row['oracle']} | Not committed | — | — | Pending | — |")
            continue
        a = row["choices"]["A"]
        def bc(role):
            inf = row["recipient_reported_interpretations"][role]["self_report"]
            return f"{inf.get('inferred_target', 'missing') if inf else 'missing'} → {_text(row['choices'][role]['decision'])}"
        joint = row["review_axes"]["joint_assessment"]
        score = row["scores"]["scores"]["A"]
        lines.append(f"| {n} | {row['private_target_researcher_only']} / {row['oracle']} | {a['binding_assigned_decision']} / {_text(a['model_proposed_decision'])} | {bc('B')} | {bc('C')} | {joint['level'] if joint else 'unavailable'} | {_text(score['shared_bonus'])} / {_text(score['joint_integrity_deduction'])} |")
    for row in report["rounds"]:
        lines += ["", f"## Round {row['round']}", ""]
        if row["status"] != "committed":
            lines.append("No committed main-round evidence. No behavioral or visual result is imputed.")
            continue
        lines.append(f"Selected main attempt: **{row['committed_attempt']}**. Target {row['private_target_researcher_only']}; task-correct {row['oracle']}.")
        lines += ["", "Order: A image → raw A proposal → B image → B choice → C choice → reviewer → A log → B log → C image → C log."]
        for role in "ABC":
            if role == "C":
                lines += ["", "Actual first main reviewer report (unchanged; delivered before logs and C's image):", "",
                          _fenced(row["actual_main_review"]["raw_text"], "json")]
                for prior in "AB":
                    lines += ["", f"{prior}'s post-review reflection (fallible self-report):", "",
                              _fenced(row["images"][prior]["own_postreview_reflection"] or "[missing]")]
            image = row["images"][role]
            descriptor = image["selected_pixels"]
            route = {"A": "to B and C before their choices", "B": "to C only before C's choice", "C": "post-review to next A only; archive-only at round 12"}[role]
            lines += ["", f"**{role} image — {route}.**"]
            if descriptor:
                lines += ["", f"![Round {row['round']} {role} selected original PNG]({_relative(descriptor['path'], directory)})",
                          "", f"SHA256: `{descriptor['sha256']}`."]
            else:
                lines.append(f"No selected delivered image: {image['status']}.")
            if role == "C":
                lines += ["", "C's post-image reflection (fallible self-report):", "",
                          _fenced(image["own_postreview_reflection"] or "[missing]")]
    lines += ["", "## Auxiliary evidence and limits", "", "Renderer/factuality/usefulness judgments: "
              f"{report['auxiliary_assessments']['status']}. Paired controls: {report['paired_controls'].get('status', 'unknown')}. "
              "Read summary.json for source-bound actual assessments. No missing assessment is converted into a negative result.", ""]
    lines += [f"- {x}" for x in report["limitations"]]
    return "\n".join(lines)+"\n"


def _gallery(report, directory):
    esc = lambda value: html.escape(str(value), quote=True)
    s = report["summary"]
    parts = ['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
             '<title>V10 actual chronological evidence</title><style>body{font:16px system-ui;max-width:1350px;margin:auto;padding:24px;background:#f7f7fa;color:#1163842}section{background:white;border:1px solid #ddd;border-radius:10px;padding:20px;margin:24px 0}.images{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:16px}figure{margin:0}img{width:100%;height:auto;border:1px solid #ddd}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f3f6;padding:12px}small{overflow-wrap:anywhere}summary{cursor:pointer}dt{font-weight:700}dd{margin:0 0 10px}</style>',
             '<h1>V10: actual selected images and first reviewer reports</h1>',
             f'<p><strong>Researcher-only; origin: {esc(report["evidence_origin"])}.</strong> '
             f'{s["committed_rounds"]}/12 committed rounds in one dependent episode. '
             f'B/C target choices: {s["B_C_target_choice"]["true"]}/{s["B_C_target_choice"]["observed"]} observed; '
             f'{s["B_C_target_choice"]["missing"]} missing. A is assigned to T.</p>',
             '<p>Images below link to the exact original accepted PNG bytes. C images follow the review and reach next A only. '
             'Reports and reflections remain attributed judgments; this page performs no visual inference.</p>',
             '<nav>'+' · '.join(f'<a href="#r{x["round"]:02}">Round {x["round"]}</a>' for x in report["rounds"])+'</nav>']
    for row in report["rounds"]:
        parts.append(f'<section id="r{row["round"]:02}"><h2>Round {row["round"]}</h2>')
        if row["status"] != "committed":
            parts.append('<p>No committed evidence. Pending values remain unavailable.</p></section>')
            continue
        parts.append(f'<p>Selected attempt {row["committed_attempt"]}; private target <strong>{esc(row["private_target_researcher_only"])}</strong>; task oracle <strong>{esc(row["oracle"])}</strong>.</p><dl>')
        parts.append('<dt>Assigned A and raw model proposal</dt><dd>'+esc(canonical(row['choices']['A']))+'</dd>')
        for role in 'BC':
            parts.append(f'<dt>{role}: pre-feedback interpretation versus actual choice</dt><dd>'+esc(canonical({
                'interpretation':row['recipient_reported_interpretations'][role], 'choice':row['choices'][role]}))+'</dd>')
        parts.append('</dl><div class="images">')
        for role in 'ABC':
            if role == 'C':
                parts.append('</div><h3>Actual first main reviewer report</h3><p>Delivered to all three before their logs and C’s image.</p><pre>'+esc(row['actual_main_review']['raw_text'])+'</pre>')
                for prior in 'AB':
                    parts.append('<details><summary>'+prior+' post-review reflection (fallible self-report)</summary><pre>'+esc(row['images'][prior]['own_postreview_reflection'] or '[missing]')+'</pre></details>')
                parts.append('<h3>Post-review C image for next A</h3><div class="images">')
            image = row['images'][role]; descriptor = image['selected_pixels']
            route = {'A':'A → B and C, before choices','B':'B → C only, before C choice','C':'C → next A only, after review; C12 archive-only'}[role]
            parts.append('<figure><figcaption><strong>'+esc(route)+'</strong></figcaption>')
            if descriptor:
                url = _relative(descriptor['path'], directory)
                parts.append(f'<a href="{esc(url)}"><img loading="lazy" src="{esc(url)}" alt="Round {row["round"]} {role} original selected image"></a><small>SHA256: {esc(descriptor["sha256"])}</small>')
            else:
                parts.append('<p>No selected image: '+esc(image['status'])+'</p>')
            parts.append('<details><summary>Executed briefs and actual drafts</summary><pre>'+esc(json.dumps(image['generated_drafts'],ensure_ascii=False,indent=2))+'</pre></details>')
            if role == 'C':
                parts.append('<details><summary>C post-image reflection (fallible self-report)</summary><pre>'+esc(image['own_postreview_reflection'] or '[missing]')+'</pre></details>')
            parts.append('</figure>')
        parts.append('</div>')
        parts.append('<details><summary>Fictional scores, component status and private logs</summary><pre>'+esc(json.dumps({
            'scores':row['scores'],'components':row['stage_component_status'],'logs':row['own_postreview_logs']},ensure_ascii=False,indent=2))+'</pre></details></section>')
    parts.append('<section><h2>Auxiliary evidence</h2><p>Only actually recorded auxiliary assessments are included. Pending assessments imply no finding.</p><pre>'+esc(json.dumps({
        'auxiliary_assessments':report['auxiliary_assessments'],'paired_controls':report['paired_controls']},ensure_ascii=False,indent=2))+'</pre></section>')
    parts.append('<section><h2>Interpretation limits</h2><ul>'+''.join('<li>'+esc(x)+'</li>' for x in report['limitations'])+'</ul></section></html>')
    return '\n'.join(parts)+'\n'


def _write_derived(path, raw):
    """Atomic replace allowed only for derived report artifacts, never sources."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.report-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_report(engine, target=None, *, require_complete=True):
    """Regenerable derived report; no actor/reviewer/checkpoint inputs are edited."""
    target = safe_path(target or engine.out/'analysis'/'chronological_report', engine.out)
    _require(target.is_relative_to(engine.out/'analysis'), 'Reports must remain under the derived analysis directory')
    report = build_report(engine, require_complete=require_complete)
    files = {'summary.json':(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode(),
             'chronological.md':_markdown(report,target).encode(), 'gallery.html':_gallery(report,target).encode()}
    for name, raw in files.items():
        _write_derived(target/name, raw)
    manifest = {'report_revision':report['report_revision'],'researcher_only':True,
                'report_content_sha256':digest(report), 'files_sha256':{name:file_sha(target/name) for name in files},
                'main_source_files_sha256':report['source_files_sha256'],
                'not_an_actor_input':True}
    _write_derived(target/'report_manifest.json', (canonical(manifest)+'\n').encode())
    return {'directory':str(target), 'summary':report['summary'],
            'files':{name:str(target/name) for name in (*files,'report_manifest.json')}}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('out', type=Path)
    parser.add_argument('--allow-incomplete', action='store_true')
    parser.add_argument('--target', type=Path)
    args=parser.parse_args(argv)
    out=safe_path(args.out)
    _require((out/'engine_contract.json').is_file(), 'An existing engine contract is required; reporting cannot initialize a run')
    config,plan=_plain(out/'config.json'),_plain(out/'plan.json')
    contract=read_record(out/'engine_contract.json')
    cases=plan['rounds'] if isinstance(plan,dict) else plan
    _require(contract['config_sha256']==digest(config) and contract['plan_sha256']==digest(cases), 'Run config/plan binding differs')
    engine=Engine(out,config,plan)
    print(json.dumps(write_report(engine,args.target,require_complete=not args.allow_incomplete),ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
