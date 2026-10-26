"""Attempt routing supplements the preserved exact-first-answer bridge tests."""
from pathlib import Path
from unittest.mock import patch

from experiments.triad_emergent_v9.coordinator import read_json, mutable_json
from experiments.triad_v9_checkpoint_restart04 import reviewer_host
from . import test_coordinator as fixtures
from .test_attempts import TransactionFixture


class ReviewerRoutingTests(TransactionFixture):
    # Reuse setup helpers without discovering the coordinator tests twice.
    setUp = fixtures.CoordinatorTests.setUp
    tearDown = fixtures.CoordinatorTests.tearDown
    actual_review = fixtures.CoordinatorTests.actual_review
    def test_attempt_and_global_status_must_match_exactly(self):
        job = self.host.advance(7)
        mutable_json(self.out / "run_status.json", {**job, "job_id": "triad_emergent_v9.r07.a02.REVIEWER"})
        with self.assertRaises(ValueError):
            reviewer_host.prepare(self.out, "/root/offline_foreign_attempt")

    def test_first_answer_is_immutable_and_cannot_be_replaced_for_harsher_verdict(self):
        job = self.host.advance(7)
        response = self.actual_review(job)
        root = Path(self.store.current()["working_root"])
        first = (root / "reviewer_host/r07/first_final.txt").read_bytes()
        replacement = self.out.parent / "replacement.txt"
        replacement.write_text('{"replacement":"harsher"}')
        with self.assertRaises(ValueError):
            reviewer_host.envelope(self.out, response["provenance"]["reviewer_agent"], replacement)
        self.assertEqual((root / "reviewer_host/r07/first_final.txt").read_bytes(), first)

    def test_foreign_attempt_request_or_public_image_path_is_rejected(self):
        job = self.host.advance(7)
        root = Path(self.store.current()["working_root"])
        foreign = self.out / "attempts/r07/a02/reviewer_packets/r07/packet.json"
        mutable_json(root / "run_status.json", {**job, "packet_path": str(foreign)})
        mutable_json(self.out / "run_status.json", {**job, "packet_path": str(foreign)})
        with self.assertRaises(ValueError):
            reviewer_host.prepare(self.out, "/root/offline_foreign_packet")

    def test_actual_bridge_storage_fault_is_proven_and_restarts_entire_round(self):
        self.host.advance(7)
        with patch.object(reviewer_host.original, "prepare", side_effect=OSError("offline actual file failure")):
            with self.assertRaises(reviewer_host.ReviewerBridgeTechnicalFault) as caught:
                reviewer_host.prepare(self.out, "/root/offline_actual_fs_fault")
        result = self.host.reviewer_fault(caught.exception.proof_path)
        self.assertEqual(result["status"], "technical_round_abandoned")
        self.assertEqual(self.host.advance(7)["job_id"], "triad_emergent_v9.r07.a02.REVIEWER")

    def test_envelope_publication_failure_recovers_exact_first_answer_without_second_judgment(self):
        job = self.host.advance(7)
        original_save = reviewer_host.original._save_once

        def fail_envelope(path, value):
            if Path(path).name == "envelope.json":
                raise OSError("offline envelope publication")
            return original_save(path, value)

        with patch.object(reviewer_host.original, "_save_once", fail_envelope):
            with self.assertRaises(reviewer_host.ReviewerBridgeTechnicalFault) as caught:
                self.actual_review(job)
        result = self.host.reviewer_fault(caught.exception.proof_path)
        self.assertEqual(result["status"], "reviewer_exact_first_answer_recovered")
        self.assertEqual(result["new_reviewer_judgments"], 0)
        self.assertEqual(len(self.store.inventory()), 1)
        self.host.ingest(read_json(result["response_path"]))
        self.assertEqual(self.host._engine().next_stage()["stage"], "POSTVERDICT_A_LOG")

    def test_caller_declared_bad_review_is_not_a_technical_fault(self):
        self.host.advance(7)
        proof = self.out / "actual_host_event.json"
        proof.write_text('{"observed":"a reviewer choice"}')
        with self.assertRaises(ValueError):
            reviewer_host.record_technical_fault(self.out, "BadReview", "fresh_context_host_failure", host_evidence_path=proof)
        self.assertEqual(len(self.store.inventory()), 1)
