"""Temporary proof/tamper fixtures for the analysis-only invalid-record adapter."""
from copy import deepcopy
import base64
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from PIL import Image

from scripts import pressure_invalid_analysis_v3 as amendment
from experiments.pressure_interview_v1.contracts import ContractError, canonical
from visual_collusion.budget import BudgetLedger


MODEL = "z-ai/glm-5.3-flash"


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical(value) + "\n")


class InvalidAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.out = self.root / "study"
        self.out.mkdir()
        self.ledger = BudgetLedger(self.root / "aggregate.sqlite3", 40)
        self.namespace = "invalid_fixture"
        write(self.out / "config.json", {"budget": {"aggregate_ledger": str(self.ledger.path), "namespace": self.namespace}})
        self.verify = amendment.build_stage_verifier()
        self.fixture = self.make_fixture("A_OUTPUT", "first")

    def tearDown(self):
        self.temp.cleanup()

    def make_fixture(self, stage, pair):
        unit = self.out / "episodes" / pair / "prefix"
        (unit / "memory").mkdir(parents=True)
        (unit / "memory/events.jsonl").write_bytes(b"")
        request = {"request_id": pair + ".prefix.r01." + stage, "stage": stage,
                   "role": "B" if stage == "B_OUTPUT" else "A", "round": 1,
                   "messages": [], "context_manifest": {"handle_bindings": {}}}
        request["request_hash"] = amendment.sha(canonical(request).encode())
        key = amendment.sha(request["request_id"].encode()) + ".json"
        write(unit / "requests" / key, request)
        value = {"action": "generate_image", "image_prompt": "Offline fixture image.", "reference": "none",
                 "inspection": {"operation": "full", "box": None}, "memory_handle": "",
                 "message": "", "private_note": "Offline fixture status."}
        if stage == "B_OUTPUT":
            value.update({"decision": None, "report": None})
        reason = "Inspection must be null outside the inspect action"
        local = request["request_id"] + ".step1"
        if stage == "A_INTERVIEW":
            value, reason, local = {"answer": "x" * 801}, "Invalid or over-limit actor text", request["request_id"] + ".answer"
        identifier = self.namespace + ":" + local
        response = {"model": MODEL, "provider": "OpenInference", "choices": [{"finish_reason": "stop",
                    "message": {"content": json.dumps(value)}}], "usage": {"cost": .001}}
        receipt = {"request_id": identifier, "request_hash": "a" * 64, "kind": "chat",
                   "requested_model": MODEL, "returned_model": MODEL, "provider": "OpenInference",
                   "usage": response["usage"], "cost_usd": .001}
        self.ledger.reserve(identifier, "chat", MODEL, "a" * 64, .05)
        self.ledger.complete(identifier, .001, response, receipt)
        action_path = self.out / "provider_evidence/actor_actions" / (amendment.sha(local.encode()) + ".json")
        evidence = []
        if stage != "A_INTERVIEW":
            write(action_path, {"step": 1, "request_id": local, "output": value, "receipt_id": identifier})
            evidence = [str(action_path)]
        known_local = request["request_id"] if stage == "A_INTERVIEW" else local
        known_path = self.out / "provider_evidence/known_invalid" / (amendment.sha(known_local.encode()) + ".json")
        known = {"request_id": known_local, "reason": reason, "receipt_ids": [identifier], "receipts": [receipt],
                 "evidence_paths": evidence, "known_invalid_observation": True, "evidence_origin": "live"}
        write(known_path, known)
        invalid = {"request_id": request["request_id"], "request_hash": request["request_hash"],
                   "status": "terminal_invalid_backend_observation", "error_class": "KnownInvalidBackendOutput",
                   "receipt_ids": [identifier], "evidence_paths": [str(known_path), *evidence], "reason": reason, "no_retry": True}
        invalid_path = unit / "invalid_responses" / key
        write(invalid_path, invalid)
        return {"unit": unit, "request": request, "key": key, "value": value, "response": response,
                "receipt": receipt, "identifier": identifier, "known": known, "known_path": known_path,
                "action_path": action_path, "invalid": invalid, "invalid_path": invalid_path}

    def verify_fixture(self, fixture=None):
        return self.verify((fixture or self.fixture)["unit"], "live")

    def test_valid_backend_invalid_proof_preserves_failure_and_writes_nothing(self):
        before = {str(path): path.read_bytes() for path in self.out.rglob("*") if path.is_file()}
        self.assertEqual(self.verify_fixture(), 0)
        after = {str(path): path.read_bytes() for path in self.out.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertFalse((self.fixture["unit"] / "observations").exists())

    def test_B_and_interview_failures_require_their_actual_raw_contract_violation(self):
        for stage in ("B_OUTPUT", "A_INTERVIEW"):
            with self.subTest(stage=stage):
                fixture = self.make_fixture(stage, stage.lower())
                self.assertEqual(self.verify_fixture(fixture), 0)

    def test_changed_action_or_missing_action_evidence_is_rejected(self):
        path = self.fixture["action_path"]
        original = path.read_bytes()
        action = json.loads(original)
        action["output"]["private_note"] = "Different purported output"
        write(path, action)
        with self.assertRaises(ContractError):
            self.verify_fixture()
        path.write_bytes(original)
        path.unlink()
        with self.assertRaises(ContractError):
            self.verify_fixture()

    def test_changed_raw_response_or_receipt_checksum_is_rejected(self):
        for field in ("response_json", "receipt_json"):
            with self.subTest(field=field):
                with sqlite3.connect(self.ledger.path) as db:
                    old = db.execute("SELECT " + field + " FROM requests WHERE request_id=?", (self.fixture["identifier"],)).fetchone()[0]
                    db.execute("UPDATE requests SET " + field + "='{}' WHERE request_id=?", (self.fixture["identifier"],))
                with self.assertRaises(ContractError):
                    self.verify_fixture()
                with sqlite3.connect(self.ledger.path) as db:
                    db.execute("UPDATE requests SET " + field + "=? WHERE request_id=?", (old, self.fixture["identifier"]))

    def test_wrong_or_unknown_receipts_and_an_extra_intent_are_rejected(self):
        invalid = deepcopy(self.fixture["invalid"])
        invalid["receipt_ids"] = ["other_namespace:another_stage.step1"]
        write(self.fixture["invalid_path"], invalid)
        with self.assertRaises(ContractError):
            self.verify_fixture()
        write(self.fixture["invalid_path"], self.fixture["invalid"])
        self.ledger.reserve(self.namespace + ":" + self.fixture["request"]["request_id"] + ".step2",
                            "chat", MODEL, "b" * 64, .05)
        with self.assertRaises(ContractError):
            self.verify_fixture()

    def test_pending_and_unknown_claimed_receipts_are_rejected(self):
        for status in ("pending", "uncertain"):
            with self.subTest(status=status):
                with sqlite3.connect(self.ledger.path) as db:
                    db.execute("UPDATE requests SET status=? WHERE request_id=?", (status, self.fixture["identifier"]))
                with self.assertRaises(ContractError):
                    self.verify_fixture()

    def test_known_invalid_missing_or_false_reason_is_rejected(self):
        known = deepcopy(self.fixture["known"])
        known["reason"] = "A fabricated failure reason"
        invalid = {**self.fixture["invalid"], "reason": known["reason"]}
        write(self.fixture["known_path"], known)
        write(self.fixture["invalid_path"], invalid)
        with self.assertRaises(ContractError):
            self.verify_fixture()
        self.fixture["known_path"].unlink()
        with self.assertRaises(ContractError):
            self.verify_fixture()

    def test_pinned_raw_model_is_checked_even_if_checksum_is_recomputed(self):
        response = {**self.fixture["response"], "model": "different/model"}
        response_text = canonical(response)
        receipt_text = canonical(self.fixture["receipt"])
        with sqlite3.connect(self.ledger.path) as db:
            db.execute("UPDATE requests SET response_json=?,result_hash=? WHERE request_id=?",
                       (response_text, amendment.sha((response_text + "\n" + receipt_text).encode()), self.fixture["identifier"]))
        with self.assertRaises(ContractError):
            self.verify_fixture()

    def test_raw_usage_provider_and_cost_must_match_even_after_rehashing(self):
        for change in ({"usage": {"cost": .002}}, {"provider": "Different provider"}):
            with self.subTest(change=change):
                response = {**self.fixture["response"], **change}
                response_text, receipt_text = canonical(response), canonical(self.fixture["receipt"])
                with sqlite3.connect(self.ledger.path) as db:
                    db.execute("UPDATE requests SET response_json=?,result_hash=? WHERE request_id=?",
                               (response_text, amendment.sha((response_text + "\n" + receipt_text).encode()), self.fixture["identifier"]))
                with self.assertRaises(ContractError):
                    self.verify_fixture()

    def test_valid_image_response_cannot_be_claimed_as_generic_provider_invalid(self):
        unit = self.out / "episodes/image_only/prefix"
        (unit / "memory").mkdir(parents=True)
        (unit / "memory/events.jsonl").write_bytes(b"")
        request = {"request_id": "image_only.prefix.r01.A_OUTPUT", "stage": "A_OUTPUT", "role": "A", "round": 1}
        request["request_hash"] = amendment.sha(canonical(request).encode())
        key = amendment.sha(request["request_id"].encode()) + ".json"
        write(unit / "requests" / key, request)
        local, model = request["request_id"] + ".image1", "qwen/qwen-image-2.1-turbo"
        identifier = self.namespace + ":" + local
        stream = io.BytesIO()
        Image.new("RGB", (1024, 1024), "white").save(stream, "PNG")
        response = {"data": [{"media_type": "image/png", "b64_json": base64.b64encode(stream.getvalue()).decode()}],
                    "usage": {"cost": .001}}
        receipt = {"request_id": identifier, "request_hash": "c" * 64, "kind": "image", "requested_model": model,
                   "returned_model": None, "provider": None, "usage": response["usage"], "cost_usd": .001}
        self.ledger.reserve(identifier, "image", model, "c" * 64, .05)
        self.ledger.complete(identifier, .001, response, receipt)
        known_path = self.out / "provider_evidence/known_invalid" / (amendment.sha(local.encode()) + ".json")
        reason = "Paid provider output was incomplete, refused or malformed"
        write(known_path, {"request_id": local, "reason": reason, "receipt_ids": [identifier], "receipts": [receipt],
                           "evidence_paths": [], "known_invalid_observation": True, "evidence_origin": "live"})
        write(unit / "invalid_responses" / key, {"request_id": request["request_id"], "request_hash": request["request_hash"],
            "status": "terminal_invalid_backend_observation", "error_class": "KnownInvalidBackendOutput",
            "receipt_ids": [identifier], "evidence_paths": [str(known_path)], "reason": reason, "no_retry": True})
        with self.assertRaisesRegex(ContractError, "Provider-parser invalidity"):
            self.verify(unit, "live")

    def test_backend_invalid_unit_symlink_is_rejected_before_resolution(self):
        alias = self.fixture["unit"].parent / "alias"
        alias.symlink_to(self.fixture["unit"], target_is_directory=True)
        with self.assertRaisesRegex(ContractError, "symlink"):
            self.verify(alias, "live")

    def test_valid_action_cannot_be_reclassified_by_fabricating_a_failure_record(self):
        value = {**self.fixture["value"], "inspection": None}
        response = deepcopy(self.fixture["response"])
        response["choices"][0]["message"]["content"] = json.dumps(value)
        response_text, receipt_text = canonical(response), canonical(self.fixture["receipt"])
        with sqlite3.connect(self.ledger.path) as db:
            db.execute("UPDATE requests SET response_json=?,result_hash=? WHERE request_id=?",
                       (response_text, amendment.sha((response_text + "\n" + receipt_text).encode()), self.fixture["identifier"]))
        action = json.loads(self.fixture["action_path"].read_text())
        action["output"] = value
        write(self.fixture["action_path"], action)
        with self.assertRaises(ContractError):
            self.verify_fixture()

    def test_regular_outer_invalid_keeps_original_observation_requirement(self):
        request = self.fixture["request"]
        observation = {"request_id": request["request_id"], "request_hash": request["request_hash"],
                       "output": {"invalid_fixture": True}}
        observation["observation_hash"] = amendment.sha(canonical(observation).encode())
        path = self.fixture["unit"] / "observations" / self.fixture["key"]
        write(path, observation)
        write(self.fixture["invalid_path"], {"request_id": request["request_id"],
            "status": "terminal_invalid_first_observation", "observation_hash": observation["observation_hash"]})
        self.assertEqual(self.verify_fixture(), 0)
        path.unlink()
        with self.assertRaises(ContractError):
            self.verify_fixture()

    def test_C_raw_answer_checks_are_unchanged_and_C_cannot_claim_backend_invalid(self):
        unit = self.fixture["unit"]
        request = {"request_id": "first.prefix.r01.C_QUESTION", "stage": "C_QUESTION", "role": "C", "round": 1}
        request["request_hash"] = amendment.sha(canonical(request).encode())
        key = amendment.sha(request["request_id"].encode()) + ".json"
        write(unit / "requests" / key, request)
        raw = b'{"recipient":"A","question":"Offline fixture question."}'
        raw_path = unit / "codex_raw" / key
        raw_path.parent.mkdir()
        raw_path.write_bytes(raw)
        response = {"request_id": request["request_id"], "request_hash": request["request_hash"],
                    "evidence_origin": "live", "provenance": {"actor_runtime": "codex",
                    "raw_answer_sha256": amendment.sha(raw), "trace": {"audit_status": "unverified"}}}
        response["response_hash"] = amendment.sha(canonical(response).encode())
        write(unit / "responses" / key, response)
        self.assertEqual(self.verify_fixture(), 1)
        raw_path.write_bytes(raw + b" ")
        with self.assertRaises(ContractError):
            self.verify_fixture()
        raw_path.write_bytes(raw)
        fake = {**self.fixture["invalid"], "request_id": request["request_id"], "request_hash": request["request_hash"]}
        write(unit / "invalid_responses" / key, fake)
        with self.assertRaises(ContractError):
            self.verify_fixture()

    def test_backend_invalid_is_not_accepted_as_fixture_or_conflicting_outer_observation(self):
        with self.assertRaises(ContractError):
            self.verify(self.fixture["unit"], "fixture")
        request = self.fixture["request"]
        observation = {"request_id": request["request_id"], "request_hash": request["request_hash"], "output": {}}
        observation["observation_hash"] = amendment.sha(canonical(observation).encode())
        write(self.fixture["unit"] / "observations" / self.fixture["key"], observation)
        with self.assertRaises(ContractError):
            self.verify_fixture()


if __name__ == "__main__":
    unittest.main()
