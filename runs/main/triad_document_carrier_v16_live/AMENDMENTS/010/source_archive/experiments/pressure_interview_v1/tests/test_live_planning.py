"""Fixed batch assignment and resource/accounting checks before inference."""
import copy
import unittest

from experiments.pressure_interview_v1.planning import build_plan, load_config, validate_config


class BatchPlanningTests(unittest.TestCase):
    def test_batches_cover_independent_cases_once_and_do_not_share_pair_memory(self):
        plan = build_plan()
        episodes = {e["episode_id"]: e for e in plan["episodes"]}
        identifiers = []
        for batch, expected_metric in zip(plan["pressure_batches"], (1.0, 0.5, 0.25)):
            self.assertEqual(len(batch["episode_ids"]), 2)
            self.assertEqual(batch["metric"], expected_metric)
            for identifier in batch["episode_ids"]:
                self.assertEqual(episodes[identifier]["pressure_metric"], expected_metric)
                self.assertEqual(episodes[identifier]["pressure_decay"], 1)
                self.assertEqual(episodes[identifier]["prefix_rounds"], 8)
                self.assertEqual(episodes[identifier]["primary_reception_round"], 9)
                identifiers.append(identifier)
        self.assertEqual(len(set(identifiers)), 6)
        self.assertEqual(plan["execution_order"][:6], identifiers)
        self.assertEqual(len(set(plan["control_episode_ids"])), 4)
        self.assertFalse(set(identifiers) & set(plan["control_episode_ids"]))

    def test_counts_include_interviews_and_neutral_turns_without_duplicate_prefixes(self):
        plan = build_plan()
        rounds = sum(e["prefix_rounds"] + len(e["branches"]) * (e["rounds"] - e["prefix_rounds"])
                     if e["condition"] == "explicit" else e["rounds"] for e in plan["episodes"])
        c = plan["counts"]
        self.assertEqual(rounds, 144)
        self.assertEqual(c["maximum_OpenRouter_chat_calls_without_optional_audits"], (2 * rounds + 12) * 4 + rounds)
        self.assertEqual(c["maximum_image_requests"], (2 * rounds + 12) * 2)
        self.assertEqual(c["Codex_question_jobs"] + c["Codex_verdict_jobs"], 2 * rounds)

    def test_unfrozen_pressure_and_expanded_cash_cap_are_rejected(self):
        original = load_config()
        for change in ("decay", "batch", "budget"):
            config = copy.deepcopy(original)
            if change == "decay":
                config["pressure"]["decay"] = 0.85
            elif change == "batch":
                config["pressure"]["batches"][1]["metric"] = 0.1
            else:
                config["budget"]["max_inference_usd"] = 50
            with self.assertRaises(ValueError):
                validate_config(config)


if __name__ == "__main__":
    unittest.main()
