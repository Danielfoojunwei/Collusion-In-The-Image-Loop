"""Attempt-isolated native actors; typed infrastructure faults abort the round."""
from copy import deepcopy
import json
import http.client
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request

from visual_collusion.archive import store_pixels
from visual_collusion.budget import BudgetError, PendingPaidRequest, _units
from visual_collusion.providers import ProviderError, PreflightError, ResponseValidationError
from experiments.triad_emergent_v9 import backend as base
from experiments.triad_emergent_v9.coordinator import read_json, file_sha
from experiments.triad_v9_protocol_repair01.backend import TOKEN_BUDGET_NOTICE, NOTICE_SHA256
from experiments.triad_v9_protocol_repair02 import backend as parent
from .faults import TechnicalRoundFault, PermanentTechnicalFault, safe_class
from .funding import REPAIR, create_guard, preflight_guard, validate_context, check_active_context


def make_policy():
    return {"repair": REPAIR, "temperature": 1.0, "token_budget_notice_sha256": NOTICE_SHA256,
        "max_technical_attempts_per_round": 3, "recovery": "restart_entire_round_from_last_completed_checkpoint",
        "unknown_reserves_retained": True, "old_intents_reposted": False}


def _activated(out, policy):
    out = Path(out).absolute()
    if ".." in out.parts or any(p.is_symlink() for p in (out, *out.parents)):
        raise PermanentTechnicalFault("source_or_path_integrity")
    path = out / "analysis/restart04/AMENDMENT.json"
    if not path.is_file():
        raise PermanentTechnicalFault("restart04_not_activated")
    amendment, frozen = read_json(path), read_json(out / "freeze.json").get("protocol_checkpoint_restart04", {})
    if (amendment.get("activated") is not True or amendment.get("policy") != policy
            or frozen.get("policy") != policy or frozen.get("amendment_sha256") != file_sha(path)):
        raise PermanentTechnicalFault("source_or_policy_integrity")


def _transport_fault(error, diagnostic=None, **details):
    if isinstance(error, TechnicalRoundFault):
        return error
    diagnostic = diagnostic or {}
    code = diagnostic.get("http_status", getattr(error, "code", None))
    info = {**details, **{k: diagnostic[k] for k in ("request_id", "request_hash", "wire_body_sha256", "endpoint", "record_path", "record_sha256") if k in diagnostic}}
    if type(code) is int:
        cls = TechnicalRoundFault if code in {408, 409, 425, 429} or 500 <= code <= 599 else PermanentTechnicalFault
        return cls("api_http_failure" if cls is TechnicalRoundFault else "api_auth_or_schema_hold", error, http_status=code, **info)
    return TechnicalRoundFault("api_transport_failure", error, **info)


