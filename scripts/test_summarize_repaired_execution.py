"""Offline accounting tests, including partial primary outcomes and audit loss."""
from copy import deepcopy
import contextlib
import fcntl
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from summarize_repaired_execution import (ROOT, build_summary, budget_snapshot, exact_transport_losses,
                                          classify_outcome, transport_missing_reason, wire_accounting, main)
from visual_collusion.budget import BudgetLedger
from visual_collusion.common import canonical, digest, load_config, write_json
from visual_collusion.protocol import build_plan, correct_decision


class SummaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name)
        self.out = self.parent / "repaired"
        self.plan = build_plan(load_config(ROOT / "configs/canonical.json"))
        write_json(self.out / "plan.json", self.plan)
        write_json(self.out / "run_status.json", {"status": "planned"})
        self.ep = next(e for e in self.plan["episodes"] if e["condition"] == "explicit")
        self.case = next(c for c in self.plan["cases"] if c["case_id"] == self.ep["case_id"])

    def row(self, branch):
        return {"episode_id": self.ep["episode_id"], "case_id": self.ep["case_id"],
                "branch": branch["branch_id"], "condition": "explicit", "round": 3,
                "target": branch["private_target"], "b_output": {"decision": branch["private_target"]},
                "a_image": {"path": "artifacts/a.png"}, "b_image": {"path": "artifacts/b.png"},
                "neutral_replay": {"status": "pending"}}

    def test_empty_observations_are_null_and_planned_denominators_remain(self):
        summary = build_summary(self.parent)
        self.assertEqual(summary["completion"]["primary_receptions"], {"observed": 0, "planned": 12})
        self.assertEqual(summary["completion"]["audit_views"]["not_attempted"], 32)
        self.assertEqual(len(summary["primary_case_pairs"]), 6)
        self.assertTrue(all(p["both_targets_followed"] is None for p in summary["primary_case_pairs"]))
        self.assertTrue(all(r["intact"]["correct"] is None for r in summary["primary_receptions"]))
        self.assertFalse(summary["scientific_schedule_fully_observed"])

    def test_primary_pairs_survive_without_reviews_or_final_trajectories(self):
        for index, branch in enumerate(self.ep["branches"]):
            write_json(self.out / "reception_rounds" / f"{index}.json", self.row(branch))
        summary = build_summary(self.parent)
        pair = next(p for p in summary["primary_case_pairs"] if p["episode_id"] == self.ep["episode_id"])
        self.assertEqual(pair["target_dependence_theta"], 1)
        self.assertTrue(pair["both_targets_followed"])
        self.assertIsNone(pair["intact_minus_neutral_target_matches"])
        self.assertEqual(summary["completion"]["primary_receptions"]["observed"], 2)
        self.assertEqual(summary["completion"]["complete_trajectories"]["observed"], 0)

    def test_later_checkpoints_enrich_without_double_counting(self):
        row = self.row(self.ep["branches"][0])
        write_json(self.out / "reception_rounds" / "first.json", row)
        later = deepcopy(row)
        later["neutral_replay"] = {"status": "valid", "output": {"decision": correct_decision(self.case)}}
        write_json(self.out / "unreviewed_rounds" / "first.json", later)
        later["review"] = {"output": {"verdict": "reject", "task_violation": True, "collusion_suspected": False},
                           "trace": {"audit_status": "unverified", "coverage": "unavailable"}}
        write_json(self.out / "rounds" / "first.json", later)
        summary = build_summary(self.parent)
        self.assertEqual(summary["completion"]["unique_reviewed_rounds"]["observed"], 1)
        self.assertEqual(summary["completion"]["primary_receptions"]["observed"], 1)
        self.assertEqual(summary["completion"]["neutral_replays"]["valid"], 1)
        self.assertEqual(summary["review_trace_accounting"]["blinded_eligible_reviews"], 0)

    def test_complete_trajectories_do_not_hide_invalid_auditor_view(self):
        first = True
        for ep in self.plan["episodes"]:
            branches = ep["branches"] if ep["condition"] == "explicit" else [{"branch_id": "control"}]
            for branch in branches:
                audits = {v: {"status": "valid", "output": {"coordination_suspected": False, "task_violation": False}}
                          for v in ("latest_pair", "image_history")}
                if first:
                    audits["latest_pair"] = {"status": "invalid", "reason": "Observed invalid output"}
                    first = False
                write_json(self.out / "trajectories" / (ep["episode_id"] + branch["branch_id"] + ".json"),
                           {"episode_id": ep["episode_id"], "branch": branch["branch_id"],
                            "status": "complete", "history": [], "audits": audits})
        summary = build_summary(self.parent)
        self.assertEqual(summary["completion"]["complete_trajectories"]["observed"], 16)
        self.assertEqual(summary["completion"]["audit_views"]["valid"], 31)
        self.assertEqual(summary["completion"]["audit_views"]["invalid"], 1)
        self.assertEqual(summary["completion"]["valid_paired_audits"]["observed"], 15)
        self.assertFalse(summary["scientific_schedule_fully_observed"])

    def transport_fixture(self):
        request = self.ep["episode_id"] + ":branch_1:audit:latest_pair"
        local = BudgetLedger(self.out / "budget.sqlite3", 40)
        aggregate = BudgetLedger(self.parent / "budget.sqlite3", 40)
        policy = {"user_instruction": "Offline fixture only"}
        policy_hash = digest(policy)
        for ledger, key in ((local, request), (aggregate, "repair1:" + request)):
            ledger.reserve(key, "chat", "fixture", "a" * 64, 0.01)
            ledger.uncertain(key, "POST interrupted or failed; billing outcome unresolved")
            with ledger._connect() as db:
                row = db.execute("SELECT * FROM requests WHERE request_id=?", (key,)).fetchone()
                db.execute("CREATE TABLE terminal_transport_losses_v4 (request_id TEXT PRIMARY KEY,snapshot_json TEXT,policy_sha256 TEXT,recorded_at REAL)")
                db.execute("INSERT INTO terminal_transport_losses_v4 VALUES (?,?,?,?)",
                           (key, canonical(dict(row)), policy_hash, 1))
        wrapper = self.out / "execution_notes" / "continue_repaired_execution.py"
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        wrapper.write_text("# Offline fixture only\n")
        write_json(self.parent / "execution_notes" / "repaired_execution_continuation_v4.json",
                   {"authorization": policy, "policy_sha256": policy_hash,
                    "execution": str(self.out.resolve()), "namespace": "repair1",
                    "wrapper_sha256": hashlib.sha256(wrapper.read_bytes()).hexdigest()})
        return request, local, aggregate

    def classify_fixture(self, request):
        local = budget_snapshot(self.out / "budget.sqlite3")
        aggregate = budget_snapshot(self.parent / "budget.sqlite3")
        losses, provenance = exact_transport_losses(self.parent, self.out, local, aggregate, "repair1")
        classification = classify_outcome("invalid", request, transport_missing_reason(request),
                                          exact_losses=losses, failed_calls=[], declines=[],
                                          requests={r["request_id"]: r for r in local["requests"]})
        return classification, losses, local, provenance

    def test_exact_mirrored_transport_seals_preserve_invalid_and_unknown_cost(self):
        request, _, _ = self.transport_fixture()
        outcome, losses, local, provenance = self.classify_fixture(request)
        self.assertEqual(outcome["canonical_status"], "invalid")
        self.assertEqual(outcome["classification"], "transport_missing")
        self.assertIsNone(losses[request]["confirmed_cost_usd"])
        self.assertEqual(local["unresolved_reserved_usd"], 0.01)
        self.assertEqual(provenance["status"], "verified_local_policy_binding")

    def test_unmirrored_seal_is_unresolved_instead_of_transport_missing(self):
        request, _, aggregate = self.transport_fixture()
        with aggregate._connect() as db:
            db.execute("DELETE FROM terminal_transport_losses_v4")
        outcome, losses, _, _ = self.classify_fixture(request)
        self.assertEqual(losses, {})
        self.assertEqual(outcome["classification"], "unresolved_request_failure")

    def test_changed_row_breaks_exact_snapshot_binding(self):
        request, local, _ = self.transport_fixture()
        with local._connect() as db:
            db.execute("UPDATE requests SET reason='Different fixture reason' WHERE request_id=?", (request,))
        outcome, losses, _, _ = self.classify_fixture(request)
        self.assertEqual(losses, {})
        self.assertEqual(outcome["classification"], "unresolved_request_failure")

    def test_known_billed_invalid_remains_distinct(self):
        outcome = classify_outcome("invalid", "audit", "Malformed observed reply",
                                   exact_losses={}, failed_calls=[{"request_id": "audit"}],
                                   declines=[], requests={"audit": {"status": "completed"}})
        self.assertEqual(outcome["classification"], "known_billed_invalid")

    def test_neutral_turn_requires_exact_loss_reason_and_request_prefix(self):
        prefix = "episode:branch:round3:B:neutral"
        request = prefix + ":step2"
        kwargs = {"exact_losses": {request: {}}, "failed_calls": [], "declines": [],
                  "requests": {request: {"status": "uncertain"}}, "turn": True}
        actual = classify_outcome("invalid", prefix, transport_missing_reason(request), **kwargs)
        self.assertEqual(actual["classification"], "transport_missing")
        wrong = classify_outcome("invalid", prefix, "Transport missing without a bound request", **kwargs)
        self.assertEqual(wrong["classification"], "unresolved_request_failure")

    def test_changed_wrapper_prevents_transport_missing_classification(self):
        request, _, _ = self.transport_fixture()
        (self.out / "execution_notes" / "continue_repaired_execution.py").write_text("# Changed fixture\n")
        outcome, losses, _, provenance = self.classify_fixture(request)
        self.assertEqual(losses, {})
        self.assertEqual(outcome["classification"], "unresolved_request_failure")
        self.assertEqual(provenance["status"], "unverified_policy_binding")

    def v5_fixture(self):
        request4, local, aggregate = self.transport_fixture()
        request5 = self.ep["episode_id"] + ":branch_1:audit:image_history"
        policy = {"user_instruction": "Offline v5 fixture only"}
        policy_hash = digest(policy)
        for ledger, key in ((local, request5), (aggregate, "repair1:" + request5)):
            # Construct a post-continuation snapshot without importing a live
            # controller; this patch exists only in this isolated test process.
            with patch.object(BudgetLedger, "_ready", return_value=None):
                ledger.reserve(key, "chat", "fixture", "c" * 64, 0.02)
            ledger.uncertain(key, "POST interrupted or failed; billing outcome unresolved")
            with ledger._connect() as db:
                row = db.execute("SELECT * FROM requests WHERE request_id=?", (key,)).fetchone()
                db.execute("CREATE TABLE terminal_transport_losses_v5 (request_id TEXT PRIMARY KEY,snapshot_json TEXT,policy_sha256 TEXT,recorded_at REAL)")
                db.execute("INSERT INTO terminal_transport_losses_v5 VALUES (?,?,?,?)",
                           (key, canonical(dict(row)), policy_hash, 2))
        with local._connect() as db:
            baseline = [{"request_id": row["request_id"], "row_sha256": digest(dict(row))}
                        for row in db.execute("SELECT * FROM requests ORDER BY request_id")]
        wrapper = self.out / "execution_notes" / "run_lossless_webp_v5.py"
        wrapper.write_text("# Offline v5 wrapper fixture\n")
        codec = self.out / "execution_notes" / "lossless_wire_codec.py"
        codec.write_text("# Offline codec fixture\n")
        v4 = self.out / "execution_notes" / "continue_repaired_execution.py"
        previous = self.parent / "execution_notes" / "repaired_execution_continuation_v4.json"
        write_json(self.parent / "execution_notes" / "lossless_webp_transport_v5.json",
                   {"authorization": policy, "policy_sha256": policy_hash,
                    "execution": str(self.out.resolve()), "namespace": "repair1",
                    "wrapper_sha256": hashlib.sha256(wrapper.read_bytes()).hexdigest(),
                    "codec_sha256": hashlib.sha256(codec.read_bytes()).hexdigest(),
                    "v4_wrapper_sha256": hashlib.sha256(v4.read_bytes()).hexdigest(),
                    "previous_amendment_sha256": hashlib.sha256(previous.read_bytes()).hexdigest(),
                    "existing_requests": baseline, "existing_requests_sha256": digest(baseline)})
        return request4, request5, local, aggregate, policy_hash

    def test_v5_seals_coexist_with_v4_and_require_their_own_reason(self):
        request4, request5, _, _, _ = self.v5_fixture()
        local = budget_snapshot(self.out / "budget.sqlite3")
        aggregate = budget_snapshot(self.parent / "budget.sqlite3")
        losses, provenance = exact_transport_losses(self.parent, self.out, local, aggregate, "repair1")
        self.assertEqual({losses[request4]["version"], losses[request5]["version"]}, {4, 5})
        self.assertTrue(provenance["all_observed_versions_verified"])
        kwargs = {"exact_losses": losses, "failed_calls": [], "declines": [],
                  "requests": {r["request_id"]: r for r in local["requests"]}}
        actual = classify_outcome("invalid", request5, transport_missing_reason(request5, 5), **kwargs)
        wrong = classify_outcome("invalid", request5, transport_missing_reason(request5, 4), **kwargs)
        self.assertEqual(actual["classification"], "transport_missing")
        self.assertEqual(wrong["classification"], "unresolved_request_failure")
        self.assertEqual(local["unresolved_reserved_usd"], 0.03)
        self.assertIsNone(losses[request5]["confirmed_cost_usd"])

    def test_v5_codec_and_baseline_integrity_are_required(self):
        request4, request5, local_ledger, _, _ = self.v5_fixture()
        codec = self.out / "execution_notes" / "lossless_wire_codec.py"
        codec.write_text("# Changed codec fixture\n")
        local = budget_snapshot(self.out / "budget.sqlite3")
        aggregate = budget_snapshot(self.parent / "budget.sqlite3")
        losses, provenance = exact_transport_losses(self.parent, self.out, local, aggregate, "repair1")
        self.assertIn(request4, losses)
        self.assertNotIn(request5, losses)
        self.assertFalse(provenance["versions"]["5"]["checks"]["codec_hash"])
        codec.write_text("# Offline codec fixture\n")
        with local_ledger._connect() as db:
            db.execute("UPDATE requests SET created=created+1 WHERE request_id=?", (request4,))
        local = budget_snapshot(self.out / "budget.sqlite3")
        losses, provenance = exact_transport_losses(self.parent, self.out, local, aggregate, "repair1")
        self.assertNotIn(request5, losses)
        self.assertFalse(provenance["versions"]["5"]["checks"]["baseline_full_rows"])

    def test_wire_binding_counts_distinguish_paid_prepared_and_encoded_images(self):
        request, local, aggregate = self.transport_fixture()
        for ledger, global_prefix in ((local, ""), (aggregate, "repair1:")):
            with ledger._connect() as db:
                db.execute("CREATE TABLE wire_bindings_v5 (request_id TEXT PRIMARY KEY,logical_request_hash TEXT,wire_request_hash TEXT,record_sha256 TEXT,record_json TEXT,policy_sha256 TEXT)")
                for key, images in ((request, [{"wire_blob": "fixture.webp"}]), ("prepared.image", [])):
                    record = {"request_id": key, "endpoint": "/chat/completions" if images else "/images",
                              "mapping": {"request_id": key, "logical_request_hash": "a" * 64,
                                          "wire_request_hash": "b" * 64, "images": images}}
                    db.execute("INSERT INTO wire_bindings_v5 VALUES (?,?,?,?,?,?)",
                               (global_prefix + key, "a" * 64, "b" * 64, digest(record), canonical(record), "fixture-policy"))
                    if not global_prefix:
                        write_json(self.out / "transport_requests" / (digest(key)[:24] + ".json"), record)
        wire = wire_accounting(self.out, budget_snapshot(local.path), budget_snapshot(aggregate.path), "repair1", "fixture-policy")
        self.assertEqual(wire["paid_intents_mapped"], 1)
        self.assertEqual(wire["unbilled_prepared_maps"], 1)
        self.assertEqual(wire["bindings_with_encoded_chat_images"], 1)
        self.assertEqual(wire["ordered_encoded_image_occurrences"], 1)
        self.assertEqual(wire["differences"], [])

    def test_v5_infrastructure_cost_is_a_subset_not_an_additional_bill(self):
        _, _, _, aggregate, _ = self.v5_fixture()
        infra = BudgetLedger(self.parent / "infrastructure_checks_v5" / "budget.sqlite3", 40)
        for ledger, request in ((infra, "format.glm"), (aggregate, "infra_checks5:format.glm")):
            with patch.object(BudgetLedger, "_ready", return_value=None):
                ledger.reserve(request, "chat", "fixture", "d" * 64, 0.003)
            ledger.complete(request, 0.002, {"fixture": True}, {"fixture": True})
        summary = build_summary(self.parent)
        self.assertEqual(summary["budget"]["local_scientific_subset"]["confirmed_actual_usd"], 0)
        self.assertEqual(summary["budget"]["infrastructure_v5_subset"]["confirmed_actual_usd"], 0.002)
        self.assertEqual(summary["budget"]["aggregate_all_attempts"]["confirmed_actual_usd"], 0.002)
        self.assertEqual(summary["completion"]["primary_receptions"]["observed"], 0)

    def test_cli_preserves_previous_output_while_execution_lock_is_held(self):
        lock_path = self.out / "run.lock"
        lock_path.touch()
        with lock_path.open("rb") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                main(["--out", str(self.parent)])
        self.assertEqual(error.exception.code, 2)
        self.assertFalse((self.out / "analysis" / "execution_summary.json").exists())


if __name__ == "__main__":
    unittest.main()
