"""OpenRouter transport: actual multimodal inputs, one paid POST per intent.

Only free metadata/key GETs run during preflight. No paid pilots, retry loops,
fallback models, URL-only image substitutes, or host credential persistence.
The adapter is contract-tested offline; live model access is checked at runtime.
"""
from __future__ import annotations

import base64
import binascii
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import io
import json
import time
import urllib.error
import urllib.request

from .budget import BudgetError, BudgetLedger, PendingPaidRequest, canonical, request_digest

DEFAULT_MODELS = {
    "producer": "z-ai/glm-5.3-flash",
    "verifier": "z-ai/glm-5.3-flash",
    "monitor": "anthropic/claude-sonnet-5.5",
    "image": "qwen/qwen-image-2.1-turbo",
}
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_SIDE = 1024
# Agent ceilings. The independent auditor has its own smaller profile below.
MAX_CHAT_TEXT_BYTES = 65_536  # Full UTF-8 history plus response schema.
MAX_CHAT_MESSAGES = 64
MAX_CHAT_IMAGES = 16
MAX_CHAT_OUTPUT_TOKENS = 4_096
MAX_INPUT_RESERVE_TOKENS = 210_000
MAX_IMAGE_PROMPT_BYTES = 4_096
VISION_RESERVE_TOKENS = 8_192  # Conservative allowance, not a tokenizer claim.
IMAGE_OUTPUT_RESERVE_USD = Decimal("0.028")  # One <=$0.025 output and <=$0.003 reference.
_CHAT_MODELS = {DEFAULT_MODELS["producer"], DEFAULT_MODELS["monitor"]}
CHAT_PROFILES = {
    DEFAULT_MODELS["producer"]: {
        "provider_tag": "sail-research/fp4", "provider_name": "Sail Research",
        "prompt_ceiling": Decimal("0.00000015"), "completion_ceiling": Decimal("0.0000005"),
        "input_reserve_rate": Decimal("0.0000002"), "cache_price_ceiling": Decimal("0.00000015"),
        "reservation_margin": Decimal("0.002"), "quantization": "fp4",
        "max_text_bytes": MAX_CHAT_TEXT_BYTES, "max_messages": MAX_CHAT_MESSAGES,
        "max_images": MAX_CHAT_IMAGES, "max_output_tokens": MAX_CHAT_OUTPUT_TOKENS,
        "max_input_reserve_tokens": MAX_INPUT_RESERVE_TOKENS,
        "reasoning_effort": "high", "expose_reasoning": True,
    },
    DEFAULT_MODELS["monitor"]: {
        "provider_tag": "anthropic", "provider_name": "Anthropic",
        "prompt_ceiling": Decimal("0.000002"), "completion_ceiling": Decimal("0.00001"),
        "input_reserve_rate": Decimal("0.000004"), "cache_price_ceiling": Decimal("0.000004"),
        "reservation_margin": Decimal("0.01"), "quantization": None,
        "max_text_bytes": 16_384, "max_messages": 40,
        "max_images": 8, "max_output_tokens": 2_048,
        "max_input_reserve_tokens": 90_000,
        "reasoning_effort": "low", "expose_reasoning": False,
    },
}
IMAGE_PROVIDER_TAG = "alibaba"
IMAGE_PROVIDER_NAME = "Alibaba Cloud Int."
_IMAGE_RATE_CEILINGS = {"input_image": Decimal("0.003"), "output_image": Decimal("0.025")}


class ProviderError(RuntimeError):
    def __init__(self, message, *, receipt=None):
        super().__init__(message)
        self.receipt = receipt


class PreflightError(ProviderError):
    pass


class ResponseValidationError(ProviderError):
    """A known billed response is unusable; the job may end without a retry.

    The caller may record that job as failed and continue independent jobs.
    Transport/cost ambiguity, model pin failures, and overruns remain fatal.
    """


def _number(value, label):
    if isinstance(value, bool):
        raise PreflightError("Invalid numeric provider metadata: " + label)
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise PreflightError("Missing or invalid provider metadata: " + label) from None
    if not number.is_finite() or number < 0:
        raise PreflightError("Invalid numeric provider metadata: " + label)
    return number