class Provider(parent.Provider):
    def adopt_preflight(self, report):
        sampling = report.get("sampling", {}) if isinstance(report, dict) else {}
        age = time.time() - report.get("checked_at_unix", 0) if isinstance(report, dict) else 1e9
        if (not isinstance(report, dict) or report.get("status") != "ready" or not 0 <= age <= 900
                or report.get("paid_requests_made") != 0
                or report.get("models") != {"actor": base.MODEL, "image": base.IMAGE_MODEL}
                or sampling.get("effective_protocol_repair") != make_policy()
                or sampling.get("reasoning_effort") != "high" or sampling.get("max_output_tokens") != 8192
                or any(sampling.get(k) != 1.0 for k in ("visual_temperature", "vote_temperature", "log_temperature"))
                or set(report.get("chat_metadata", {})) != {base.MODEL} or not isinstance(report.get("image_endpoint"), dict)):
            raise PermanentTechnicalFault("cached_free_preflight_hold")
        self.ledger.assert_ready()
        self._models = deepcopy(report["chat_metadata"])
        self._image_endpoint = deepcopy(report["image_endpoint"])
        self._preflight_time, self._preflight = time.monotonic() - age, deepcopy(report)
        return deepcopy(report)

    def _request(self, method, path, payload=None):
        # Preserve safe GET failure classification before older adapters erase
        # its HTTP status. No arbitrary headers or error body are inspected.
        if method != "GET" or not isinstance(path, str) or not path.startswith("/") or any(c in path for c in ("..", "?", "#")):
            raise PermanentTechnicalFault("invalid_free_endpoint")
        try:
            if self._transport is not None:
                result = self._transport("GET", path, deepcopy(payload))
            else:
                request = urllib.request.Request(self.BASE_URL + path, method="GET", headers={"Authorization": "Bearer " + self._key, "Content-Type": "application/json"})
                class NoRedirect(urllib.request.HTTPRedirectHandler):
                    def redirect_request(self, *args, **kwargs):
                        return None
                opener = self._opener or urllib.request.build_opener(NoRedirect())
                with opener.open(request, timeout=self.timeout) as response:
                    raw = response.read(64 * 1024 * 1024 + 1)
                    if len(raw) > 64 * 1024 * 1024:
                        raise TechnicalRoundFault("free_response_size_limit")
                    result = base.parse_object(raw.decode())
            if not isinstance(result, dict):
                raise TechnicalRoundFault("free_provider_envelope")
            if isinstance(result.get("error"), dict):
                code = result["error"].get("code")
                raise _transport_fault(ProviderError("Provider error envelope"), {"http_status": code} if type(code) is int else {}, endpoint=path, phase="free_get")
            return result
        except TechnicalRoundFault:
            raise
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
            raise _transport_fault(error, endpoint=path, phase="free_get") from None
        except (UnicodeError, ValueError, TypeError) as error:
            raise TechnicalRoundFault("free_provider_envelope", error, endpoint=path, phase="free_get") from None

    def preflight(self, *args, **kwargs):
        try:
            result = super().preflight(*args, **kwargs)
        except TechnicalRoundFault:
            raise
        except (BudgetError, PendingPaidRequest, PreflightError, ProviderError, ValueError) as error:
            raise PermanentTechnicalFault("funding_or_metadata_hold", error, phase="free_preflight") from None
        result["sampling"]["effective_protocol_repair"] = make_policy()
        self._preflight = deepcopy(result)
        return result

    def _post_exact(self, body, binding):
        try:
            response = super()._post_exact(body, binding)
            if isinstance(response, dict) and response.get("error") is not None:
                api_error = response["error"]
                code = api_error.get("code") if isinstance(api_error, dict) else None
                self._diagnostic(binding, "provider_error_envelope", "ProviderError", code if type(code) is int and 400 <= code <= 599 else None)
                raise TechnicalRoundFault("provider_error_envelope", request_id=binding["request_id"])
            if (binding.get("endpoint") == "/chat/completions" and isinstance(response, dict) and "error" not in response
                    and isinstance(response.get("choices"), list) and response["choices"]
                    and isinstance(response.get("usage"), dict) and isinstance(response.get("id"), str)
                    and re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", response["id"])):
                try:
                    _units(response["usage"].get("cost"))
                    base.canonical(response)
                except (BudgetError, ValueError, TypeError):
                    pass
                else:
                    base._save_once(self.evidence_dir / "observed_responses" / (base.sha(binding["request_id"].encode()) + ".json"),
                        {"record_type": "observed_chat_response_before_receipt", "binding": binding,
                         "response": response, "automatic_recovery_authorized": False})
            return response
        except BaseException as error:
            if self.last_transport_diagnostic is None:
                self._diagnostic(binding, "unclassified_callback_failure", safe_class(error))
            raise

    def _paid(self, request_id, kind, endpoint, payload, reserve_usd):
        try:
            result = super()._paid(request_id, kind, endpoint, payload, reserve_usd)
        except TechnicalRoundFault:
            raise
        except PendingPaidRequest as error:
            row = self.ledger.lookup(request_id)
            if row is None:
                raise PermanentTechnicalFault("unrelated_unresolved_funding_hold", error, request_id=request_id) from None
            diagnostic = self.last_transport_diagnostic
            if diagnostic:
                raise _transport_fault(error, diagnostic, phase="paid") from None
            response = row.get("response")
            api_error = response.get("error") if isinstance(response, dict) else None
            code = api_error.get("code") if isinstance(api_error, dict) else None
            if type(code) is int and 400 <= code <= 599:
                raise _transport_fault(error, {"http_status": code}, request_id=request_id, phase="paid") from None
            raise TechnicalRoundFault("provider_receipt_or_callback_failure", error, request_id=request_id, endpoint=endpoint) from None
        except BudgetError as error:
            raise PermanentTechnicalFault("budget_or_request_identity_hold", error, request_id=request_id) from None
        response, receipt, cached = result
        if not isinstance(response, dict) or response.get("error") is not None:
            raise TechnicalRoundFault("provider_error_envelope", request_id=request_id, endpoint=endpoint)
        if kind == "chat":
            if not isinstance(response.get("model"), str) or not response.get("model"):
                raise TechnicalRoundFault("provider_chat_envelope", request_id=request_id)
            allowed = {base.MODEL, self._models.get(base.MODEL, {}).get("canonical_slug")}
            allowed = {item for item in allowed if isinstance(item, str)}
            allowed |= {item.split("/", 1)[1] for item in allowed if "/" in item}
            if response.get("model") not in allowed or response.get("provider") not in {None, "OpenInference", "open-inference/fp4"}:
                raise PermanentTechnicalFault("provider_identity_hold", request_id=request_id)
            choices = response.get("choices")
            if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict) or not isinstance(choices[0].get("message"), dict):
                raise TechnicalRoundFault("provider_chat_envelope", request_id=request_id)
        elif response.get("model") not in {None, base.IMAGE_MODEL, base.IMAGE_MODEL.split("/", 1)[1]} or response.get("provider") not in {None, "Alibaba Cloud Int.", "alibaba"}:
            raise PermanentTechnicalFault("provider_identity_hold", request_id=request_id)
        return result


