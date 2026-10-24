"""Render a portable, read-only gallery from actual first-run triad outcomes.

Only analysis/FIRST_RUN_GALLERY.html is written. PNGs are linked byte-for-byte,
never edited/copied, and all observations are projected from retained outcomes.
This script uses only the standard library and performs no network/model calls.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "runs/main/triad_consensus_live"
STAGES = ("A_IMAGE_AND_VOTE", "B_MESSAGE_AND_VOTE", "C_VOTE", "REVIEWER",
          "POSTVERDICT_A_LOG", "POSTVERDICT_B_LOG", "POSTVERDICT_C_IMAGE_LOG")
VOTE_STAGES = {"A": "A_IMAGE_AND_VOTE", "B": "B_MESSAGE_AND_VOTE", "C": "C_VOTE"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def build_projection(out, case_id="case_05"):
    out = Path(out).resolve()
    case = out / "cases" / case_id
    destination = out / "analysis" / "FIRST_RUN_GALLERY.html"
    sources, image_sources = {}, {}

    def read(path, optional=False):
        if not path.exists():
            if optional:
                return None
            raise FileNotFoundError(path)
        if path.is_symlink():
            raise ValueError("Gallery sources cannot be symlinks")
        data = path.read_bytes()
        try:
            value = json.loads(data)
        except json.JSONDecodeError:
            if optional:
                return None  # A live producer may still be writing this stage.
            raise
        sources[str(path.relative_to(out))] = sha(data)
        return value

    def image_descriptor(outcome):
        if not outcome or outcome.get("status") != "valid":
            return None
        descriptor = (outcome.get("output") or {}).get("image")
        if descriptor is None:
            return None
        path = Path(descriptor["path"])
        if not path.is_absolute():
            path = case / "memory" / path
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError("Gallery images cannot follow symlinks")
        path = path.resolve()
        if not path.is_relative_to(out):
            raise ValueError("An image escaped the portable run archive")
        raw = path.read_bytes()
        if sha(raw) != descriptor["sha256"] or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Exact original PNG integrity check failed")
        image_sources[str(path.relative_to(out))] = sha(raw)
        return {"src": quote(os.path.relpath(path, destination.parent), safe="/"),
                "sha256": sha(raw), "path": str(path.relative_to(out))}

    manifest = read(case / "engine_manifest.json")
    task = manifest["case"]["task"]
    facts, policy = task["facts"], task["facts"]["policy"]
    if policy["selection"] != "lowest_energy_then_id":
        raise ValueError("This gallery requires the declared lowest-energy-then-ID policy")
    candidates = []
    for original in facts["candidates"]:
        item = dict(original)
        item["eligible"] = item["quality"] >= policy["min_quality"] and item["defect_rate"] <= policy["max_defect_rate"]
        candidates.append(item)
    eligible = [item for item in candidates if item["eligible"]]
    correct = min(eligible, key=lambda item: (item["energy"], item["id"]))["id"] if eligible else "reject_all"
    all_outcomes = {}
    for number in range(1, 13):
        all_outcomes[number] = {stage: read(case / "rounds" / f"r{number:02d}" / (stage + ".outcome.json"), optional=True)
                                for stage in STAGES}
    rounds = []
    for number, outcomes in all_outcomes.items():
        ballots = {}
        for role, stage in VOTE_STAGES.items():
            outcome = outcomes[stage]
            valid = outcome is not None and outcome.get("status") == "valid"
            ballots[role] = {"available": valid, "decision": outcome["output"]["decision"] if valid else None,
                             "rationale": outcome["output"]["rationale"] if valid else None,
                             "stage_status": outcome["status"] if outcome else "pending"}
        valid_votes = all(ballot["available"] for ballot in ballots.values())
        unanimous = valid_votes and len({ballot["decision"] for ballot in ballots.values()}) == 1
        consensus = {"status": "unanimous" if unanimous else ("disagreement" if valid_votes else "incomplete"),
                     "decision": ballots["A"]["decision"] if unanimous else None}
        review_outcome = outcomes["REVIEWER"]
        review = review_outcome["output"] if review_outcome and review_outcome.get("status") == "valid" else None
        b = outcomes["B_MESSAGE_AND_VOTE"]
        previous_c = all_outcomes[number-1]["POSTVERDICT_C_IMAGE_LOG"] if number > 1 else None
        rounds.append({"round": number, "finished": all(value is not None for value in outcomes.values()),
                       "started": any(value is not None for value in outcomes.values()),
                       "stage_statuses": {stage: value["status"] if value else "pending" for stage,value in outcomes.items()},
                       "ballots": ballots, "consensus": consensus, "review": review,
                       "B_message": b["output"]["message"] if b and b.get("status") == "valid" else None,
                       "A_image": image_descriptor(outcomes["A_IMAGE_AND_VOTE"]),
                       "prior_C_image": image_descriptor(previous_c),
                       "next_C_image": image_descriptor(outcomes["POSTVERDICT_C_IMAGE_LOG"])})
    supplement_path = out / "execution_notes/format_v4/round01_C_technical_supplement.json"
    supplement = read(supplement_path, optional=True)
    historical = None
    if supplement:
        parsed = supplement["parsed_original_value"]
        historical = {"round": 1, "explicit_decision": parsed.get("decision"),
                      "full_original_rationale": parsed.get("rationale"),
                      "syntax_flags": supplement.get("syntax_flags", []),
                      "original_result_hash": supplement.get("original_result_hash"),
                      "source": quote(os.path.relpath(supplement_path, destination.parent), safe="/")}
    projection = {"title": "First triad run", "case_id": case_id,
                  "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                  "rounds_planned": 12, "rounds_finished": sum(item["finished"] for item in rounds),
                  "rounds_reviewed": sum(item["review"] is not None for item in rounds),
                  "provisional": not all(item["finished"] for item in rounds),
                  "task_brief": task["brief"], "policy": policy, "candidates": candidates,
                  "authoritative_correct_decision": correct, "rounds": rounds,
                  "round1_C_posthoc_supplement": historical,
                  "source_file_sha256": sources, "original_image_sha256": image_sources}
    # Check projection values against the exact loaded source objects.
    for item in rounds:
        for role, stage in VOTE_STAGES.items():
            source = all_outcomes[item["round"]][stage]
            if source and source["status"] == "valid":
                assert item["ballots"][role]["decision"] == source["output"]["decision"]
        if item["review"] is not None:
            assert item["review"] == all_outcomes[item["round"]]["REVIEWER"]["output"]
    return projection, destination


TEMPLATE = r'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>First triad run — image and decision gallery</title>
<style>
:root{color-scheme:light;--ink:#172b35;--muted:#52656e;--paper:#f5f7f7;--line:#d6e0e3;--accent:#10676a;--soft:#e7f3f1;--warn:#744d14}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}main{max-width:1360px;margin:auto;padding:32px 24px 48px}h1{font-size:clamp(25px,4vw,39px);line-height:1.15;margin:8px 0 14px}h2{font-size:21px;margin:0 0 14px}h3{font-size:16px;margin:0 0 6px}p{margin:8px 0}.eyebrow{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--accent);font-weight:750}.muted{color:var(--muted)}.notice{padding:12px 16px;background:#fff9eb;border:1px solid #ead9b7;border-radius:10px;margin:18px 0}.route{font-size:14px;border-left:3px solid var(--accent);padding:6px 14px;margin:20px 0}.toolbar{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin:22px 0}.rounds{display:flex;gap:7px;flex-wrap:wrap}.rounds button{width:43px;height:42px;border:1px solid var(--line);border-radius:8px;background:white;color:var(--ink);font:inherit;cursor:pointer}.rounds button.pending{color:#7a8a91;background:transparent}.rounds button[aria-pressed=true]{background:var(--accent);border-color:var(--accent);color:white}.rounds button:focus-visible,a:focus-visible,summary:focus-visible{outline:3px solid #d29438;outline-offset:3px}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.card,.panel{background:white;border:1px solid var(--line);border-radius:12px;overflow:hidden}.image-header{padding:16px 16px 10px;min-height:94px}.image-header p{font-size:13px;color:var(--muted)}.image-area{display:flex;align-items:center;justify-content:center;background:#eef2f3;aspect-ratio:1;max-height:520px}.image-area img{display:block;width:100%;height:100%;object-fit:contain}.empty{padding:24px;text-align:center;color:var(--muted);font-size:14px}.image-footer{padding:10px 16px;font-size:12px;color:var(--muted);word-break:break-all}.panel{padding:20px;margin-top:18px}.summary{display:flex;gap:10px;flex-wrap:wrap}.pill{display:inline-block;border-radius:7px;padding:7px 12px;background:#eef3f4;font-size:14px}.pill.strong{background:var(--soft);color:var(--accent);font-weight:700}.votes{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:16px 0}.vote{padding:12px;border:1px solid var(--line);border-radius:9px}.vote b{display:block;font-size:24px}.vote span{font-size:13px;color:var(--muted)}details{border-top:1px solid var(--line);padding:13px 0 0;margin-top:13px}summary{cursor:pointer;font-weight:650}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.55 system-ui,sans-serif;background:#f6f8f8;padding:14px;border-radius:8px}table{width:100%;border-collapse:collapse;margin:12px 0;font-size:14px}th,td{text-align:left;padding:9px;border-bottom:1px solid var(--line)}tr.correct{background:var(--soft)}.policy-grid{display:grid;grid-template-columns:1fr 1fr;gap:22px}.small{font-size:13px}a{color:var(--accent)}footer{font-size:12px;color:var(--muted);margin-top:24px}#posthoc{background:#fff9eb;border-color:#ead9b7}ul{padding-left:21px}.rca h3{margin-top:16px}@media(max-width:800px){main{padding:22px 14px}.grid{grid-template-columns:1fr}.image-header{min-height:auto}.image-area{max-height:650px}.policy-grid{grid-template-columns:1fr}.votes{gap:6px}.vote{padding:9px}}
.image-area>a{display:block;width:100%;height:100%}
</style>
<main><header><div class="eyebrow">Actual live observations · separate independent reviewer</div><h1>Images, votes and reviewer feedback</h1><p id="subtitle" class="muted"></p><div id="notice" class="notice"></div></header>
<p class="route"><strong>Each round:</strong> A image → B and C; B message → C; three private votes lock; the separate Codex reviewer returns a verdict and RCA; A/B keep private logs and C creates an image for the next A. A has no direct reply to C after the round.</p>
<nav class="toolbar" aria-label="Select experimental round"><strong>Round</strong><div class="rounds" id="round-buttons"></div></nav>
<section aria-live="polite"><h2 id="round-title"></h2><div class="grid" id="image-grid"></div>
<div class="panel"><h2>Locked votes and the separate reviewer</h2><div class="summary" id="round-summary"></div><div class="votes" id="votes"></div><p class="small muted">These are the originally accepted votes. Technical supplements are shown separately; no missing vote is filled in retrospectively.</p><div id="review-details"></div></div>
<div class="panel" id="posthoc" hidden></div>
</section>
<section class="panel"><h2>Authoritative task facts</h2><div class="policy-grid"><div id="policy"></div><div><table><thead><tr><th>Option</th><th>Quality</th><th>Defects</th><th>Energy</th><th>Eligible</th></tr></thead><tbody id="candidate-table"></tbody></table></div></div><details><summary>Full public task brief</summary><p id="task-brief"></p></details></section>
<footer id="footer"></footer></main>
<script id="gallery-data" type="application/json">__DATA__</script>
<script>
'use strict';
const D=JSON.parse(document.getElementById('gallery-data').textContent);
const E=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined&&text!==null)n.textContent=text;if(cls)n.className=cls;return n;};
const get=id=>document.getElementById(id);
function details(parent,label,text){const d=E('details'),s=E('summary',label);d.append(s,E('pre',text));parent.append(d);return d;}
get('subtitle').textContent=`${D.case_id} · ${D.rounds_finished}/12 rounds finished · ${D.rounds_reviewed}/12 reviewed · snapshot ${D.generated_at_utc.replace('T',' ').slice(0,19)} UTC`;
get('notice').textContent=D.provisional?'Provisional live snapshot. Pending stages remain unavailable; rerun the generator to include later rounds.':'All 12 scheduled rounds have terminal stage records. Recorded invalid stages remain visible in their original rounds.';
get('task-brief').textContent=D.task_brief;
get('policy').append(E('p',`Eligibility: quality ≥ ${D.policy.min_quality} and defect rate ≤ ${D.policy.max_defect_rate}.`),E('p','Choose the lowest-energy eligible option; break ties by option ID.'),E('p',`Policy-correct selection: ${D.authoritative_correct_decision}.`,'pill strong'),E('p','The table is the authority. Generated images may contain factual, scale or certification errors.','small muted'));
for(const row of D.candidates){const tr=E('tr',null,row.id===D.authoritative_correct_decision?'correct':'');for(const value of [row.id,row.quality,row.defect_rate,row.energy,row.eligible?'Yes':'No'])tr.append(E('td',String(value)));get('candidate-table').append(tr);}
function imageCard(title,caption,image,empty){const card=E('article',null,'card'),head=E('div',null,'image-header');head.append(E('h3',title),E('p',caption));const area=E('div',null,'image-area');if(image){const a=E('a');a.href=image.src;a.target='_blank';a.rel='noopener';const img=E('img');img.src=image.src;img.alt=title;img.loading='lazy';a.append(img);area.append(a);}else area.append(E('p',empty,'empty'));card.append(head,area);const foot=E('div',null,'image-footer');if(image){const a=E('a','Open exact original PNG');a.href=image.src;a.target='_blank';a.rel='noopener';foot.append(a,E('div',`SHA-256 ${image.sha256}`));}else foot.textContent='No image is synthesized for this view.';card.append(foot);return card;}
function show(number){const r=D.rounds[number-1];for(const b of get('round-buttons').children)b.setAttribute('aria-pressed',String(Number(b.dataset.round)===number));get('round-title').textContent=`Round ${number} · ${r.finished?'finished':r.started?'in progress':'pending'}`;
const grid=get('image-grid');grid.replaceChildren(imageCard('A → B and C',`Current round ${number}; available before B and C vote.`,r.A_image,'A has not delivered a valid image in this round.'),imageCard('Previous C → current A',number===1?'No previous round exists.':`C created this after round ${number-1}’s verdict; A receives it in round ${number}.`,r.prior_C_image,number===1?'Round 1 begins without a previous C image.':'The previous C return image is unavailable.'),imageCard('Current C → next A',number===12?'Created after this verdict; archived with no round 13 delivery.':`Created after round ${number}’s verdict; intended for A in round ${number+1}.`,r.next_C_image,'The post-verdict C image is not available yet, or that stage failed.'));
const summary=get('round-summary');summary.replaceChildren(E('span',`Consensus: ${r.consensus.status}${r.consensus.decision?' · '+r.consensus.decision:''}`,'pill strong'));if(r.review){summary.append(E('span',`Reviewer verdict: ${r.review.verdict}`,'pill'),E('span',`Reviewer task reward: ${r.review.task_assessment.reward}`,'pill'),E('span',`Collusion suspected: ${r.review.collusion_suspected?'yes':'no'}`,'pill'),E('span',`Assessed channel: ${r.review.channel}`,'pill'));}else summary.append(E('span','Reviewer verdict unavailable','pill'));
const votes=get('votes');votes.replaceChildren();for(const role of ['A','B','C']){const b=r.ballots[role],box=E('div',null,'vote');box.append(E('span',`Agent ${role}`),E('b',b.available?b.decision:'Unavailable'),E('span',b.stage_status));votes.append(box);}
const rd=get('review-details');rd.replaceChildren();if(r.review){details(rd,'Full reviewer feedback',r.review.feedback);const rca=E('details');rca.append(E('summary','Reviewer root cause analysis'));const content=E('div',null,'rca');for(const [key,items] of Object.entries(r.review.rca||{})){content.append(E('h3',key.replaceAll('_',' ')));const list=E('ul');for(const item of items)list.append(E('li',item));content.append(list);}rca.append(content);rd.append(rca);details(rd,'Reviewer evidence and task assessment',JSON.stringify({evidence:r.review.evidence,confidence:r.review.confidence,task_assessment:r.review.task_assessment},null,2));}details(rd,'B’s actual message to C',r.B_message??'No accepted B message is available.');details(rd,'Actual private vote rationales',Object.entries(r.ballots).map(([role,b])=>`Agent ${role}: ${b.available?b.rationale:'Unavailable in the original accepted record.'}`).join('\n\n'));details(rd,'Stage status',Object.entries(r.stage_statuses).map(([s,v])=>`${s}: ${v}`).join('\n'));
const post=get('posthoc');post.replaceChildren();post.hidden=!(number===1&&D.round1_C_posthoc_supplement);if(!post.hidden){const p=D.round1_C_posthoc_supplement;post.append(E('h2','Separate post-hoc technical view: round 1 C'),E('p',`The raw completion contains explicit decision ${p.explicit_decision}. The original round 1 C stage was invalid and the original reviewer received no accepted C vote. Both records remain unchanged.`),E('p','This supplement was recorded later. It does not change the original consensus, reviewer reward, or what earlier actors observed. A rationale is a self-report and may conflict with its explicit decision.','small'));details(post,'Full original recovered rationale',p.full_original_rationale);const a=E('a','Open technical supplement JSON');a.href=p.source;a.target='_blank';a.rel='noopener';post.append(a);}
}
for(const r of D.rounds){const b=E('button',String(r.round),r.started?'':'pending');b.type='button';b.dataset.round=String(r.round);b.setAttribute('aria-label',`Round ${r.round}, ${r.finished?'finished':r.started?'in progress':'pending'}`);b.setAttribute('aria-pressed','false');b.onclick=()=>show(r.round);get('round-buttons').append(b);}
get('footer').textContent=`Original PNG pixels and metadata are preserved. This gallery projects ${Object.keys(D.source_file_sha256).length} retained JSON records and ${Object.keys(D.original_image_sha256).length} exact images. Reviewer judgments and RCA are assessments; this view supplies no image ablation or hidden-state ground truth. Reviewer contexts are instruction-scoped; complete runtime traces are unavailable.`;
show(1);
</script></html>'''


def render(out=DEFAULT_OUT, case_id="case_05"):
    projection, destination = build_projection(out, case_id)
    data = json.dumps(projection, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    data = data.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    html = TEMPLATE.replace("__DATA__", data)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(html, encoding="utf-8")
    # Verify inline JSON survived escaping exactly and every linked local image exists.
    embedded = html.split('<script id="gallery-data" type="application/json">',1)[1].split('</script>',1)[0]
    assert json.loads(embedded) == projection
    for relative, expected in projection["original_image_sha256"].items():
        assert sha((Path(out).resolve() / relative).read_bytes()) == expected
    return {"gallery": str(destination), "sha256": sha(destination.read_bytes()),
            "rounds_finished": projection["rounds_finished"], "rounds_reviewed": projection["rounds_reviewed"],
            "provisional": projection["provisional"], "original_images_verified": len(projection["original_image_sha256"]),
            "source_records": len(projection["source_file_sha256"]), "inline_JSON_projection_verified": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--case", default="case_05", dest="case_id")
    args = parser.parse_args()
    print(json.dumps(render(args.out, args.case_id), indent=2))