def _validate_png(data: bytes, *, require_square=False):
    if not isinstance(data, bytes) or not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) > MAX_IMAGE_BYTES:
        raise ProviderError("Expected size-bounded actual PNG bytes")
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            if img.format != "PNG" or width < 1 or height < 1 or max(width, height) > MAX_IMAGE_SIDE:
                raise ValueError("dimensions")
            if require_square and (width, height) != (1024, 1024):
                raise ValueError("fixed image size")
            if getattr(img, "n_frames", 1) != 1:
                raise ValueError("animated image")
            img.verify()
    except ImportError:
        raise ProviderError("Pillow is required to validate actual image input/output") from None
    except (OSError, ValueError, SyntaxError):
        raise ProviderError("PNG cannot be decoded or exceeds the locked dimensions") from None
    return width, height


def image_content(png_bytes: bytes):
    """An actual image content block, never a filename or textual proxy."""
    _validate_png(png_bytes)
    return {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii"), "detail": "high"}}


def build_chat_payload(model, messages, max_output_tokens, schema=None,
                       reasoning_effort=None, *, structured_schema=True):
    """Pure canonical request construction, shared with offline evidence checks."""
    if model not in _CHAT_MODELS:
        raise ProviderError("Model/effort differs from the locked experiment")
    profile = CHAT_PROFILES[model]
    effort = profile["reasoning_effort"] if reasoning_effort is None else reasoning_effort
    if effort != profile["reasoning_effort"]:
        raise ProviderError("Model/effort differs from the locked experiment")
    if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int) or not 1 <= max_output_tokens <= profile["max_output_tokens"]:
        raise ProviderError("Output budget exceeds this model's locked token limit")
    if type(structured_schema) is not bool:
        raise ProviderError("Recorded structured-output capability must be explicit")
    payload = {"model": model, "messages": messages, "max_tokens": max_output_tokens, "stream": False,
               "reasoning": {"effort": effort, "exclude": not profile["expose_reasoning"]},
               "provider": {"only": [profile["provider_tag"]], "allow_fallbacks": False, "require_parameters": True,
                            "max_price": {"prompt": float(profile["prompt_ceiling"] * 1_000_000),
                                          "completion": float(profile["completion_ceiling"] * 1_000_000), "request": 0}}}
    if schema is not None and structured_schema:
        payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "experiment_response", "strict": True, "schema": schema}}
    else:
        payload["response_format"] = {"type": "json_object"}
    return payload


def build_image_payload(prompt, model=DEFAULT_MODELS["image"], quality="standard",
                        size="1024x1024", input_reference=None):
    """Pure fixed image request construction, including actual reference bytes."""
    if model != DEFAULT_MODELS["image"] or quality != "standard" or size != "1024x1024":
        raise ProviderError("Qwen image model, standard 1K tier, and 1024x1024 size are fixed")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode("utf-8")) > MAX_IMAGE_PROMPT_BYTES:
        raise ProviderError("Image prompt is empty or exceeds its locked byte budget")
    payload = {"model": model, "prompt": prompt, "n": 1, "resolution": "1K", "aspect_ratio": "1:1", "output_format": "png",
               "provider": {"only": [IMAGE_PROVIDER_TAG], "allow_fallbacks": False}}
    if input_reference is not None:
        block = image_content(input_reference)
        payload["input_references"] = [{"type": "image_url", "image_url": {"url": block["image_url"]["url"]}}]
    return payload


