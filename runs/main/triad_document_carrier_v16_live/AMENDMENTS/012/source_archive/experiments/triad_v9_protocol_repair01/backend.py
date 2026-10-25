"""Bounded tool-format notice; no new actor action, retry or image convention.

Production use requires the separate repair seal. Offline injected transports
are supported for genuine native-wire tests in temporary ledgers only.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

from experiments.triad_emergent_v9 import backend as original


REPAIR = "triad_v9_protocol_repair01"
TOKEN_BUDGET_NOTICE = (
    "Mechanical output budget: this call has at most 8192 generated tokens in total, "
    "including reasoning and native-function JSON. Reserve room for one complete, "
    "concise native call. A create_visual/revise_visual prompt has at most 1200 Unicode "
    "characters; all other fields must fit their advertised limits. Truncated or "
    "malformed arguments execute no operation. Do not output private reasoning."
)
NOTICE_SHA256 = hashlib.sha256(TOKEN_BUDGET_NOTICE.encode()).hexdigest()


def validate_policy(policy):
    expected = {"repair", "temperature", "token_budget_notice_sha256"}
    if (not isinstance(policy, dict) or set(policy) != expected
            or policy.get("repair") != REPAIR
            or type(policy.get("temperature")) not in {int, float}
            or policy["temperature"] not in {1.0, 2.0}
            or policy.get("token_budget_notice_sha256") != NOTICE_SHA256):
        raise original.BackendError("An exact declared repair policy is required")
    return deepcopy(policy)


def make_policy(temperature):
    return validate_policy({"repair": REPAIR, "temperature": temperature,
                            "token_budget_notice_sha256": NOTICE_SHA256})


def _production_activation(out, policy):
    """Read-only fail-closed guard before any original ledger construction."""
    from experiments.triad_emergent_v9.coordinator import read_json, file_sha
    out = Path(out).absolute()
    if any(p.is_symlink() for p in (out, *out.parents)):
        raise original.BackendError("Repair output cannot traverse symlinks")
    path = out / "analysis/repair01/AMENDMENT.json"
    if not path.is_file():
        raise original.BackendError("Production repair has not been activated")
    amendment = read_json(path)
    frozen = read_json(out / "freeze.json").get("protocol_repair01", {})
    if (amendment.get("activated") is not True or amendment.get("policy") != policy
            or frozen.get("amendment_sha256") != file_sha(path)
            or frozen.get("policy") != policy):
        raise original.BackendError("Production repair policy differs from its active seal")


class Provider(original.Provider):
    def __init__(self, *args, policy, **kwargs):
        self.repair_policy = validate_policy(policy)
        super().__init__(*args, **kwargs)
        self.call_temperature = self.repair_policy["temperature"]

    def native_call(self, request_id, messages, tools, temperature):
        if temperature != self.repair_policy["temperature"]:
            raise original.BackendError("Native sampling differs from the selected repair policy")
        # An already-called native intent cannot acquire a new mechanical prompt.
        # Whole terminal stage caches are reused by the unchanged Backend first.
        if self.ledger.lookup(request_id) is not None:
            raise original.BackendError("Repair cannot replay an earlier native paid intent")
        self.call_temperature = temperature
        with original.repair.repaired_route():
            return self._chat_result(request_id, messages, native_tools=tools)

    def preflight(self, *args, **kwargs):
        result = super().preflight(*args, **kwargs)
        result["sampling"].update({
            "base_configuration_temperature": 2.0,
            "visual_temperature": self.repair_policy["temperature"],
            "vote_temperature": self.repair_policy["temperature"],
            "log_temperature": self.repair_policy["temperature"],
            "effective_protocol_repair": deepcopy(self.repair_policy),
            "prior_temperature2_observations_retained": True,
        })
        self._preflight = deepcopy(result)
        return result


class Backend(original.Backend):
    def __init__(self, config, out, aggregate_path, *, policy, transport=None,
                 api_key=None, guard=None, preflight=True):
        self.repair_policy = validate_policy(policy)
        if transport is None:
            _production_activation(out, self.repair_policy)
        # Original construction is preserved, except no free preflight occurs
        # until the replacement provider has the selected prospective policy.
        super().__init__(config, out, aggregate_path, transport=transport,
                         api_key=api_key, guard=guard, preflight=False)
        key = os.environ.get("OPENROUTER_API_KEY") if api_key is None else api_key
        self.provider = Provider(key, self.guard, self.evidence_dir,
                                 transport=transport, policy=self.repair_policy)
        if preflight:
            self.preflight()

    def _native(self, local_id, messages, tools, temperature):
        if temperature != 2.0:
            raise original.BackendError("The unchanged v9 engine must retain its declared base policy")
        actual = deepcopy(messages)
        if not actual or actual[0].get("role") != "system" or not isinstance(actual[0].get("content"), str):
            raise original.BackendError("Mechanical notice needs the original explicit system message")
        actual[0]["content"] += "\n\n" + TOKEN_BUDGET_NOTICE
        return super()._native(local_id, actual, tools, self.repair_policy["temperature"])
