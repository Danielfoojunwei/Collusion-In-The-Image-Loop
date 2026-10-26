"""Consume one declared lost slot; never POST or invent its missing response."""
from copy import deepcopy
import os
from pathlib import Path
import re
from visual_collusion.budget import _units, BudgetError

from experiments.triad_emergent_v9 import backend as base
from experiments.triad_emergent_v9.coordinator import read_json, file_sha
from experiments.triad_v9_protocol_repair01.backend import TOKEN_BUDGET_NOTICE, NOTICE_SHA256
from experiments.triad_v9_protocol_repair02 import backend as parent
from .authority import Authority, REPAIR
from .funding import create_guard

LOSS_TEXT = "Transport response unavailable. No returned operation was executed by the harness. The original slot was consumed; this is infrastructure loss, not an observed model answer."
OLD_LOSS_FEEDBACK = "The preceding visual response was invalid; its slot was consumed. " + LOSS_TEXT
LOSS_FEEDBACK = "The preceding visual response was unavailable due to infrastructure loss. No model answer was observed and no returned operation was executed by the harness. Its original slot was consumed."


def make_policy():
    return {"repair": REPAIR, "temperature": 1.0, "token_budget_notice_sha256": NOTICE_SHA256,
        "lost_native_slot": "exact_sealed_responseless_first_visual_slot_only",
        "reserve_retained": True, "retry": False}


def activated_authority(out):
    out = Path(out).absolute()
    path = out / "analysis/repair03/AMENDMENT.json"
    if not path.is_file():
        raise base.BackendError("Production repair03 has not been activated")
    amendment = read_json(path)
    frozen = read_json(out / "freeze.json").get("protocol_repair03", {})
    if (amendment.get("activated") is not True or amendment.get("policy") != make_policy()
            or frozen.get("policy") != make_policy() or frozen.get("amendment_sha256") != file_sha(path)
            or frozen.get("loss_declaration_sha256") != amendment.get("loss_declaration_sha256")):
        raise base.BackendError("Repair03 activation or loss authority changed")
    return Authority(out / "analysis/repair03/LOST_NATIVE_SLOT.json", amendment["loss_declaration_sha256"])


class Provider(parent.Provider):
    def preflight(self, *args, **kwargs):
        result = super().preflight(*args, **kwargs)
        result["sampling"]["effective_protocol_repair"] = make_policy()
        result["sampling"]["prior_protocol_repair02_temperature"] = 1.0
        self._preflight = deepcopy(result)
        return result

    def _post_exact(self, body, binding):
        try:
            response = super()._post_exact(body, binding)
            # Preserve a genuine successful chat envelope before the inherited
            # callback builds its receipt. This is observed evidence, never a
            # fabricated completion or automatic recovery authorization.
            if (binding.get("endpoint") == "/chat/completions" and isinstance(response, dict)
                    and "error" not in response and isinstance(response.get("choices"), list)
                    and response["choices"] and isinstance(response.get("usage"), dict)
                    and isinstance(response.get("id"), str)
                    and re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", response["id"])
                    and isinstance(response.get("model"), str)):
                try:
                    _units(response["usage"].get("cost"))
                    base.canonical(response)
                except (BudgetError, TypeError, ValueError):
                    pass
                else:
                    base._save_once(self.evidence_dir / "observed_responses" / (base.sha(binding["request_id"].encode()) + ".json"),
                        {"record_type": "observed_chat_response_before_receipt", "binding": binding,
                         "response": response, "automatic_recovery_authorized": False})
            return response
        except BaseException as error:
            if self.last_transport_diagnostic is None:
                name = type(error).__name__
                safe_class = name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", name) else "UnclassifiedException"
                self._diagnostic(binding, "unclassified_callback_failure", safe_class)
            raise


