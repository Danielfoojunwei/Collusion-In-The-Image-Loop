from copy import deepcopy
import http.client
import json
from pathlib import Path
import tempfile
import unittest

from visual_collusion.budget import BudgetLedger, BudgetError, PendingPaidRequest
from experiments.triad_emergent_v9 import backend as original
from experiments.triad_emergent_v9 import funding as original_funding
from experiments.triad_emergent_v9.coordinator import file_sha
from experiments.triad_emergent_v9.prompts import actor_system
from experiments.triad_repaired_v8.tests.test_backend import NativeWireTransport, RawAnswer
from experiments.triad_v9_protocol_repair02.backend import Backend as ParentBackend, make_policy as parent_policy
from experiments.triad_v9_protocol_repair03.authority import Authority, raw_rows, REPAIR
from experiments.triad_v9_protocol_repair03.funding import FundingLedger, create_guard
from experiments.triad_v9_protocol_repair03.backend import Backend, make_policy, LOSS_FEEDBACK, OLD_LOSS_FEEDBACK, TOKEN_BUDGET_NOTICE


class LostSlotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.out, self.path = self.root / "run", self.root / "budget.sqlite3"
        BudgetLedger(self.path)
        ledger = original_funding.FundingLedger(self.path)
        guard = original_funding.create_guard(self.path, ledger=ledger)
        self.config = json.loads((Path(__file__).parents[2] / "triad_emergent_v9/config.json").read_text())
        self.request = {"request_id": "episode.r07.A_IMAGE_AND_VOTE", "round": 7, "role": "A", "mode": "visual_vote",
            "messages": [{"role": "system", "content": actor_system("A", "visual_vote")},
                         {"role": "user", "content": "Authoritative current task and retained own history."}], "image_refs": {}}
        native = NativeWireTransport(ledger, [])
        def interrupted(method, path, body):
            if method == "POST":
                raise http.client.IncompleteRead(b"", 4096)
            return native(method, path, body)
        parent = ParentBackend(self.config, self.out, self.path, policy=parent_policy(),
            transport=interrupted, api_key="OFFLINE_FIXTURE_NOT_A_SECRET", guard=guard)
        with self.assertRaises(PendingPaidRequest):
            parent.run_actor(self.request)
        self.old_rows = raw_rows(self.path)
        self.lost_id = "triad_emergent_v9:" + self.request["request_id"] + ".visual1"
        self.row = self.old_rows[self.lost_id]
        self.declaration_path = self.out / "analysis/repair03/LOST_NATIVE_SLOT.json"
        self.declaration_path.parent.mkdir(parents=True)
        archive = self.out / "provider_evidence/transport_requests" / (original.sha((self.request["request_id"] + ".visual1").encode()) + ".json")
        self.record = {"repair": REPAIR, "out": str(self.out), "row": self.row, "slot": 1,
            "row_sha256": original.sha(original.canonical(self.row).encode()), "no_retry": True,
            "unknown_charge_retained": True, "transport_archive": str(archive.relative_to(self.out)),
            "wire_body_sha256": json.loads(archive.read_text())["mapping"]["wire_body_sha256"],
            "bound_files": {str(archive.relative_to(self.out)): file_sha(archive)}}
        self.write_authority()

    def tearDown(self):
        self.temp.cleanup()

    def write_authority(self):
        self.declaration_path.write_text(original.canonical(self.record) + "\n")
        self.authority = Authority(self.declaration_path, file_sha(self.declaration_path))

    def backend(self, actions):
        self.ledger = FundingLedger(self.path, authority=self.authority)
        self.guard = create_guard(self.path, self.out / "provider_evidence", self.authority, ledger=self.ledger)
        self.transport = NativeWireTransport(self.ledger, actions)
        return Backend(self.config, self.out, self.path, policy=make_policy(), transport=self.transport,
            api_key="OFFLINE_FIXTURE_NOT_A_SECRET", guard=self.guard, authority=self.authority)

    def test_exact_lost_slot_zero_post_remaining_original_slots_and_two_images(self):
        backend = self.backend([{"action": "create_visual", "arguments": {"prompt": "First actual image."}},
            {"action": "revise_visual", "arguments": {"prompt": "Second actual image."}},
            {"action": "accept_visual", "arguments": {}}, {"decision": "P2"}])
        result = backend.run_actor(self.request)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(raw_rows(self.path)[self.lost_id], self.row)
        self.assertEqual(len(self.transport.posts_exact), 6)
        chats = [json.loads(body) for path, body in self.transport.posts_exact if path == "/chat/completions"]
        self.assertIn("slot2/4", chats[0]["messages"][-1]["content"])
        self.assertTrue(any(m.get("content") == LOSS_FEEDBACK for m in chats[0]["messages"]))
        self.assertFalse(any(m.get("content") == OLD_LOSS_FEEDBACK for m in chats[0]["messages"]))
        self.assertFalse(any(m.get("role") == "assistant" for m in chats[0]["messages"]))
        for chat in chats:
            self.assertEqual(chat["temperature"], 1.0)
            self.assertEqual(chat["max_tokens"], 8192)
            self.assertEqual(chat["reasoning"], {"effort": "high", "exclude": False})
            self.assertEqual(chat["messages"][0]["content"].count(TOKEN_BUDGET_NOTICE), 1)
        loss = result["actions"][0]
        self.assertEqual(loss["kind"], "transport_slot_loss")
        self.assertNotIn("validation_error", loss)
        self.assertIn("transport_error", loss)
        event = loss["receipt"]
        self.assertEqual(event["reservation_status"], "uncertain")
        self.assertGreater(event["reserved_usd"], 0)
        self.assertIsNone(event["actual_cost_usd"])
        self.assertNotIn("cost_usd", event)
        self.assertFalse(event["response_observed"])
        self.assertEqual(backend.provider._preflight["sampling"]["effective_protocol_repair"], make_policy())
        before = len(self.transport.posts_exact)
        self.assertEqual(backend.run_actor(self.request), result)
        self.assertEqual(len(self.transport.posts_exact), before)
        with self.assertRaisesRegex(original.BackendError, "cannot replay"):
            backend.provider.native_call(self.request["request_id"] + ".visual1", [], [], 1.0)
        self.assertEqual(len(self.transport.posts_exact), before)

    def test_reserve_stays_counted_and_any_other_unknown_blocks(self):
        self.backend([])
        self.ledger.assert_ready()
        summary = self.ledger.summary()
        self.assertEqual(summary["reserved_unresolved_usd"], self.row["reserved_units"] / 1e9)
        other = "triad_emergent_v9:episode.r08.A_IMAGE_AND_VOTE.visual1"
        self.ledger.reserve(other, "chat", original.MODEL, "a" * 64, .01)
        self.ledger.uncertain(other)
        with self.assertRaises(PendingPaidRequest):
            self.ledger.assert_ready()
        self.assertEqual(raw_rows(self.path)[self.lost_id], self.row)

    def test_bad_authority_or_wire_drift_blocks_without_post(self):
        backend = self.backend([])
        changed = deepcopy(self.request)
        changed["messages"][1]["content"] += " changed"
        with self.assertRaises(ValueError):
            backend.run_actor(changed)
        self.assertEqual(self.transport.posts_exact, [])
        self.declaration_path.write_bytes(self.declaration_path.read_bytes() + b" ")
        with self.assertRaises(BudgetError):
            self.ledger.assert_ready()
        self.assertEqual(raw_rows(self.path)[self.lost_id], self.row)

    def test_path_escape_and_another_run_are_rejected(self):
        self.record["bound_files"]["../outside"] = "a" * 64
        with self.assertRaisesRegex(BudgetError, "relative"):
            self.write_authority()
        self.record["bound_files"].pop("../outside")
        self.write_authority()
        ledger = FundingLedger(self.path, authority=self.authority)
        guard = create_guard(self.path, self.out / "provider_evidence", self.authority, ledger=ledger)
        with self.assertRaisesRegex(original.BackendError, "another run"):
            Backend(self.config, self.root / "other", self.path, policy=make_policy(), transport=lambda *a: None,
                api_key="FIXTURE", guard=guard, authority=self.authority)

    def test_future_incomplete_read_is_safe_diagnosed_and_remains_uncertain(self):
        backend = self.backend([])
        prior = backend.provider._transport
        def interrupted(method, path, body):
            if method == "POST":
                raise http.client.IncompleteRead(b"sensitive provider body", 1024)
            return prior(method, path, body)
        backend.provider._transport = interrupted
        with self.assertRaises(PendingPaidRequest):
            backend.provider.native_call("episode.r08.A_IMAGE_AND_VOTE.visual1", [{"role": "system", "content": "fixture"}], original.legal_visual_tools(1, 0, False), 1.0)
        diagnostic = backend.provider.last_transport_diagnostic
        self.assertEqual(diagnostic["exception_class"], "IncompleteRead")
        self.assertEqual(diagnostic["category"], "unclassified_callback_failure")
        self.assertNotIn("sensitive", json.dumps(diagnostic))
        self.assertNotIn("body", json.dumps(diagnostic).replace("wire_body", ""))
        row = raw_rows(self.path)["triad_emergent_v9:episode.r08.A_IMAGE_AND_VOTE.visual1"]
        self.assertEqual(row["status"], "uncertain")
        self.assertIsNone(row["response_json"])
        with self.assertRaises(PendingPaidRequest):
            self.ledger.assert_ready()

    def test_valid_response_survives_later_receipt_callback_failure_without_fake_completion(self):
        backend = self.backend([{"action": "decline_visual", "arguments": {"reason": "No image."}}])
        native = backend.provider._transport
        class ReceiptFaultUsage(dict):
            reads = 0
            def get(self, key, default=None):
                if key == "cost":
                    self.reads += 1
                    if self.reads > 1:
                        raise TypeError("sensitive fixture callback text")
                return super().get(key, default)
        def receipt_fault(method, path, body):
            response = native(method, path, body)
            if method == "POST":
                response["usage"] = ReceiptFaultUsage(response["usage"])
            return response
        backend.provider._transport = receipt_fault
        local = "episode.r08.A_IMAGE_AND_VOTE.visual1"
        with self.assertRaises(PendingPaidRequest):
            backend.provider.native_call(local, [{"role": "system", "content": "fixture"}], original.legal_visual_tools(1, 0, False), 1.0)
        stem = original.sha(("triad_emergent_v9:" + local).encode()) + ".json"
        observed = json.loads((backend.evidence_dir / "observed_responses" / stem).read_text())
        diagnostic = json.loads((backend.evidence_dir / "callback_diagnostics" / stem).read_text())
        self.assertTrue(observed["response"]["choices"])
        self.assertGreaterEqual(observed["response"]["usage"]["cost"], 0)
        self.assertFalse(observed["automatic_recovery_authorized"])
        self.assertEqual(diagnostic["exception_class"], "TypeError")
        self.assertNotIn("sensitive", json.dumps(diagnostic))
        row = raw_rows(self.path)["triad_emergent_v9:" + local]
        self.assertEqual(row["status"], "uncertain")
        self.assertIsNone(row["actual_units"])
        self.assertIsNone(row["response_json"])
        self.assertEqual(raw_rows(self.path)[self.lost_id], self.row)


if __name__ == "__main__":
    unittest.main()
