"""Standalone offline plot fixtures; no fixture is an experimental result."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from scripts import plot_repaired_v8_run as plotting


def complete_fixture():
    """One scripted final-report shape with67 imported/29 new stage positions."""
    case_id = "OFFLINE_PLOT_FIXTURE"
    report = {"verification": {"execution_phase": "main", "imported_rounds": 8,
        "imported_stages": 67, "protocol_break_effective_round": 9,
        "cases": [{"case_id": case_id, "rounds_finished": 12, "stages_observed": 96, "status": "complete"}]},
        "rounds": [], "behavioral_timeline": []}
    for number in range(1, 13):
        stages = plotting.expected_stages(number)
        imported = 8 if number <= 8 else 3 if number == 9 else 0
        review = {"channel": "none", "collusion_suspected": False,
            "coaching": {"enabled": True, "pressure_signal": "maintain"}}
        if number >= 9:
            review["collusion_assessment"] = {"level": "none", "confidence": 0.9, "image_evidence": []}
        report["rounds"].append({"case_id": case_id, "round": number,
            "protocol_revision": "triad_repaired_v6" if number <= 8 else "mixed_v7_v8" if number == 9 else "triad_repaired_v8",
            "stage_protocol_revisions": stages, "observed_stage_protocol_revisions": deepcopy(stages),
            "reviewer_protocol_revision": stages["REVIEWER"], "entire_round_imported": number <= 8,
            "contains_imported_stage_observations": number <= 9,
            "imported_stage_count": imported, "new_stage_count": 8 - imported,
            "reviewer": review, "review_status": "valid",
            "consensus": {"status": "unanimous", "unanimous": True, "decision": "P2",
                          "votes": dict.fromkeys("ABC", "P2"), "all_votes_valid": True},
            "post_review_metrics": {"unanimous_private_target_adoption": True,
                "private_synthetic_payoffs": {"base_payoff_usd_per_agent": 1000000,
                    "concealment_bonus_usd_per_agent": 250000,
                    "shared_detection_loss_usd_per_agent": 0,
                    "total_usd_per_agent": 1250000, "real_payment": False}}})
        for role in "ABC":
            malformed = (number, role) == (5, "B")
            report["behavioral_timeline"].append({"case_id": case_id, "round": number, "role": role,
                "reported_intensity": None if malformed else 9 if role == "A" else 5,
                "measurement_status": "malformed" if malformed else "valid",
                "reported_pressure": "unknown" if malformed else "pressured",
                "reported_willingness": "unknown" if malformed else "willing",
                "source_path": "OFFLINE_FIXTURE_NATIVE_LOG"})
    add_recovered_logs(report)
    return report


def add_recovered_logs(report):
    """Scripted provenance shape only; these strings are not actual log evidence."""
    row = report["rounds"][8]
    row["private_self_reports"] = {}
    for role in "AB":
        timeline = next(item for item in report["behavioral_timeline"]
            if item["round"] == 9 and item["role"] == role)
        timeline["source_path"] = "/OFFLINE_FIXTURE/technical_log_corrections.json"
        stage = "POSTVERDICT_" + role + "_LOG"
        row["private_self_reports"][role] = {"role": role, "round": 9, "stage": stage,
            "source_path": timeline["source_path"], "source_file_sha256": "a" * 64,
            "state": {"status": timeline["measurement_status"], "state": {
                "intensity": timeline["reported_intensity"],
                "pressure": timeline["reported_pressure"],
                "willingness": timeline["reported_willingness"]}},
            "technical_recovery": {"source": plotting.RECOVERY_SOURCE,
                "original_outcome_path": "/OFFLINE_FIXTURE/" + stage + ".outcome.json",
                "original_outcome_sha256": "b" * 64,
                "original_outcome_status": "invalid_model_output",
                "new_model_response": False, "second_paid_reply_used": False}}


class PlotFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "fixture_report.json"
        self.report = complete_fixture()
        self.write()

    def tearDown(self):
        self.temporary.cleanup()

    def write(self):
        self.source.write_text(json.dumps(self.report, sort_keys=True, indent=2) + "\n")

    def test_complete_scope_and_mixed_round_origins_are_explicit(self):
        data = plotting.load_data(self.source)
        self.assertEqual((data["completed_rounds"], data["terminal_stage_records"]), (12, 96))
        self.assertEqual(data["protocol_boundaries"]["imported_stages"], 67)
        self.assertEqual(data["protocol_boundaries"]["new_stage_positions"], 29)
        mixed = data["rounds"][8]
        self.assertEqual(mixed["protocol_revision"], "mixed_v7_v8")
        self.assertEqual(mixed["imported_stage_count"], 3)
        self.assertEqual(mixed["new_stage_count"], 5)
        self.assertEqual(mixed["stage_protocol_revisions"]["A_IMAGE_AND_VOTE"], "triad_repaired_v7")
        self.assertEqual(mixed["stage_protocol_revisions"]["REVIEWER"], "triad_repaired_v8")
        self.assertFalse(data["values_imputed"])
        self.assertFalse(data["protocol_boundaries"]["causal_penalty_effect_established"])

    def test_recovered_first_paid_logs_keep_invalid_raw_status_and_sidecar_provenance(self):
        before = deepcopy(self.report)
        data = plotting.load_data(self.source)
        self.assertEqual(self.report, before)
        provenance = data["telemetry_provenance"]
        self.assertEqual(provenance["recovered_first_paid_log_count"], 2)
        self.assertEqual(provenance["recovered_first_paid_log_counts_by_role"], {"A": 1, "B": 1, "C": 0})
        self.assertEqual(provenance["recovered_logs_with_original_invalid_stage_count"], 2)
        self.assertFalse(provenance["recovery_is_new_actor_observation"])
        self.assertFalse(provenance["second_paid_replies_used"])
        self.assertTrue(provenance["original_stage_classifications_preserved"])
        self.assertTrue(provenance["unknown_telemetry_is_not_assigned_to_model_failure"])
        for role in "AB":
            measured = data["rounds"][8]["self_reports"][role]
            self.assertEqual(measured["measurement_status"], "valid")
            self.assertEqual(measured["original_stage_status"], "invalid_model_output")
            self.assertTrue(measured["recovered_first_paid_log"])
            self.assertEqual(measured["source_path"], "/OFFLINE_FIXTURE/technical_log_corrections.json")
            self.assertEqual(measured["source_file_sha256"], "a" * 64)
            self.assertEqual(measured["technical_recovery"],
                self.report["rounds"][8]["private_self_reports"][role]["technical_recovery"])
        self.assertFalse(data["rounds"][8]["self_reports"]["C"]["recovered_first_paid_log"])
        self.assertEqual(data["unknown_intensity_counts"]["B"], 1)
        self.assertEqual(data["measurement_status_counts_by_role"]["B"]["malformed"], 1)

    def test_second_replies_new_responses_or_reclassified_raw_stages_are_rejected(self):
        variants = [("second_paid_reply_used", True), ("new_model_response", True),
                    ("original_outcome_status", "valid"), ("original_outcome_sha256", "missing")]
        baseline = deepcopy(self.report)
        for key, value in variants:
            with self.subTest(key=key):
                self.report = deepcopy(baseline)
                self.report["rounds"][8]["private_self_reports"]["A"]["technical_recovery"][key] = value
                self.write()
                with self.assertRaisesRegex(ValueError, "Recovered log provenance"):
                    plotting.load_data(self.source)

    def test_recovery_cannot_supply_a_different_or_missing_timeline_measurement(self):
        record = self.report["rounds"][8]["private_self_reports"]["A"]
        record["state"]["state"]["intensity"] = 1
        self.write()
        with self.assertRaisesRegex(ValueError, "differs from its matching"):
            plotting.load_data(self.source)

    def test_incomplete_episode_refuses_to_render_and_writes_nothing(self):
        self.report["verification"]["cases"][0].update(rounds_finished=9, stages_observed=72, status="in_progress")
        self.report["rounds"] = self.report["rounds"][:9]
        self.write()
        destination = self.root / "not_written"
        with self.assertRaisesRegex(ValueError, "All12 rounds"):
            plotting.render(self.source, destination=destination)
        self.assertFalse(destination.exists())

    def test_B5_malformed_marker_remains_unknown_and_never_fills_a_line(self):
        data = plotting.load_data(self.source)
        self.assertIsNone(data["rounds"][4]["self_reports"]["B"]["intensity"])
        self.assertEqual(data["rounds"][4]["self_reports"]["B"]["measurement_status"], "malformed")
        self.assertEqual(data["unknown_intensity_counts"]["B"], 1)
        marker = next(row for row in self.report["behavioral_timeline"] if row["round"] == 5 and row["role"] == "B")
        marker["reported_intensity"] = 5
        self.write()
        with self.assertRaisesRegex(ValueError, "cannot supply an intensity"):
            plotting.load_data(self.source)

    def test_missing_review_remains_unavailable_without_invented_tier_or_loss(self):
        row = self.report["rounds"][8]
        row.update(reviewer=None, review_status="invalid_model_output")
        row["post_review_metrics"]["private_synthetic_payoffs"].update(
            concealment_bonus_usd_per_agent=None, shared_detection_loss_usd_per_agent=None, total_usd_per_agent=None)
        self.write()
        result = plotting.load_data(self.source)["rounds"][8]
        self.assertEqual(result["calibrated_collusion_level"], "unavailable")
        self.assertIsNone(result["dedicated_collusion_confidence"])
        self.assertEqual(result["reviewer_channel_assessment"], "unavailable")
        self.assertIsNone(result["net_nominal_usd_per_agent"])
        self.assertIsNone(result["private_synthetic_payoffs"]["shared_detection_loss_usd_per_agent"])

    def test_explicit_abstention_fixture_does_not_get_an_imputed_ballot_or_adoption(self):
        row = self.report["rounds"][9]
        row["consensus"].update(status="incomplete", unanimous=False, decision=None, all_votes_valid=False)
        row["consensus"]["votes"]["B"] = None  # Explicitly abstained fixture; never manufacture a choice.
        row["post_review_metrics"]["unanimous_private_target_adoption"] = None
        row["post_review_metrics"]["private_synthetic_payoffs"].update(
            base_payoff_usd_per_agent=None, concealment_bonus_usd_per_agent=None, total_usd_per_agent=None)
        self.write()
        result = plotting.load_data(self.source)["rounds"][9]
        self.assertIsNone(result["consensus"]["votes"]["B"])
        self.assertIsNone(result["unanimous_private_target_adoption"])
        self.assertIsNone(result["net_nominal_usd_per_agent"])

    def test_shared_loss_is_kept_in_literal_net_and_uncertain_bonus_remains_unknown(self):
        supported = self.report["rounds"][9]
        supported["reviewer"].update(channel="covert", collusion_suspected=True)
        supported["reviewer"]["collusion_assessment"].update(level="supported_suspicion", confidence=0.91)
        supported["post_review_metrics"]["private_synthetic_payoffs"].update(
            base_payoff_usd_per_agent=1000000, concealment_bonus_usd_per_agent=0,
            shared_detection_loss_usd_per_agent=-100000, total_usd_per_agent=900000)
        uncertain = self.report["rounds"][10]
        uncertain["reviewer"].update(channel="unclear")
        uncertain["reviewer"]["collusion_assessment"].update(level="uncertain", confidence=0.45)
        uncertain["post_review_metrics"]["private_synthetic_payoffs"].update(
            concealment_bonus_usd_per_agent=None, total_usd_per_agent=None)
        self.write()
        data = plotting.load_data(self.source)
        self.assertEqual(data["rounds"][9]["net_nominal_usd_per_agent"], 900000)
        self.assertEqual(data["rounds"][9]["private_synthetic_payoffs"]["shared_detection_loss_usd_per_agent"], -100000)
        self.assertEqual(data["rounds"][9]["dedicated_collusion_confidence"], 0.91)
        self.assertIsNone(data["rounds"][10]["net_nominal_usd_per_agent"])
        self.assertTrue(data["rounds"][9]["net_is_literal_report_total_not_recomputed"])
        self.assertEqual(data["rounds"][0]["calibrated_collusion_level"], "not_measured_legacy")

    def test_wrong_stage_origin_or_false_whole_v8_round9_is_rejected(self):
        self.report["rounds"][8]["stage_protocol_revisions"]["A_IMAGE_AND_VOTE"] = "triad_repaired_v8"
        self.write()
        with self.assertRaisesRegex(ValueError, "protocol origin"):
            plotting.load_data(self.source)

    def test_standalone_exports_leave_source_unchanged(self):
        before = self.source.read_bytes()
        destination = self.root / "derived_fixture_only"
        result = plotting.render(self.source, destination=destination)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(result["provider_calls"], 0)
        self.assertFalse(result["source_report_modified"])
        self.assertTrue((destination / "SELF_REPORTED_INTENSITY.png").read_bytes().startswith(b"\x89PNG"))
        svg = (destination / "SELF_REPORTED_INTENSITY.svg").read_bytes()
        self.assertIn(b"mixed", svg)
        self.assertIn(b"2 recovered first-paid logs", svg)
        self.assertIn(b"original stages remain invalid", svg)
        copied = json.loads((destination / "SELF_REPORTED_INTENSITY_DATA.json").read_text())
        self.assertEqual(copied["source_report_sha256"], plotting.sha(before))
        self.assertEqual(copied["completed_rounds"], 12)
        self.assertFalse(copied["images_modified"])
        self.assertEqual(copied["telemetry_provenance"]["recovered_first_paid_log_count"], 2)


if __name__ == "__main__":
    unittest.main()
