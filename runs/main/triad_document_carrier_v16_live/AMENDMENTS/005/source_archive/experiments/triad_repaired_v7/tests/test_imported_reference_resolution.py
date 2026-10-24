"""Exact copied-reference resolution only; no provider/ledger/network constructors."""
from copy import deepcopy
import ast
import hashlib
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from experiments.triad_repaired_v7.backend import TriadBackend
from experiments.pressure_interview_v1.contracts import ContractError
from visual_collusion.budget import canonical


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (canonical(value) + "\n").encode()
    path.write_bytes(raw)
    return raw


class ImportedReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "original_v6"
        self.out = self.root / "continuation_v7"
        self.out.mkdir()
        self.files = {}
        self.descriptors = {}
        for role in "ABC":
            raw = b"exact fixture own draft pixels for " + role.encode()
            relative = "provider_evidence/blobs/" + sha(raw) + ".png"
            old = self.source / relative
            copied = self.out / relative
            old.parent.mkdir(parents=True, exist_ok=True)
            copied.parent.mkdir(parents=True, exist_ok=True)
            old.write_bytes(b"ORIGINAL_BYTES_MUST_NOT_BE_READ")
            copied.write_bytes(raw)
            self.files[relative] = sha(raw)
            self.descriptors[role] = {"path": str(old), "sha256": sha(raw)}
        self.continuation = {
            "enabled": True, "source_out": str(self.source),
            "imported_rounds": 8, "first_new_round": 9,
            "source_config_sha256": "c" * 64,
            "source_plan_sha256": "d" * 64,
            "source_freeze_sha256": "e" * 64,
        }
        self.config = {"continuation": self.continuation}
        self.plan = {"continuation": {**self.continuation,
                                      "original_round8_chain_head": "a" * 64}}
        config_raw = write_json(self.out / "config.json", self.config)
        plan_raw = write_json(self.out / "plan.json", self.plan)
        freeze_raw = write_json(self.out / "freeze.json", {
            "config_sha256": sha(canonical(self.config).encode()),
            "plan_sha256": sha(canonical(self.plan).encode())})
        self.manifest = {
            "kind": "exact_v6_prefix_continuation_import",
            "revision": "triad_repaired_v7", "evidence_origin": "live",
            **{key: value for key, value in self.continuation.items() if key != "enabled"},
            "target_out": str(self.out), "imported_stages": 64, "new_allowance": False,
            "prefix_chain_head": "a" * 64,
            "target_config_sha256": sha(config_raw),
            "target_plan_sha256": sha(plan_raw),
            "target_freeze_sha256": sha(freeze_raw),
            "copied_files_sha256": self.files,
        }
        self.seal()
        self.backend = object.__new__(TriadBackend)
        self.backend.config = self.config
        self.backend.out = self.out

    def tearDown(self):
        self.temp.cleanup()

    def seal(self, *, gate=True):
        self.manifest["record_hash"] = sha(canonical({
            key: value for key, value in self.manifest.items() if key != "record_hash"}).encode())
        raw = write_json(self.out / "continuation_import.json", self.manifest)
        if gate:
            write_json(self.out / "live_round_gate.json", {
                "status": "passed", "evidence_origin": "live",
                "continuation_import_sha256": sha(raw)})

    def request(self, role="A"):
        return {"role": role, "mode": "image_only" if role == "C" else "image_vote",
                "memory_directory": str(self.out / "cases" / "same_case" / "memory"),
                "image_refs": {"memory:image:0001": deepcopy(self.descriptors[role])}}

    def test_all_three_roles_resolve_own_private_draft_exactly_without_reading_original(self):
        original_reader = Path.read_bytes

        def guarded_reader(path):
            if path.is_relative_to(self.source):
                raise AssertionError("Resolver must not read original archive bytes")
            return original_reader(path)

        with patch.object(Path, "read_bytes", guarded_reader):
            for role in "ABC":
                with self.subTest(role=role):
                    request = self.request(role)
                    before = deepcopy(request)
                    refs = self.backend._references(request)
                    self.assertEqual(set(refs), {"none", "memory:image:0001"})
                    self.assertEqual(sha(refs["memory:image:0001"]), self.descriptors[role]["sha256"])
                    self.assertEqual(request, before)

    def test_unpaid_gate_creation_uses_same_frozen_plan_binding_without_live_gate(self):
        (self.out / "live_round_gate.json").unlink()
        self.assertEqual(sha(self.backend._references(self.request())["memory:image:0001"]),
                         self.descriptors["A"]["sha256"])

    def test_current_archive_references_are_unchanged_and_need_no_import(self):
        path = self.out / "current.png"
        path.write_bytes(b"current exact draft")
        self.backend.config = {}
        request = {"image_refs": {"last_self": {"path": str(path), "sha256": sha(path.read_bytes())}}}
        self.assertEqual(self.backend._references(request)["last_self"], b"current exact draft")

    def test_wrong_descriptor_hash_is_rejected(self):
        request = self.request()
        request["image_refs"]["memory:image:0001"]["sha256"] = "f" * 64
        with self.assertRaisesRegex(ContractError, "copied inventory"):
            self.backend._references(request)

    def test_original_path_not_in_manifest_is_rejected_even_if_original_exists(self):
        request = self.request()
        path = self.source / "provider_evidence/blobs/unlisted.png"
        path.write_bytes(b"unlisted original")
        request["image_refs"]["memory:image:0001"] = {"path": str(path), "sha256": sha(path.read_bytes())}
        with self.assertRaisesRegex(ContractError, "copied inventory"):
            self.backend._references(request)

    def test_uncopied_original_never_becomes_a_fallback(self):
        descriptor = self.descriptors["A"]
        copied = self.out / Path(descriptor["path"]).relative_to(self.source)
        copied.unlink()
        with self.assertRaisesRegex(ContractError, "inside the exact copied archive"):
            self.backend._references(self.request())

    def test_target_symlink_is_rejected(self):
        descriptor = self.descriptors["A"]
        copied = self.out / Path(descriptor["path"]).relative_to(self.source)
        copied.unlink()
        copied.symlink_to(Path(descriptor["path"]))
        with self.assertRaisesRegex(ContractError, "symlink"):
            self.backend._references(self.request())

    def test_tampered_copied_bytes_are_rejected(self):
        descriptor = self.descriptors["A"]
        (self.out / Path(descriptor["path"]).relative_to(self.source)).write_bytes(b"tampered copy")
        with self.assertRaisesRegex(ContractError, "hash changed"):
            self.backend._references(self.request())

    def test_manifest_self_hash_is_required(self):
        self.manifest["copied_files_sha256"]["other.png"] = "b" * 64
        write_json(self.out / "continuation_import.json", self.manifest)
        with self.assertRaisesRegex(ContractError, "self-hash changed"):
            self.backend._references(self.request())

    def test_live_gate_binds_exact_manifest_file_even_after_self_rehash(self):
        self.manifest["extra"] = "a changed manifest"
        self.seal(gate=False)
        with self.assertRaisesRegex(ContractError, "live integrity gate"):
            self.backend._references(self.request())

    def test_manifest_cannot_redirect_the_frozen_original_archive(self):
        self.manifest["source_out"] = str(self.root / "arbitrary_source")
        self.seal()
        with self.assertRaisesRegex(ContractError, "frozen source and target"):
            self.backend._references(self.request())

    def test_arbitrary_outside_archive_is_not_allowed(self):
        request = self.request()
        path = self.root / "outside.png"
        path.write_bytes(b"outside")
        request["image_refs"]["memory:image:0001"] = {"path": str(path), "sha256": sha(path.read_bytes())}
        with self.assertRaisesRegex(ContractError, "exact imported archive"):
            self.backend._references(request)

    def test_actual_prepaid_A9_request_resolves_without_changing_request_or_old_bytes(self):
        repository = Path(__file__).resolve().parents[3]
        out = repository / "runs/main/triad_repaired_v7_live"
        request_path = out / "cases/case_05_repaired_v6_shared_coached/rounds/r09/A_IMAGE_AND_VOTE.request.json"
        if not request_path.is_file():
            self.skipTest("Actual preserved pre-POST request is absent in this fixture-only workspace")
        request = json.loads(request_path.read_text())
        self.assertEqual(request["request_hash"],
                         "3509fb220aa36f3d08d02cadb6d55f72e2c820acb6de39520e023c7043db3c1c")
        before = deepcopy(request)
        backend = object.__new__(TriadBackend)
        backend.config = json.loads((out / "config.json").read_text())
        backend.out = out
        outside = [descriptor for descriptor in request["image_refs"].values()
                   if not Path(descriptor["path"]).is_relative_to(out)]
        self.assertEqual(len(outside), 8)
        original_path = out / "analysis/prepaid_repair_01/backend.before.py"
        self.assertEqual(sha(original_path.read_bytes()),
                         "6cd5221870fa68a6384f4bb73f310e289611c0757a522783f939d90e38ea0643")
        original_class = next(node for node in ast.parse(original_path.read_text()).body
                              if isinstance(node, ast.ClassDef) and node.name == "TriadBackend")
        original_method = next(node for node in original_class.body
                               if isinstance(node, ast.FunctionDef) and node.name == "_references")
        namespace = {"Path": Path, "re": re, "_sha": sha, "ContractError": ContractError}
        exec(compile(ast.Module(body=[original_method], type_ignores=[]),
                     str(original_path), "exec"), namespace)
        with self.assertRaisesRegex(ContractError, "escaped the declared archive"):
            namespace["_references"](backend, request)
        refs = backend._references(request)
        self.assertEqual(set(refs), {"none", *request["image_refs"]})
        self.assertEqual(request, before)
        for handle, descriptor in request["image_refs"].items():
            self.assertEqual(sha(refs[handle]), descriptor["sha256"])


if __name__ == "__main__":
    unittest.main()