class Backend(parent.Backend):
    def __init__(self, config, out, aggregate_path, *, global_out, attempt_context,
                 policy=None, transport=None, api_key=None, guard=None, preflight=True, cached_preflight=None):
        policy = make_policy() if policy is None else policy
        if policy != make_policy():
            raise PermanentTechnicalFault("source_or_policy_integrity")
        self.global_out = Path(global_out).absolute()
        self.attempt_context = validate_context(self.global_out, attempt_context)
        if Path(out).absolute() != Path(self.attempt_context["working_root"]):
            raise PermanentTechnicalFault("attempt_workspace_hold")
        if transport is None:
            _activated(self.global_out, policy)
        check_active_context(self.global_out, self.attempt_context)
        self.repair_policy = deepcopy(policy)
        if guard is None:
            guard = create_guard(aggregate_path, global_out=self.global_out, attempt_context=self.attempt_context,
                evidence_dir=Path(out) / "provider_evidence")
        if guard.guard.context != self.attempt_context:
            raise PermanentTechnicalFault("funding_attempt_identity_hold")
        base.Backend.__init__(self, config, out, aggregate_path, transport=transport, api_key=api_key, guard=guard, preflight=False)
        key = os.environ.get("OPENROUTER_API_KEY") if api_key is None else api_key
        self.provider = Provider(key, guard, self.evidence_dir, transport=transport, policy=parent.make_policy())
        if cached_preflight is not None:
            result = self.provider.adopt_preflight(cached_preflight)
            base._save_once(self.evidence_dir / "preflights" / (base.sha(base.canonical(result).encode()) + ".json"), result)
        elif preflight:
            self.preflight()

    @staticmethod
    def preflight_before_attempt(config, global_out, aggregate_path, policy=None, *, transport=None, api_key=None, guard=None):
        policy = make_policy() if policy is None else policy
        if policy != make_policy():
            raise PermanentTechnicalFault("source_or_policy_integrity")
        if transport is None:
            _activated(global_out, policy)
        if guard is None:
            guard = preflight_guard(aggregate_path, global_out=global_out)
        key = os.environ.get("OPENROUTER_API_KEY") if api_key is None else api_key
        provider = Provider(key, guard, Path(global_out) / "analysis/restart04/preflight_evidence", transport=transport, policy=parent.make_policy())
        return provider.preflight(minimum_remaining=.05)

    def _native(self, local_id, messages, tools, temperature):
        if temperature != 2.0:
            raise PermanentTechnicalFault("historical_configuration_hold")
        actual = deepcopy(messages)
        actual[0]["content"] += "\n\n" + TOKEN_BUDGET_NOTICE
        try:
            result = self.provider.native_call(local_id, actual, tools, 1.0)
        except ResponseValidationError as error:
            if error.receipt is None:
                raise TechnicalRoundFault("missing_native_receipt", error, request_id=local_id) from None
            raw = self.provider.ledger.lookup(local_id)
            response = raw.get("response") if isinstance(raw, dict) else None
            if base._semantic_call_present(response, tools):
                raise PermanentTechnicalFault("native_envelope_compatibility_hold", error, request_id=local_id) from None
            value = {"invalid": True, "refused": isinstance(error, base.TerminalVoteResponseError) and base._explicit_refusal(response),
                "reason": str(error), "receipt": error.receipt, "native_message": None,
                "format_retry_eligible": getattr(error, "format_retry_eligible", True)}
        else:
            value = {"content": result["content"], "receipt": result["receipt"], "native_message": result["native_message"], "sampling": result["actor_sampling"]}
        base._save_once(self.evidence_dir / "calls" / (base.sha(local_id.encode()) + ".json"), {"request_id": local_id, "kind": "chat", **value})
        return value

    def _generate(self, local_id, prompt, reference):
        try:
            with base.repair.repaired_route():
                result = self.provider.image(local_id, prompt, input_reference=reference)
        except ResponseValidationError as error:
            fault = TechnicalRoundFault("invalid_provider_png", error, request_id=local_id)
            base._save_once(self.evidence_dir / "technical_image_failures" / (base.sha(local_id.encode()) + ".json"), {"fault": fault.fault, "receipt": error.receipt})
            raise fault from None
        raw = result["png_bytes"]
        store_pixels(self.evidence_dir, raw)
        value = {"receipt": result["receipt"], "png_bytes": raw,
            "image": {"path": str(self.evidence_dir / "blobs" / (base.sha(raw) + ".png")), "sha256": base.sha(raw)}}
        base._save_once(self.evidence_dir / "image_calls" / (base.sha(local_id.encode()) + ".json"), {"request_id": local_id, "prompt": prompt,
            "reference": "current_draft" if reference is not None else "none", "reference_sha256": base.sha(reference) if reference is not None else None,
            "result_sha256": base.sha(raw), "receipt": result["receipt"]})
        base._save_once(self.evidence_dir / "calls" / (base.sha(local_id.encode()) + ".json"), {"request_id": local_id, "kind": "image", **{k: v for k, v in value.items() if k != "png_bytes"}})
        return value

    def run_actor(self, request):
        check_active_context(self.global_out, self.attempt_context)
        try:
            return base.Backend.run_actor(self, request)
        except TechnicalRoundFault:
            raise
        except OSError as error:
            raise TechnicalRoundFault("local_storage_failure", error) from None
        except (BudgetError, base.BackendError, ProviderError, ValueError, TypeError, KeyError) as error:
            raise PermanentTechnicalFault("harness_or_integrity_hold", error) from None
