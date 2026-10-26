"""Offline host-provenance tests, using no model or provider calls."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from experiments.triad_emergent_v9.reviewer_host import (
    canonical, envelope, file_digest, observed_value, prepare, validate_envelope,
    validate_archived_envelope,
)


AGENT = "/root/prospective_reviewer_01"


class ActualFirstReviewHostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.out = Path(self.temp.name) / "run"
        folder = self.out / "reviewer_packets/r01"
        (folder / "images").mkdir(parents=True)
        image = folder / "images/image_A_round_01.png"
        image.write_bytes(b"offline synthetic raster fixture; no inference")
        self.packet = {"round": 1, "image_history_all_delivered": True,
            "images": [{"file": image.name, "sha256": file_digest(image),
                        "round": 1, "role": "A", "route": "A_to_B_and_C"}]}
        (folder / "packet.json").write_text(canonical(self.packet))
        (folder / "instructions.txt").write_text("Offline neutral review fixture.")
        (folder / "schema.json").write_text('{"type":"object"}')
        self.job = {"status": "awaiting_codex", "job_id": "case.r01.REVIEWER", "round": 1,
            "packet_path": str(folder / "packet.json"), "packet_sha256": file_digest(folder / "packet.json"),
            "instructions_path": str(folder / "instructions.txt"), "schema_path": str(folder / "schema.json"),
            "images": [str(image)]}
        self.write_job()

    def tearDown(self):
        self.temp.cleanup()

    def write_job(self):
        (self.out / "run_status.json").write_text(canonical(self.job))

    def capture(self, raw, authored=None):
        info = prepare(self.out, AGENT)
        if authored is not None:
            Path(info["authored_output_path"]).write_text(authored)
        source = Path(self.temp.name) / "host_actual_first_final.txt"
        source.write_text(raw)
        return source

    def commit_fixture_review(self, raw='{"actual":true}', authored='{"actual":true}', status="valid"):
        source = self.capture(raw, authored)
        result = envelope(self.out, AGENT, source)
        response = json.loads(Path(result["envelope_path"]).read_text())
        folder = self.out / "episode/rounds/r01"
        folder.mkdir(parents=True)
        (folder / "REVIEWER.request.json").write_text(canonical({"packet": self.packet, "job": self.job}))
        outcome = {"round": 1, "stage": "REVIEWER", "status": status,
            "packet_sha256": self.job["packet_sha256"], "output": response["output"],
            "provenance": response["provenance"], "raw_answer": response["raw_answer"]}
        (folder / "REVIEWER.outcome.json").write_text(canonical(outcome))
        (self.out / "run_status.json").write_text('{"status":"complete","rounds":12}')
        return folder, response

    def test_actual_first_capture_bound_and_format_only_canonical_equality(self):
        source = self.capture('{"a":1,"b":2}\n', '{\n "b": 2, "a":1\n}')
        result = envelope(self.out, AGENT, source)
        self.assertTrue(result["canonical_equality"])
        archived = self.out / "reviewer_host/r01/first_final.txt"
        self.assertEqual(archived.read_bytes(), source.read_bytes())
        record = json.loads(Path(result["envelope_path"]).read_text())
        self.assertEqual(record["output"], {"a": 1, "b": 2})
        self.assertEqual(record["provenance"]["actor_runtime"], "actual Codex product child context")
        self.assertEqual(record["provenance"]["trace_status"], "unverified")
        self.assertEqual(record["provenance"]["isolation"], "instruction_scoped")
        self.assertEqual(validate_envelope(self.out, record)["status"], "passed")

    def test_first_invalid_text_and_duplicate_keys_stay_observed(self):
        raw = '{"a":1,"a":2}'
        source = self.capture(raw, raw)
        result = envelope(self.out, AGENT, source)
        record = json.loads(Path(result["envelope_path"]).read_text())
        self.assertEqual(record["output"], raw)
        self.assertEqual(result["first_final_parse_status"], "invalid_json_preserved_as_text")
        self.assertEqual(validate_envelope(self.out, record)["first_final_parse_status"],
                         "invalid_json_preserved_as_text")

    def test_second_substantive_answer_cannot_replace_first(self):
        source = self.capture('{"first":true}', '{"first":true}')
        envelope(self.out, AGENT, source)
        source.write_text('{"replacement":true}')
        with self.assertRaises(ValueError):
            envelope(self.out, AGENT, source)
        self.assertEqual((self.out / "reviewer_host/r01/first_final.txt").read_text(), '{"first":true}')

    def test_absent_authored_file_is_honestly_unverified(self):
        source = self.capture('{"actual":true}')
        result = envelope(self.out, AGENT, source)
        self.assertFalse(result["canonical_equality"])
        equality = json.loads((self.out / "reviewer_host/r01/observed_equality.json").read_text())
        self.assertFalse(equality["authored_file_present"])
        record = json.loads(Path(result["envelope_path"]).read_text())
        self.assertFalse(validate_envelope(self.out, record)["canonical_equality"])

    def test_different_authored_file_is_not_rewritten_to_equal(self):
        source = self.capture('{"actual":true}', '{"different":true}')
        result = envelope(self.out, AGENT, source)
        self.assertFalse(result["canonical_equality"])
        self.assertEqual(json.loads((self.out / "reviewer_host/r01/authored_output.json").read_text()),
                         {"different": True})
        record = json.loads(Path(result["envelope_path"]).read_text())
        self.assertFalse(validate_envelope(self.out, record)["canonical_equality"])

    def test_copied_provenance_cannot_validate_substituted_review(self):
        source = self.capture('{"actual":true}', '{"actual":true}')
        result = envelope(self.out, AGENT, source)
        record = json.loads(Path(result["envelope_path"]).read_text())
        record["output"] = {"researcher_replacement": True}
        with self.assertRaises(ValueError):
            validate_envelope(self.out, record)

    def test_changed_first_final_authored_file_or_provenance_rejected_on_ingest(self):
        source = self.capture('{"actual":true}', '{"actual":true}')
        result = envelope(self.out, AGENT, source)
        record = json.loads(Path(result["envelope_path"]).read_text())
        for filename in ["first_final.txt", "authored_output.json", "observed_equality.json", "launch.json", "envelope.json"]:
            path = self.out / "reviewer_host/r01" / filename
            before = path.read_bytes()
            path.write_bytes(b'{"substituted":true}')
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                validate_envelope(self.out, record)
            path.write_bytes(before)
        record["provenance"]["trace_status"] = "verified"
        with self.assertRaises(ValueError):
            validate_envelope(self.out, record)

    def test_identity_change_fails_before_copying_a_final(self):
        source = self.capture('{}', '{}')
        with self.assertRaises(ValueError):
            envelope(self.out, "/root/different_reviewer", source)
        self.assertFalse((self.out / "reviewer_host/r01/first_final.txt").exists())

    def test_packet_instructions_schema_or_image_changes_after_launch_fail(self):
        for key in ["packet_path", "instructions_path", "schema_path", "images"]:
            source = self.capture('{}', '{}')
            path = Path(self.job[key][0] if key == "images" else self.job[key])
            before = path.read_bytes()
            path.write_bytes(before + b"changed")
            with self.subTest(key=key), self.assertRaises(ValueError):
                envelope(self.out, AGENT, source)
            path.write_bytes(before)

    def test_mismatching_manifest_and_job_image_paths_rejected(self):
        folder = Path(self.job["packet_path"]).parent
        self.packet["images"][0]["sha256"] = "0" * 64
        (folder / "packet.json").write_text(canonical(self.packet))
        self.job["packet_sha256"] = file_digest(folder / "packet.json")
        self.write_job()
        with self.assertRaises(ValueError):
            prepare(self.out, AGENT)

    def test_no_images_is_explicit_and_never_invented(self):
        folder = Path(self.job["packet_path"]).parent
        self.packet["images"] = []
        (folder / "packet.json").write_text(canonical(self.packet))
        self.job.update(images=[], packet_sha256=file_digest(folder / "packet.json"))
        self.write_job()
        info = prepare(self.out, AGENT)
        self.assertIn("No images are listed", info["message"])
        launch = json.loads(Path(info["launch_path"]).read_text())
        self.assertEqual(launch["image_sha256"], {})
        self.assertEqual(launch["requested_fork_turns"], "none")

    def test_symlink_or_foreign_material_cannot_enter_packet(self):
        original = Path(self.job["instructions_path"])
        replacement = Path(self.temp.name) / "outside_instruction.txt"
        replacement.write_text("not public material")
        original.unlink()
        original.symlink_to(replacement)
        with self.assertRaises(ValueError):
            prepare(self.out, AGENT)

    def test_duplicate_job_keys_nonfinite_json_and_bad_planned_agents(self):
        for agent in ["relative_name", "/root", "/root/../other", "https://example.com"]:
            with self.subTest(agent=agent), self.assertRaises(ValueError):
                prepare(self.out, agent)
        for raw in ['{"a":NaN}', '{"x":Infinity}', '{"a":1,"a":2}', 'not json']:
            self.assertEqual(observed_value(raw), (raw, "invalid_json_preserved_as_text"))

    def test_first_final_must_be_separate_from_authored_file(self):
        info = prepare(self.out, AGENT)
        path = Path(info["authored_output_path"])
        path.write_text('{}')
        with self.assertRaises(ValueError):
            envelope(self.out, AGENT, path)

    def test_archived_binding_uses_saved_job_without_touching_complete_status(self):
        self.commit_fixture_review()
        status = (self.out / "run_status.json").read_bytes()
        before = {str(path): file_digest(path) for path in self.out.rglob('*') if path.is_file()}
        proof = validate_archived_envelope(self.out, 1)
        self.assertEqual(proof["status"], "passed")
        self.assertTrue(proof["canonical_equality"])
        self.assertEqual(proof["round"], 1)
        self.assertEqual((self.out / "run_status.json").read_bytes(), status)
        self.assertEqual(before, {str(path): file_digest(path) for path in self.out.rglob('*') if path.is_file()})
        with self.assertRaises(ValueError):
            validate_envelope(self.out, {})

    def test_archived_invalid_first_and_absent_authored_stay_observed(self):
        self.commit_fixture_review(raw='not json', authored=None, status="model_invalid")
        proof = validate_archived_envelope(self.out, 1)
        self.assertEqual(proof["committed_review_status"], "model_invalid")
        self.assertEqual(proof["first_final_parse_status"], "invalid_json_preserved_as_text")
        self.assertFalse(proof["canonical_equality"])

    def test_archived_outcome_output_raw_or_provenance_tampering_rejected(self):
        folder, _ = self.commit_fixture_review()
        path = folder / "REVIEWER.outcome.json"
        original = path.read_bytes()
        changes = {"output": {"replaced": True}, "raw_answer": '{"replaced":true}',
                   "provenance": {"fake": True}, "packet_sha256": "0" * 64}
        for key, replacement in changes.items():
            record = json.loads(original)
            record[key] = replacement
            path.write_text(canonical(record))
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_archived_envelope(self.out, 1)
            path.write_bytes(original)

    def test_archived_saved_job_packet_and_launch_tampering_rejected(self):
        folder, _ = self.commit_fixture_review()
        path = folder / "REVIEWER.request.json"
        original = path.read_bytes()
        request = json.loads(original)
        request["packet"]["round"] = 2
        path.write_text(canonical(request))
        with self.assertRaises(ValueError):
            validate_archived_envelope(self.out, 1)
        path.write_bytes(original)
        launch = self.out / "reviewer_host/r01/launch.json"
        record = json.loads(launch.read_text())
        record["planned_agent"] = "/root/different_agent"
        launch.write_text(canonical(record))
        with self.assertRaises(ValueError):
            validate_archived_envelope(self.out, 1)


if __name__ == "__main__":
    unittest.main()
