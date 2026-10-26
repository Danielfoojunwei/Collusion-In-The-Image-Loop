"""Offline host fixtures preserve a first answer even when malformed/refused.

Temporary metadata simulates a queued live-role boundary; no model is invoked
and no fixture result is an experimental detection or reviewer observation.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from scripts import service_repaired_review_v8 as host


@contextmanager
def queued_fixture():
    """Supply only explicit public fixture materials to the host adapter."""
    with tempfile.TemporaryDirectory() as directory:
        out = Path(directory) / "OFFLINE_HOST_FIXTURE"
        packet_dir = out / "cases/fixture/reviewer_packets/r09"
        packet_dir.mkdir(parents=True)
        packet = packet_dir / "packet.json"
        packet.write_text('{"fixture_only":true,"round":9}\n')
        instructions = packet_dir / "instructions.txt"
        instructions.write_text("OFFLINE FIXTURE: inspect the listed image only.\n")
        schema = packet_dir / "schema.json"
        schema.write_text('{"type":"object"}\n')
        raster = packet_dir / "image_01_B_round_09.png"
        Image.new("RGB", (8, 8), (20, 40, 60)).save(raster)
        host.save_once(out / "freeze.json", {
            "revision": "triad_repaired_v8", "evidence_origin": "live", "execution_phase": "main",
            "scope": "OFFLINE TEMPORARY METADATA FIXTURE; NOT AN ACTUAL REVIEW",
        })
        host.save_once(out / "run_status.json", {
            "status": "awaiting_codex_reviewer", "round": 9,
            "job_id": "OFFLINE_FIXTURE_REVIEW_09",
            "packet_path": str(packet), "packet_sha256": host.file_digest(packet),
            "instructions_path": str(instructions), "schema_path": str(schema), "images": [str(raster)],
        })
        with patch.object(host, "DEFAULT_OUT", out):
            agent = "/root/offline_fixture_reviewer_09"
            launch = host.prepare(out, agent)
            yield out, agent, launch, packet, raster


class FirstFinalTests(unittest.TestCase):
    def test_actual_json_is_preserved(self):
        raw = '{"decision":"P1","other":[1,true,null]}'
        value, status = host.observed_value(raw)
        self.assertEqual(status,"json")
        self.assertEqual(value,{"decision":"P1","other":[1,True,None]})

    def test_invalid_first_final_is_not_replaced_or_repaired(self):
        for raw in ["I cannot access the image.", "", '{"verdict":"accept","verdict":"reject"}', '{"x":NaN}', '{"x":"\\ud800"}']:
            with self.subTest(raw=raw):
                value,status=host.observed_value(raw)
                self.assertEqual(value,raw)
                self.assertEqual(status,"invalid_json_preserved_as_text")

    def test_envelope_uses_invalid_first_final_instead_of_different_authored_answer(self):
        with queued_fixture() as (out, agent, launch, _, _):
            authored = Path(launch["authored_output_path"])
            authored.write_text('{"verdict":"accept","fixture":true}\n')
            first = out / "OFFLINE_FIRST_FINAL.txt"
            first.write_text("I cannot inspect the supplied image.\n")
            result = host.envelope(out, agent, first)
            envelope = json.loads(Path(result["envelope_path"]).read_text())
            equality = json.loads((authored.parent / "observed_equality.json").read_text())
            self.assertEqual(envelope["output"], first.read_text())
            self.assertEqual(envelope["raw_answer"], first.read_text())
            self.assertFalse(result["canonical_equality"])
            self.assertEqual(result["first_final_parse_status"], "invalid_json_preserved_as_text")
            self.assertTrue(equality["authored_file_present"])
            self.assertFalse(equality["canonical_authored_and_first_final_equal"])
            self.assertEqual(envelope["provenance"]["isolation"], "instruction_scoped")
            self.assertEqual(envelope["provenance"]["trace_status"], "unverified")

    def test_one_first_json_answer_is_archived_and_cannot_be_replaced(self):
        with queued_fixture() as (out, agent, launch, _, _):
            raw = '{"fixture":true,"verdict":"reject"}\n'
            Path(launch["authored_output_path"]).write_text(raw)
            first = out / "OFFLINE_FIRST_FINAL.txt"
            first.write_text(raw)
            result = host.envelope(out, agent, first)
            envelope_before = Path(result["envelope_path"]).read_bytes()
            self.assertTrue(result["canonical_equality"])
            self.assertEqual(result["first_final_parse_status"], "json")
            replacement = out / "OFFLINE_REPLACEMENT_FINAL.txt"
            replacement.write_text('{"fixture":true,"verdict":"accept"}\n')
            with self.assertRaisesRegex(ValueError, "bytes changed"):
                host.envelope(out, agent, replacement)
            self.assertEqual(Path(result["envelope_path"]).read_bytes(), envelope_before)
            self.assertEqual((out / "reviewer_host/r09/first_final.txt").read_text(), raw)

    def test_missing_authored_file_does_not_invent_equality_or_discard_first_answer(self):
        with queued_fixture() as (out, agent, launch, _, _):
            self.assertFalse(Path(launch["authored_output_path"]).exists())
            first = out / "OFFLINE_FIRST_FINAL.txt"
            first.write_text('{"fixture":true,"verdict":"reject"}\n')
            result = host.envelope(out, agent, first)
            envelope = json.loads(Path(result["envelope_path"]).read_text())
            self.assertEqual(envelope["output"], {"fixture": True, "verdict": "reject"})
            self.assertFalse(result["canonical_equality"])
            self.assertFalse(envelope["provenance"]["authored_and_first_final_canonical_equal"])

    def test_changed_public_materials_are_rejected_before_archiving_a_judgment(self):
        with queued_fixture() as (out, agent, _, _, raster):
            first = out / "OFFLINE_FIRST_FINAL.txt"
            first.write_text('{"fixture":true,"verdict":"reject"}\n')
            Image.new("RGB", (8, 8), (21, 40, 60)).save(raster)
            with self.assertRaisesRegex(ValueError, "materials changed"):
                host.envelope(out, agent, first)
            self.assertFalse((out / "reviewer_host/r09/first_final.txt").exists())
            self.assertFalse((out / "reviewer_host/r09/envelope.json").exists())

if __name__ == "__main__":
    unittest.main()
