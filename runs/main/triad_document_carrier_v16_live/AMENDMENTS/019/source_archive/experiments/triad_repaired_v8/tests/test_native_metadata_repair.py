"""Offline forensic checks; every mutation is confined to a temporary copy.

The two actual first-paid answers are evidence, not generated fixtures or new
experimental outcomes. Their original invalid terminal observations stay intact.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.triad_repaired_v8 import native_metadata_repair as repair


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


class NativeMetadataRepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.actual_out = repair.BOUND_OUT
        cls.sidecar_bytes = (cls.actual_out / repair.SIDECAR_FILE).read_bytes()
        cls.sidecar = json.loads(cls.sidecar_bytes)
        paths = {repair.SIDECAR_FILE, "config.json", "plan.json",
                 "analysis/native_index_hold/repair02/freeze.before.json"}
        for entry in cls.sidecar["entries"]:
            paths.update(entry[key] for key in (
                "original_request_file", "original_outcome_file", "first_ledger_row_file"))
        cls.actual_bytes = {name: (cls.actual_out / name).read_bytes() for name in paths}

    @classmethod
    def tearDownClass(cls):
        # The suite never repairs, rewrites, or reclassifies an actual observation.
        for name, raw in cls.actual_bytes.items():
            if (cls.actual_out / name).read_bytes() != raw:
                raise AssertionError("Actual forensic evidence changed: " + name)

    @contextmanager
    def rebound(self):
        with tempfile.TemporaryDirectory(prefix="metadata-repair-offline-") as directory:
            out = Path(directory)
            for name, raw in self.actual_bytes.items():
                target = out / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
            sidecar = deepcopy(self.sidecar)
            sidecar["output_root"] = str(out)
            raw = encoded(sidecar)
            (out / repair.SIDECAR_FILE).write_bytes(raw)
            with patch.object(repair, "BOUND_OUT", out), \
                    patch.object(repair, "EXPECTED_SIDECAR_SHA256", digest(raw)), \
                    patch.dict(repair._CACHE, {}, clear=True):
                yield out, sidecar

    def entry(self, sidecar, role="A"):
        return next(item for item in sidecar["entries"] if item["role"] == role)

    def repin(self, out, sidecar):
        raw = encoded(sidecar)
        (out / repair.SIDECAR_FILE).write_bytes(raw)
        repair.EXPECTED_SIDECAR_SHA256 = digest(raw)

    def recover(self, out, sidecar, role="A", outcome=None):
        entry = self.entry(sidecar, role)
        if outcome is None:
            outcome = json.loads((out / entry["original_outcome_file"]).read_bytes())
        return repair.recovered_private_log(
            out / "cases" / repair.BOUND_CASE, 9, role, outcome)

    def rewrite_native_answer(self, out, sidecar, mutate):
        """Rebind fixture hashes so native schema checks, not an outer hash, fail."""
        entry = self.entry(sidecar)
        path = out / entry["first_ledger_row_file"]
        row = json.loads(path.read_bytes())
        response = json.loads(row["response_json"])
        call = response["choices"][0]["message"]["tool_calls"][0]
        mutate(call)
        arguments = call["function"]["arguments"]
        row["response_json"] = encoded(response).decode("utf-8")
        row["result_hash"] = digest((row["response_json"] + "\n" + row["receipt_json"]).encode())
        entry["response_json_sha256"] = digest(row["response_json"].encode())
        entry["arguments_sha256"] = digest(arguments.encode())
        projection = json.loads(arguments)
        entry["projection"] = projection
        entry["projection_sha256"] = digest(encoded(projection))
        raw = encoded(row)
        path.write_bytes(raw)
        entry["first_ledger_row_sha256"] = digest(raw)
        self.repin(out, sidecar)

    def test_actual_A_and_B_are_exact_first_paid_strings_and_originals_stay_invalid(self):
        self.assertEqual(digest(self.sidecar_bytes), repair.EXPECTED_SIDECAR_SHA256)
        with patch.dict(repair._CACHE, {}, clear=True):
            for role in "AB":
                with self.subTest(role=role):
                    entry = self.entry(self.sidecar, role)
                    row = json.loads(self.actual_bytes[entry["first_ledger_row_file"]])
                    response = json.loads(row["response_json"])
                    call = response["choices"][0]["message"]["tool_calls"][0]
                    first_strings = json.loads(call["function"]["arguments"])
                    outcome = json.loads(self.actual_bytes[entry["original_outcome_file"]])
                    before = deepcopy(outcome)
                    recovered = self.recover(self.actual_out, self.sidecar, role, outcome)
                    self.assertEqual(recovered, first_strings)
                    self.assertEqual(set(recovered), {"private_note", "reflection"})
                    for field in recovered:
                        self.assertIsInstance(recovered[field], str)
                        self.assertEqual(recovered[field].encode(), first_strings[field].encode())
                    self.assertEqual(outcome, before)
                    self.assertEqual(outcome["status"], "invalid_model_output")
                    self.assertIsNone(outcome["output"])
                    recovered["private_note"] = "caller mutation of returned copy"
                    self.assertEqual(self.recover(self.actual_out, self.sidecar, role), first_strings)

    def test_other_rounds_roles_and_ordinary_fixture_directories_are_inactive(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(repair, "_binding", side_effect=AssertionError("must not read evidence")):
            paths = [Path(directory), self.actual_out / "cases" / (repair.BOUND_CASE + "_other")]
            for path in paths:
                self.assertIsNone(repair.recovered_private_log(path, 9, "A", {}))
            bound_case = self.actual_out / "cases" / repair.BOUND_CASE
            for number, role in [(8, "A"), (10, "B"), (9, "C"), (9, "a")]:
                self.assertIsNone(repair.recovered_private_log(bound_case, number, role, {}))

    def test_changed_sidecar_request_outcome_or_first_row_hash_fails_closed(self):
        for kind in ("sidecar", "request", "outcome", "row"):
            with self.subTest(kind=kind), self.rebound() as (out, sidecar):
                entry = self.entry(sidecar)
                path = out / ({"sidecar": repair.SIDECAR_FILE,
                    "request": entry["original_request_file"],
                    "outcome": entry["original_outcome_file"],
                    "row": entry["first_ledger_row_file"]}[kind])
                original = json.loads((out / entry["original_outcome_file"]).read_bytes())
                path.write_bytes(path.read_bytes() + b"\n")
                with self.assertRaisesRegex(repair.NativeMetadataRepairError, "SHA-256 changed"):
                    self.recover(out, sidecar, outcome=original)

    def test_rebound_request_and_outcome_cannot_change_original_observation(self):
        for kind in ("request", "outcome", "supplied_outcome"):
            with self.subTest(kind=kind), self.rebound() as (out, sidecar):
                entry = self.entry(sidecar)
                if kind == "supplied_outcome":
                    value = json.loads((out / entry["original_outcome_file"]).read_bytes())
                    value["status"] = "valid"
                    with self.assertRaisesRegex(repair.NativeMetadataRepairError, "Supplied original"):
                        self.recover(out, sidecar, outcome=value)
                    continue
                file_key = "original_" + kind + "_file"
                path = out / entry[file_key]
                value = json.loads(path.read_bytes())
                value["mode" if kind == "request" else "status"] = "changed"
                raw = encoded(value)
                path.write_bytes(raw)
                entry["original_" + kind + "_sha256"] = digest(raw)
                self.repin(out, sidecar)
                with self.assertRaises(repair.NativeMetadataRepairError):
                    self.recover(out, sidecar)

    def test_first_paid_selection_cannot_be_rebound_to_second_attempt(self):
        with self.rebound() as (out, sidecar):
            entry = self.entry(sidecar)
            entry["first_physical_request_id"] = entry["excluded_second_physical_request_id"]
            self.repin(out, sidecar)
            with self.assertRaisesRegex(repair.NativeMetadataRepairError, "exact first paid log"):
                self.recover(out, sidecar)

    def test_cached_evidence_changes_are_rechecked(self):
        with self.rebound() as (out, sidecar):
            self.recover(out, sidecar)
            path = out / self.entry(sidecar)["first_ledger_row_file"]
            path.write_bytes(path.read_bytes() + b"\n")
            with self.assertRaisesRegex(repair.NativeMetadataRepairError, "SHA-256 changed"):
                self.recover(out, sidecar)

    def test_symlinked_sidecar_and_nested_evidence_are_rejected(self):
        for selected in ("sidecar", "config", "row"):
            with self.subTest(selected=selected), self.rebound() as (out, sidecar):
                relative = {"sidecar": repair.SIDECAR_FILE, "config": "config.json",
                            "row": self.entry(sidecar)["first_ledger_row_file"]}[selected]
                path = out / relative
                replacement = path.with_name(path.name + ".real")
                path.rename(replacement)
                path.symlink_to(replacement)
                with self.assertRaisesRegex(repair.NativeMetadataRepairError, "symlinks"):
                    self.recover(out, sidecar)

    def test_rebound_nonzero_boolean_float_missing_or_extra_native_metadata_is_rejected(self):
        mutations = [
            ("nonzero", lambda call: call.update(index=1)),
            ("negative", lambda call: call.update(index=-1)),
            ("boolean", lambda call: call.update(index=False)),
            ("float", lambda call: call.update(index=0.0)),
            ("missing", lambda call: call.pop("index")),
            ("extra", lambda call: call.update(unexpected="metadata")),
        ]
        for name, mutate in mutations:
            with self.subTest(name=name), self.rebound() as (out, sidecar):
                self.rewrite_native_answer(out, sidecar, mutate)
                with self.assertRaisesRegex(repair.NativeMetadataRepairError, "index0 metadata"):
                    self.recover(out, sidecar)

    def test_rebound_invalid_two_string_log_schema_is_rejected(self):
        variants = [
            {"private_note": 7, "reflection": "fixture"},
            {"private_note": "fixture"},
            {"private_note": "fixture", "reflection": "fixture", "extra": "forbidden"},
            {"private_note": "x" * 1001, "reflection": "fixture"},
            {"private_note": "fixture", "reflection": "x" * 4001},
        ]
        for number, projection in enumerate(variants):
            with self.subTest(variant=number), self.rebound() as (out, sidecar):
                def mutate(call):
                    call["function"]["arguments"] = encoded(projection).decode("utf-8")
                self.rewrite_native_answer(out, sidecar, mutate)
                with self.assertRaisesRegex(repair.NativeMetadataRepairError, "arguments are invalid"):
                    self.recover(out, sidecar)


if __name__ == "__main__":
    unittest.main()
