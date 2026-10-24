"""Adversarial evidence tests use deterministic offline fixtures only."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil
import tempfile
import unittest

from visual_collusion import protocol
from visual_collusion.archive import archive_messages, hydrate_messages
from visual_collusion.budget import BudgetLedger
from visual_collusion.common import digest, load_config, read_json, write_json
from visual_collusion.evidence import validate_evidence
from visual_collusion.mock import MockProvider
from visual_collusion.providers import OpenRouterProvider
from visual_collusion.runtime import advance, prepare_run


CONFIG = Path(__file__).resolve().parents[1] / "configs" / "canonical.json"


class EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.fixture_temp.cleanup)
        cls.fixture = Path(cls.fixture_temp.name) / "canonical_mock"
        cls.config = load_config(CONFIG)
        plan = prepare_run(cls.config, cls.fixture, evidence_origin="mock")
        provider = MockProvider(plan, BudgetLedger(cls.fixture / "budget.sqlite3"))
        provider.auto_reviews = True
        status = advance(provider, cls.config, cls.fixture)
        if status["status"] != "complete":
            raise AssertionError("Offline evidence fixture did not complete")
        cls.checked = validate_evidence(cls.fixture)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name) / "run"
        shutil.copytree(self.fixture, self.out)

    def alter_round_everywhere(self, episode, branch, number, transform):
        def matches(row):
            return row["episode_id"] == episode and row["branch"] == branch and row["round"] == number
        for directory in ("reception_rounds", "unreviewed_rounds", "rounds"):
            for path in (self.out / directory).glob("*.json"):
                row = read_json(path)
                if matches(row):
                    transform(row)
                    write_json(path, row)
        for path in (self.out / "trajectories").glob("*.json"):
            trajectory = read_json(path)
            for row in trajectory["history"]:
                if matches(row):
                    transform(row)
            write_json(path, trajectory)

    def first_reception(self):
        for path in sorted((self.out / "rounds").glob("*.json")):
            row = read_json(path)
            if row["condition"] == "explicit" and row["round"] == 3:
                return row
        raise AssertionError("Fixture has no primary reception")

    def test_complete_fixture_checks_all_primary_replays_and_request_counts(self):
        self.assertEqual(self.checked["rounds"], 48)
        self.assertEqual(self.checked["reception_checkpoints"], 48)
        self.assertEqual(self.checked["reception_rows"], 48)
        self.assertEqual(self.checked["primary_reception_rows"], 12)
        self.assertEqual(self.checked["trajectories"], 16)
        self.assertEqual(self.checked["request_counts"], {"chat": 356, "image": 108})
        self.assertEqual(self.checked["codex_accepted_jobs"], 58)

    def test_consistently_changed_neutral_decision_cannot_become_a_result(self):
        row = self.first_reception()
        def change(value):
            if value["neutral_replay"].get("status") == "valid":
                old = value["neutral_replay"]["output"]["decision"]
                value["neutral_replay"]["output"]["decision"] = next(choice for choice in protocol.CANDIDATE_IDS if choice != old)
        self.alter_round_everywhere(row["episode_id"], row["branch"], 3, change)
        with self.assertRaisesRegex(ValueError, "Final delivery"):
            validate_evidence(self.out)

    def test_another_valid_generated_image_cannot_replace_the_delivered_draft(self):
        row = self.first_reception()
        def change(value):
            value["b_image"] = deepcopy(value["a_image"])
        self.alter_round_everywhere(row["episode_id"], row["branch"], 3, change)
        with self.assertRaisesRegex(ValueError, "Final delivery"):
            validate_evidence(self.out)

    def test_changed_input_plus_recomputed_local_hash_still_fails_ledger_binding(self):
        call_path = next((self.out / "calls").glob("*.json"))
        call = read_json(call_path)
        input_path = self.out / "private_inputs" / call_path.name
        archived = read_json(input_path)
        archived["messages"][0]["content"] += "\nInvented post-hoc instruction."
        call["messages_sha256"] = digest(hydrate_messages(self.out, archived["messages"]))
        write_json(input_path, archived)
        write_json(call_path, call)
        with self.assertRaisesRegex(ValueError, "actual paid/mock request hash"):
            validate_evidence(self.out)

    def test_image_prompt_change_cannot_relabel_the_actual_generator_request(self):
        path = next((self.out / "image_calls").glob("*.json"))
        value = read_json(path)
        value["prompt"] += " An invented later prompt."
        write_json(path, value)
        with self.assertRaisesRegex(ValueError, "Image prompt/reference archive"):
            validate_evidence(self.out)

    def test_complete_trajectory_requires_all_its_reviewed_rounds(self):
        path = next((self.out / "trajectories").glob("*.json"))
        value = read_json(path)
        value["history"] = value["history"][:-1]
        write_json(path, value)
        with self.assertRaisesRegex(ValueError, "completion status"):
            validate_evidence(self.out)

    def test_replay_interruption_keeps_primary_reception_without_imputing_replay(self):
        class StopBeforeReplay(RuntimeError):
            pass
        class InterruptedMock(MockProvider):
            def chat(self, request_id, *args, **kwargs):
                if ":B:neutral:step1" in request_id:
                    raise StopBeforeReplay("Offline simulation: stop before the neutral request")
                return super().chat(request_id, *args, **kwargs)
        out = Path(self.temp.name) / "partial"
        plan = prepare_run(self.config, out, evidence_origin="mock")
        provider = InterruptedMock(plan, BudgetLedger(out / "budget.sqlite3"))
        provider.auto_reviews = True
        with self.assertRaises(StopBeforeReplay):
            advance(provider, self.config, out)
        checked = validate_evidence(out)
        self.assertEqual(checked["primary_reception_rows"], 1)
        self.assertEqual(checked["reception_rows"], checked["rounds"] + 1)
        pending = [read_json(path) for path in (out / "reception_rounds").glob("*.json")
                   if read_json(path)["neutral_replay"] == {"status": "pending"}]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["b_output"]["action"], "deliver")
        unreviewed = [read_json(path) for path in (out / "unreviewed_rounds").glob("*.json")]
        self.assertFalse(any(row["condition"] == "explicit" and row["round"] == 3
                             for row in unreviewed))

    def test_live_payload_digest_is_reconstructed_with_the_actual_transport_helper(self):
        # Offline injection exercises the live payload shape without any network.
        from test_provider_budget import FakeTransport
        out = Path(self.temp.name) / "live_contract_fixture"
        plan = prepare_run(self.config, out, evidence_origin="live")
        ledger = BudgetLedger(out / "budget.sqlite3")
        transport = FakeTransport(ledger)
        provider = OpenRouterProvider("SYNTHETIC_KEY_PLACEHOLDER", ledger, transport=transport)
        provider.preflight()
        request = plan["episodes"][0]["episode_id"] + ":prefix:round1:A:step1"
        messages = [{"role": "user", "content": "SYNTHETIC FIXTURE: return JSON."}]
        input_path = out / "private_inputs" / (digest(request)[:24] + ".json")
        write_json(input_path, {"request_id": request, "messages": archive_messages(out, messages)})
        provider.chat(request, self.config["models"]["producer"], messages, 4096,
                      schema=protocol.AGENT_A_SCHEMA, reasoning_effort="high")
        self.assertEqual(validate_evidence(out)["request_counts"], {"chat": 1})
        changed = read_json(input_path)
        changed["messages"][0]["content"] += " changed"
        write_json(input_path, changed)
        with self.assertRaisesRegex(ValueError, "actual paid/mock request hash"):
            validate_evidence(out)
        self.assertEqual(len(transport.posts), 1)


if __name__ == "__main__":
    unittest.main()
