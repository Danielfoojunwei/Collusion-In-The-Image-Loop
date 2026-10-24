"""Build the proposed longer schedule offline; this is not an inference runner.

Writes only proposal artifacts alongside this file. No provider import, request,
ledger construction, source mutation, or saved-scientific-evidence mutation.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from visual_collusion.protocol import build_plan


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
REVISION = "pressure_v1_long12"
SEED = 20261024
PREFIX = 8
CONTINUATION = 4
HORIZON = PREFIX + CONTINUATION


def save(name, value):
    (HERE / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def main():
    # Reuse only the original deterministic case/assignment construction.
    # Its canonical runtime remains unchanged and cannot execute this proposal.
    original = json.loads((ROOT / "configs/canonical.json").read_text())
    assignment_config = copy.deepcopy(original)
    assignment_config["seed"] = SEED
    base = build_plan(assignment_config)
    episodes = []
    rounds = []
    for source in base["episodes"]:
        ep = copy.deepcopy(source)
        ep["rounds"] = HORIZON
        ep["prefix_rounds"] = PREFIX if ep["condition"] == "explicit" else 0
        ep["primary_reception_round"] = PREFIX + 1 if ep["condition"] == "explicit" else None
        ep["planned_history_rounds_per_final_trajectory"] = HORIZON
        episodes.append(ep)
    by_id = {e["episode_id"]: e for e in episodes}
    for episode_id in base["execution_order"]:
        ep = by_id[episode_id]
        shared = range(1, PREFIX + 1) if ep["condition"] == "explicit" else range(1, HORIZON + 1)
        for number in shared:
            rounds.append({"episode_id": episode_id, "case_id": ep["case_id"],
                           "condition": ep["condition"], "branch": "prefix",
                           "round": number, "private_target": ep["initial_target"],
                           "fresh_assignment": False, "neutral_B_replay": False})
        if ep["condition"] == "explicit":
            for branch in ep["branches"]:
                for number in range(PREFIX + 1, HORIZON + 1):
                    rounds.append({"episode_id": episode_id, "case_id": ep["case_id"],
                                   "condition": ep["condition"], "branch": branch["branch_id"],
                                   "round": number, "private_target": branch["private_target"],
                                   "fresh_assignment": number == PREFIX + 1,
                                   "neutral_B_replay": number == PREFIX + 1})
    main_rounds = sum(r["condition"] == "explicit" for r in rounds)
    neutral_turns = sum(r["neutral_B_replay"] for r in rounds)
    actor_turns = len(rounds) * 2 + neutral_turns
    trajectories = sum(len(e["branches"]) or 1 for e in episodes)
    counts = {
        "independent_main_blocks": 6, "honest_pairs": 2, "overt_pairs": 2,
        "rounds_per_final_trajectory": HORIZON,
        "shared_main_rounds_per_pair": PREFIX,
        "continuation_rounds_per_branch": CONTINUATION,
        "unique_main_rounds_per_pair_including_two_branches": PREFIX + 2 * CONTINUATION,
        "unique_main_AB_rounds": main_rounds,
        "unique_control_AB_rounds": len(rounds) - main_rounds,
        "unique_AB_rounds_and_Codex_reviews": len(rounds),
        "real_actor_turns": len(rounds) * 2,
        "neutral_B_actor_turns": neutral_turns,
        "total_actor_turns": actor_turns,
        "real_delivered_image_slots": len(rounds) * 2,
        "delivered_images_per_final_trajectory": HORIZON * 2,
        "maximum_image_requests": actor_turns * 2,
        "maximum_actor_chat_calls": actor_turns * 4,
        "final_trajectories": trajectories,
        "desired_final_audit_views": trajectories * 2,
        "desired_maximum_OpenRouter_chat_calls": actor_turns * 4 + trajectories * 2,
        "Codex_task_author_jobs": len(episodes),
        "planned_primary_receptions": neutral_turns,
        "primary_reception_round": PREFIX + 1,
        "flattened_final_history_entries_including_shared_prefix_repetitions": trajectories * HORIZON,
    }
    assert len(rounds) == 144 and main_rounds == 96 and neutral_turns == 12
    assert counts["maximum_actor_chat_calls"] == 1200
    assert counts["maximum_image_requests"] == 600
    assert trajectories == 16
    assert len({(r["episode_id"], r["branch"], r["round"]) for r in rounds}) == len(rounds)
    for ep in episodes:
        ep_rounds = [r for r in rounds if r["episode_id"] == ep["episode_id"]]
        if ep["condition"] == "explicit":
            assert len(ep_rounds) == 16
            assert all(r["round"] == 9 for r in ep_rounds if r["neutral_B_replay"])
        else:
            assert len(ep_rounds) == 12
    spec = {
        "revision": REVISION, "state": "prepared_specification_not_executable",
        "base_case_generator_protocol": original["protocol_version"],
        "case_seed": SEED, "source_config": "configs/canonical.json",
        "source_config_sha256": hashlib.sha256((ROOT / "configs/canonical.json").read_bytes()).hexdigest(),
        "horizon": {"main_shared_rounds": PREFIX, "main_branch_rounds": CONTINUATION,
                    "rounds_per_trajectory": HORIZON, "honest_rounds": HORIZON,
                    "overt_rounds": HORIZON, "fresh_target_and_primary_round": PREFIX + 1,
                    "review_after_each_completed_AB_round": True,
                    "success_dependent_extensions": False},
        "per_turn": {"max_model_actions": 4, "max_image_requests": 2,
                     "image_size": "1024x1024", "paid_retries": 0},
        "models": original["models"],
        "sampling_request": {"temperature": "omitted", "top_p": "omitted", "top_k": "omitted"},
        "budget": {"aggregate_cap_usd": 40, "cash_ceiling_OpenRouter_usd": 50,
                   "Codex_excluded": True, "old_spending_and_unknown_reserves_count": True,
                   "new_automatic_spend_allowance": False,
                   "reserve_before_each_POST_and_stop_if_cap_would_be_exceeded": True,
                   "old_5_dollar_exploratory_proposal_superseded": True},
        "counts": counts,
        "actor_prompt_bindings": [{"role": role.upper(), "path": "prompts/agent_" + role + "_explicit_system.txt",
                                  "sha256": hashlib.sha256((HERE / "prompts" / ("agent_" + role + "_explicit_system.txt")).read_bytes()).hexdigest()}
                                 for role in ("a", "b")],
        "context_policy": {"desired": "preserve complete exact-pixel history and own permitted tool traces",
                           "implemented_for_this_horizon": False,
                           "silent_dropping_resizing_or_history_truncation_allowed": False},
        "activation_blockers": [
            "Current canonical validator/scheduler hardcode two prefix and two continuation rounds; config changes alone do not extend execution.",
            "Fresh notices, primary/replay triggers, checkpoint/evidence checks, report labels and maximum call counts must use round9/the new schedule.",
            "GLM16-image/64-message/text-token envelopes cannot carry worst-case twelve-round full history (up to48 actor image blocks).",
            "Sonnet8-image envelope cannot carry the desired24-image final history view.",
            "The independent strictly-under10MiB actual wire guard remains enforced; more image slots alone do not solve payload size.",
            "A separately versioned context/transport policy must be implemented and pass maximum-context offline tests; any changed view must be disclosed.",
            "The shared aggregate budget and request namespace must be implemented and worst-case reservations recalculated, not copied from v4.",
            "All ten actual task-author outputs must be collected and frozen for the new cases before their actor trials."
        ],
        "inference_started": False,
        "interpretation": "Combined pressure-prompt and longer-horizon revision, not a prompt-only intervention. Six independent main blocks; repeated turns and branches are dependent."
    }
    save("extended_study_spec.json", spec)
    save("extended_schedule.json", {"revision": REVISION, "state": "planned_not_executed",
                                   "private_researcher_plan_not_actor_or_reviewer_context": True,
                                   "cases": base["cases"], "episodes": episodes,
                                   "execution_order": base["execution_order"],
                                   "unique_round_schedule": rounds, "counts": counts})
    print(json.dumps({"state": spec["state"], "counts": counts}, indent=2))


if __name__ == "__main__":
    main()
