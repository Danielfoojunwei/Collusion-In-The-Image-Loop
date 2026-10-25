"""Offline coordinator and real funding-guard fixtures; no external API calls."""
from copy import deepcopy
import io
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from visual_collusion.budget import BudgetLedger, PendingPaidRequest
from research_plans.triad_committed_sender_v10.commitment import build_cases
from .. import funding, reviewer
from ..backend import BackendError, TechnicalFault
from ..coordinator import Coordinator, CoordinatorError, producer_lock
from ..engine import Engine, EngineError, PendingOperation, canonical, file_sha, recorded, save_once
from .test_engine import mock_result, review_fixture


def locked_child(out, queue):
    try:
        with producer_lock(out):
            queue.put("incorrectly_acquired")
    except CoordinatorError as error:
        queue.put(error.code)


class FixtureBackend:
    """Scripted decisions and a real local ledger; POST callback is in memory."""
    def __init__(self, harness, config, out, aggregate_path, *, guard, preflight=False):
        self.harness, self.config, self.out, self.guard = harness, config, out, guard

    def preflight(self):
        self.harness.preflights += 1
        return {"status": "ready", "paid_requests_made": 0,
                "key": {"remaining_usd": self.harness.provider_remaining}}

    def perform(self, request):
        self.harness.performs.append(deepcopy(request))
        action = self.harness.failures.pop(request["request_id"], None)
        if action == "permanent":
            raise TechnicalFault("authentication_hold", request["request_id"], permanent=True)
        if action == "integrity":
            raise BackendError("SCRIPTED integrity hold")
        if self.harness.mutate_before_post:
            self.harness.mutate_before_post()
        def post(body, binding):
            self.harness.posts.append(deepcopy(binding))
            if action == "transport":
                raise TimeoutError("SCRIPTED local callback failure; no API used")
            return {"response": {"offline_fixture": True},
                    "receipt": {**binding, "cost_usd": .001}}
        try:
            self.guard.execute(request["request_id"] + ".fixture", "chat", funding.MODELS["chat"],
                {"model": funding.MODELS["chat"], "offline_fixture": request["request_id"]}, .02, post)
        except PendingPaidRequest:
            raise TechnicalFault("api_transport_failure", request["request_id"]) from None
        return mock_result(request, self.out, self.harness.plan,
                           invalid=request["request_id"] in self.harness.invalid)


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.out = self.root / "run"
        self.out.mkdir()
        self.path = self.root / "budget.sqlite3"
        BudgetLedger(self.path, 40)
        self.ledger = funding.FundingLedger(self.path, run_root=self.out)
        self.plan = build_cases()
        self.config = {"revision": "triad_committed_sender_v10", "offline_fixture": True,
                       "budget": {"predeclared_reservations": {
                           "minimum_key_remaining_before_launch_usd": "19.276096"}}}
        self.config_path = self.out / "config.json"
        self.plan_path = self.out / "plan.json"
        self.config_path.write_text(canonical(self.config))
        self.plan_path.write_text(canonical(self.plan))
        for relative in ("src/fixture.py", "experiments/fixture.py", "scripts/codec.py",
                         "research_plans/triad_committed_sender_v10/fixture.py"):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# SCRIPTED OFFLINE fixture\n")
        self.protocol_path = self.out / "protocol.md"
        self.protocol_path.write_text("SCRIPTED OFFLINE protocol, not live evidence.\n")
        self.gate_path = self.out / "gate.json"
        self.write_freeze()
        self.performs, self.posts, self.failures, self.invalid = [], [], {}, set()
        self.preflights = 0
        self.provider_remaining = 40
        self.mutate_before_post = None
        self.coordinator = self.new_coordinator()

    def write_freeze(self, *, omit=None, gate_status="passed"):
        files = {str(p.relative_to(self.root)): file_sha(p)
                 for pattern in ("src/**/*.py", "experiments/**/*.py", "scripts/**/*.py",
                                 "research_plans/triad_committed_sender_v10/**/*.py")
                 for p in self.root.glob(pattern) if p.is_file()}
        if omit: files.pop(omit)
        gate = {"status": gate_status, "evidence_origin": "offline_scripted_validation_not_live_results",
                "tests_passed": 1, "tests_failed": 0, "source_files": files,
                "config_sha256": file_sha(self.config_path), "plan_sha256": file_sha(self.plan_path),
                "test_log_sha256": "f" * 64}
        self.gate_path.write_text(canonical(gate))
        freeze = {"revision": "triad_committed_sender_v10", "source_root": str(self.root),
                  "source_files": files}
        for kind, path in (("config", self.config_path), ("plan", self.plan_path),
                           ("protocol", self.protocol_path), ("gate", self.gate_path)):
            freeze.update({kind + "_path": str(path), kind + "_sha256": file_sha(path)})
        (self.out / "freeze.json").write_text(canonical(recorded(freeze)))

    def factory(self, *args, **kwargs):
        return FixtureBackend(self, *args, **kwargs)

    def new_coordinator(self):
        return Coordinator(self.out, backend_factory=self.factory,
                           aggregate_path=self.path, ledger=self.ledger,
                           expected_source_root=self.root)

    def test_one_producer_lock_and_no_unguarded_mutation(self):
        with self.assertRaises(CoordinatorError): self.coordinator.init()
        with self.coordinator.locked():
            self.coordinator.init()
            ctx = multiprocessing.get_context("fork")
            queue = ctx.Queue()
            process = ctx.Process(target=locked_child, args=(self.out, queue))
            process.start()
            self.assertEqual(queue.get(timeout=5), "producer_already_running")
            process.join(timeout=5)
            self.assertEqual(process.exitcode, 0)
        self.assertFalse(funding.owns_lock(self.out))
        self.assertEqual(self.posts, [])

    def test_all_scripts_must_be_frozen_and_failed_gate_rejected(self):
        for omit, gate, expected in (("scripts/codec.py", "passed", "freeze_source_inventory_incomplete"),
                                     (None, "failed", "offline_gate_not_passed_for_exact_frozen_inputs")):
            with self.subTest(omit=omit, gate=gate):
                self.write_freeze(omit=omit, gate_status=gate)
                coordinator = self.new_coordinator()
                with coordinator.locked(), self.assertRaises(CoordinatorError) as raised:
                    coordinator.init()
                self.assertEqual(raised.exception.code, expected)
        self.assertEqual(self.posts, [])

    def test_changed_source_blocks_actual_guard_callback_before_post(self):
        def change(): (self.root / "scripts/codec.py").write_text("# changed after begin\n")
        self.mutate_before_post = change
        with self.coordinator.locked():
            self.coordinator.init()
            with self.assertRaises(CoordinatorError) as raised:
                self.coordinator.advance(maximum_actor_stages=1)
            self.assertEqual(raised.exception.code, "frozen_source_bytes_changed")
            self.assertEqual(self.posts, [])
            self.assertEqual(self.ledger.namespace_summary()["request_count"], 0)

    def test_initial_full_bound_only_then_per_request_budget(self):
        with self.coordinator.locked():
            self.coordinator.init()
            self.provider_remaining = 19
            with self.assertRaises(CoordinatorError) as raised: self.coordinator.preflight()
            self.assertEqual(raised.exception.code, "provider_remaining_cannot_cover_frozen_bound")
            self.assertEqual(self.posts, [])
            self.provider_remaining = 40
            self.coordinator.advance(maximum_actor_stages=1)
            self.assertTrue((self.out / "launch_preflight.json").is_file())
            self.provider_remaining = .05
            self.coordinator.preflight()  # full19.276 bound is launch-only
            self.assertEqual(len(self.posts), 1)
        reopened = self.new_coordinator()
        with reopened.locked():
            reopened.advance(maximum_actor_stages=1)
            self.assertEqual(len(self.posts), 2)

    def test_reviewer_pause_actual_first_report_then_post_logs_and_checkpoint(self):
        with self.coordinator.locked():
            state = self.coordinator.advance()
            self.assertEqual(state["status"], "awaiting_review")
            self.assertEqual(state["actor_stages"], 5)
            self.assertEqual(len(self.posts), 5)
            repeated = self.coordinator.advance()
            self.assertEqual(repeated["review_job"], state["review_job"])
            self.assertEqual(len(self.posts), 5)
            # Host bridge is exercised separately; this is labeled synthetic
            # first review, not coordinator-authored experimental feedback.
            envelope = review_fixture(self.coordinator.engine)
            source = self.out / "reviewer_packets" / envelope["job_id"] / "first_review_envelope.json"
            ingested = self.coordinator.ingest_review(source)
            self.assertEqual(ingested["review_status"], "valid")
            state = self.coordinator.advance(through_round=1)
            self.assertEqual(state["committed_rounds"], 1)
            self.assertEqual(len(self.posts), 9)
            for request in self.performs[5:]:
                strings = [part["text"] for message in request["messages"]
                           if isinstance(message.get("content"), list)
                           for part in message["content"] if part.get("type") == "text"]
                self.assertIn(envelope["raw_text"], strings)
        reopened = self.new_coordinator()
        with reopened.locked():
            state = reopened.init()
            self.assertEqual(state["committed_rounds"], 1)
            self.assertEqual(len(self.posts), 9)

    def test_native_invalidity_is_one_observation_not_technical_reroll(self):
        self.invalid.add("r01.a01.A_IMAGE")
        with self.coordinator.locked():
            state = self.coordinator.advance()
            self.assertEqual(state["active_attempt"]["attempt"], 1)
            self.assertEqual(state["technical_recoveries"], [])
            self.assertEqual(len(self.posts), 5)
            self.assertEqual(self.coordinator.engine.outcome(1, "A_IMAGE")["result"]["component_status"]["image"], "model_invalid")
            self.assertFalse(any((self.out / "attempts").glob("r*/a*/abandonment.json")))

    def test_typed_transport_fault_restarts_whole_round_retains_unknown_reserve(self):
        self.failures["r01.a01.B_IMAGE"] = "transport"
        with self.coordinator.locked():
            state = self.coordinator.advance()
            self.assertEqual(state["status"], "awaiting_review")
            self.assertEqual(state["active_attempt"]["attempt_id"], "r01.a02")
            self.assertEqual(len(self.posts), 8)  #3abandoned +5 fresh stages
            self.assertEqual(state["technical_recoveries"][0]["fault"]["code"], "api_transport_failure")
            self.assertEqual(self.ledger.namespace_summary()["unresolved_reserved_usd"], .02)
            self.ledger.assert_ready()
            source = self.out / "attempts/r01/a01/abandonment.json"
            self.assertTrue(source.is_file())
            packet = json.loads(Path(state["review_job"]["packet_path"]).read_text())
            self.assertNotIn("a01", canonical(packet))
            before = self.performs[0]["messages"]
            after = next(p["messages"] for p in self.performs if p["request_id"] == "r01.a02.A_IMAGE")
            self.assertEqual(before, after)
            self.assertEqual(len({p["request_id"] for p in self.performs}), len(self.performs))

    def test_technical_attempt_limit_three_never_four(self):
        self.failures.update({f"r01.a{i:02d}.A_IMAGE": "transport" for i in range(1, 4)})
        with self.coordinator.locked():
            with self.assertRaisesRegex(EngineError, "three technical-attempt limit"):
                self.coordinator.advance()
            self.assertEqual(len(self.posts), 3)
            self.assertFalse((self.out / "attempts/r01/a04").exists())
            self.assertAlmostEqual(self.ledger.namespace_summary()["unresolved_reserved_usd"], .06)

    def test_permanent_and_integrity_holds_survive_reopen(self):
        self.failures["r01.a01.A_IMAGE"] = "permanent"
        with self.coordinator.locked():
            with self.assertRaises(CoordinatorError): self.coordinator.advance()
            self.assertEqual(len(self.posts), 0)
            self.assertEqual(len(self.coordinator.status()["durable_holds"]), 1)
        reopened = self.new_coordinator()
        with reopened.locked():
            with self.assertRaises(CoordinatorError) as raised: reopened.advance()
            self.assertEqual(raised.exception.code, "durable_integrity_or_permanent_hold_requires_diagnosis")
            self.assertFalse((self.out / "attempts/r01/a02").exists())

    def test_untyped_integrity_failure_is_not_converted_to_transport_retry(self):
        self.failures["r01.a01.A_IMAGE"] = "integrity"
        with self.coordinator.locked():
            with self.assertRaises(BackendError): self.coordinator.advance()
        reopened = self.new_coordinator()
        with reopened.locked():
            with self.assertRaises(CoordinatorError): reopened.advance()
            self.assertEqual(len(self.posts), 0)
            self.assertFalse((self.out / "attempts/r01/a02").exists())

    def test_all_twelve_rounds_resume_review_after_each_then_finish(self):
        with self.coordinator.locked():
            for number in range(1, 13):
                state = self.coordinator.advance(through_round=number)
                self.assertEqual(state["status"], "awaiting_review")
                self.assertEqual(state["review_job"]["round"], number)
                envelope = review_fixture(self.coordinator.engine)
                source = self.out / "reviewer_packets" / envelope["job_id"] / "first_review_envelope.json"
                self.coordinator.ingest_review(source)
                state = self.coordinator.advance(through_round=number)
                self.assertEqual(state["committed_rounds"], number)
            self.assertEqual(state["status"], "complete")
            self.assertEqual(len(self.posts), 108)
            self.assertEqual(self.ledger.namespace_summary()["request_count"], 108)
            self.assertEqual(len(list((self.out / "checkpoints").glob("*.json"))), 12)
            self.assertFalse((self.out / "attempts/r13").exists())
            before = len(self.posts)
            self.coordinator.advance()
            self.assertEqual(len(self.posts), before)

    def test_interrupted_dead_producer_restarts_without_reposting_identity(self):
        with self.coordinator.locked():
            self.coordinator.init()
            request = self.coordinator.engine.actor_request()
            with patch("experiments.triad_committed_sender_v10.engine.process_start_ticks", return_value="DEAD_FIXTURE"):
                self.coordinator.engine.begin_actor_request(request)
        reopened = self.new_coordinator()
        with reopened.locked():
            state = reopened.advance(maximum_actor_stages=1)
            self.assertEqual(state["active_attempt"]["attempt_id"], "r01.a02")
            self.assertEqual(state["technical_recoveries"][0]["fault"]["code"], "producer_interrupted_during_actor_operation")
            self.assertEqual([p["request_id"] for p in self.performs], ["r01.a02.A_IMAGE"])

    def test_original_operation_still_alive_cannot_be_reposted(self):
        with self.coordinator.locked():
            self.coordinator.init()
            request = self.coordinator.engine.actor_request()
            self.coordinator.engine.begin_actor_request(request)
            with self.assertRaises(PendingOperation): self.coordinator.advance()
            self.assertEqual(self.performs, [])

    def test_jsonline_session_keeps_state_and_rejects_unknown_options(self):
        stream = io.StringIO("\n".join([canonical({"command": "init"}),
            canonical({"command": "advance", "maximum_actor_stages": 1}),
            canonical({"command": "status", "unexpected": True}),
            canonical({"command": "status"}), canonical({"command": "quit"})]) + "\n")
        output = io.StringIO()
        self.coordinator.serve(stream, output)
        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(rows), 5)
        self.assertTrue(rows[0]["ok"])
        self.assertEqual(rows[1]["result"]["actor_stages"], 1)
        self.assertEqual(rows[2]["error"]["code"], "unknown_command_or_option")
        self.assertEqual(rows[3]["result"]["active_attempt"]["attempt_id"], "r01.a01")
        self.assertEqual(rows[4]["result"]["status"], "bye")
        self.assertFalse(funding.owns_lock(self.out))

    def test_production_cannot_redirect_original_budget(self):
        with self.assertRaises(CoordinatorError) as raised:
            Coordinator(self.out, aggregate_path=self.path)
        self.assertEqual(raised.exception.code, "original_aggregate_required")


if __name__ == "__main__":
    unittest.main()
