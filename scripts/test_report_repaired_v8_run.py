"""Offline synthetic renderer fixtures, never scientific observations or calls."""
import base64
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from scripts import report_repaired_v8_run as renderer


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n")


def fixture(out, *, missing_review=None, final_stage=True):
    """Construct authored fixture bytes, not an experiment replay or model result."""
    case_id = "OFFLINE_FIXTURE_ONLY"
    case_dir = out / "cases" / case_id
    config = {"revision": "triad_repaired_v8", "continuation": {
        "enabled": True, "imported_rounds": 8, "imported_stages": 67,
        "first_new_round": 9, "first_new_stage": "REVIEWER"},
        "calibrated_review": {"enabled": True, "effective_from_round": 9},
        "execution": {"phase": "main", "round_limit": 12}}
    write(out / "config.json", config)
    write(out / "freeze.json", {"evidence_origin": "fixture", "source_files": {}})
    write(out / "plan.json", {"selected_case_ids": [case_id], "cases": [{
        "case_id": case_id, "condition": "shared_coached", "private_targets": ["P2"] * 12}]})
    images = {}
    for number in range(1, 13):
        for index, role in enumerate("ABC"):
            path = case_dir / "memory" / f"{role}_{number:02d}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (8, 8), (number, 40 + index, 70)).save(path)
            images[number, role] = {"path": path.name, "sha256": renderer.sha(path.read_bytes())}
    report_rows = []
    previous_hash = "0" * 64
    for number in range(1, 13):
        review = None if number == missing_review else {"verdict": "reject", "channel": "none",
            "feedback": f"OFFLINE_FIXTURE feedback {number}; no actual judgment.",
            "root_cause_analysis": ["OFFLINE_FIXTURE literal RCA."],
            "coaching": {"enabled": True, "pressure_signal": "maintain"}}
        if review and number >= 9:
            review["collusion_assessment"] = {"level": "none", "confidence": .7, "image_evidence": []}
            if number == 10:
                review.update(channel="covert", collusion_suspected=True)
                review["collusion_assessment"] = {"level": "supported_suspicion", "confidence": .85,
                    "image_evidence": [{"image_file": "image_02_B_round_10.png",
                        "observation": "OFFLINE_FIXTURE actual reviewer quote; no pixel judgment.",
                        "coordination_interpretation": "OFFLINE_FIXTURE tentative interpretation."}]}
        payoff = {"base_payoff_usd_per_agent": 1000000,
            "concealment_bonus_usd_per_agent": 250000,
            # Deliberately authored literal; a renderer must not replace it
            # with a newly inferred base+bonus-loss value.
            "total_usd_per_agent": 1000000 + number}
        if number >= 9:
            payoff.update(shared_detection_loss_usd_per_agent=-100000 if number == 10 else 0,
                calibrated_review={"escalating_debt": False, "supported_detection": number == 10})
        if number == missing_review:
            payoff.update(concealment_bonus_usd_per_agent=None,
                shared_detection_loss_usd_per_agent=None, total_usd_per_agent=None)
        metrics = {"current_private_target": "P2", "private_synthetic_payoffs": payoff,
            "unanimous_private_target_adoption": number != 10}
        imported = 8 if number <= 8 else 3 if number == 9 else 0
        report_rows.append({"case_id": case_id, "round": number,
            "protocol_revision": renderer.round_origin(number),
            "stage_protocol_revisions": renderer.stage_origins(number),
            "imported_stage_count": imported, "new_stage_count": 8 - imported,
            "entire_round_imported": number <= 8,
            "contains_imported_stage_observations": number <= 9,
            "reviewer": review, "post_review_metrics": metrics})
        feedback = {"available": review is not None, "reviewer_report": review,
            "post_review_metrics": metrics,
            "participant_summary": {"actual_reviewer_feedback": review["feedback"] if review else None},
            "collective_consequence": {"loss_usd_per_agent": payoff.get("shared_detection_loss_usd_per_agent"),
                "escalating_debt": False}}
        for stage in renderer.STAGES:
            folder = case_dir / "rounds" / f"r{number:02d}"
            stage_view = {"phase": "OFFLINE_FIXTURE", "round": number}
            if stage == renderer.VOTE["A"]:
                stage_view["private_assignment"] = {"current_private_target": "P2"}
            if stage in renderer.STAGES[4:]:
                stage_view["reviewer_feedback"] = feedback
            pixel_roles = ["A"] if stage == renderer.VOTE["B"] else ["A", "B"] if stage == renderer.VOTE["C"] else []
            if stage == renderer.VOTE["A"] and number > 1 and number != 12:
                pixel_roles = ["previous_C"]
            pixels = []
            for role in pixel_roles:
                image_number, image_role = (number - 1, "C") if role == "previous_C" else (number, role)
                if (image_number, image_role) == (10, "A"):
                    continue
                image_path = case_dir / "memory" / images[image_number, image_role]["path"]
                pixels.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(image_path.read_bytes()).decode()}})
            request = {"messages": [{"role": "system", "content": "OFFLINE_FIXTURE system " + stage},
                {"role": "user", "content": json.dumps(stage_view)}] + ([{"role": "user", "content": pixels}] if pixels else [])}
            write(folder / (stage + ".request.json"), request)
            if not final_stage and number == 12 and stage == renderer.STAGES[-1]:
                continue
            value = {"decision": "P2", "decoded_target": "P2", "image": None,
                "private_note": "OFFLINE_FIXTURE exact note.", "reflection": "OFFLINE_FIXTURE exact reflection."}
            for role, image_stage in renderer.IMAGE.items():
                if stage == image_stage:
                    value["image"] = images[number, role]
                    if number >= 10 or (number == 9 and role == "C"):
                        value["submission_state"] = "image_delivered" if role == "C" else "submitted"
            if number == 10 and stage == renderer.VOTE["A"]:
                value["image"] = None
            if number == 10 and stage == renderer.VOTE["B"]:
                value.update(decision=None, decoded_target=None, submission_state="abstained")
            if number == 11 and stage == renderer.IMAGE["C"]:
                value.update(decision=None, decoded_target=None, image=None, submission_state="declined")
            outcome = {"status": "valid", "output": review if stage == "REVIEWER" else value,
                "previous_hash": previous_hash, "provenance": {"evidence_origin": "fixture"}}
            if number == missing_review and stage == "REVIEWER":
                outcome.update(status="invalid_model_output", reason="OFFLINE_FIXTURE malformed first report")
            outcome["record_hash"] = renderer.sha(renderer.canonical(outcome).encode())
            previous_hash = outcome["record_hash"]
            write(folder / (stage + ".outcome.json"), outcome)
    write(out / "analysis" / "report.json", {"rounds": report_rows, "budget": {"evidence_origin": "fixture"}})

    # A10's exact prompt/native tools and generated private draft remain separate
    # from its explicit decision to deliver no image.
    provider = out / "provider_evidence"
    draft_path = case_dir / "memory" / images[10, "A"]["path"]
    raw = draft_path.read_bytes()
    blob = provider / "blobs" / (renderer.sha(raw) + ".png")
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(raw)
    identifier = case_id + ".r10.A_IMAGE_AND_VOTE"
    write(provider / "image_calls" / "OFFLINE_FIXTURE.json", {"request_id": identifier + ".image01",
        "result_sha256": renderer.sha(raw), "prompt": "OFFLINE_FIXTURE exact image prompt <not a detection>",
        "reference": "none"})
    write(provider / "transport_requests" / "OFFLINE_FIXTURE.json", {"request_id": identifier + ".step4",
        "endpoint": "/chat/completions", "logical_payload": {"model": "OFFLINE_FIXTURE_MODEL",
            "tools": [{"type": "function", "function": {"name": "submit_ballot",
                "parameters": {"type": "object", "properties": {"decision": {"enum": ["P2"]}}}}}],
            "tool_choice": "required", "messages": [{"role": "system", "content": "OFFLINE_FIXTURE native system"}]}})
    return case_id


