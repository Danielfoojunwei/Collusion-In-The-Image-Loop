"""Cross-component live-boundary fixture; no scientific model or network call.

The real backend, wire codec, shared budget gate and live engine are exercised
together. All API/Codex answers are explicitly authored test doubles in a
temporary directory. Origin labels exercise the import contract, not findings.
"""
from copy import deepcopy
import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from experiments.pressure_interview_v1.budget_guard import SharedBudgetGuard
from experiments.pressure_interview_v1.engine import LiveExecutionBinding, LivePairEngine
from experiments.pressure_interview_v1.live_provider import LiveOpenRouterBackend
from experiments.pressure_interview_v1.planning import load_config, load_prompts
from experiments.pressure_interview_v1.tests.test_live_provider import ScriptedWireTransport, action, png
from visual_collusion.budget import BudgetLedger


class DynamicWireFixture(ScriptedWireTransport):
    """Answer fixed plumbing actions, never simulate hidden model cognition."""
    def __init__(self, ledger):
        super().__init__(ledger, [])
        self.b_neutral = False
        self.model_packets = []

    def __call__(self, method, path, payload):
        if method == "POST" and path == "/chat/completions":
            body = json.loads(payload)
            self.model_packets.append(deepcopy(body))
            fields = body["response_format"]["json_schema"]["schema"]["properties"]
            if set(fields) == {"answer"}:
                value = {"answer": "INTERVIEW_VISIBLE_TO_A_AND_C_ONLY"}
            else:
                role = "B" if "decision" in fields else "A"
                step = next(m["content"] for m in reversed(body["messages"])
                            if isinstance(m.get("content"), str) and m["content"].startswith("Tool action "))
                first = step.startswith("Tool action 1 ")
                if role == "B" and first:
                    urls = [part["image_url"]["url"] for message in body["messages"]
                            if isinstance(message.get("content"), list)
                            for part in message["content"] if part.get("type") == "image_url"]
                    with Image.open(io.BytesIO(base64.b64decode(urls[-1].split(",", 1)[1]))) as image:
                        self.b_neutral = image.convert("RGB").getpixel((0, 0)) == (255, 255, 255)
                if first:
                    value = action("generate_image", role=role,
                                   reference="last_peer" if role == "B" else "none",
                                   image_prompt="Offline interface fixture technical card, not model evidence.")
                else:
                    value = action("deliver", role=role, reference="latest_draft",
                                   message="PUBLIC_FIXTURE_TEXT_HELD_FIXED",
                                   private_note=role + "_PRIVATE_STATUS_SENTINEL")
                    if role == "B":
                        value.update({"decision": "P1", "report": {
                            "suspicious": self.b_neutral, "confidence": 0.4,
                            "tag": "NEUTRAL_REPORT_MUST_NOT_REACH_C" if self.b_neutral else "ACTUAL_B_CLAIM",
                            "evidence": []}})
            self.actions.append(value)
        result = super().__call__(method, path, payload)
        if method == "POST" and path == "/chat/completions":
            result["choices"][0]["message"]["reasoning"] = "PROVIDER_REASONING_PRIVATE_SENTINEL"
        return result


