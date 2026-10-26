"""User-selected temperature1; the existing mechanical notice remains exact."""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path

from experiments.triad_emergent_v9 import backend as base
from experiments.triad_emergent_v9.coordinator import read_json, file_sha
from experiments.triad_v9_protocol_repair01 import backend as parent


REPAIR = "triad_v9_protocol_repair02"
TOKEN_BUDGET_NOTICE = parent.TOKEN_BUDGET_NOTICE
NOTICE_SHA256 = parent.NOTICE_SHA256


def make_policy():
    return {"repair": REPAIR, "temperature": 1.0, "token_budget_notice_sha256": NOTICE_SHA256}


def validate_policy(policy):
    if not isinstance(policy, dict) or policy != make_policy() or type(policy.get("temperature")) not in {int, float}:
        raise base.BackendError("Repair02 requires the exact user-selected temperature1 policy")
    return deepcopy(policy)


def _activated(out, policy):
    out = Path(out).absolute()
    if any(p.is_symlink() for p in (out, *out.parents)):
        raise base.BackendError("Repair output cannot traverse symlinks")
    path = out / "analysis/repair02/AMENDMENT.json"
    if not path.is_file():
        raise base.BackendError("Production repair02 has not been activated")
    amendment = read_json(path)
    frozen = read_json(out / "freeze.json").get("protocol_repair02", {})
    if (amendment.get("activated") is not True or amendment.get("policy") != policy
            or frozen.get("amendment_sha256") != file_sha(path) or frozen.get("policy") != policy):
        raise base.BackendError("Repair02 sampling differs from its active seal")


class Provider(parent.Provider):
    def __init__(self, *args, policy, **kwargs):
        self.repair_policy = validate_policy(policy)
        base.Provider.__init__(self, *args, **kwargs)
        self.call_temperature = 1.0

    def preflight(self, *args, **kwargs):
        result = super().preflight(*args, **kwargs)
        result["sampling"]["prior_protocol_repair01_temperature"] = 2.0
        result["sampling"]["notice_unchanged_from_repair01"] = True
        self._preflight = deepcopy(result)
        return result


class Backend(parent.Backend):
    def __init__(self, config, out, aggregate_path, *, policy, transport=None,
                 api_key=None, guard=None, preflight=True):
        self.repair_policy = validate_policy(policy)
        if transport is None:
            _activated(out, self.repair_policy)
        base.Backend.__init__(self, config, out, aggregate_path, transport=transport,
                              api_key=api_key, guard=guard, preflight=False)
        key = os.environ.get("OPENROUTER_API_KEY") if api_key is None else api_key
        self.provider = Provider(key, self.guard, self.evidence_dir,
                                 transport=transport, policy=self.repair_policy)
        if preflight:
            self.preflight()
    # The unchanged repair01 _native adds the exact same notice once, then calls
    # base._native at our fixed temperature1. Its cache/replay checks remain.
