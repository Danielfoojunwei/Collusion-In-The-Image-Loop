"""Offline recovery-boundary checks. No actual ledger or network is used."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image, PngImagePlugin

from scripts import run_pressure_xmp_v2 as controller
from experiments.pressure_interview_v1.contracts import ContractError, canonical
from experiments.pressure_interview_v1.engine import UnresolvedRequest
from visual_collusion.budget import BudgetLedger


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.out = self.root / "study"
        self.out.mkdir()
        self.namespace = "fixture_pressure"
        (self.out / "config.json").write_text(json.dumps({"budget": {"namespace": self.namespace}}))
        self.unit = self.out / "episodes/unit/prefix"
        self.unit.mkdir(parents=True)
        self.request = {"request_id": "unit.prefix.r01.A_OUTPUT", "stage": "A_OUTPUT",
                        "role": "A", "round": 1, "messages": ["fixed fixture"]}
        self.request["request_hash"] = controller.sha(canonical(self.request).encode())
        key = controller.sha(self.request["request_id"].encode()) + ".json"
        self.request_path = self.unit / "requests" / key
        self.dispatch_path = self.unit / "dispatches" / key
        self.request_path.parent.mkdir()
        self.dispatch_path.parent.mkdir()
        self.request_path.write_text(canonical(self.request) + "\n")
        self.dispatch = {"request_id": self.request["request_id"],
                         "request_hash": self.request["request_hash"], "evidence_origin": "live"}
        self.dispatch_path.write_text(canonical(self.dispatch) + "\n")
        self.draft = self.out / "provider_evidence/blobs/original.png"
        self.draft.parent.mkdir(parents=True)
        self.draft.write_bytes(b"fixture immutable source bytes; not a scientific image")
        self.ledger = BudgetLedger(self.root / "aggregate.sqlite3", 40)
        for suffix, kind, model in ((".step1", "chat", "z-ai/glm-5.3-flash"),
                                    (".image1", "image", "qwen/qwen-image-2.1-turbo")):
            identifier = self.namespace + ":" + self.request["request_id"] + suffix
            request_hash = hashlib.sha256(suffix.encode()).hexdigest()
            self.ledger.reserve(identifier, kind, model, request_hash, .05)
            self.ledger.complete(identifier, .001, {"actual_fixture": suffix},
                {"request_id": identifier, "request_hash": request_hash,
                 "requested_model": model, "kind": kind, "cost_usd": .001})
        self.amendment = {"recovery": {
            "request_id": self.request["request_id"], "request_hash": self.request["request_hash"],
            "unit_directory": "episodes/unit/prefix",
            "request_file_sha256": controller.sha(self.request_path.read_bytes()),
            "dispatch_file_sha256": controller.sha(self.dispatch_path.read_bytes()),
            "completed_requests": {key: controller.sha(canonical(value).encode()) for key, value in
                controller._namespace_rows(self.ledger.path, self.namespace).items()},
            "original_draft": {"path": str(self.draft.relative_to(self.out)),
                               "sha256": controller.sha(self.draft.read_bytes())}}}

    def tearDown(self):
        self.temp.cleanup()

    def verify(self, **kw):
        return controller.verify_recovery_proof(self.out, self.amendment, self.ledger.path, **kw)

    def test_exact_completed_proof_allows_one_claim_without_changing_dispatch(self):
        before = self.dispatch_path.read_bytes()
        claim = controller.make_recovery_claim(self.out, self.amendment, self.ledger.path)
        result = claim(SimpleNamespace(directory=self.unit), deepcopy(self.request))
        self.assertEqual(result, self.dispatch)
        self.assertEqual(before, self.dispatch_path.read_bytes())
        self.assertEqual(self.ledger.summary()["completed_count"], 2)
        with self.assertRaises(UnresolvedRequest):
            claim(SimpleNamespace(directory=self.unit), deepcopy(self.request))

    def test_pending_or_unknown_cached_calls_cannot_be_recovered(self):
        identifier = next(iter(self.amendment["recovery"]["completed_requests"]))
        for state in ("pending", "uncertain"):
            with self.subTest(state=state):
                with sqlite3.connect(self.ledger.path) as db:
                    db.execute("UPDATE requests SET status=? WHERE request_id=?", (state, identifier))
                with self.assertRaises(ContractError):
                    self.verify(require_pristine=True)

    def test_an_additional_paid_intent_blocks_the_one_shot_claim(self):
        identifier = self.namespace + ":" + self.request["request_id"] + ".step2"
        self.ledger.reserve(identifier, "chat", "z-ai/glm-5.3-flash", "b" * 64, .05)
        with self.assertRaises(UnresolvedRequest):
            self.verify(require_pristine=True)
        # The original completed proof can still be audited at a later point.
        self.verify(require_pristine=False)

    def test_observed_outer_response_cannot_be_recovered(self):
        path = self.unit / "observations" / self.request_path.name
        path.parent.mkdir()
        path.write_text("{}\n")
        with self.assertRaises(UnresolvedRequest):
            self.verify(require_pristine=True)

    def test_changed_request_marker_row_or_draft_is_rejected(self):
        paths = (self.request_path, self.dispatch_path, self.draft)
        for path in paths:
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(original + b" ")
                with self.assertRaises(ContractError):
                    self.verify()
                path.write_bytes(original)
        identifier = next(iter(self.amendment["recovery"]["completed_requests"]))
        with sqlite3.connect(self.ledger.path) as db:
            db.execute("UPDATE requests SET response_json='{}' WHERE request_id=?", (identifier,))
        with self.assertRaises(ContractError):
            self.verify()

    def test_nonallowlisted_dispatches_keep_original_exclusivity(self):
        observed = []
        def original(*args):
            observed.append(args)
            raise UnresolvedRequest("Original exclusive dispatch")
        claim = controller.make_recovery_claim(self.out, self.amendment, self.ledger.path, original)
        changed = {**self.request, "request_id": "unit.prefix.r01.B_OUTPUT"}
        with self.assertRaises(UnresolvedRequest):
            claim(SimpleNamespace(directory=self.unit), changed)
        self.assertEqual(len(observed), 1)
        with self.assertRaises(UnresolvedRequest):
            claim(SimpleNamespace(directory=self.unit), {**self.request, "request_hash": "0" * 64})
        self.assertEqual(len(observed), 1)

    def test_wrong_stage_and_escaped_paths_fail_even_with_rebound_hashes(self):
        self.request["stage"] = "B_OUTPUT"
        self.request["request_hash"] = controller.sha(canonical({k:v for k,v in self.request.items() if k != "request_hash"}).encode())
        self.request_path.write_text(canonical(self.request) + "\n")
        proof = self.amendment["recovery"]
        proof["request_hash"] = self.request["request_hash"]
        proof["request_file_sha256"] = controller.sha(self.request_path.read_bytes())
        with self.assertRaises(ContractError):
            self.verify()
        proof["unit_directory"] = "../elsewhere"
        with self.assertRaises(ContractError):
            self.verify()

    def test_analysis_adapter_preserves_ledger_and_wire_checks(self):
        called = []
        codec = SimpleNamespace(verify_wire=lambda *args: called.append(args))
        verify = controller.build_analysis_verifier(codec)
        # No requests is a real unchanged verifier path, without codec use.
        result = verify(self.out, {"budget": {"aggregate_ledger": str(self.root / "empty.sqlite3"),
                                             "namespace": self.namespace}})
        self.assertEqual(result, {"mapped_requests": 0, "paid_intents": 0,
                                  "unreserved_prepared_requests": 0})
        self.assertEqual(called, [])
        # A paid intent without wire evidence still fails exactly as before.
        with self.assertRaises(ContractError):
            verify(self.out, {"budget": {"aggregate_ledger": str(self.ledger.path),
                                        "namespace": self.namespace}})

    def test_amendment_gate_binds_baseline_sidecars_and_offline_validation(self):
        config = {"budget": {"namespace": self.namespace, "aggregate_ledger": str(self.ledger.path)}}
        (self.out / "config.json").write_text(canonical(config) + "\n")
        freeze = {"source_files": controller.live_cli.source_files()}
        freeze_path = self.out / "freeze.json"
        freeze_path.write_text(canonical(freeze) + "\n")
        hashes = {}
        for relative in controller.SIDECARS:
            file = self.root / relative
            file.parent.mkdir(exist_ok=True)
            file.write_bytes(("Offline source binding fixture: " + relative).encode())
            hashes[relative] = controller.sha(file.read_bytes())
        validation = self.root / "offline_validation.json"
        validation.write_text(canonical({"status": "passed", "paid_calls": 0, "source_files": hashes}) + "\n")
        manifest = {**self.amendment, "version": "png_xmp_v2", "status": "offline_verified",
                    "original_freeze_sha256": controller.sha(freeze_path.read_bytes()),
                    "original_source_files": freeze["source_files"], "source_files": hashes,
                    "analysis_function_sha256": controller.ANALYSIS_FUNCTION_SHA256,
                    "validation": {"path": validation.name, "sha256": controller.sha(validation.read_bytes()),
                                   "status": "passed"}}
        path = self.out / controller.AMENDMENT_NAME
        path.parent.mkdir()
        path.write_text(canonical(manifest) + "\n")
        with patch.object(controller, "ROOT", self.root), patch.object(controller.live_cli, "OUT", self.out):
            loaded, aggregate, manifest_sha = controller.load_amendment(self.out)
            self.assertEqual(loaded, manifest)
            self.assertEqual(aggregate, self.ledger.path)
            self.assertEqual(manifest_sha, controller.sha(path.read_bytes()))
            one_script = self.root / next(iter(hashes))
            original = one_script.read_bytes()
            one_script.write_bytes(original + b" changed")
            with self.assertRaises(ContractError):
                controller.load_amendment(self.out)
            one_script.write_bytes(original)
            validation.write_bytes(validation.read_bytes() + b" ")
            with self.assertRaises(ContractError):
                controller.load_amendment(self.out)

    def test_actual_backend_reconstruction_uses_two_cached_calls_and_only_one_new_POST(self):
        from scripts import pressure_png_xmp_v2 as xmp_codec
        from experiments.pressure_interview_v1 import live_provider
        from experiments.pressure_interview_v1.budget_guard import SharedBudgetGuard
        from experiments.pressure_interview_v1.engine import LivePairEngine, LiveExecutionBinding
        from experiments.pressure_interview_v1.planning import load_config, load_prompts
        from experiments.pressure_interview_v1.tests.test_live_provider import ScriptedWireTransport, action
        out = self.root / "actual_backend_recovery"
        out.mkdir()
        ledger = BudgetLedger(self.root / "actual_backend.sqlite3", 40)
        guard = SharedBudgetGuard(ledger.path, "cached_recovery_fixture", ledger=ledger)
        config = load_config()
        config["budget"]["aggregate_ledger"] = str(ledger.path)
        config["budget"]["namespace"] = guard.namespace
        (out / "config.json").write_text(canonical(config) + "\n")
        metadata = PngImagePlugin.PngInfo()
        metadata.add_itxt("XML:com.adobe.xmp", '<x:xmpmeta xmlns:x="adobe:ns:meta/"/>')
        buffer = io.BytesIO()
        Image.new("RGBA", (1024, 1024), (255, 255, 255, 240)).save(buffer, "PNG", pnginfo=metadata)
        source = buffer.getvalue()
        transport = ScriptedWireTransport(ledger, [
            action("generate_image", image_prompt="Offline cached-recovery fixture image."),
            action("deliver", reference="latest_draft", message="Offline fixture delivery.")])
        transport.image_bytes = source
        backend = live_provider.LiveOpenRouterBackend(guard, out / "provider_evidence",
            api_key="OFFLINE_FIXTURE_NOT_A_CREDENTIAL", transport=transport)
        unit = out / "episodes/fixture/prefix"
        engine = LivePairEngine(unit, config=config,
            public_task={"brief": "Offline recovery fixture.", "facts": {"candidates": ["P1", "P2", "P3", "P4"]}},
            private_target="P3", pair_id="fixture", prompts=load_prompts(), budget_guard=guard,
            live_binding=LiveExecutionBinding(backend, guard, config))
        # Reproduce the exact local failure before a step2 intent or POST.
        with self.assertRaises(live_provider.codec.WireEncodingError):
            engine.run_available(backend, max_requests=1)
        self.assertEqual(len(transport.posts_exact), 2)
        before = controller._namespace_rows(ledger.path, guard.namespace)
        self.assertEqual(len(before), 2)
        self.assertTrue(all(row["status"] == "completed" for row in before.values()))
        request = engine.next_request()
        key = controller.sha(request["request_id"].encode()) + ".json"
        request_path, dispatch_path = unit / "requests" / key, unit / "dispatches" / key
        dispatch_before = dispatch_path.read_bytes()
        draft = out / "provider_evidence/blobs" / (controller.sha(source) + ".png")
        amendment = {"recovery": {
            "request_id": request["request_id"], "request_hash": request["request_hash"],
            "unit_directory": str(unit.relative_to(out)),
            "request_file_sha256": controller.sha(request_path.read_bytes()),
            "dispatch_file_sha256": controller.sha(dispatch_before),
            "completed_requests": {key: controller.sha(canonical(value).encode()) for key, value in before.items()},
            "original_draft": {"path": str(draft.relative_to(out)), "sha256": controller.sha(source)}}}
        claim = controller.make_recovery_claim(out, amendment, ledger.path)
        with patch.object(live_provider, "codec", xmp_codec), patch.object(LivePairEngine, "_claim_dispatch", claim):
            engine.run_available(backend, max_requests=1)
        self.assertEqual(len(transport.posts_exact), 3)
        after = controller._namespace_rows(ledger.path, guard.namespace)
        self.assertEqual({key: after[key] for key in before}, before)
        self.assertEqual(ledger.summary()["completed_count"], 3)
        self.assertEqual(dispatch_path.read_bytes(), dispatch_before)
        delivered = next(e for e in engine.store.events_for("experimenter") if e["kind"] == "a_public")
        self.assertEqual((engine.memory_directory / delivered["payload"]["image"]["path"]).read_bytes(), source)
        checks = controller.build_analysis_verifier(xmp_codec)(out, config)
        self.assertEqual(checks["paid_intents"], 3)
        self.assertEqual(checks["mapped_requests"], 3)


if __name__ == "__main__":
    unittest.main()
