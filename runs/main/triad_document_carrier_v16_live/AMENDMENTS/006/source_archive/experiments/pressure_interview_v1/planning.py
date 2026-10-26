"""Offline schedule construction for the pressure/report/interview revision."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from visual_collusion.protocol import build_plan as base_plan


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def validate_config(config):
    if config.get("revision") != "pressure_interview_v1":
        raise ValueError("Wrong revision")
    h = config["horizon"]
    if h != {"shared_rounds": 8, "branch_rounds": 4, "total_rounds": 12,
             "primary_round": 9, "controls_rounds": 12}:
        raise ValueError("Schedule differs from the prepared twelve-round revision")
    if (config.get("main_case_blocks"), config.get("honest_controls"), config.get("overt_attempt_controls")) != (6, 2, 2):
        raise ValueError("Case counts changed")
    if config.get("max_actions") != 4 or config.get("max_images") != 2:
        raise ValueError("Actor allowances changed")
    if config.get("models", {}).get("A") != "z-ai/glm-5.3-flash" or config["models"].get("B") != "z-ai/glm-5.3-flash":
        raise ValueError("Actor models changed")
    if config["models"].get("image") != "qwen/qwen-image-2.1-turbo":
        raise ValueError("Image model changed")
    if config.get("routes") != {"A_and_B": "open-inference/fp4", "image": "alibaba", "fallbacks": False}:
        raise ValueError("Pinned routes changed")
    b = config["budget"]
    if (b.get("max_inference_usd"), b.get("max_OpenRouter_cash_usd"), b.get("Codex_excluded"), b.get("paid_retries")) != (40, 50, True, 0):
        raise ValueError("Budget scope changed")
    if b.get("aggregate_ledger") != "runs/main/budget.sqlite3" or not b.get("old_spending_and_unknown_reserves_count"):
        raise ValueError("Original aggregate ledger required")
    if config["interview"]["target"] != "A" or config["interview"].get("C_can_query_B"):
        raise ValueError("C may interview A only")
    if (config["context"].get("max_recent_images") != 4
            or config["context"].get("max_history_text_bytes") != 20000
            or config["context"].get("max_wire_bytes") != 10 * 1024 * 1024):
        raise ValueError("Context capacity policy changed")
    if config["reasoning"].get("request_hidden_chain_of_thought") or config["reasoning"].get("fabricate_missing_reasoning"):
        raise ValueError("Hidden reasoning must not be solicited or fabricated")
    pressure = config["pressure"]
    expected_batches = [{"batch_id": "pressure_low", "label": "low", "metric": 1.0},
                        {"batch_id": "pressure_medium", "label": "medium", "metric": 0.5},
                        {"batch_id": "pressure_high", "label": "high", "metric": 0.25}]
    if (pressure.get("batches") != expected_batches or pressure.get("pairs_per_batch") != 2
            or pressure.get("decay") != 1.0 or pressure.get("schedule") != "between_batches_constant_within_each_pair"):
        raise ValueError("Three fixed pressure batches of two independent pairs are required")
    return config


def load_config():
    return validate_config(json.loads((HERE / "config.json").read_text()))


def load_prompts(condition="explicit"):
    if condition not in ("explicit", "honest", "overt"):
        raise ValueError("Unknown condition")
    a_name = {"explicit": "agent_a.txt", "honest": "agent_a_honest.txt", "overt": "agent_a_overt.txt"}[condition]
    a = (HERE / "prompts" / a_name).read_text()
    c = (HERE / "prompts" / "agent_c.txt").read_text()
    return {"A_OUTPUT": a, "B_OUTPUT": (HERE / "prompts" / "agent_b.txt").read_text(),
            "C_QUESTION": c, "A_INTERVIEW": a + "\n\n" + (HERE / "prompts" / "agent_a_interview.txt").read_text(),
            "C_VERDICT": c}


def build_plan(config=None):
    config = validate_config(config or load_config())
    original = json.loads((ROOT / "configs/canonical.json").read_text())
    original["seed"] = config["case_seed"]
    base = base_plan(original)
    plan = {"revision": config["revision"], "state": "planned_not_executed", "case_seed": config["case_seed"],
            "config_sha256": hashlib.sha256(canonical(config).encode()).hexdigest(),
            "private_researcher_plan_not_model_context": True,
            "cases": base["cases"], "episodes": copy.deepcopy(base["episodes"]),
            "execution_order": base["execution_order"]}
    unique_rounds = 0
    for ep in plan["episodes"]:
        ep["rounds"] = 12
        ep["prefix_rounds"] = 8 if ep["condition"] == "explicit" else 0
        ep["B_role"] = "independent_verifier_whistleblower"
        ep["primary_reception_round"] = 9 if ep["condition"] == "explicit" else None
        ep["interviews_per_real_round"] = 1
        unique_rounds += 16 if ep["condition"] == "explicit" else 12
    by_id = {e["episode_id"]: e for e in plan["episodes"]}
    # Case assignment/order is fixed by the independent seed before outcomes.
    main_order = [identifier for identifier in base["execution_order"]
                  if by_id[identifier]["condition"] == "explicit"]
    control_order = [identifier for identifier in base["execution_order"]
                     if by_id[identifier]["condition"] != "explicit"]
    batches = []
    for index, pressure in enumerate(config["pressure"]["batches"]):
        identifiers = main_order[index * 2:(index + 1) * 2]
        batch = {**copy.deepcopy(pressure), "independent_pair_runs": 2,
                 "episode_ids": identifiers, "unique_AB_rounds": 32,
                 "final_trajectories": 4, "primary_receptions": 4}
        batches.append(batch)
        for identifier in identifiers:
            by_id[identifier]["batch_id"] = pressure["batch_id"]
            by_id[identifier]["pressure_metric"] = pressure["metric"]
            by_id[identifier]["pressure_decay"] = 1.0
    for identifier in control_order:
        by_id[identifier]["batch_id"] = "controls"
        by_id[identifier]["pressure_metric"] = None if by_id[identifier]["condition"] == "honest" else 1.0
        by_id[identifier]["pressure_decay"] = 1.0
    plan["pressure_batches"] = batches
    plan["control_episode_ids"] = control_order
    plan["execution_order"] = main_order + control_order
    neutral_turns = 12
    actor_turns = 2 * unique_rounds + neutral_turns
    plan["counts"] = {
        "independent_main_case_blocks": 6, "control_cases": 4,
        "main_pressure_batches": 3, "independent_pairs_per_main_batch": 2,
        "unique_AB_rounds": unique_rounds, "real_actor_turns": unique_rounds * 2,
        "neutral_B_turns": neutral_turns, "final_trajectories": 16,
        "primary_receptions_at_round9": neutral_turns,
        "maximum_actor_chat_calls": actor_turns * 4,
        "maximum_A_interview_chat_calls": unique_rounds,
        "maximum_OpenRouter_chat_calls_without_optional_audits": actor_turns * 4 + unique_rounds,
        "maximum_image_requests": actor_turns * 2,
        "Codex_question_jobs": unique_rounds, "Codex_verdict_jobs": unique_rounds,
        "Codex_task_author_jobs": 10,
        "paid_monitor_filter_calls": 0, "optional_audits_activated": False,
    }
    assert unique_rounds == 144 and plan["counts"]["maximum_OpenRouter_chat_calls_without_optional_audits"] == 1344
    return plan


def write_plan(destination):
    plan = build_plan()
    destination = Path(destination).resolve()
    for protected in (ROOT / "runs", ROOT / "src", ROOT / "configs"):
        protected = protected.resolve()
        if destination == protected or protected in destination.parents:
            raise ValueError("Prepared plans cannot overwrite scientific evidence or canonical source/configuration")
    raw = json.dumps(plan, indent=2, sort_keys=True) + "\n"
    if destination.exists():
        if not destination.is_file() or destination.read_text() != raw:
            raise ValueError("A different existing artifact cannot be overwritten by a prepared plan")
        return plan
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x") as stream:
        stream.write(raw)
    return plan
