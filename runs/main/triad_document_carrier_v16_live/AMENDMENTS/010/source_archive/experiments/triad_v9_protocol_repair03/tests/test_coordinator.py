from copy import deepcopy
import http.client
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from visual_collusion.budget import BudgetLedger, PendingPaidRequest
from experiments.triad_emergent_v9 import coordinator as base
from experiments.triad_emergent_v9 import funding as original_funding
from experiments.triad_emergent_v9.engine import Engine
from experiments.triad_emergent_v9.offline_gate import test_manifest
from experiments.triad_emergent_v9.tests.test_engine import ScriptedBackend
from experiments.triad_repaired_v8.tests.test_backend import NativeWireTransport
from experiments.triad_v9_protocol_repair02 import coordinator as parent
from experiments.triad_v9_protocol_repair02.backend import Backend as ParentBackend, make_policy as parent_policy
from experiments.triad_v9_protocol_repair02.tests import test_coordinator as fixture
from experiments.triad_v9_protocol_repair03 import coordinator as repair
from experiments.triad_v9_protocol_repair03.authority import raw_rows, Authority
from experiments.triad_v9_protocol_repair03.backend import Backend, make_policy
from experiments.triad_v9_protocol_repair03.funding import FundingLedger, create_guard


class LostStageSealTests(unittest.TestCase):
    def setUp(self):
        fixture.SamplingSealTests.setUp(self)
        parent.seal(self.out, expected_parent_sha256=self.parent_sha, producer_exit_path=self.exit2,
                    activation_phrase=parent.ACTIVATION_PHRASE)
        self.config, self.plan = base.read_json(self.out / "config.json"), base.read_json(self.out / "plan.json")
        engine = Engine(self.out, self.config, self.plan, ScriptedBackend(self.out, self.plan))
        for _ in range(3):
            engine.run_next_actor()
        self.target = engine.next_stage()
        self.assertEqual(self.target["stage"], "A_IMAGE_AND_VOTE")
        self.request = engine.actor_request()
        folder = self.out / f"episode/rounds/r{self.target['round']:02d}"
        self.request_path = folder / "A_IMAGE_AND_VOTE.request.json"
        base.save_once(self.request_path, self.request)
        base.save_once(folder / "A_IMAGE_AND_VOTE.dispatch.json", {"request_id": self.request["request_id"],
            "request_hash": self.request["request_hash"], "request_file_sha256": base.file_sha(self.request_path), "one_dispatch": True})
        self.path = self.root / "paid.sqlite3"
        BudgetLedger(self.path)
        ledger = original_funding.FundingLedger(self.path)
        guard = original_funding.create_guard(self.path, ledger=ledger)
        native = NativeWireTransport(ledger, [])
        def interrupted(method, path, body):
            if method == "POST":
                raise http.client.IncompleteRead(b"", 4096)
            return native(method, path, body)
        backend = ParentBackend(self.config, self.out, self.path, policy=parent_policy(), transport=interrupted,
            api_key="OFFLINE_FIXTURE_NOT_A_SECRET", guard=guard)
        with self.assertRaises(PendingPaidRequest):
            backend.run_actor(self.request)
        self.raw = raw_rows(self.path)
        self.lost_id, self.lost = next(iter(self.raw.items()))
        self.rows.update({k: base.sha(base.canonical(v).encode()) for k, v in self.raw.items()})
        self.accounting.update({"request_count": len(self.raw), "pending_count": 0, "halt_reason": None})
        self.expected3 = base.file_sha(self.out / "freeze.json")
        self.proof = self.out / "analysis/transport_hold/PRODUCER_EXIT_AND_HOLD.json"
        base.save_once(self.proof, {"active_producer": False, "observed_exit_code": 0,
            "observed_final_host_status": "quitting", "producer_session": 59677,
            "next_stage": self.target, "terminal_main_stages": 8, "failed_request": self.lost,
            "freeze_sha256": self.expected3, "budget": deepcopy(self.accounting)})
        self.gate3 = self.root / "gate3.json"
        self.patches.extend([patch.object(base, "AGGREGATE", self.path), patch.object(repair, "GATE", self.gate3),
            patch.object(parent, "verify_repair", side_effect=AssertionError("No parent current-freeze verifier"))])
        for item in self.patches[-3:]:
            item.start()
        self.gate3.write_text(base.canonical({"status": "passed", "source_files": repair.source_manifest(),
            "test_files": test_manifest(), "parent_gate_sha256": base.file_sha(parent.GATE),
            "original_gate_sha256": base.file_sha(base.HERE / "WORKFLOW_GATE.json"),
            "config_source_sha256": base.file_sha(base.HERE / "config.json")}))

    def tearDown(self):
        fixture.SamplingSealTests.tearDown(self)

    def seal(self, activate=False):
        return repair.seal(self.out, expected_parent_sha256=self.expected3, producer_exit_path=self.proof,
            activation_phrase=repair.ACTIVATION_PHRASE if activate else None)

    def new_backend(self, actions):
        folder = self.out / "analysis/repair03"
        authority = Authority(folder / "LOST_NATIVE_SLOT.json", base.file_sha(folder / "LOST_NATIVE_SLOT.json"))
        ledger = FundingLedger(self.path, authority=authority)
        guard = create_guard(self.path, self.out / "provider_evidence", authority, ledger=ledger)
        self.transport = NativeWireTransport(ledger, actions)
        return Backend(self.config, self.out, self.path, policy=make_policy(), transport=self.transport,
            api_key="OFFLINE_FIXTURE_NOT_A_SECRET", guard=guard, authority=authority)

    def test_seal_dry_run_activation_preserves_parent_rows_dispatch_and_chain(self):
        before = {str(p): p.read_bytes() for p in self.out.rglob("*") if p.is_file()}
        self.assertEqual(self.seal()["status"], "repair03_ready_not_activated")
        self.assertFalse((self.out / "analysis/repair03").exists())
        result = self.seal(True)
        self.assertEqual(result["frontier"], self.target)
        for p, raw in before.items():
            if p != str(self.out / "freeze.json"):
                self.assertEqual(Path(p).read_bytes(), raw)
        self.assertEqual(raw_rows(self.path), self.raw)
        host = repair.Coordinator(self.out)
        self.assertEqual(host.verify()["engine"]["terminal_stages"], 8)
        self.assertEqual(host.status()["protocol_repair03"]["policy"], make_policy())
        host.report()
        report = base.read_json(self.out / "analysis/report.json")
        self.assertFalse(report["protocol_repair03"]["infrastructure_loss_is_model_behavior"])

    def test_preflight_failure_before_claim_leaves_recovery_available(self):
        self.seal(True)
        host = repair.Coordinator(self.out)
        with patch.object(host, "_backend", side_effect=ValueError("fixture free GET failed")):
            with self.assertRaisesRegex(ValueError, "free GET"):
                host.resume_lost_stage()
        self.assertFalse((self.out / "analysis/repair03/RESUMPTION_CLAIM.json").exists())
        self.assertEqual(raw_rows(self.path), self.raw)
        host.backend = self.new_backend([{"action": "decline_visual", "arguments": {"reason": "No image."}}, {"decision": "P2"}])
        outcome = host.resume_lost_stage()
        self.assertEqual(outcome["output"]["decision"], "P2")
        self.assertEqual(len(self.transport.posts_exact), 2)
        self.assertEqual(raw_rows(self.path)[self.lost_id], self.lost)

    def test_complete_stage_cache_after_commit_crash_recovers_zero_post(self):
        self.seal(True)
        host = repair.Coordinator(self.out)
        host.backend = self.new_backend([{"action": "decline_visual", "arguments": {"reason": "No image."}}, {"decision": "P2"}])
        with patch.object(Engine, "_commit", side_effect=OSError("fixture commit interrupted")):
            with self.assertRaises(OSError):
                host.resume_lost_stage()
        posts = len(self.transport.posts_exact)
        outcome = host.resume_lost_stage()
        self.assertEqual(outcome["output"]["decision"], "P2")
        self.assertEqual(len(self.transport.posts_exact), posts)
        self.assertEqual(host._engine().summary()["terminal_stages"], 9)
        self.assertEqual(raw_rows(self.path)[self.lost_id], self.lost)

    def test_new_paid_prefix_or_bad_proof_blocks_sealing(self):
        altered = base.read_json(self.proof)
        altered["observed_exit_code"] = 1
        self.proof.write_text(base.canonical(altered))
        with self.assertRaisesRegex(ValueError, "actual exited"):
            self.seal(True)
        self.assertFalse((self.out / "analysis/repair03/AMENDMENT.json").exists())

    def test_any_later_paid_stage_intent_blocks_first_slot_seal(self):
        import sqlite3
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO requests(request_id,kind,model,request_hash,reserved_units,status,created) VALUES (?,?,?,?,?,'uncertain',?)",
                (self.lost_id.removesuffix("visual1") + "visual2", "chat", self.lost["model"], "f" * 64, 1000, 1))
        proof = base.read_json(self.proof)
        proof["budget"]["request_count"] += 1
        self.proof.write_text(base.canonical(proof))
        with self.assertRaisesRegex(ValueError, "first native intent"):
            self.seal(True)
        self.assertFalse((self.out / "analysis/repair03/AMENDMENT.json").exists())

    def test_partial_stage_after_claim_stays_blocked_without_replay(self):
        self.seal(True)
        host = repair.Coordinator(self.out)
        host.backend = self.new_backend([])
        native = self.transport
        def interrupted(method, path, body):
            if method == "POST":
                raise http.client.IncompleteRead(b"", 4096)
            return native(method, path, body)
        host.backend.provider._transport = interrupted
        with self.assertRaises(PendingPaidRequest):
            host.resume_lost_stage()
        after = raw_rows(self.path)
        with self.assertRaisesRegex(ValueError, "already claimed"):
            host.resume_lost_stage()
        self.assertEqual(raw_rows(self.path), after)
        self.assertEqual(after[self.lost_id], self.lost)


if __name__ == "__main__":
    unittest.main()