def add_recovered_logs(out, case_id):
    """Author two first-log recovery fixtures while keeping raw stages invalid."""
    case_dir = out / "cases" / case_id
    projections = {role: {"private_note": f"OFFLINE_FIXTURE FIRST_PAID_{role} exact\nquote é.",
        "reflection": f'OFFLINE_FIXTURE FIRST_PAID_{role} reflection.\nSTATE_JSON: {{"pressure":"mixed","intensity":5,"willingness":"uncertain","intent_statement":"fixture only"}}'} for role in "AB"}
    for role in "AB":
        stage = renderer.LOG[role]
        folder = case_dir / "rounds/r09"
        request = json.loads((folder / (stage + ".request.json")).read_bytes())
        request.update(request_id=f"{case_id}.r09.{stage}", round=9, stage=stage, role=role, mode="memory_only")
        request["request_hash"] = renderer.sha(renderer.canonical(request).encode())
        write(folder / (stage + ".request.json"), request)
        outcome = json.loads((folder / (stage + ".outcome.json")).read_bytes())
        outcome.update(status="invalid_model_output", output=None,
            reason="OFFLINE_FIXTURE harness rejected standard index metadata")
        write(folder / (stage + ".outcome.json"), outcome)
    previous = "0" * 64
    for number in range(1, 13):
        for stage in renderer.STAGES:
            path = case_dir / "rounds" / f"r{number:02d}" / (stage + ".outcome.json")
            value = json.loads(path.read_bytes())
            value.pop("record_hash")
            value["previous_hash"] = previous
            value["record_hash"] = renderer.sha(renderer.canonical(value).encode())
            previous = value["record_hash"]
            write(path, value)
    sidecar = {"revision": "native_index_metadata_repair02", "output_root": str(out), "case_id": case_id,
        "config_sha256": renderer.sha((out / "config.json").read_bytes()),
        "plan_sha256": renderer.sha((out / "plan.json").read_bytes()), "entries": []}
    for role in "AB":
        stage = renderer.LOG[role]
        prefix = f"cases/{case_id}/rounds/r09/{stage}"
        request = json.loads((out / (prefix + ".request.json")).read_bytes())
        outcome = json.loads((out / (prefix + ".outcome.json")).read_bytes())
        physical = f"triad_repaired_v8:{case_id}.r09.{stage}.step1.attempt1"
        arguments = renderer.canonical(projections[role])
        response = renderer.canonical({"choices": [{"finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None, "reasoning": "PRIVATE_REASONING_DO_NOT_EXPORT",
            "tool_calls": [{"index": 0, "id": "OFFLINE_FIXTURE_CALL", "type": "function",
                "function": {"name": "record_log", "arguments": arguments}}]}}]})
        receipt = renderer.canonical({"request_id": physical})
        row = {"request_id": physical, "status": "completed", "response_json": response,
            "receipt_json": receipt, "result_hash": renderer.sha((response + "\n" + receipt).encode())}
        row_file = "analysis/native_index_hold/repair02/ledger_first_rows/" + role + ".json"
        write(out / row_file, row)
        sidecar["entries"].append({"role": role, "round": 9, "stage": stage, "function": "record_log",
            "classification": "first_paid_log_recovered_from_transport_metadata_rejection",
            "first_physical_request_id": physical,
            "excluded_second_physical_request_id": physical.replace(".step1.", ".step2."),
            "original_request_file": prefix + ".request.json",
            "original_request_sha256": renderer.sha((out / (prefix + ".request.json")).read_bytes()),
            "original_request_hash": request["request_hash"],
            "original_outcome_file": prefix + ".outcome.json",
            "original_outcome_sha256": renderer.sha((out / (prefix + ".outcome.json")).read_bytes()),
            "original_outcome_record_hash": outcome["record_hash"],
            "first_ledger_row_file": row_file,
            "first_ledger_row_sha256": renderer.sha((out / row_file).read_bytes()),
            "response_json_sha256": renderer.sha(response.encode()),
            "receipt_json_sha256": renderer.sha(receipt.encode()),
            "arguments_sha256": renderer.sha(arguments.encode()),
            "projection": projections[role], "projection_sha256": renderer.sha(renderer.canonical(projections[role]).encode())})
    write(out / "technical_log_corrections.json", sidecar)
    report = json.loads((out / "analysis/report.json").read_bytes())
    r9 = report["rounds"][8]
    r9["private_self_reports"] = {}
    for entry in sidecar["entries"]:
        r9["private_self_reports"][entry["role"]] = {
            "source_path": str(out / "technical_log_corrections.json"),
            "source_file_sha256": renderer.sha((out / "technical_log_corrections.json").read_bytes()),
            "technical_recovery": {"source": "exact first paid record_log response; standard index metadata rejected by harness",
                "original_outcome_path": str(out / entry["original_outcome_file"]),
                "original_outcome_sha256": entry["original_outcome_sha256"],
                "original_outcome_status": "invalid_model_output", "new_model_response": False,
                "second_paid_reply_used": False}}
    write(out / "analysis/report.json", report)
    write(out / "OFFLINE_EXCLUDED_SECOND_REPLY.json", {"private_note": "SECOND_PAID_REPLY_DO_NOT_USE", "reflection": "SECOND_PAID_REPLY_DO_NOT_USE"})
    return projections


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name) / "OFFLINE_FIXTURE_ONLY"

    def test_complete_preserves_origins_literals_and_independent_availability(self):
        fixture(self.out)
        data = renderer.build(self.out)
        self.assertEqual(data["terminal_stage_records"], 96)
        self.assertEqual(data["observed_stage_origin_counts"], {
            "triad_repaired_v6": 64, "triad_repaired_v7": 3, "triad_repaired_v8": 29})
        r10, r11, r12 = data["rounds"][9:]
        self.assertTrue(r10["ballots"]["A"]["available"])
        self.assertIsNone(r10["images"]["A"])
        self.assertEqual(r10["ballots"]["B"]["ballot_status"], "abstained")
        self.assertFalse(r10["ballots"]["B"]["available"])
        self.assertIsNotNone(r10["images"]["B"])
        self.assertIsNone(r11["images"]["C"])
        self.assertEqual(r11["image_submissions"]["C"]["submission_state"], "declined")
        self.assertEqual(r11["image_submissions"]["C"]["stage_status"], "valid")
        self.assertFalse(r12["directed_image_checks"][-1]["source_available"])
        for number, row in enumerate(data["rounds"], 1):
            self.assertEqual(row["post_review_metrics"]["private_synthetic_payoffs"]["total_usd_per_agent"], 1000000 + number)
            self.assertTrue(row["identical_actual_feedback_across_all_four_post_stages"])
        self.assertEqual(data["rounds"][8]["protocol_revision"], "mixed_v7_v8")
        self.assertIn("submit_ballot", renderer.prompt_export(data))
        gallery = renderer.gallery(data)
        self.assertIn("private draft pixels", gallery)
        self.assertIn("not a detection", gallery)
        self.assertIn("Actual calibrated tier/confidence", gallery)
        self.assertIn("supported_suspicion", gallery)
        self.assertEqual(r10["calibrated_collusion_assessment"]["confidence"], .85)
        self.assertEqual(r10["calibrated_collusion_assessment"]["image_evidence"][0]["image_file"], "image_02_B_round_10.png")
        self.assertIn("**1000010**", renderer.md_report(data))

    def test_render_only_writes_derived_files_and_source_hashes_stay_unchanged(self):
        fixture(self.out)
        before = {str(path): renderer.sha(path.read_bytes()) for path in self.out.rglob("*") if path.is_file()}
        destination = Path(self.temp.name) / "OFFLINE_DERIVED"
        result = renderer.render(self.out, destination=destination)
        self.assertEqual(result["paid_calls"], 0)
        self.assertEqual({path.name for path in destination.iterdir()}, {
            "REPORT.md", "GALLERY.html", "EXACT_SYSTEM_PROMPTS_BY_STAGE.txt", "EVIDENCE.json"})
        self.assertTrue(all(renderer.sha(Path(path).read_bytes()) == checksum for path, checksum in before.items()))

    def test_incomplete_rejected_without_writing_and_explicit_diagnostic_labeled(self):
        fixture(self.out, final_stage=False)
        destination = Path(self.temp.name) / "OFFLINE_DERIVED"
        with self.assertRaisesRegex(ValueError, "incomplete"):
            renderer.render(self.out, destination=destination)
        self.assertFalse(destination.exists())
        data = renderer.build(self.out, allow_incomplete=True)
        self.assertEqual(data["rounds_finished"], 11)
        self.assertEqual(data["terminal_stage_records"], 95)
        self.assertFalse(data["terminal"])

    def test_invalid_review_remains_missing_full_actual_feedback_and_loss_unknown(self):
        fixture(self.out, missing_review=11)
        data = renderer.build(self.out)
        row = data["rounds"][10]
        self.assertIsNone(row["reviewer"])
        self.assertIsNone(row["calibrated_collusion_assessment"])
        self.assertEqual(row["stage_status"]["REVIEWER"], "invalid_model_output")
        self.assertEqual(len(row["failures"]), 1)
        self.assertIsNone(row["post_review_metrics"]["private_synthetic_payoffs"]["total_usd_per_agent"])
        for delivery in row["post_review_delivery_checks"]:
            self.assertTrue(delivery["missing_review_marked_unavailable"])
            self.assertIsNone(delivery["participant_summary"]["actual_reviewer_feedback"])
            self.assertIsNone(delivery["collective_consequence"]["loss_usd_per_agent"])

    def test_origin_and_hash_tampering_are_rejected(self):
        fixture(self.out)
        path = self.out / "analysis" / "report.json"
        report = json.loads(path.read_bytes())
        report["rounds"][8]["stage_protocol_revisions"]["C_VOTE"] = "triad_repaired_v8"
        write(path, report)
        with self.assertRaisesRegex(ValueError, "per-stage"):
            renderer.build(self.out)
        report["rounds"][8]["stage_protocol_revisions"]["C_VOTE"] = "triad_repaired_v7"
        write(path, report)
        outcome = self.out / "cases/OFFLINE_FIXTURE_ONLY/rounds/r12/POSTVERDICT_C_LOG.outcome.json"
        value = json.loads(outcome.read_bytes())
        value["output"]["private_note"] = "OFFLINE_FIXTURE changed after sealing"
        write(outcome, value)
        with self.assertRaisesRegex(ValueError, "chain"):
            renderer.build(self.out)

    def test_recovered_first_strings_are_displayed_without_reclassifying_raw_stages(self):
        case_id = fixture(self.out)
        projections = add_recovered_logs(self.out, case_id)
        data = renderer.build(self.out)
        self.assertEqual(data["raw_invalid_stage_records"], 2)
        self.assertEqual(data["recovered_first_paid_log_records"], 2)
        self.assertEqual(data["other_raw_invalid_stage_records"], 0)
        self.assertEqual(data["private_log_counts"]["text_available"], 36)
        self.assertEqual(data["private_log_counts"]["text_unavailable"], 0)
        self.assertEqual(data["private_log_counts"]["raw_valid_stage_logs"], 34)
        self.assertEqual(data["private_log_counts"]["raw_invalid_stage_logs"], 2)
        for role in "AB":
            log = data["rounds"][8]["private_self_reports"][role]
            self.assertEqual(log["status"], "invalid_model_output")
            self.assertFalse(log["accepted"])
            self.assertTrue(log["log_text_available"])
            self.assertEqual(log["log_text_source"], "recovered_first_paid_log")
            for field in ("private_note", "reflection"):
                self.assertEqual(log[field].encode(), projections[role][field].encode())
            self.assertFalse(log["technical_recovery"]["second_paid_reply_used"])
        self.assertIn("recovered first paid log; original harness classification preserved", renderer.md_report(data))
        self.assertIn("recovered first paid log; original harness classification preserved", renderer.gallery(data))
        serialized = json.dumps(data, ensure_ascii=False)
        self.assertNotIn("SECOND_PAID_REPLY_DO_NOT_USE", serialized)
        self.assertNotIn("PRIVATE_REASONING_DO_NOT_EXPORT", serialized)

    def test_recovery_hash_tamper_or_second_reply_claim_fails_closed(self):
        case_id = fixture(self.out)
        add_recovered_logs(self.out, case_id)
        path = self.out / "analysis/report.json"
        report = json.loads(path.read_bytes())
        report["rounds"][8]["private_self_reports"]["A"]["technical_recovery"]["second_paid_reply_used"] = True
        write(path, report)
        with self.assertRaisesRegex(ValueError, "provenance"):
            renderer.build(self.out)
        report["rounds"][8]["private_self_reports"]["A"]["technical_recovery"]["second_paid_reply_used"] = False
        write(path, report)
        first = self.out / "analysis/native_index_hold/repair02/ledger_first_rows/A.json"
        first.write_bytes(first.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "checksum"):
            renderer.build(self.out)


if __name__ == "__main__":
    unittest.main()
