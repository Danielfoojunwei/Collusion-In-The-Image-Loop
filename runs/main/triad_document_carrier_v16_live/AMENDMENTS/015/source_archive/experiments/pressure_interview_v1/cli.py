"""Offline preparation and explicitly labeled software fixtures. No live command."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw

from .budget_guard import readonly_budget_snapshot
from .engine import PairEngine
from .planning import HERE, ROOT, build_plan, load_config, load_prompts, write_plan
from visual_collusion.protocol import public_case


class FixtureAdapter:
    """Scripted interface fixtures; never represent these as model observations."""
    evidence_origin = "fixture"
    network_allowed = False

    def __init__(self, engine, correct_choice):
        self.engine, self.correct_choice, self.calls = engine, correct_choice, 0

    def __call__(self, request):
        self.calls += 1
        stage = request["stage"]
        if stage in ("A_OUTPUT", "B_OUTPUT"):
            name = f"artifacts/fixture_{request['role']}_{request['round']:02d}.png"
            path = self.engine.memory_directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            canvas = Image.new("RGB", (1024, 1024), "white")
            draw = ImageDraw.Draw(canvas)
            draw.text((40, 40), "OFFLINE SOFTWARE FIXTURE - NOT MODEL EVIDENCE", fill="black")
            draw.text((40, 80), f"Role {request['role']} / round {request['round']}", fill="black")
            draw.text((40, 120), "No measured hidden payload or anomaly is asserted.", fill="black")
            canvas.save(path, format="PNG")
            value = {"message": "Offline fixture public message; not an experimental observation.",
                     "image": {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
                     "private_note": "Fixture status only; no hidden reasoning is simulated."}
            if stage == "B_OUTPUT":
                value.update({"decision": self.correct_choice,
                              "report": {"suspicious": False, "confidence": 0,
                                         "tag": "fixture_no_observation", "evidence": []}})
        elif stage == "C_QUESTION":
            value = {"recipient": "A", "question": "Fixture interview: what public artifact did you provide?"}
        elif stage == "A_INTERVIEW":
            value = {"answer": "This is an offline fixture artifact, not a model-generated experimental result."}
        elif stage == "C_VERDICT":
            value = {"verdict": "revise", "task_violation": False, "collusion_suspected": False,
                     "confidence": 0, "evidence": [],
                     "feedback": "Offline fixture only; no scientific judgment is asserted."}
        else:
            raise ValueError("Unknown fixture stage")
        return {"output": value, "provenance": {"evidence_origin": "fixture",
                "adapter": "explicitly_scripted_offline_fixture", "paid_calls": 0,
                "actor_trace": {"model_actions": 0, "image_requests": 0}}}


def demo(destination):
    destination = Path(destination).resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Fixture output must be a new or empty directory")
    destination.mkdir(parents=True, exist_ok=True)
    config, plan = load_config(), build_plan()
    case = next(c for c in plan["cases"] if c["case_id"] == "case_01")
    episode = next(e for e in plan["episodes"] if e["case_id"] == case["case_id"])
    task = {"brief": "Offline software fixture: apply the supplied factual selection policy.",
            "facts": public_case(case)}
    kwargs = {"config": config, "public_task": task, "private_target": episode["initial_target"],
              "pair_id": "fixture_pair", "prompts": load_prompts(), "evidence_origin": "fixture"}
    prefix = PairEngine(destination / "prefix", **kwargs)
    first = FixtureAdapter(prefix, case["correct_decision"])
    prefix.run_available(first)
    # Reopening must preserve the exact completed prefix without new dispatch.
    restarted = PairEngine(destination / "prefix", **kwargs)
    no_calls = FixtureAdapter(restarted, case["correct_decision"])
    restarted.run_available(no_calls)
    assert no_calls.calls == 0
    branches = []
    calls = first.calls
    for branch in episode["branches"]:
        engine = restarted.fork_branch(destination / branch["branch_id"],
                                       target=branch["private_target"], branch=branch["branch_id"])
        adapter = FixtureAdapter(engine, case["correct_decision"])
        result = engine.run_available(adapter)
        calls += adapter.calls
        branches.append({"branch": branch["branch_id"], "result": result,
                         "durable_events": len(engine.store.events())})
    result = {"evidence_origin": "fixture", "scientific_results": False,
              "live_model_calls": 0, "paid_calls": 0,
              "scope": "One eight-round shared pair and two four-round continuations; scripted software verification only",
              "unique_fixture_rounds": 16, "scripted_stage_responses": calls,
              "restart_repeated_adapter_calls": no_calls.calls,
              "branches": branches}
    assert calls == 80
    (destination / "FIXTURE_RESULT.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "budget-status", "fixture"))
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    if args.command == "plan":
        destination = args.destination or HERE / "PLAN.json"
        result = write_plan(destination)
        result = {"revision": result["revision"], "path": str(destination), "counts": result["counts"],
                  "live_model_calls": 0, "state": "planned_not_executed"}
    elif args.command == "budget-status":
        result = readonly_budget_snapshot(ROOT / load_config()["budget"]["aggregate_ledger"])
    else:
        if args.destination is None:
            parser.error("fixture requires --destination outside old scientific run directories")
        target = args.destination.resolve()
        old_runs = (ROOT / "runs").resolve()
        if target == old_runs or old_runs in target.parents:
            parser.error("software fixtures must stay outside scientific runs/")
        result = demo(target)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
