"""Declared OFFLINE fixtures. No provider, API, or actual research observations."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from research_plans.triad_required_images_v10r1.commitment import build_cases, IDS
from experiments.triad_required_images_v10r1.backend import TechnicalFault
from experiments.triad_required_images_v10r1.controls import (
    ControlRunner, HONEST_SYSTEM, alternate_case, diagnostic_fixture, manifest,
    paired_target_effect, replace_images, report_summary,
)
from experiments.triad_required_images_v10r1.engine import (
    Engine, EngineError, canonical, digest, file_sha, read_record, recorded, save_once,
)
from experiments.triad_required_images_v10r1.tests.test_engine import finish_round, mock_result


def vote(choice, valid=True):
    return {"component_status": {"decision": "valid" if valid else "model_invalid"},
            "output": {"decision": choice if valid else None}}


class ScriptedBackend:
    """Scripted native operation substitute, not a model or provider mock claim."""
    def __init__(self, context, main, calls, *, fail=None, decline=False):
        self.context, self.main, self.calls = context, main, calls
        self.fail, self.decline = fail, decline

    def perform(self, request):
        self.calls.append(deepcopy(request))
        if self.fail:
            self.fail(request)
        invalid = self.decline and request["role"] == "A" and request["mode"] == "image"
        result = mock_result(request, self.context["working_root"], self.main.plan, invalid=invalid)
        if request["mode"] == "image" and not invalid:
            notice = json.loads(request["messages"][1]["content"]).get("private_sender_notice", {})
            result["actions"][0]["arguments"]["prompt"] = "SCRIPTED exact brief: " + notice.get("target", "none")
            result["actions"].append({"action": "accept_visual", "selected_draft_id": "D1"})
        return result

    def _generate(self, request_id, prompt, reference):
        self.calls.append({"direct_renderer": True, "request_id": request_id,
                           "prompt": prompt, "reference": reference})
        path = Path(self.context["working_root"]) / "fresh_renderer.png"
        Image.new("RGB", (1024, 1024), "purple").save(path)
        return {"image": {"path": str(path), "sha256": file_sha(path)}, "receipt": {"offline_fixture": True}}


class ControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.out = Path(cls.directory.name)
        cls.main = Engine(cls.out, {"revision": "triad_required_images_v10r1", "offline_fixture": True}, build_cases())
        for _ in range(12):
            finish_round(cls.main)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.calls, self.sealed = [], []
        self.root = self.out / ("controls_" + self._testMethodName)

    def runner(self, *, fail=None, decline=False, seal=True):
        return ControlRunner(self.main, self.root,
            lambda context: ScriptedBackend(context, self.main, self.calls, fail=fail, decline=decline),
            (lambda context, marker: self.sealed.append((context["attempt_id"], read_record(marker)))) if seal else None)

    def test_fixed_manifest_and_all_16_symmetric_theta_pairs(self):
        schedule = manifest()
        self.assertEqual((schedule["max_chat"], schedule["max_images"], schedule["max_paid_requests"]), (97, 33, 130))
        self.assertEqual(len(schedule["branches"]), 17)
        for first in IDS:
            for second in IDS:
                actual = paired_target_effect(vote(first), vote(second), "P1", "P2")
                expected = .5 * ((first == "P1") - (first == "P2") + (second == "P2") - (second == "P1"))
                self.assertEqual(actual["theta"], expected)
        self.assertEqual(paired_target_effect(vote("P1"), vote("P3"), "P1", "P2")["theta"], .5)
        for absent in (None, vote(None, False)):
            self.assertIsNone(paired_target_effect(vote("P1"), absent, "P1", "P2")["theta"])
        self.assertIsNone(paired_target_effect(vote("P1"), vote("P2"), "P1", "P2", images_available=False)["theta"])

    def test_facts_artifact_contains_no_oracle_recommendation_or_target(self):
        case = self.main.plan[3]
        result = diagnostic_fixture(case, "neutral", self.root)
        text = "\n".join(result["provenance"]["rendered_text"])
        self.assertNotIn("Choose", text)
        self.assertNotIn("oracle", text.lower())
        self.assertNotIn("reward", text.lower())
        self.assertFalse(result["provenance"]["target_in_pixels"])
        self.assertEqual([line.split()[0] for line in result["provenance"]["rendered_text"][3:7]], list(IDS))
        self.assertEqual(result, diagnostic_fixture(case, "neutral", self.root))
        alternate = alternate_case(case)
        for key in ("round", "oracle", "candidates"):
            self.assertEqual(case[key], alternate[key])
        self.assertNotIn(alternate["private_target"], {case["oracle"], case["private_target"]})

    def test_alias_removal_rebuilds_actual_pixels_and_preserves_same_sha_other_speaker(self):
        runner = self.runner()
        source, _ = runner._source(4, "C_VOTE")
        original = deepcopy(source["image_refs"]["current_A"])
        source["image_refs"]["hidden_alias"] = deepcopy(original)
        # The alias has an additional actual inline message as well.
        inline = next(m for m in source["messages"] if isinstance(m.get("content"), list)
                      and any(p.get("type") == "image_url" for p in m["content"]))
        source["messages"].append(deepcopy(inline))
        source["image_refs"]["current_B"] = {**original, "speaker": "B"}
        new = diagnostic_fixture(self.main.plan[3], "neutral", self.root / "neutral")["image"]
        changed = replace_images(source, {"current_A": new})
        self.assertEqual(set(changed["image_refs"]), {"current_A", "current_B"})
        self.assertEqual(changed["image_refs"]["current_B"]["sha256"], original["sha256"])
        self.assertNotEqual(changed["image_refs"]["current_A"]["sha256"], original["sha256"])
        self.assertEqual(sum(isinstance(m.get("content"), list) and any(p.get("type") == "image_url" for p in m["content"])
                             for m in changed["messages"]), 2)
        self.assertNotIn("hidden_alias", canonical(changed))

    def test_frozen_memories_fresh_A_target_only_intervention_and_coherent_relay(self):
        runner = self.runner()
        before = runner.verify_main()
        original = runner.run_branch(4, "original")
        alternate = runner.run_branch(4, "alternate")
        a_calls = [r for r in self.calls if r["role"] == "A"]
        self.assertEqual(len(a_calls), 2)
        first, second = [deepcopy(r["messages"]) for r in a_calls]
        view = json.loads(second[1]["content"])
        notice = json.loads(first[1]["content"])["private_sender_notice"]
        view["private_sender_notice"] = notice
        second[1]["content"] = canonical(view)
        self.assertEqual(first, second)
        self.assertNotEqual(original["result"]["stages"]["A_IMAGE"]["output"]["image"]["path"],
                            alternate["result"]["stages"]["A_IMAGE"]["output"]["image"]["path"])
        for branch in (original, alternate):
            stages = branch["result"]["stages"]
            a, b = stages["A_IMAGE"]["output"]["image"], stages["B_IMAGE"]["output"]["image"]
            votes = [r for r in self.calls if r["request_id"].startswith(branch["attempt_id"]) and r["mode"] == "vote"]
            self.assertEqual(votes[0]["image_refs"]["own_current"]["path"], b["path"])
            self.assertEqual(votes[1]["image_refs"]["current_A"]["path"], a["path"])
            self.assertEqual(votes[1]["image_refs"]["current_B"]["path"], b["path"])
        self.assertEqual(before, runner.verify_main())

    def test_all_controls_diagnostics_renderer_and_read_only_report(self):
        runner = self.runner()
        self.assertEqual(report_summary(self.main, self.root)["status"], "pending")
        report = runner.run_all()
        actor_calls = [r for r in self.calls if not r.get("direct_renderer")]
        direct_calls = [r for r in self.calls if r.get("direct_renderer")]
        self.assertEqual((len(actor_calls), len(direct_calls)), (52, 3))
        # 15 image stages each <=4native chats+2images, 37otherchat stages.
        self.assertEqual(sum(4 if r["mode"] == "image" else 1 for r in actor_calls), 97)
        self.assertEqual(sum(2 if r["mode"] == "image" else 0 for r in actor_calls) + len(direct_calls), 33)
        for number in (4, 8, 12):
            renderer = runner._completed(number, "renderer")["result"]
            self.assertTrue(renderer["receive_only_variant"])
            samples = [r for r in actor_calls if f"ctrl.r{number:02d}.renderer." in r["request_id"]]
            self.assertEqual(len(samples), 4)
            self.assertTrue(all(set(r["image_refs"]) == {"current_A"} for r in samples))
            context = []
            for request in samples:
                view = json.loads(request["messages"][1]["content"])
                context.append({k:v for k,v in view.items() if k not in {"incoming_images", "unavailable_images"}})
            self.assertTrue(all(v == context[0] for v in context))
            self.assertEqual(samples[0]["image_refs"]["current_A"]["sha256"], samples[2]["image_refs"]["current_A"]["sha256"])
            self.assertEqual(samples[1]["image_refs"]["current_A"]["sha256"], samples[3]["image_refs"]["current_A"]["sha256"])
            self.assertEqual(direct_calls[(number // 4) - 1]["prompt"], renderer["exact_executed_brief"])
        for family in ("overt", "honest"):
            requests = [r for r in actor_calls if f"ctrl.r04.{family}." in r["request_id"]]
            self.assertEqual(len(requests), 2)
            for request in requests:
                self.assertEqual(set(request["image_refs"]), {"current_A"})
                view = json.loads(request["messages"][1]["content"])
                self.assertNotIn("private_sender_notice", view)
                if family == "honest":
                    self.assertEqual(request["messages"][0]["content"], HONEST_SYSTEM)
        result = report_summary(self.main, self.root)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["results"], report)
        prior_calls = len(self.calls)
        self.assertEqual(runner.run_all(), report)
        self.assertEqual(len(self.calls), prior_calls)

    def test_declined_source_renderer_unavailable_no_replacement_no_favorable_retry(self):
        runner = self.runner(decline=True)
        original = runner.run_branch(4, "original")
        self.assertEqual(original["attempt"], 1)
        self.assertIsNone(original["result"]["stages"]["A_IMAGE"]["output"]["image"])
        renderer = runner.run_branch(4, "renderer")
        self.assertEqual(renderer["result"]["status"], "unavailable")
        self.assertFalse(renderer["result"]["replacement_sample"])
        self.assertEqual(len(self.calls), 4)
        self.assertFalse(self.sealed)

    def test_whole_branch_technical_restart_preserves_first_attempt_and_checkpoint(self):
        failed = []
        def fail(request):
            if request["stage"] == "B_IMAGE" and not failed:
                failed.append(True)
                raise TechnicalFault("scripted_transport_loss", request["request_id"])
        runner = self.runner(fail=fail)
        result = runner.run_branch(4, "original")
        self.assertEqual(result["attempt"], 2)
        a_calls = [r for r in self.calls if r["stage"] == "A_IMAGE"]
        self.assertEqual(len(a_calls), 2)
        self.assertEqual(a_calls[0]["messages"], a_calls[1]["messages"])
        self.assertNotEqual(a_calls[0]["request_id"], a_calls[1]["request_id"])
        self.assertEqual(len(self.sealed), 1)
        self.assertTrue((runner._attempt_root(4, "original", 1) / "results" / "A_IMAGE.json").exists())
        self.assertEqual(runner.verify_main(), runner.preservation_sha)

    def test_permanent_and_untyped_holds_cannot_be_reopened_as_new_samples(self):
        def permanent(request):
            raise TechnicalFault("scripted_configuration_hold", request["request_id"], permanent=True)
        runner = self.runner(fail=permanent)
        with self.assertRaises(TechnicalFault):
            runner.run_branch(4, "original")
        count = len(self.calls)
        with self.assertRaises(EngineError):
            self.runner().run_branch(4, "original")
        self.assertEqual(len(self.calls), count)
        # An untyped integrity error uses a separate declared control endpoint.
        def untyped(request):
            raise ValueError("scripted integrity failure")
        with self.assertRaises(ValueError):
            self.runner(fail=untyped).run_branch(4, "alternate")
        count = len(self.calls)
        with self.assertRaises(EngineError):
            self.runner().run_branch(4, "alternate")
        self.assertEqual(len(self.calls), count)

    def test_live_pending_operation_holds_but_dead_interruption_restarts_whole_branch(self):
        runner = self.runner()
        context = runner._context(4, "original", 1)
        request, _ = runner._source(4, "A_IMAGE")
        request.update(request_id=context["attempt_id"] + ".A_IMAGE", attempt=1)
        request.pop("request_hash")
        request["request_hash"] = digest(request)
        # Inject an interrupted callback with no result, preserving startproof.
        def interrupt():
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            runner._operation(context, "A_IMAGE", request, interrupt)
        with self.assertRaises(EngineError):
            runner.run_branch(4, "original")
        self.assertFalse(self.calls)
        self.assertFalse((Path(context["working_root"]) / "abandonment.json").exists())
        # A separate branch fixture explicitly simulates a dead prior process.
        context = runner._context(4, "alternate", 1)
        root = Path(context["working_root"])
        request, _ = runner._source(4, "A_IMAGE")
        request = replace_images(request, {}, target=alternate_case(self.main.plan[3])["private_target"])
        request.update(request_id=context["attempt_id"] + ".A_IMAGE", attempt=1, stage="A_IMAGE")
        request["request_hash"] = digest(request)
        save_once(root / "requests" / "A_IMAGE.json", request)
        save_once(root / "operations" / "A_IMAGE.started.json", recorded({"attempt_id": context["attempt_id"],
            "request_id": request["request_id"], "request_sha256": file_sha(root / "requests" / "A_IMAGE.json"),
            "producer_pid": 999999999, "producer_process_start_ticks": "dead_fixture"}))
        result = runner.run_branch(4, "alternate")
        self.assertEqual(result["attempt"], 2)
        self.assertEqual(len(self.sealed), 1)

    def test_durable_result_recovers_finish_without_second_callback(self):
        runner = self.runner()
        result = runner.run_branch(4, "neutralB")
        root = runner._attempt_root(4, "neutralB", 1)
        finished = root / "operations" / "C_VOTE.finished.json"
        finished.unlink()
        (self.root / "branches" / "r04" / "neutralB" / "complete.json").unlink()
        count = len(self.calls)
        resumed = runner.run_branch(4, "neutralB")
        self.assertEqual(result, resumed)
        self.assertTrue(finished.exists())
        self.assertEqual(len(self.calls), count)

    def test_revision_repeat_uses_exact_brief_and_exact_source_raster(self):
        outer = self
        class RevisedBackend(ScriptedBackend):
            def perform(self, request):
                result = super().perform(request)
                if request["stage"] == "A_IMAGE":
                    first = deepcopy(result["output"]["image"])
                    path = Path(self.context["working_root"]) / "revision_selected.png"
                    Image.new("RGB", (1024, 1024), "orange").save(path)
                    second = {"path": str(path), "sha256": file_sha(path)}
                    result["output"]["image"] = second
                    result["actions"] = [
                        {"action": "create_visual", "draft_id": "D1", "generation_attempt": 1,
                         "arguments": {"prompt": "SCRIPTED initial draft"}, "generated_image": first},
                        {"action": "revise_visual", "draft_id": "D2", "generation_attempt": 2,
                         "arguments": {"prompt": "SCRIPTED exact revision 🌀", "source_draft_id": "D1"}, "generated_image": second},
                        {"action": "accept_visual", "selected_draft_id": "D2"}]
                return result
        runner = ControlRunner(self.main, self.root,
            lambda context: RevisedBackend(context, self.main, self.calls))
        original = runner.run_branch(4, "original")
        runner.run_branch(4, "renderer")
        repeated = next(c for c in self.calls if c.get("direct_renderer"))
        source = original["result"]["stages"]["A_IMAGE"]["actions"][0]["generated_image"]
        self.assertEqual(repeated["prompt"], "SCRIPTED exact revision 🌀")
        self.assertEqual(repeated["reference"], Path(source["path"]).read_bytes())

    def test_unaccepted_B_draft_stays_private_to_B_and_never_reaches_C(self):
        class DecliningB(ScriptedBackend):
            def perform(self, request):
                result = super().perform(request)
                if request["stage"] == "B_IMAGE":
                    result["output"]["image"] = None
                    result["component_status"]["image"] = "declined"
                    result["status"] = "partial"
                    result["actions"] = result["actions"][:1] + [{"action": "decline_visual"}]
                return result
        runner = ControlRunner(self.main, self.root,
            lambda context: DecliningB(context, self.main, self.calls))
        result = runner.run_branch(4, "original")
        self.assertIsNone(result["result"]["stages"]["B_IMAGE"]["output"]["image"])
        b_vote = next(c for c in self.calls if c["stage"] == "B_VOTE")
        c_vote = next(c for c in self.calls if c["stage"] == "C_VOTE")
        self.assertIn("own_current", b_vote["image_refs"])
        self.assertNotIn("current_B", c_vote["image_refs"])
        self.assertIn("current_B", json.loads(c_vote["messages"][1]["content"])["unavailable_images"])


if __name__ == "__main__":
    unittest.main()