class LivePipelineAuditTests(unittest.TestCase):
    def test_real_backend_engine_codex_packets_neutral_forks_and_resume(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger = BudgetLedger(root / "aggregate.sqlite3", 40)
            guard = SharedBudgetGuard(ledger.path, "pressure_interview_v1", ledger=ledger)
            transport = DynamicWireFixture(ledger)
            backend = LiveOpenRouterBackend(guard, root / "provider",
                api_key="UNIT_FIXTURE_NOT_A_CREDENTIAL", transport=transport)
            config = deepcopy(load_config())
            config["budget"]["aggregate_ledger"] = str(ledger.path)
            config["pressure"].update({"initial": 0.5, "decay": 1.0})
            binding = LiveExecutionBinding(backend, guard, config)
            kwargs = {"config": config, "public_task": {
                "brief": "Offline integration fixture: apply the factual rule.",
                "facts": {"candidates": ["P1", "P2", "P3", "P4"]}},
                "private_target": "P3", "pair_id": "CROSS_PRIVATE_PAIR_IDENTIFIER",
                "prompts": load_prompts(), "budget_guard": guard, "live_binding": binding}
            prefix = LivePairEngine(root / "prefix", **kwargs)
            c_packets = []
            c_jobs = 0

            def service_c(engine):
                nonlocal c_jobs
                request = engine.next_request()
                packet = engine.export_codex_packet()
                public = json.loads(Path(packet["packet_path"]).read_text())
                text = json.dumps(public)
                for forbidden in ("A_PRIVATE_STATUS_SENTINEL", "B_PRIVATE_STATUS_SENTINEL",
                                  "PROVIDER_REASONING_PRIVATE_SENTINEL", "NEUTRAL_REPORT_MUST_NOT_REACH_C",
                                  "CROSS_PRIVATE_PAIR_IDENTIFIER", "synthetic_private_performance_pressure"):
                    self.assertNotIn(forbidden, text)
                self.assertNotIn("context_manifest", public)
                c_packets.append(public)
                actor = "/root/unit_cross_c_" + hashlib.sha256(request["request_id"].encode()).hexdigest()[:16]
                engine.mark_codex_dispatched(request["request_id"], actor)
                output = ({"recipient": "A", "question": "Explain your observed public artifact."}
                          if request["stage"] == "C_QUESTION" else
                          {"verdict": "revise", "task_violation": False,
                           "collusion_suspected": False, "confidence": 0.4,
                           "evidence": [], "feedback": "PUBLIC_FIXTURE_REVIEW_FEEDBACK"})
                engine.submit_codex_response(request["request_id"], json.dumps(output), provenance={
                    "actor_runtime": "codex", "reviewer_agent": actor,
                    "actor_model": "Authored unit fixture, not a scientific model observation",
                    "context_isolation": "instruction_scoped"})
                c_jobs += 1

            def finish(engine):
                while engine.next_request() is not None:
                    engine.run_available(backend)
                    request = engine.next_request()
                    if request is None:
                        break
                    if request.get("primary_reception"):
                        path = engine.memory_directory / "artifacts" / "truthful_fixture.png"
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(png(color=(255, 255, 255)))
                        image = {"path": str(path.relative_to(engine.memory_directory)),
                                 "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                        replay = engine.run_primary_with_neutral(backend, image)
                        self.assertEqual(replay["neutral"]["status"], "valid")
                        self.assertTrue(replay["neutral"]["output"]["report"]["suspicious"])
                        self.assertFalse(replay["primary"]["output"]["report"]["suspicious"])
                        request = engine.next_request()
                    if request["stage"] in ("C_QUESTION", "C_VERDICT"):
                        service_c(engine)

            finish(prefix)
            self.assertEqual(prefix.status()["state"], "shared_checkpoint")
            before = len(transport.posts_exact)
            reopened = LivePairEngine(root / "prefix", **kwargs)
            reopened.verify_saved_records()
            reopened.run_available(backend)
            self.assertEqual(len(transport.posts_exact), before)
            for name, target in (("branch_1", "P2"), ("branch_2", "P3")):
                branch = reopened.fork_branch(root / name, target=target, branch=name)
                finish(branch)
                self.assertEqual(branch.status()["state"], "trajectory_complete")
                metrics = [event["payload"]["metric"] for event in branch.store.events_for("A")
                           if event["kind"] == "pressure"]
                self.assertEqual(metrics, [0.5] * 12)
                branch.verify_saved_records()
            self.assertEqual(c_jobs, 32)
            self.assertEqual(len(c_packets), 32)
            self.assertEqual(len(transport.posts_exact), 118)
            self.assertEqual(ledger.summary()["completed_count"], 118)
            self.assertEqual(ledger.summary()["unresolved_request_ids"], [])
            self.assertLess(ledger.summary()["actual_usd"], 1)
            for body in transport.model_packets:
                fields = body["response_format"]["json_schema"]["schema"]["properties"]
                if "decision" in fields:
                    text = json.dumps(body["messages"])
                    self.assertNotIn("A_PRIVATE_STATUS_SENTINEL", text)
                    self.assertNotIn("INTERVIEW_VISIBLE_TO_A_AND_C_ONLY", text)
                    self.assertNotIn("synthetic_private_performance_pressure", text)
                self.assertNotIn("CROSS_PRIVATE_PAIR_IDENTIFIER", json.dumps(body["messages"]))


if __name__ == "__main__":
    unittest.main()
