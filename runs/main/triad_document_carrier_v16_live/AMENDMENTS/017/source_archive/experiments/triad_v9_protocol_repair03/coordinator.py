"""Manual seal and one bounded continuation of an already claimed first slot."""
from copy import deepcopy
import datetime as dt
import html
from pathlib import Path
import sqlite3

from experiments.triad_emergent_v9 import coordinator as base
from experiments.triad_emergent_v9 import funding as original_funding
from experiments.triad_emergent_v9.offline_gate import test_manifest
from experiments.triad_emergent_v9.engine import digest, file_digest
from experiments.triad_v9_protocol_repair01 import coordinator as first
from experiments.triad_v9_protocol_repair02 import coordinator as parent
from .authority import Authority, REPAIR, eligible, raw_rows
from .backend import Backend, make_policy, TOKEN_BUDGET_NOTICE

ROOT, HERE = base.ROOT, Path(__file__).resolve().parent
GATE = HERE / "WORKFLOW_GATE.json"
ACTIVATION_PHRASE = "ACTIVATE_REPAIR03_CONSUME_EXACT_LOST_SLOT_NO_RETRY"


def source_manifest():
    sources = parent.source_manifest()
    for path in sorted(HERE.rglob("*")):
        if path.is_file() and path.suffix in {".py", ".md", ".txt"} and "tests" not in path.relative_to(HERE).parts and "__pycache__" not in path.parts:
            sources[str(path.relative_to(ROOT))] = base.file_sha(path)
    return sources


def verify_parent_snapshot(out, snapshot, *, config_path=None):
    """Directly validate preserved repair02; never replace the active freeze."""
    out, folder = Path(out).absolute(), Path(out).absolute() / "analysis/repair02"
    amendment, baseline = first._read(folder / "AMENDMENT.json"), first._read(folder / "BASELINE.json")
    record = snapshot.get("protocol_repair02", {})
    if (snapshot.get("source_files") != parent.source_manifest() or first._read(folder / "ACTIVE_FREEZE.json") != snapshot
            or record.get("policy") != parent.make_policy() or amendment.get("policy") != parent.make_policy()
            or amendment.get("activated") is not True or record.get("amendment_sha256") != base.file_sha(folder / "AMENDMENT.json")
            or record.get("baseline_sha256") != base.file_sha(folder / "BASELINE.json")
            or record.get("repair_gate_sha256") != base.file_sha(parent.GATE)
            or record.get("parent_freeze_sha256") != base.file_sha(folder / "PARENT_FREEZE.json")):
        raise ValueError("The preserved parent174 snapshot changed")
    for name, expected in baseline["original_input_copies"].items():
        if base.file_sha(folder / name) != expected:
            raise ValueError("A repair02 preserved input changed")
    parent.verify_parent_snapshot(out, first._read(folder / "PARENT_FREEZE.json"), config_path=config_path)
    for relative, expected in baseline["protected_files"].items():
        if base.file_sha(out / relative) != expected:
            raise ValueError("A repair02 protected observation changed")
    for relative, expected in baseline["parent_sources"].items():
        if base.file_sha(folder / "parent_sources" / relative) != expected:
            raise ValueError("A repair02 source copy changed")
    rows = base.ledger_rows()
    if any(rows.get(key) != value for key, value in baseline["ledger_rows"].items()):
        raise ValueError("A repair02 paid record changed")
    for relative, expected in snapshot["source_files"].items():
        if base.file_sha(ROOT / relative) != expected:
            raise ValueError("An existing174 runtime source changed")
    return snapshot


