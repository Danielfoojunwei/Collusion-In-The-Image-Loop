"""Real native-wire fault classification and exact original-ledger authorities."""
from copy import deepcopy
import http.client
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from visual_collusion.budget import BudgetLedger, BudgetError, PendingPaidRequest
from experiments.triad_emergent_v9 import backend as original
from experiments.triad_emergent_v9 import funding as original_funding
from experiments.triad_emergent_v9.coordinator import file_sha
from experiments.triad_emergent_v9.coordinator import producer_lock
from experiments.triad_emergent_v9.prompts import actor_system
from experiments.triad_repaired_v8.tests.test_backend import NativeWireTransport, RawAnswer
from experiments.triad_v9_checkpoint_restart04.tests import test_attempts as fixture
from experiments.triad_v9_checkpoint_restart04.attempts import AttemptStore
from experiments.triad_v9_checkpoint_restart04 import funding
from experiments.triad_v9_checkpoint_restart04.backend import Backend, make_policy, TOKEN_BUDGET_NOTICE
from experiments.triad_v9_checkpoint_restart04.faults import TechnicalRoundFault, PermanentTechnicalFault


class NativeAttemptTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.TransactionFixture()
        self.fixture.setUp()
        self.fixture.setup_store(legacy_rounds=6)
        self.out = self.fixture.out
        self.root, self.path = self.out.parent, self.out.parent / "budget.sqlite3"
        self.store = self.fixture.store
        self.legacy_fault = {"technical": True, "fault_class": "offline_legacy_loss"}
        self.store.register_legacy_abort(self.legacy_fault)
        self.context = self.store.begin_attempt(7)
        self.assertEqual(self.context["attempt_id"], "r07.a02")
        self.config = deepcopy(self.fixture.config)
        BudgetLedger(self.path)
        self.ledger = funding.FundingLedger(self.path, global_out=self.out)
        self.guard = funding.create_guard(self.path, global_out=self.out, attempt_context=self.context,
            evidence_dir=Path(self.context["working_root"]) / "provider_evidence", ledger=self.ledger)

    def tearDown(self):
        self.fixture.tearDown()

    def request(self, mode="visual_vote", role="A", suffix="A_IMAGE_AND_VOTE"):
        return {"request_id": "episode.r07." + suffix, "round": 7, "role": role, "mode": mode,
            "messages": [{"role": "system", "content": actor_system(role, mode)},
                         {"role": "user", "content": "Committed checkpoint facts only."}], "image_refs": {}}

    def backend(self, actions, wrapper=None):
        self.transport = NativeWireTransport(self.ledger, actions)
        transport = self.transport if wrapper is None else wrapper(self.transport)
        return Backend(self.config, self.context["working_root"], self.path, global_out=self.out,
            attempt_context=self.context, policy=make_policy(), transport=transport,
            api_key="OFFLINE_FIXTURE_NOT_A_SECRET", guard=self.guard)

    def rows(self):
        return funding._rows(self.path)

    def abandon(self, fault):
        marker = self.store.abandon_attempt(fault.fault)
        path = self.store.abandonment_path(self.context["round"], self.context["attempt"])
        return funding.seal_abandoned_attempt(self.out, self.context, marker["fault"], aggregate_path=self.path,
            abandonment_path=path, expected_abandonment_sha256=file_sha(path))

    def test_actual_wire_attempt_qualified_sampling_and_no_old_intent_repost(self):
        backend = self.backend([{"action": "decline_visual", "arguments": {"reason": "No image."}}, {"decision": "P4"}])
        result = backend.run_actor(self.request())
        self.assertEqual(result["output"]["decision"], "P4")
        self.assertEqual(len(self.transport.posts_exact), 2)
        for rid in self.rows():
            self.assertTrue(rid.startswith("triad_emergent_v9:r07.a02:episode.r07."))
        for endpoint, body in self.transport.posts_exact:
            payload = json.loads(body)
            self.assertEqual(payload["temperature"], 1.0)
            self.assertEqual(payload["max_tokens"], 8192)
            self.assertEqual(payload["reasoning"], {"effort": "high", "exclude": False})
            self.assertEqual(payload["messages"][0]["content"].count(TOKEN_BUDGET_NOTICE), 1)
        with self.assertRaises(original.BackendError):
            backend.provider.native_call(self.request()["request_id"] + ".visual1", [], [], 1.0)
        self.assertEqual(len(self.transport.posts_exact), 2)

    def test_timeout_uncertain_exact_authority_allows_new_attempt_full_reserve_counted(self):
        def wrap(native):
            def interrupted(method, path, body):
                if method == "POST":
                    raise http.client.IncompleteRead(b"sensitive provider bytes", 1024)
                return native(method, path, body)
            return interrupted
        backend = self.backend([], wrap)
        with self.assertRaises(TechnicalRoundFault) as caught:
            backend.run_actor(self.request())
        self.assertNotIsInstance(caught.exception, PermanentTechnicalFault)
        self.assertEqual(backend.provider.last_transport_diagnostic["exception_class"], "IncompleteRead")
        self.assertNotIn("sensitive", json.dumps(caught.exception.fault))
        before = self.rows()
        row = next(iter(before.values()))
        self.assertEqual(row["status"], "uncertain")
        self.assertGreater(row["reserved_units"], 0)
        self.assertIsNone(row["actual_units"])
        with self.assertRaises(PendingPaidRequest):
            self.ledger.assert_ready()
        self.abandon(caught.exception)
        self.assertEqual(self.rows(), before)
        self.ledger.assert_ready()
        self.assertEqual(self.ledger.summary()["reserved_unresolved_usd"], row["reserved_units"] / 1e9)
        self.context = self.store.begin_attempt(7)
        self.assertEqual(self.context["attempt_id"], "r07.a03")
        self.guard = funding.create_guard(self.path, global_out=self.out, attempt_context=self.context,
            evidence_dir=Path(self.context["working_root"]) / "provider_evidence", ledger=self.ledger)
        retry = self.backend([{"action": "decline_visual", "arguments": {"reason": "No image."}}, {"decision": "P2"}])
        retry.run_actor(self.request())
        self.assertEqual(self.rows()[row["request_id"]], row)
        self.assertTrue(any(rid.startswith("triad_emergent_v9:r07.a03:") for rid in self.rows()))

    def test_model_length_refusal_and_wrong_business_arguments_do_not_restart(self):
        for answer, expected in ((RawAnswer({"role": "assistant", "content": None}, "length"), "model_invalid"),
                (RawAnswer({"role": "assistant", "content": None, "refusal": "No."}, "stop"), "refused"),
                ({"decision": "P5"}, "model_invalid")):
            with self.subTest(expected=expected):
                backend = self.backend([answer, answer])
                suffix = "C_VOTE_" + str(len(self.rows()))
                result = backend.run_actor(self.request("vote", "C", suffix))
                self.assertEqual(result["status"], expected)
                self.assertIsNotNone(self.store.current())
                self.assertTrue(all(row["status"] == "completed" for row in self.rows().values()))

    def test_missing_outer_envelope_and_invalid_png_are_technical_known_costs(self):
        def outer(native):
            def wrapped(method, path, body):
                response = native(method, path, body)
                if method == "POST":
                    response.pop("choices", None)
                return response
            return wrapped
        backend = self.backend([{"action": "decline_visual", "arguments": {"reason": "No image."}}], outer)
        with self.assertRaises(TechnicalRoundFault) as caught:
            backend.run_actor(self.request())
        self.assertEqual(caught.exception.fault["fault_class"], "provider_chat_envelope")
        self.assertTrue(all(row["status"] == "completed" and row["actual_units"] is not None for row in self.rows().values()))
        def invalid_png(native):
            def wrapped(method, path, body):
                response = native(method, path, body)
                if method == "POST" and path == "/images":
                    response["data"][0]["b64_json"] = "bm90IGFuIGltYWdl"
                return response
            return wrapped
        backend = self.backend([{"action": "create_visual", "arguments": {"prompt": "A chart."}}], invalid_png)
        with self.assertRaises(TechnicalRoundFault) as caught:
            backend.run_actor(self.request(suffix="A_IMAGE_PNG"))
        self.assertEqual(caught.exception.fault["fault_class"], "invalid_provider_png")
        self.assertEqual(len(self.transport.posts_exact), 2)
        self.assertTrue(all(row["status"] == "completed" for row in self.rows().values()))

    def test_api_error_auth_and_other_unknown_have_safe_distinct_handling(self):
        def api_error(native):
            def wrapped(method, path, body):
                if method == "POST":
                    return {"error": {"code": 503, "message": "sensitive error text"}}
                return native(method, path, body)
            return wrapped
        backend = self.backend([], api_error)
        with self.assertRaises(TechnicalRoundFault) as caught:
            backend.run_actor(self.request())
        self.assertNotIsInstance(caught.exception, PermanentTechnicalFault)
        row = next(iter(self.rows().values()))
        self.assertIsNone(row["response_json"])
        self.assertNotIn("sensitive", json.dumps(caught.exception.fault))
        self.abandon(caught.exception)
        self.ledger.assert_ready()
        other = "triad_emergent_v9:foreign:episode.r07.A.visual1"
        self.ledger.reserve(other, "chat", original.MODEL, "a" * 64, .01)
        with self.assertRaises(PendingPaidRequest):
            self.ledger.assert_ready()
        self.ledger.uncertain(other)
        with self.assertRaises(PendingPaidRequest):
            self.ledger.assert_ready()

    def test_free_short_read_is_typed_and_has_no_paid_intent(self):
        guard = funding.preflight_guard(self.path, global_out=self.out, ledger=self.ledger)
        def interrupted(*args):
            raise http.client.IncompleteRead(b"sensitive", 1024)
        with self.assertRaises(TechnicalRoundFault) as caught:
            Backend.preflight_before_attempt(self.config, self.out, self.path, make_policy(),
                transport=interrupted, api_key="FIXTURE", guard=guard)
        self.assertNotIsInstance(caught.exception, PermanentTechnicalFault)
        self.assertEqual(self.rows(), {})

    def test_abandoned_context_tamper_and_parent_path_escape_block_without_post(self):
        backend = self.backend([])
        self.store.abandon_attempt({"technical": True, "fault_class": "offline_storage_failure"})
        with self.assertRaises(BudgetError):
            backend.run_actor(self.request())
        self.assertEqual(self.transport.posts_exact, [])
        with self.assertRaises(BudgetError):
            funding._safe(self.out / ".." / "outside", root=self.out)

    def test_max_unicode_view_rejected_history_and_two_full_valid_briefs_fit_new_native(self):
        brief = "🧪" * 1200
        backend = self.backend([{"action": "create_visual", "arguments": {"prompt": "🧪" * 2000}},
            {"action": "create_visual", "arguments": {"prompt": brief}},
            {"action": "revise_visual", "arguments": {"prompt": brief}},
            {"action": "accept_visual", "arguments": {}}, {"decision": "P2"}])
        request = self.request()
        request["messages"][1]["content"] = "🧪" * 27500
        result = backend.run_actor(request)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(len(self.transport.posts_exact), 7)
        self.assertIn("rejected_history_summary", result["actions"][0])
        self.assertEqual(len([a for a in result["actions"] if a.get("generation_attempt")]), 2)
        for endpoint, body in self.transport.posts_exact:
            if endpoint == "/chat/completions":
                payload = json.loads(body)
                self.assertEqual(payload["temperature"], 1.0)
                self.assertEqual(payload["max_tokens"], 8192)

    def test_successful_response_is_saved_before_receipt_callback_failure(self):
        backend = self.backend([{"action": "decline_visual", "arguments": {"reason": "No image."}}])
        native = self.transport
        class ReceiptFault(dict):
            reads = 0
            def get(self, key, default=None):
                if key == "cost":
                    self.reads += 1
                    if self.reads > 1:
                        raise TypeError("sensitive receipt fixture")
                return super().get(key, default)
        def wrapped(method, path, body):
            response = native(method, path, body)
            if method == "POST":
                response["usage"] = ReceiptFault(response["usage"])
            return response
        backend.provider._transport = wrapped
        with self.assertRaises(TechnicalRoundFault):
            backend.run_actor(self.request())
        row = next(iter(self.rows().values()))
        self.assertEqual(row["status"], "uncertain")
        stem = funding._sha(row["request_id"].encode()) + ".json"
        response = json.loads((backend.evidence_dir / "observed_responses" / stem).read_text())
        diagnostic = json.loads((backend.evidence_dir / "callback_diagnostics" / stem).read_text())
        self.assertTrue(response["response"]["choices"])
        self.assertFalse(response["automatic_recovery_authorized"])
        self.assertEqual(diagnostic["exception_class"], "TypeError")
        self.assertNotIn("sensitive", json.dumps(diagnostic))
        self.assertIsNone(row["actual_units"])

    def test_auth_error_is_a_hard_hold_without_arbitrary_error_body(self):
        def wrap(native):
            def wrapped(method, path, body):
                if method == "POST":
                    return {"error": {"code": 401, "message": "sensitive authorization body"}}
                return native(method, path, body)
            return wrapped
        backend = self.backend([], wrap)
        with self.assertRaises(PermanentTechnicalFault) as caught:
            backend.run_actor(self.request())
        self.assertFalse(caught.exception.recoverable)
        self.assertEqual(caught.exception.fault["http_status"], 401)
        self.assertIsNone(next(iter(self.rows().values()))["response_json"])

    def test_hard_exit_pending_needs_actual_callback_owner_dead_process_and_current_lock(self):
        backend = self.backend([])
        counter = self.root / "simulated_POST_count.txt"
        operation = self.out / "checkpoints_restart04/operations/hard_exit.started.json"
        operation.parent.mkdir(parents=True, exist_ok=True)
        def interrupted(method, path, body):
            if method == "POST":
                counter.write_text("one simulated POST")
                os._exit(17)
            return self.transport(method, path, body)
        backend.provider._transport = interrupted
        child = os.fork()
        if child == 0:
            marker = {"operation": "advance", "sequence": 1, "committed_rounds": 6,
                "active_attempt": self.context, "producer_pid": os.getpid(),
                "producer_process_start_ticks": funding.process_start_ticks(os.getpid())}
            marker["record_hash"] = funding._sha(original.canonical(marker).encode())
            operation.write_text(original.canonical(marker) + "\n")
            backend.run_actor(self.request())
            os._exit(99)
        _, status = os.waitpid(child, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 17)
        before = self.rows()
        row = next(iter(before.values()))
        self.assertEqual(row["status"], "pending")
        self.assertEqual(counter.read_text(), "one simulated POST")
        old = json.loads(operation.read_text())
        proof_path = operation.with_name("hard_exit.crash_proof.json")
        proof = {"operation_marker_path": str(operation), "operation_marker_sha256": file_sha(operation),
            "old_producer_pid": child, "old_process_start_ticks": old["producer_process_start_ticks"],
            "recovered_by_pid": os.getpid(), "recovered_process_start_ticks": funding.process_start_ticks(os.getpid()),
            "exclusive_producer_lock_reacquired": True}
        proof_path.write_text(original.canonical(proof) + "\n")
        fault = TechnicalRoundFault("interrupted_paid_callback").fault
        fault["pending_crash_proof_path"] = str(proof_path)
        self.store.abandon_attempt(fault)
        abandoned = self.store.abandonment_path(7, 2)
        kwargs = dict(aggregate_path=self.path, abandonment_path=abandoned,
            expected_abandonment_sha256=file_sha(abandoned), pending_crash_proof_path=proof_path)
        with self.assertRaisesRegex(BudgetError, "exclusive lock"):
            funding.seal_abandoned_attempt(self.out, self.context, fault, **kwargs)
        owner_path = Path(self.context["working_root"]) / "provider_evidence/paid_callback_owners" / (funding._sha(row["request_id"].encode()) + ".json")
        owner_raw = owner_path.read_bytes()
        owner_path.unlink()
        with producer_lock(self.out), self.assertRaises((OSError, BudgetError)):
            funding.seal_abandoned_attempt(self.out, self.context, fault, **kwargs)
        owner_path.write_bytes(owner_raw + b" ")
        with producer_lock(self.out), self.assertRaises(BudgetError):
            funding.seal_abandoned_attempt(self.out, self.context, fault, **kwargs)
        owner_path.write_bytes(owner_raw)
        with producer_lock(self.out):
            funding.seal_abandoned_attempt(self.out, self.context, fault, **kwargs)
        self.assertEqual(self.rows(), before)
        self.ledger.assert_ready()
        self.assertEqual(self.ledger.summary()["reserved_unresolved_usd"], row["reserved_units"] / 1e9)
        self.assertEqual(counter.read_text(), "one simulated POST")
        with self.assertRaises(BudgetError):
            backend.run_actor(self.request())

    def test_same_live_producer_cannot_authorize_a_pending_row(self):
        backend = self.backend([])
        native = self.transport
        def interrupted(method, path, body):
            if method == "POST":
                raise http.client.IncompleteRead(b"", 1024)
            return native(method, path, body)
        backend.provider._transport = interrupted
        operation = self.out / "checkpoints_restart04/operations/live.started.json"
        operation.parent.mkdir(parents=True, exist_ok=True)
        marker = {"producer_pid": os.getpid(), "producer_process_start_ticks": funding.process_start_ticks(os.getpid()), "operation": "advance"}
        marker["record_hash"] = funding._sha(original.canonical(marker).encode())
        operation.write_text(original.canonical(marker) + "\n")
        with patch.object(self.ledger, "uncertain", side_effect=OSError("fixture database write failed")):
            with self.assertRaises(TechnicalRoundFault):
                backend.run_actor(self.request())
        row = next(iter(self.rows().values()))
        self.assertEqual(row["status"], "pending")
        proof_path = operation.with_name("live.crash_proof.json")
        proof_path.write_text(original.canonical({"operation_marker_path": str(operation), "operation_marker_sha256": file_sha(operation),
            "old_producer_pid": os.getpid(), "old_process_start_ticks": funding.process_start_ticks(os.getpid()),
            "recovered_by_pid": os.getpid(), "recovered_process_start_ticks": funding.process_start_ticks(os.getpid()),
            "exclusive_producer_lock_reacquired": True}) + "\n")
        fault = TechnicalRoundFault("interrupted_paid_callback").fault
        self.store.abandon_attempt(fault)
        abandoned = self.store.abandonment_path(7, 2)
        with producer_lock(self.out), self.assertRaisesRegex(BudgetError, "conclusively stopped"):
            funding.seal_abandoned_attempt(self.out, self.context, fault, aggregate_path=self.path,
                abandonment_path=abandoned, expected_abandonment_sha256=file_sha(abandoned), pending_crash_proof_path=proof_path)
        self.assertEqual(next(iter(self.rows().values())), row)

    def test_cached_genuine_free_preflight_avoids_second_metadata_gets(self):
        native = NativeWireTransport(self.ledger, [])
        gets = []
        def watched(method, path, body):
            if method == "GET":
                gets.append(path)
            return native(method, path, body)
        free_guard = funding.preflight_guard(self.path, global_out=self.out, ledger=self.ledger)
        report = Backend.preflight_before_attempt(self.config, self.out, self.path, make_policy(),
            transport=watched, api_key="FIXTURE", guard=free_guard)
        count = len(gets)
        backend = Backend(self.config, self.context["working_root"], self.path, global_out=self.out,
            attempt_context=self.context, policy=make_policy(), transport=watched,
            api_key="FIXTURE", guard=self.guard, cached_preflight=report)
        self.assertEqual(len(gets), count)
        self.assertEqual(backend.provider._preflight["checked_at_unix"], report["checked_at_unix"])
        self.assertEqual(self.rows(), {})


if __name__ == "__main__":
    unittest.main()
