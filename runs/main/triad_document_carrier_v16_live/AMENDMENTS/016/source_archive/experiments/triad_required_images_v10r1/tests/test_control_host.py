"""Offline post-main host integration, including the real local funding guard."""
from copy import deepcopy
import io
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from PIL import Image
from visual_collusion.budget import PendingPaidRequest
from .. import funding
from ..backend import TechnicalFault
from ..control_host import ControlHost
from ..coordinator import CoordinatorError
from ..engine import EngineError, canonical, file_sha, read_record
from . import test_coordinator as fixture_module
from .test_engine import finish_round


class ControlFixtureBackend(fixture_module.FixtureBackend):
    def perform(self, request):
        result = super().perform(request)
        if result["component_status"]["image"] == "delivered":
            result["actions"].append({"phase": "visual", "action": "accept_visual", "slot": 2,
                                      "selected_draft_id": "D1", "arguments": {"draft_id": "D1"}})
        return result

    def _generate(self, request_id, brief, reference):
        def post(body, binding):
            self.harness.posts.append(deepcopy(binding))
            return {"response": {"offline_fixture": True}, "receipt": {**binding, "cost_usd": .001}}
        self.guard.execute(request_id, "image", funding.MODELS["image"],
            {"model": funding.MODELS["image"], "prompt": brief, "offline_fixture": True}, .02, post)
        path = Path(self.out) / "fixture_renderer" / "repeat.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1024, 1024), "gray").save(path)
        return {"image": {"path": str(path), "sha256": file_sha(path)}, "offline_fixture": True}


class ControlHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # One real completed twelve-round fixture, reused read-only. Control
        # artifacts and the isolated temporary ledger are reset between tests.
        cls.fixture = fixture_module.CoordinatorTests()
        cls.fixture.setUp()
        coordinator = cls.fixture.coordinator
        with coordinator.locked():
            coordinator.init()
            coordinator.preflight()
            for _ in range(12): finish_round(coordinator.engine)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.doCleanups()

    def setUp(self):
        self.out, self.ledger = self.fixture.out, self.fixture.ledger
        for name in ("controls", "paid_operations", "funding_seals", "coordinator_holds"):
            shutil.rmtree(self.out / name, ignore_errors=True)
        with self.ledger._connect() as db:
            db.execute("DELETE FROM requests")  # only this isolated offline fixture ledger
        self.fixture.performs, self.fixture.posts, self.fixture.failures, self.fixture.invalid = [], [], {}, set()
        self.fixture.provider_remaining, self.fixture.mutate_before_post = 40, None
        self.host = self.new_host()

    def factory(self, *args, **kwargs):
        return ControlFixtureBackend(self.fixture, *args, **kwargs)

    def new_host(self):
        return ControlHost(self.out, coordinator=self.fixture.new_coordinator(), backend_factory=self.factory)

    def test_status_requires_lock_and_completed_main_makes_no_post(self):
        with self.assertRaises(CoordinatorError): self.host.status()
        with self.host.locked():
            state = self.host.status()
            self.assertEqual(state["main_committed_rounds"], 12)
            self.assertEqual(len(state["branches"]), 17)
            self.assertEqual(state["fixed_normal_bound"], {"chat": 97, "images": 33})
            self.assertEqual(self.fixture.posts, [])

    def test_control_before_complete_main_is_rejected(self):
        with self.host.locked():
            self.host.coordinator.init()
            with patch.object(self.host.coordinator.engine, "status", return_value={"committed_rounds": 11}):
                with self.assertRaises(CoordinatorError) as raised: self.host.status()
            self.assertEqual(raised.exception.code, "all_twelve_main_rounds_must_commit_before_controls")
            self.assertEqual(self.fixture.posts, [])

    def test_branch_guards_share_main_lock_ledger_and_leave_main_unchanged(self):
        with self.host.locked():
            before = self.host.status()["main_preservation_sha256"]
            result = self.host.run_branch(4, "original")
            self.assertEqual(result["status"], "complete")
            self.assertEqual(len(self.fixture.posts), 4)
            self.assertEqual(self.host.status()["main_preservation_sha256"], before)
            for request in self.fixture.performs:
                self.assertTrue(request["request_id"].startswith("ctrl.r04.original.a01."))
            for stage in result["result"]["stages"].values():
                image = stage["output"]["image"]
                if image:
                    self.assertTrue(Path(image["path"]).is_relative_to(self.out / "controls/branches/r04/original/attempts/a01"))
            self.host.run_branch(4, "original")
            self.assertEqual(len(self.fixture.posts), 4)
            self.assertEqual(self.ledger.namespace_summary()["request_count"], 4)

    def test_invalid_model_image_is_retained_without_branch_reroll(self):
        self.fixture.invalid.add("ctrl.r04.original.a01.A_IMAGE")
        with self.host.locked():
            result = self.host.run_branch(4, "original")
            self.assertEqual(result["attempt"], 1)
            self.assertEqual(result["result"]["stages"]["A_IMAGE"]["component_status"]["image"], "model_invalid")
            self.assertEqual(len(self.fixture.posts), 4)
            self.assertFalse(list((self.out / "controls").rglob("abandonment.json")))

    def test_typed_failure_seals_and_resumes_a02_after_old_a01_seal(self):
        self.fixture.failures["ctrl.r04.original.a01.B_IMAGE"] = "transport"
        self.fixture.failures["ctrl.r04.original.a02.B_IMAGE"] = "transport"
        original = self.host._on_abandon
        interrupted = {"done": False}
        def stop_after_second_seal(context, path):
            value = original(context, path)
            if context["attempt"] == 2 and not interrupted["done"]:
                interrupted["done"] = True
                raise OSError("SCRIPTED process interruption after durable seal")
            return value
        with self.host.locked():
            self.host._ready()
            self.host.runner.on_abandon = stop_after_second_seal
            with self.assertRaises(OSError): self.host.run_branch(4, "original")
            self.assertAlmostEqual(self.ledger.namespace_summary()["unresolved_reserved_usd"], .04)
        reopened = self.new_host()
        with reopened.locked():
            result = reopened.run_branch(4, "original")
            self.assertEqual(result["attempt"], 3)
            self.assertEqual(len(self.fixture.posts), 8)  #2+2abandoned,4fresh
            self.ledger.assert_ready()
            self.assertAlmostEqual(self.ledger.namespace_summary()["unresolved_reserved_usd"], .04)
            self.assertEqual(len({r["request_id"] for r in self.fixture.performs}), len(self.fixture.performs))

    def test_permanent_hold_persists_without_a_new_sample(self):
        self.fixture.failures["ctrl.r04.original.a01.A_IMAGE"] = "permanent"
        with self.host.locked():
            with self.assertRaises(TechnicalFault): self.host.run_branch(4, "original")
        reopened = self.new_host()
        with reopened.locked():
            with self.assertRaises(EngineError): reopened.run_branch(4, "original")
            self.assertEqual(len(self.fixture.posts), 0)
            self.assertFalse((self.out / "controls/branches/r04/original/attempts/a02").exists())

    def test_source_mutation_blocks_callback_before_any_control_charge(self):
        source = self.fixture.root / "scripts/codec.py"
        original = source.read_text()
        self.addCleanup(source.write_text, original)
        self.fixture.mutate_before_post = lambda: source.write_text("# SCRIPTED source corruption\n")
        with self.host.locked():
            with self.assertRaises(CoordinatorError) as raised: self.host.run_branch(4, "original")
            self.assertEqual(raised.exception.code, "frozen_source_bytes_changed")
            self.assertEqual(len(self.fixture.posts), 0)
            self.assertEqual(self.ledger.namespace_summary()["request_count"], 0)

    def test_context_cannot_redirect_working_root_or_bypass_manifest(self):
        with self.host.locked():
            self.host._ready()
            bad = self.host.runner._context(4, "original", 1)
            bad["working_root"] = str(self.out)
            with self.assertRaises(CoordinatorError): self.host._backend_for(bad)
            with self.assertRaises(EngineError): self.host.run_branch(5, "original")
            self.assertEqual(self.fixture.posts, [])

    def test_run_all_and_jsonline_resume_do_not_repost_completed_branches(self):
        with self.host.locked():
            result = self.host.run_all()
            self.assertEqual(result["status"], "complete")
            self.assertEqual(len(result["branches"]), 17)
            self.assertEqual(len(result["target_swap_pairs"]), 3)
            self.assertEqual(result["main_memory_contamination"], False)
            self.assertEqual(result["fixed_normal_bound"], {"chat": 97, "images": 33})
            before = len(self.fixture.posts)
            self.assertEqual(before, 55)  #52 scripted actor stages +3 renderer images
            self.host.run_all()
            self.assertEqual(len(self.fixture.posts), before)
        reopened = self.new_host()
        stream = io.StringIO(canonical({"command": "status"}) + "\n" +
                             canonical({"command": "run_branch", "round": 4, "family": "original"}) + "\n" +
                             canonical({"command": "quit"}) + "\n")
        output = io.StringIO()
        reopened.serve(stream, output)
        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertTrue(all(row["ok"] for row in rows))
        self.assertEqual(rows[0]["result"]["status"], "complete")
        self.assertEqual(len(self.fixture.posts), before)


if __name__ == "__main__":
    unittest.main()