def _declaration(out, snapshot, proof_path, engine):
    proof, out = first._read(proof_path), Path(out).absolute()
    target = engine.next_stage()
    rows = raw_rows(base.AGGREGATE)
    row = proof.get("failed_request")
    if (proof.get("active_producer") is not False or proof.get("observed_exit_code") != 0
            or proof.get("observed_final_host_status") != "quitting"
            or type(proof.get("producer_session")) is not int or proof["producer_session"] <= 0
            or proof.get("next_stage") != target or proof.get("terminal_main_stages") != engine.summary()["terminal_stages"]
            or proof.get("budget", {}).get("request_count") != len(rows)
            or proof.get("freeze_sha256") != base.file_sha(out / "freeze.json")
            or not eligible(row) or rows.get(row["request_id"]) != row
            or target["request_id"] + ".visual1" != row["request_id"].split(":", 1)[1]
            or target["stage"] != "A_IMAGE_AND_VOTE"):
        raise ValueError("The actual exited producer must bind this exact first-slot loss and frontier")
    pid = proof.get("producer_pid")
    if pid is not None and (type(pid) is not int or pid <= 1 or Path(f"/proc/{pid}").exists()):
        raise ValueError("The original producer must be absent")
    prefix = original_funding.NAMESPACE + ":" + target["request_id"] + "."
    if [key for key in rows if key.startswith(prefix)] != [row["request_id"]]:
        raise ValueError("Only the current stage's first native intent may already exist")
    request_path = out / f"episode/rounds/r{target['round']:02d}/{target['stage']}.request.json"
    dispatch_path = request_path.with_name(target["stage"] + ".dispatch.json")
    request, dispatch = first._read(request_path), first._read(dispatch_path)
    if (request != engine.actor_request() or dispatch != {"request_id": target["request_id"],
            "request_hash": request["request_hash"], "request_file_sha256": base.file_sha(request_path), "one_dispatch": True}
            or request_path.with_name(target["stage"] + ".outcome.json").exists()):
        raise ValueError("The already claimed exact stage cannot be reset or replaced")
    for path in (out / "episode/rounds").glob("*/*.dispatch.json"):
        if not path.with_name(path.name.replace(".dispatch.json", ".outcome.json")).exists() and path != dispatch_path:
            raise ValueError("Another unresolved stage blocks recovery")
    local = row["request_id"].split(":", 1)[1]
    for path in (out / "provider_evidence/stage_results" / (base.sha(target["request_id"].encode()) + ".json"),
                 out / "actor_deliveries" / (base.sha(target["request_id"].encode()) + ".png"),
                 out / "provider_evidence/calls" / (base.sha(local.encode()) + ".json")):
        if path.exists():
            raise ValueError("The current first-slot case cannot already contain a returned result or delivery")
    archive = out / "provider_evidence/transport_requests" / (base.sha(local.encode()) + ".json")
    mapping = first._read(archive)["mapping"]
    bound = [request_path, dispatch_path, archive, Path(proof_path)]
    if any(not path.absolute().is_relative_to(out) for path in bound):
        raise ValueError("The retained host proof and original request evidence must be inside this run")
    return {"repair": REPAIR, "out": str(out), "slot": 1, "row": row,
        "row_sha256": base.sha(base.canonical(row).encode()), "no_retry": True,
        "unknown_charge_retained": True, "effective_frontier": target,
        "parent_freeze_sha256": base.sha((base.canonical(snapshot) + "\n").encode()),
        "transport_archive": str(archive.relative_to(out)), "wire_body_sha256": mapping["wire_body_sha256"],
        "bound_files": {str(p.relative_to(out)): base.file_sha(p) for p in bound},
        "interpretation": "Infrastructure loss only; no model answer or charge amount was observed."}