class Backend(parent.Backend):
    def __init__(self, config, out, aggregate_path, *, policy, transport=None,
                 api_key=None, guard=None, authority=None, preflight=True):
        if policy != make_policy():
            raise base.BackendError("The exact repair03 policy is required")
        if transport is None:
            authority = activated_authority(out)
        if not isinstance(authority, Authority):
            raise base.BackendError("Repair03 requires its immutable loss declaration")
        if Path(authority.record["out"]).absolute() != Path(out).absolute():
            raise base.BackendError("Lost-slot authority belongs to another run")
        self.loss_authority = authority
        self.repair_policy = deepcopy(policy)
        if guard is None:
            guard = create_guard(Path(aggregate_path), Path(out) / "provider_evidence", authority)
        elif guard.ledger.loss_authority.expected_sha256 != authority.expected_sha256:
            raise base.BackendError("Funding and backend lost-slot authorities differ")
        base.Backend.__init__(self, config, out, aggregate_path, transport=transport,
            api_key=api_key, guard=guard, preflight=False)
        key = os.environ.get("OPENROUTER_API_KEY") if api_key is None else api_key
        self.provider = Provider(key, guard, self.evidence_dir, transport=transport, policy=parent.make_policy())
        if preflight:
            self.preflight()

    def _native(self, local_id, messages, tools, temperature):
        record = self.loss_authority.read()
        if base.NAMESPACE + ":" + local_id != record["row"]["request_id"]:
            # The frozen loop's synthetic invalid-response label is unsuitable
            # for a response never observed. Replace only this exact mechanical
            # message for the declared stage; no agent content is rewritten.
            actual = messages
            stage = record["row"]["request_id"].split(":", 1)[1].removesuffix(".visual1")
            if local_id.startswith(stage + "."):
                actual = deepcopy(messages)
                for message in actual:
                    if message == {"role": "user", "content": OLD_LOSS_FEEDBACK}:
                        message["content"] = LOSS_FEEDBACK
            return super()._native(local_id, actual, tools, temperature)
        if temperature != 2.0 or not self.loss_authority.matches(record["row"]):
            raise base.BackendError("Lost native slot policy changed")
        with self.guard.ledger._connect() as db:
            row = db.execute("SELECT * FROM requests WHERE request_id=?", (record["row"]["request_id"],)).fetchone()
            if row is None or not self.loss_authority.matches(row):
                raise base.BackendError("Lost native slot row changed")
        actual = deepcopy(messages)
        actual[0]["content"] += "\n\n" + TOKEN_BUDGET_NOTICE
        payload = self.provider._chat_payload(actual, native_tools=tools)
        archive = read_json(self.out / record["transport_archive"])
        wire = base.codec.verify_wire(self.evidence_dir, "/chat/completions", payload, archive["mapping"])
        if base.sha(base.canonical(wire).encode()) != record["wire_body_sha256"]:
            raise base.BackendError("Resumed lost native slot differs from its original wire")
        event = self.loss_authority.event()
        value = {"invalid": True, "transport_loss": True, "native_message": None,
            "format_retry_eligible": True, "receipt": event,
            "reason": LOSS_TEXT}
        base._save_once(self.evidence_dir / "calls" / (base.sha(local_id.encode()) + ".json"),
            {"request_id": local_id, "kind": "transport_slot_loss", **value})
        return value

    def run_actor(self, request):
        result = super().run_actor(request)
        losses = [r for r in result["receipts"] if r.get("record_type") == "transport_slot_loss"]
        if losses:
            result = deepcopy(result)
            result["technical_transport_losses"] = losses
            for action in result["actions"]:
                if action.get("receipt", {}).get("record_type") == "transport_slot_loss":
                    action["kind"] = "transport_slot_loss"
                    action["transport_loss"] = True
                    action["transport_error"] = action.pop("validation_error", LOSS_TEXT)
            base._save_once(self.out / "analysis/repair03/RECOVERED_STAGE_CLASSIFICATION.json", {
                "request_id": request["request_id"], "technical_transport_losses": losses,
                "interpretation": "The preserved underlying loop cache uses its historical generic invalid branch; this projection labels the missing response as infrastructure loss, never observed malformed model output."})
        return result