class OpenRouterProvider:
    BASE_URL = "https://openrouter.ai/api/v1"
    evidence_origin = "live"

    def __init__(self, api_key: str, ledger: BudgetLedger, *, timeout=120, opener=None, transport=None):
        # Cloud environments can substitute secrets at the proxy; no sk- prefix check.
        if not isinstance(api_key, str) or not api_key or "\r" in api_key or "\n" in api_key:
            raise ProviderError("OPENROUTER_API_KEY is missing or malformed; no request sent")
        self._key = api_key
        self.ledger = ledger
        self.timeout = timeout
        self._transport = transport
        self._opener = opener
        self._preflight = None
        self._models = {}
        self._image_endpoint = None
        self._preflight_time = 0.0

    def _request(self, method, path, payload=None):
        if not path.startswith("/") or ".." in path or "?" in path or "#" in path:
            raise ProviderError("Invalid fixed API path")
        if self._transport is not None:
            # Explicit offline dependency injection; never records authentication.
            return self._transport(method, path, deepcopy(payload))
        data = canonical(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(self.BASE_URL + path, data=data, method=method,
                                        headers={"Authorization": "Bearer " + self._key, "Content-Type": "application/json"})

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        # build_opener preserves standard HTTPS_PROXY handling. Never alter proxies.
        opener = self._opener or urllib.request.build_opener(NoRedirect())
        try:
            with opener.open(request, timeout=self.timeout) as response:
                raw = response.read(64 * 1024 * 1024 + 1)
                if len(raw) > 64 * 1024 * 1024:
                    raise ProviderError("Provider response exceeded 64 MiB")
                value = json.loads(raw.decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                if not isinstance(value, dict):
                    raise ValueError("object response required")
                return value
        except urllib.error.HTTPError as exc:
            # Do not read/echo an error body or a request carrying an authorization key.
            raise ProviderError("API HTTP " + str(exc.code) + "; no automatic retry") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise ProviderError("Transport or JSON decoding failed; no automatic retry") from None

    def _key_check(self, minimum_remaining):
        result = self._request("GET", "/key")
        data = result.get("data")
        if not isinstance(data, dict):
            raise PreflightError("Free key metadata was unavailable")
        if "limit_reset" not in data or data["limit_reset"] is not None:
            raise PreflightError("Use a dedicated lifetime key cap without daily/weekly/monthly resets")
        limit = _number(data.get("limit"), "key limit")
        remaining = _number(data.get("limit_remaining"), "key limit remaining")
        usage = _number(data.get("usage"), "key usage")
        byok_usage = _number(data.get("byok_usage", 0), "BYOK usage")
        if not 0 < limit <= 40 or remaining > limit:
            raise PreflightError("Dedicated OpenRouter key must have a positive lifetime limit no greater than $40")
        if remaining < _number(minimum_remaining, "required allowance"):
            raise PreflightError("Key remaining allowance cannot cover the required budget/reservation")
        if byok_usage != 0 or data.get("is_management_key") is True or data.get("is_free_tier") is True:
            raise PreflightError("Use a paid inference key without BYOK usage; management/free keys are not suitable")
        if usage + remaining > limit + Decimal("0.000001"):
            raise PreflightError("Inconsistent key limit metadata")
        return {"limit_usd": float(limit), "remaining_usd": float(remaining), "usage_usd": float(usage),
                "limit_reset": None, "byok_usage_usd": 0.0,
                "key_identity": "unverified: keep the same dedicated key; a Cloud placeholder is not a credential identifier",
                "wallet_funding": "unverified: normal inference keys cannot read management-only wallet credits"}

    @staticmethod
    def _chat_metadata(row, model, endpoints):
        profile = CHAT_PROFILES[model]
        architecture = row.get("architecture", {})
        if not {"text", "image"}.issubset(set(architecture.get("input_modalities", []))) or "text" not in architecture.get("output_modalities", []):
            raise PreflightError("Required real image input/text output missing for " + model)
        data = endpoints.get("data")
        if not isinstance(data, dict) or data.get("id") != model:
            raise PreflightError("Free chat endpoint metadata is missing or has the wrong model")
        matches = [entry for entry in data.get("endpoints", [])
                   if entry.get("tag") == profile["provider_tag"]]
        if len(matches) != 1:
            raise PreflightError("The exact pinned chat provider endpoint is unavailable: " + model)
        endpoint = matches[0]
        if endpoint.get("provider_name") != profile["provider_name"] or endpoint.get("model_id") != model or endpoint.get("status") != 0:
            raise PreflightError("Pinned chat provider identity/status differs from the experiment")
        if profile["quantization"] is not None and endpoint.get("quantization") != profile["quantization"]:
            raise PreflightError("Pinned chat quantization changed")
        parameters = set(row.get("supported_parameters", [])) & set(endpoint.get("supported_parameters", []))
        if not {"response_format", "max_tokens", "reasoning"}.issubset(parameters):
            raise PreflightError("Required JSON-output/reasoning capability missing for " + model)
        if profile["reasoning_effort"] not in row.get("reasoning", {}).get("supported_efforts", []):
            raise PreflightError("Locked " + profile["reasoning_effort"] + " reasoning effort is unavailable for " + model)
        for source in (row, endpoint):
            if _number(source.get("context_length"), "context length") < profile["max_input_reserve_tokens"] + profile["max_output_tokens"]:
                raise PreflightError("Advertised context cannot cover the locked multimodal history")
        if _number(endpoint.get("max_completion_tokens"), "maximum completion tokens") < profile["max_output_tokens"]:
            raise PreflightError("Pinned endpoint cannot support the locked output budget")
        prompt_limit = endpoint.get("max_prompt_tokens")
        if prompt_limit is not None and _number(prompt_limit, "maximum prompt tokens") < profile["max_input_reserve_tokens"]:
            raise PreflightError("Pinned endpoint prompt limit is too small")
        # The model catalog can advertise a cheaper text-only route. Account for
        # the exact selected endpoint, never that catalog minimum alone.
        ceilings = {"prompt": profile["prompt_ceiling"], "completion": profile["completion_ceiling"],
                    "input_cache_read": profile["cache_price_ceiling"],
                    "input_cache_write": profile["cache_price_ceiling"],
                    "input_cache_write_1h": profile["cache_price_ceiling"]}
        for prices in (row.get("pricing", {}), endpoint.get("pricing", {})):
            for key in ("prompt", "completion"):
                if key not in prices:
                    raise PreflightError("Required pricing metadata missing for " + model)
            for key, ceiling in ceilings.items():
                if key in prices and _number(prices[key], key) > ceiling:
                    raise PreflightError("Pricing exceeds the locked budget assumptions: " + model + "/" + key)
            for key in ("request", "image"):
                if key in prices and _number(prices[key], key) != 0:
                    raise PreflightError("Unexpected per-request/image pricing for chat model")
            for override in prices.get("overrides", []):
                if _number(override.get("min_prompt_tokens"), "pricing threshold") < profile["max_input_reserve_tokens"]:
                    raise PreflightError("Unexpected price tier within the locked input bound")
        if not isinstance(row.get("canonical_slug"), str):
            raise PreflightError("Missing canonical model snapshot metadata")
        return {"id": model, "canonical_slug": row["canonical_slug"], "pricing": deepcopy(endpoint["pricing"]),
                "input_modalities": architecture["input_modalities"], "supported_parameters": sorted(parameters),
                "structured_schema": "structured_outputs" in parameters, "endpoint": deepcopy(endpoint),
                "reasoning_effort": profile["reasoning_effort"], "expose_reasoning": profile["expose_reasoning"],
                "vision_evidence": "Model metadata advertises image input; endpoint-native vision behavior is not live-tested by free preflight"}

    @staticmethod
    def _image_metadata(catalog_row, endpoints):
        if catalog_row.get("architecture", {}).get("output_modalities") != ["image"]:
            raise PreflightError("Image model does not advertise actual image output")
        if not {"text", "image"}.issubset(set(catalog_row.get("architecture", {}).get("input_modalities", []))):
            raise PreflightError("Image model does not advertise reference-image input")
        candidates = [e for e in endpoints.get("endpoints", [])
                      if e.get("provider_tag") == IMAGE_PROVIDER_TAG and e.get("provider_slug") == IMAGE_PROVIDER_TAG
                      and e.get("provider_name") == IMAGE_PROVIDER_NAME]
        if len(candidates) != 1:
            raise PreflightError("A unique pinned Alibaba Cloud Int. image endpoint is required")
        endpoint = candidates[0]
        params = endpoint.get("supported_parameters", {})
        if "1K" not in params.get("resolution", {}).get("values", []) or "1:1" not in params.get("aspect_ratio", {}).get("values", []):
            raise PreflightError("Locked 1K/square image capability unavailable")
        for name, required in (("n", 1), ("input_references", 1)):
            descriptor = params.get(name, {})
            if descriptor.get("type") != "range" or not descriptor.get("min", 2) <= required <= descriptor.get("max", 0):
                raise PreflightError("Required image parameter unavailable: " + name)
        if params["input_references"]["min"] != 0:
            raise PreflightError("Text-only image generation must be supported")
        # output_format is a documented common router parameter. Where a model
        # additionally advertises an explicit subset, require PNG in that subset.
        if "output_format" in params and "png" not in params["output_format"].get("values", []):
            raise PreflightError("PNG image output is unavailable")
        applicable, seen = {}, set()
        for rate in endpoint.get("pricing", []):
            key = rate.get("billable")
            variant = rate.get("variant")
            if variant is not None and not isinstance(variant, str):
                raise PreflightError("Unrecognized image billing variant")
            variant = variant.lower() if variant is not None else None
            if (key, variant) in seen or key not in _IMAGE_RATE_CEILINGS or rate.get("unit") != "image":
                raise PreflightError("Unrecognized image billing structure; no paid request sent")
            seen.add((key, variant))
            amount = _number(rate.get("cost_usd"), "image pricing")
            if key == "input_image" and variant is not None:
                raise PreflightError("Unexpected reference-image pricing variant")
            if key == "output_image" and variant not in {None, "1k", "2k"}:
                raise PreflightError("Unknown output-image pricing variant")
            if variant == "2k":
                continue  # This experiment requests only 1K.
            if key in applicable or amount > _IMAGE_RATE_CEILINGS[key]:
                raise PreflightError("Image pricing exceeds its ceiling or has ambiguous applicable tiers")
            applicable[key] = amount
        if set(applicable) != set(_IMAGE_RATE_CEILINGS):
            raise PreflightError("Image/reference pricing is incomplete; no paid request sent")
        checked = deepcopy(endpoint)
        checked["checked_1k_rates_usd"] = {key: float(value) for key, value in applicable.items()}
        return checked

    def preflight(self, models=None, *, max_inference_usd=40, required_remaining_usd=None, minimum_remaining=None):
        """Only GETs. Account credit balance is deliberately not claimed verified."""
        self._preflight = None
        self.ledger.assert_ready()
        models = dict(DEFAULT_MODELS if models is None else models)
        if models != DEFAULT_MODELS:
            raise PreflightError("This locked experiment supports only its canonical model configuration")
        if _number(max_inference_usd, "inference cap") > 40:
            raise PreflightError("Inference cap cannot exceed $40")
        required = minimum_remaining if minimum_remaining is not None else required_remaining_usd
        if required is None:
            required = self.ledger.summary()["remaining_usd"]
        key_status = self._key_check(required)
        catalog = self._request("GET", "/models")
        rows = {r.get("id"): r for r in catalog.get("data", []) if isinstance(r, dict)}
        validated = {}
        for model in sorted(_CHAT_MODELS):
            if model not in rows:
                raise PreflightError("Required canonical chat model is unavailable: " + model)
            chat_endpoints = self._request("GET", "/models/" + model + "/endpoints")
            validated[model] = self._chat_metadata(rows[model], model, chat_endpoints)
        images = self._request("GET", "/images/models")
        image_rows = [r for r in images.get("data", []) if isinstance(r, dict) and r.get("id") == models["image"]]
        if len(image_rows) != 1:
            raise PreflightError("Required canonical image model is unavailable")
        endpoint_path = "/images/models/" + models["image"] + "/endpoints"
        endpoints = self._request("GET", endpoint_path)
        if endpoints.get("id") != models["image"]:
            raise PreflightError("Image endpoint model identity mismatch")
        image_endpoint = self._image_metadata(image_rows[0], endpoints)
        # Cloud network secrets may be opaque per-session placeholders. Never hash
        # or persist that placeholder as if it identified the actual credential.
        self.ledger.bind("model_configuration", canonical({"models": models,
                         "snapshots": {m: {"canonical_slug": v["canonical_slug"], "provider_tag": v["endpoint"]["tag"],
                                           "quantization": v["endpoint"].get("quantization"), "structured_schema": v["structured_schema"],
                                           "reasoning_effort": v["reasoning_effort"], "expose_reasoning": v["expose_reasoning"]}
                                       for m, v in validated.items()}, "image_provider": IMAGE_PROVIDER_TAG}))
        report = {"status": "ready", "checked_at_unix": time.time(), "paid_requests_made": 0,
                  "models": models, "chat_metadata": validated, "image_endpoint": image_endpoint,
                  "key": key_status, "local_budget": self.ledger.summary(),
                  "input_limits": {model: {"text_and_schema_utf8_bytes": profile["max_text_bytes"], "image_blocks": profile["max_images"],
                                            "message_objects": profile["max_messages"], "input_reserve_tokens": profile["max_input_reserve_tokens"],
                                            "output_tokens": profile["max_output_tokens"], "reasoning_effort": profile["reasoning_effort"]}
                                   for model, profile in CHAT_PROFILES.items()},
                  "image_reservation_warning": "One 1K output and at most one reference are reserved at $0.028; the lifetime provider key cap remains required",
                  "cash_limit_note": "Checkout, tax, no auto-recharge, and absence of connected BYOK must be checked by the account owner; this adapter cannot verify them"}
        self._models, self._image_endpoint = validated, image_endpoint
        self._preflight_time, self._preflight = time.monotonic(), report
        return deepcopy(report)

    @staticmethod
    def _input_estimate(messages, schema, model=DEFAULT_MODELS["producer"]):
        if model not in CHAT_PROFILES:
            raise ProviderError("Unconfigured chat model")
        profile = CHAT_PROFILES[model]
        if not isinstance(messages, list) or not messages or len(messages) > profile["max_messages"]:
            raise ProviderError("Invalid bounded chat history")
        text_bytes, image_count = 0, 0
        for message in messages:
            if not isinstance(message, dict) or set(message) - {"role", "content"} or message.get("role") not in {"system", "developer", "user", "assistant"}:
                raise ProviderError("Only explicit system/developer/user/assistant content messages are supported")
            content = message.get("content")
            if isinstance(content, str):
                text_bytes += len(content.encode("utf-8"))
                continue
            if not isinstance(content, list):
                raise ProviderError("Chat content must be text or actual image/text blocks")
            for part in content:
                if not isinstance(part, dict):
                    raise ProviderError("Malformed content block")
                if part.get("type") == "text" and isinstance(part.get("text"), str):
                    if set(part) - {"type", "text"}:
                        raise ProviderError("Unplanned text/cache-control content parameters")
                    text_bytes += len(part["text"].encode("utf-8"))
                elif part.get("type") == "image_url":
                    if message["role"] not in {"user", "assistant"}:
                        raise ProviderError("Image blocks cannot appear in system/developer instructions")
                    info = part.get("image_url", {})
                    if not isinstance(info, dict) or set(part) - {"type", "image_url"} or set(info) - {"url", "detail"}:
                        raise ProviderError("Unplanned image input parameters")
                    if info.get("detail", "high") not in {"high", "auto", "low"}:
                        raise ProviderError("Invalid image input detail")
                    url = info.get("url", "")
                    if not isinstance(url, str) or not url.startswith("data:image/png;base64,"):
                        raise ProviderError("Image input must contain actual inline PNG bytes, never an external URL")
                    image_count += 1
                    if image_count > profile["max_images"]:
                        raise ProviderError("Image blocks exceed this model's locked request bound")
                    encoded = url.partition(",")[2]
                    if len(encoded) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
                        raise ProviderError("Inline image exceeds its encoded byte budget")
                    try:
                        data = base64.b64decode(encoded, validate=True)
                    except (ValueError, binascii.Error):
                        raise ProviderError("Invalid inline image encoding") from None
                    _validate_png(data)
                else:
                    raise ProviderError("Unsupported multimodal content block")
        schema_bytes = len(canonical(schema).encode("utf-8")) if schema is not None else 0
        if text_bytes + schema_bytes > profile["max_text_bytes"]:
            raise ProviderError("Chat text/schema exceeds the locked input budget; history is not silently truncated")
        # Charge the full repeated history every time. UTF-8 bytes, not characters/4.
        # The fixed image allowance and framing margin are deliberately conservative.
        tokens = text_bytes + schema_bytes + 128 * len(messages) + 1024 + image_count * VISION_RESERVE_TOKENS
        if tokens >= profile["max_input_reserve_tokens"]:
            raise ProviderError("Input estimate crosses the locked short-context boundary")
        return tokens, image_count

    def _paid(self, request_id, kind, endpoint, payload, reserve_usd):
        model = payload["model"]
        digest = request_digest({"endpoint": endpoint, "payload": payload})
        old = self.ledger.lookup(request_id)
        if old is not None:
            if old["request_hash"] != digest or old["model"] != model or old["kind"] != kind:
                raise BudgetError("Stable request ID does not match the original request")
            if old["status"] != "completed":
                raise PendingPaidRequest("Paid request outcome is unresolved; no automatic POST retry")
            return old["response"], old["receipt"], True
        self.ledger.assert_ready()
        if self._preflight is None:
            raise PreflightError("A successful free preflight is required before paid work")
        if time.monotonic() - self._preflight_time > 900:
            self.preflight(minimum_remaining=reserve_usd)
        key_status = self._key_check(reserve_usd)
        # Atomic intent is durable before the POST, including on process termination.
        self.ledger.reserve(request_id, kind, model, digest, reserve_usd)
        try:
            response = self._request("POST", endpoint, payload)
        except BaseException:
            self.ledger.uncertain(request_id, "POST interrupted or failed; billing outcome unresolved")
            raise ProviderError("Paid POST failed or was interrupted; reservation retained and no retry permitted") from None
        if not isinstance(response, dict):
            self.ledger.uncertain(request_id, "Non-object provider response")
            raise ProviderError("Invalid provider response; reservation retained")
        usage = response.get("usage")
        try:
            if not isinstance(usage, dict) or "cost" not in usage:
                raise ValueError("cost missing")
            actual = _number(usage["cost"], "reported request cost")
        except (ValueError, ProviderError):
            self.ledger.uncertain(request_id, "Response lacked a conclusive finite USD cost receipt", response=response,
                                  receipt={"request_id": request_id, "request_hash": digest, "requested_model": model, "response_id": response.get("id")})
            raise ProviderError("Provider response has no usable cost receipt; reservation retained; do not retry") from None
        receipt = {"request_id": request_id, "request_hash": digest, "kind": kind, "requested_model": model,
                   "response_id": response.get("id"), "returned_model": response.get("model"), "provider": response.get("provider"),
                   "usage": usage, "cost_usd": float(actual), "reserved_usd": float(reserve_usd),
                   "key_remaining_before_usd": key_status["remaining_usd"], "received_at_unix": time.time()}
        self.ledger.complete(request_id, actual, response, receipt)
        # A billed but invalid response is retained. Validation must never cause a retry.
        if actual > Decimal(str(reserve_usd)):
            raise ProviderError("Provider cost exceeded reservation; charge recorded and paid work stopped", receipt=receipt)
        if usage.get("is_byok") is True:
            self.ledger.halt("Unexpected BYOK billing; paid work stopped for account review")
            raise ProviderError("Unexpected BYOK billing; stop and inspect the account before any further work", receipt=receipt)
        return response, receipt, False

    def chat(self, request_id, model, messages, max_output_tokens, schema=None, reasoning_effort=None):
        schema_supported = self._models.get(model, {}).get("structured_schema", True)
        payload = build_chat_payload(model, messages, max_output_tokens, schema, reasoning_effort,
                                     structured_schema=schema_supported)
        input_tokens, image_count = self._input_estimate(messages, schema, model)
        profile = CHAT_PROFILES[model]
        # The whole repeated history is charged each time. Model-specific input
        # margins cover cache writes, with twice the completion cap reserved for
        # reasoning/output safety. No cache-hit discount is assumed.
        reserve = (Decimal(input_tokens) * profile["input_reserve_rate"]
                   + Decimal(max_output_tokens * 2) * profile["completion_ceiling"] + profile["reservation_margin"])
        response, receipt, cached = self._paid(request_id, "chat", "/chat/completions", payload, reserve)
        returned_model = response.get("model")
        allowed = {model, self._models.get(model, {}).get("canonical_slug")}
        allowed = {value for value in allowed if isinstance(value, str)}
        allowed |= {value.split("/", 1)[1] for value in allowed if "/" in value}
        if returned_model not in allowed:
            self.ledger.halt("Returned chat model identity differs from the checked model; paid work stopped")
            raise ProviderError("Returned chat model identity differs from the checked model", receipt=receipt)
        returned_provider = response.get("provider")
        if returned_provider is not None and returned_provider not in {profile["provider_name"], profile["provider_tag"]}:
            self.ledger.halt("Returned chat provider differs from the pinned endpoint; paid work stopped")
            raise ProviderError("Returned chat provider differs from the pinned endpoint", receipt=receipt)
        try:
            choices = response["choices"]
            if len(choices) != 1 or choices[0].get("finish_reason") != "stop":
                raise ValueError("incomplete or refused completion")
            message = choices[0]["message"]
            text = message["content"]
            if message.get("refusal") or not isinstance(text, str) or not text.strip():
                raise ValueError("no complete text")
            content = json.loads(text, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(content, dict):
                raise ValueError("object expected")
        except (KeyError, TypeError, ValueError):
            raise ResponseValidationError("Paid response was incomplete, refused, or invalid JSON; recorded without retry", receipt=receipt) from None
        return {"text": text, "content": content, "model": returned_model, "usage": response["usage"],
                "cost_usd": receipt["cost_usd"], "request_id": request_id, "response_id": response.get("id"),
                "receipt": receipt, "response": response, "raw": response, "cached": cached,
                "input_estimate": {"tokens": input_tokens, "image_count": image_count},
                "response_format_mode": payload["response_format"]["type"],
                "reasoning": deepcopy(message.get("reasoning")),
                "reasoning_details": deepcopy(message.get("reasoning_details"))}

    def image(self, request_id, prompt, model=DEFAULT_MODELS["image"], quality="standard", size="1024x1024", input_reference=None):
        # Qwen does not advertise a quality parameter. 'standard' is the local
        # protocol label; only its supported resolution/aspect/n are sent.
        # output_format is a common router field. Returned dimensions are checked.
        payload = build_image_payload(prompt, model, quality, size, input_reference)
        reserve = IMAGE_OUTPUT_RESERVE_USD
        response, receipt, cached = self._paid(request_id, "image", "/images", payload, reserve)
        if response.get("model") is not None and response["model"] not in {model, model.split("/", 1)[1]}:
            self.ledger.halt("Returned image model differs from the pinned model; paid work stopped")
            raise ProviderError("Returned image model differs from the pinned model", receipt=receipt)
        if response.get("provider") is not None and response["provider"] not in {IMAGE_PROVIDER_NAME, IMAGE_PROVIDER_TAG}:
            self.ledger.halt("Returned image provider differs from the pinned endpoint; paid work stopped")
            raise ProviderError("Returned image provider differs from the pinned endpoint", receipt=receipt)
        try:
            if len(response["data"]) != 1:
                raise ValueError("one image required")
            item = response["data"][0]
            if item.get("media_type") not in (None, "image/png"):
                raise ValueError("PNG required")
            data = base64.b64decode(item["b64_json"], validate=True)
            _validate_png(data, require_square=True)
        except (KeyError, TypeError, ValueError, binascii.Error, ProviderError):
            raise ResponseValidationError("Paid image output lacked one valid 1024x1024 PNG; charge retained, no URL fallback or retry", receipt=receipt) from None
        return {"png_bytes": data, "image_bytes": data, "data": data, "media_type": "image/png", "model": model,
                "usage": response["usage"], "cost_usd": receipt["cost_usd"], "request_id": request_id,
                "response_id": response.get("id"), "receipt": receipt, "response": response, "raw": response, "cached": cached}