def _ready(out, expected_parent_sha256, producer_exit_path, config_path=None):
    out = Path(out).absolute()
    snapshot = first._read(out / "freeze.json")
    if snapshot.get("protocol_repair03") is not None or base.file_sha(out / "freeze.json") != expected_parent_sha256:
        raise ValueError("The exact current parent174 freeze is required")
    verify_parent_snapshot(out, snapshot, config_path=config_path)
    gate = first._read(GATE)
    if (gate.get("status") != "passed" or gate.get("source_files") != source_manifest()
            or gate.get("test_files") != test_manifest() or gate.get("parent_gate_sha256") != base.file_sha(parent.GATE)
            or gate.get("original_gate_sha256") != base.file_sha(base.HERE / "WORKFLOW_GATE.json")
            or gate.get("config_source_sha256") != base.file_sha(config_path or base.HERE / "config.json")):
        raise ValueError("The exact repair03 sources and tests need a passing new unpaid gate")
    accounting = base.budget()
    if accounting["pending_count"] or accounting["halt_reason"]:
        raise ValueError("Pending reservations or a funding halt block repair03")
    host = base.Coordinator(out, config_path=config_path)
    engine, scientific = parent._scientific_check(host)
    declaration = _declaration(out, snapshot, producer_exit_path, engine)
    with sqlite3.connect(Path(base.AGGREGATE).absolute().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        for row in db.execute("SELECT * FROM requests WHERE status IN ('pending','uncertain')"):
            if dict(row) != declaration["row"] and not original_funding.FundingLedger._sealed(db, row):
                raise ValueError("Every other unresolved request must retain its original verified seal")
    return snapshot, declaration, scientific


def seal(out, *, expected_parent_sha256, producer_exit_path, activation_phrase=None, config_path=None):
    out = Path(out).absolute()
    with base.producer_lock(out):
        snapshot, declaration, scientific = _ready(out, expected_parent_sha256, producer_exit_path, config_path)
        target = declaration["effective_frontier"]
        if activation_phrase is None:
            return {"status": "repair03_ready_not_activated", "policy": make_policy(), "frontier": target,
                    "lost_slot": declaration["row"]["request_id"], "provider_calls": 0, "new_allowance": False}
        if activation_phrase != ACTIVATION_PHRASE:
            raise ValueError("The explicit repair03 activation phrase is required")
        folder = out / "analysis/repair03"
        if (folder / "AMENDMENT.json").exists():
            raise ValueError("Preserve the existing repair03 amendment")
        inputs = {"PARENT_FREEZE.json": out / "freeze.json", "PARENT_WORKFLOW_GATE.json": parent.GATE,
            "REPAIR_WORKFLOW_GATE.json": GATE, "PRODUCER_EXIT.json": Path(producer_exit_path),
            "ORIGINAL_CONFIG.json": out / "config.json", "ORIGINAL_PLAN.json": out / "plan.json",
            "ORIGINAL_ENGINE_MANIFEST.json": out / "episode/engine_manifest.json"}
        protected = first._protected_files(out)
        for name in ("repair01", "repair02"):
            protected.update({str(p.relative_to(out)): base.file_sha(p) for p in (out / "analysis" / name).rglob("*") if p.is_file()})
        baseline = {"protected_files": protected, "ledger_rows": base.ledger_rows(), "parent_sources": snapshot["source_files"],
            "original_input_copies": {name: base.file_sha(p) for name, p in inputs.items()}, "scientific_verification": scientific,
            "original_budget": base.budget(), "effective_frontier": target, "no_new_allowance": True}
        for name, path in inputs.items():
            first._save_raw(folder / name, path.read_bytes())
        for relative in snapshot["source_files"]:
            first._save_raw(folder / "parent_sources" / relative, (ROOT / relative).read_bytes())
        base.save_once(folder / "BASELINE.json", baseline)
        base.save_once(folder / "LOST_NATIVE_SLOT.json", declaration)
        amendment = {"repair": REPAIR, "activated": True, "activated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "policy": make_policy(), "effective_frontier": target, "parent_freeze_sha256": expected_parent_sha256,
            "baseline_sha256": base.file_sha(folder / "BASELINE.json"), "repair_gate_sha256": base.file_sha(GATE),
            "loss_declaration_sha256": base.file_sha(folder / "LOST_NATIVE_SLOT.json"),
            "authorization": "Authorized technical repair and completion; no replay, added slot, charge erasure, or strategic prompt change.",
            "technical_deviation": "Consume the exact response-less first native slot; continue only original remaining slots with the full unknown reservation retained.",
            "infrastructure_loss_is_model_behavior": False, "network_success_guaranteed": False}
        base.save_once(folder / "AMENDMENT.json", amendment)
        active = deepcopy(snapshot)
        active["source_files"] = source_manifest()
        active["protocol_repair03"] = {key: amendment[key] for key in ("repair", "policy", "effective_frontier", "parent_freeze_sha256", "baseline_sha256", "repair_gate_sha256", "loss_declaration_sha256")}
        active["protocol_repair03"]["amendment_sha256"] = base.file_sha(folder / "AMENDMENT.json")
        base.save_once(folder / "ACTIVE_FREEZE.json", active)
        base.mutable_json(out / "freeze.json", active)
        return {"status": "repair03_activated", "frontier": target, "policy": make_policy(),
            "active_freeze_sha256": base.file_sha(out / "freeze.json"), "provider_calls": 0, "new_allowance": False}


def verify_repair(out, *, config_path=None):
    out, folder = Path(out).absolute(), Path(out).absolute() / "analysis/repair03"
    active, amendment, baseline = first._read(out / "freeze.json"), first._read(folder / "AMENDMENT.json"), first._read(folder / "BASELINE.json")
    record = active.get("protocol_repair03", {})
    if (active.get("source_files") != source_manifest() or record.get("policy") != make_policy()
            or amendment.get("policy") != make_policy() or amendment.get("activated") is not True
            or record.get("amendment_sha256") != base.file_sha(folder / "AMENDMENT.json")
            or record.get("baseline_sha256") != base.file_sha(folder / "BASELINE.json")
            or record.get("repair_gate_sha256") != base.file_sha(GATE)
            or record.get("loss_declaration_sha256") != base.file_sha(folder / "LOST_NATIVE_SLOT.json")
            or first._read(folder / "ACTIVE_FREEZE.json") != active):
        raise ValueError("Repair03 active seal changed")
    for name, expected in baseline["original_input_copies"].items():
        if base.file_sha(folder / name) != expected:
            raise ValueError("A preserved repair03 input changed")
    verify_parent_snapshot(out, first._read(folder / "PARENT_FREEZE.json"), config_path=config_path)
    for relative, expected in baseline["protected_files"].items():
        if base.file_sha(out / relative) != expected:
            raise ValueError("A pre-repair03 observation or artifact changed")
    for relative, expected in baseline["parent_sources"].items():
        if base.file_sha(folder / "parent_sources" / relative) != expected:
            raise ValueError("A parent174 source copy changed")
    rows = base.ledger_rows()
    if any(rows.get(k) != v for k, v in baseline["ledger_rows"].items()):
        raise ValueError("A pre-repair03 paid record changed")
    authority = Authority(folder / "LOST_NATIVE_SLOT.json", record["loss_declaration_sha256"])
    if not authority.matches(raw_rows(base.AGGREGATE).get(authority.record["row"]["request_id"], {})):
        raise ValueError("The unresolved reserved loss changed")
    return active


class Coordinator(parent.Coordinator):
    def verify_freeze(self):
        return verify_repair(self.out, config_path=self.config_path)

    def _backend(self):
        if self.backend is None:
            self.backend = Backend(self.config, self.out, base.AGGREGATE, policy=make_policy())
        return self.backend

    def resume_lost_stage(self):
        self.verify_freeze()
        with base.producer_lock(self.out):
            active = self.verify_freeze()
            engine = self._engine()
            record = first._read(self.out / "analysis/repair03/LOST_NATIVE_SLOT.json")
            target = record["effective_frontier"]
            if engine.next_stage() != target:
                raise ValueError("Only the declared already-dispatched stage can resume")
            claim = self.out / "analysis/repair03/RESUMPTION_CLAIM.json"
            prefix = original_funding.NAMESPACE + ":" + target["request_id"] + "."
            rows = raw_rows(base.AGGREGATE)
            cache = self.out / "provider_evidence/stage_results" / (base.sha(target["request_id"].encode()) + ".json")
            if claim.exists() and not cache.is_file():
                raise ValueError("The one resumption was already claimed without a complete stage cache; no replay")
            if not claim.exists() and [key for key in rows if key.startswith(prefix)] != [record["row"]["request_id"]]:
                raise ValueError("A new paid stage prefix blocks another resumption")
            request_path = self.out / f"episode/rounds/r{target['round']:02d}/{target['stage']}.request.json"
            request = first._read(request_path)
            if request != engine.actor_request():
                raise ValueError("The exact original actor view changed")
            claim_record = {"request_id": target["request_id"], "request_file_sha256": file_digest(request_path),
                "active_freeze_sha256": base.file_sha(self.out / "freeze.json"), "no_new_dispatch": True}
            if claim.exists() and first._read(claim) != claim_record:
                raise ValueError("The original resumption claim changed")
            if cache.is_file():
                saved = first._read(cache)
                if saved.get("request_hash") != base.sha(base.canonical(request).encode()):
                    raise ValueError("The exact completed stage cache changed")
                for receipt in saved["result"]["receipts"]:
                    if receipt.get("record_type") == "transport_slot_loss":
                        continue
                    paid = rows.get(receipt.get("request_id"))
                    if not paid or paid["status"] != "completed" or base.canonical(receipt) != paid["receipt_json"]:
                        raise ValueError("Cached stage requires every exact completed original receipt")
            backend = self._backend()  # Free preflight must succeed before irreversible claim.
            self.verify_freeze()
            if not cache.is_file() and [key for key in raw_rows(base.AGGREGATE) if key.startswith(prefix)] != [record["row"]["request_id"]]:
                raise ValueError("The paid prefix changed during free preflight")
            base.save_once(claim, claim_record)
            result = engine.validate_actor_result(request, backend.run_actor(deepcopy(request)))
            result["technical_transport_losses"] = [r for r in result["receipts"] if r.get("record_type") == "transport_slot_loss"]
            if result["output"]["image"]:
                result["output"]["image"] = engine._copy_image(result["output"]["image"])
            return engine._commit(target, {**result, "request_hash": request["request_hash"], "request_file_sha256": file_digest(request_path)})

    def advance(self, through_round=None):
        self.verify_freeze()
        engine = self._engine()
        record = first._read(self.out / "analysis/repair03/LOST_NATIVE_SLOT.json")
        if engine.next_stage() == record["effective_frontier"]:
            self.resume_lost_stage()
        return super().advance(through_round)

    def status(self):
        result = super().status()
        result["protocol_repair03"] = first._read(self.out / "analysis/repair03/AMENDMENT.json")
        return result

    def report(self):
        result = super().report()
        path = self.out / "analysis/report.json"
        report = first._read(path)
        report["protocol_repair03"] = first._read(self.out / "analysis/repair03/AMENDMENT.json")
        report["transport_slot_loss"] = first._read(self.out / "analysis/repair03/LOST_NATIVE_SLOT.json")
        report["interpretation"] += " Repair03 records an infrastructure response loss, not a model refusal or collusion observation; its unknown cost remains reserved and the original slot is consumed without replay."
        base.mutable_json(path, report)
        return result

    def render(self, *, allow_incomplete=False):
        result = super().render(allow_incomplete=allow_incomplete)
        destination = Path(result["destination"])
        text = "Declared repair03 technical deviation: one response-less first native slot is consumed without replay. No model action was observed; the full unknown charge remains reserved. Only the original remaining visual slots are available."
        path = destination / "REPORT.md"
        path.write_text(text + "\n\n" + path.read_text())
        path = destination / "GALLERY.html"
        path.write_text(path.read_text().replace("<body>", "<body><aside>" + html.escape(text) + "</aside>", 1))
        path = destination / "EVIDENCE.json"
        data = first._read(path)
        data["protocol_amendments"]["repair03"] = first._read(self.out / "analysis/repair03/AMENDMENT.json")
        base.mutable_json(path, data)
        return {**result, "protocol_repair03": make_policy()}
