"""Offline contracts and crash/budget adversaries. These tests never use a key."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from PIL import Image

from visual_collusion.budget import BudgetError, BudgetLedger, PendingPaidRequest, canonical, request_digest
from visual_collusion.providers import (
    CHAT_PROFILES, DEFAULT_MODELS, MAX_CHAT_IMAGES, MAX_CHAT_MESSAGES, MAX_CHAT_TEXT_BYTES,
    IMAGE_OUTPUT_RESERVE_USD, VISION_RESERVE_TOKENS, OpenRouterProvider, PreflightError, ProviderError,
    ResponseValidationError, image_content,
)


def png(size=(1024, 1024), color=(245, 245, 245)):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, "PNG")
    return output.getvalue()


def model_catalog():
    return {"data": [{"id": model, "canonical_slug": model + "-20260929",
                      "context_length": 1_000_000,
                      "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
                      "pricing": {"prompt": str(CHAT_PROFILES[model]["prompt_ceiling"]),
                                  "completion": str(CHAT_PROFILES[model]["completion_ceiling"])},
                      "supported_parameters": ["response_format", "structured_outputs", "reasoning", "max_tokens"],
                      "reasoning": {"supported_efforts": ["high", "medium", "low"]}}
                     for model in sorted({DEFAULT_MODELS["producer"], DEFAULT_MODELS["monitor"]})]}


def chat_endpoints(model):
    profile = CHAT_PROFILES[model]
    return {"data": {"id": model, "endpoints": [{
        "model_id": model, "provider_name": profile["provider_name"], "tag": profile["provider_tag"],
        "status": 0, "quantization": profile["quantization"] or "unknown",
        "context_length": 1_000_000, "max_completion_tokens": 131_072, "max_prompt_tokens": None,
        "supported_parameters": ["response_format", "structured_outputs", "reasoning", "max_tokens"],
        "pricing": {"prompt": str(profile["prompt_ceiling"]), "completion": str(profile["completion_ceiling"])},
    }]}}


def image_endpoints():
    return {"id": DEFAULT_MODELS["image"], "endpoints": [{
        "provider_tag": "alibaba", "provider_slug": "alibaba", "provider_name": "Alibaba Cloud Int.",
        "supported_parameters": {"resolution": {"type": "enum", "values": ["1K", "2K"]},
                                 "aspect_ratio": {"type": "enum", "values": ["1:1", "3:2"]},
                                 "n": {"type": "range", "min": 1, "max": 6},
                                 "input_references": {"type": "range", "min": 0, "max": 4}},
        "pricing": [{"billable": "input_image", "unit": "image", "cost_usd": 0},
                    {"billable": "output_image", "unit": "image", "cost_usd": 0.0184, "variant": "1k"},
                    {"billable": "output_image", "unit": "image", "cost_usd": 0.0184, "variant": "2k"}]}]}


class FakeTransport:
    def __init__(self, ledger):
        self.ledger = ledger
        self.calls = []
        self.models = model_catalog()
        self.chat_endpoints = {model: chat_endpoints(model) for model in CHAT_PROFILES}
        self.endpoints = image_endpoints()
        self.key = {"limit": 40, "limit_remaining": 40, "usage": 0, "limit_reset": None,
                    "byok_usage": 0, "is_management_key": False, "is_free_tier": False}
        self.failure = None
        self.cost = 0.001
        self.finish_reason = "stop"
        self.cost_missing = False
        self.response_model = None
        self.response_provider = None
        self.reasoning = None
        self.reasoning_details = None
        self.image_bytes = png()
        self.last_response = None

    def __call__(self, method, path, payload):
        self.calls.append((method, path, deepcopy(payload)))
        if method == "GET":
            if path == "/key":
                return {"data": deepcopy(self.key)}
            if path == "/models":
                return deepcopy(self.models)
            for model, endpoints in self.chat_endpoints.items():
                if path == "/models/" + model + "/endpoints":
                    return deepcopy(endpoints)
            if path == "/images/models":
                return {"data": [{"id": DEFAULT_MODELS["image"], "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["image"]}}]}
            if path == "/images/models/" + DEFAULT_MODELS["image"] + "/endpoints":
                return deepcopy(self.endpoints)
            raise AssertionError("Unexpected free endpoint: " + path)
        # Actual adapter must have committed its intent BEFORE invoking transport.
        if not self.ledger.summary()["unresolved_request_ids"]:
            raise AssertionError("POST happened without a durable reservation")
        if self.failure:
            raise self.failure
        usage = {"prompt_tokens": 100, "completion_tokens": 50,
                 "completion_tokens_details": {"reasoning_tokens": 20}, "cost": self.cost}
        if self.cost_missing:
            del usage["cost"]
        if path == "/chat/completions":
            result = {"id": "response-chat-1", "model": self.response_model or payload["model"],
                      "provider": self.response_provider or CHAT_PROFILES[payload["model"]]["provider_name"],
                      "usage": usage, "choices": [{"finish_reason": self.finish_reason,
                                                     "message": {"role": "assistant", "content": '{"decision":"review"}'}}]}
        elif path == "/images":
            result = {"id": "response-image-1", "usage": usage,
                      "data": [{"b64_json": base64.b64encode(self.image_bytes).decode(), "media_type": "image/png"}]}
        else:
            raise AssertionError("Unexpected paid endpoint")
        if path == "/chat/completions":
            if self.reasoning is not None:
                result["choices"][0]["message"]["reasoning"] = deepcopy(self.reasoning)
            if self.reasoning_details is not None:
                result["choices"][0]["message"]["reasoning_details"] = deepcopy(self.reasoning_details)
        self.last_response = deepcopy(result)
        return result

    @property
    def posts(self):
        return [call for call in self.calls if call[0] == "POST"]


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "budget.sqlite3"
        self.ledger = BudgetLedger(self.path)

    def reserve(self, request_id="first", amount=1):
        return self.ledger.reserve(request_id, "chat", DEFAULT_MODELS["producer"], request_digest({"id": request_id}), amount)

    def test_pending_survives_process_restart_and_blocks_all_new_work(self):
        self.reserve()
        resumed = BudgetLedger(self.path)
        with self.assertRaises(PendingPaidRequest):
            resumed.reserve("another", "chat", DEFAULT_MODELS["producer"], request_digest({}), 0.1)
        self.assertEqual(resumed.summary()["reserved_unresolved_usd"], 1)

    def test_concurrent_reservation_is_serialized_before_post(self):
        def attempt(name):
            try:
                self.reserve(name)
                return "reserved"
            except PendingPaidRequest:
                return "blocked"
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, ("one", "two")))
        self.assertEqual(sorted(outcomes), ["blocked", "reserved"])
        self.assertEqual(self.ledger.summary()["request_count"], 1)

    def test_budget_cap_is_immutable_and_cash_limit_not_inference_limit(self):
        with self.assertRaises(BudgetError):
            BudgetLedger(self.path, cap_usd=50)
        with self.assertRaises(BudgetError):
            BudgetLedger(self.path, cap_usd=30)
        with self.assertRaises(BudgetError):
            self.reserve(amount=40.000000001)
        self.assertEqual(self.ledger.summary()["request_count"], 0)

    def test_actual_cost_releases_only_unused_reservation(self):
        self.reserve(amount=3)
        self.ledger.complete("first", 1.25, {"usage": {"cost": 1.25}}, {"cost_usd": 1.25})
        report = self.ledger.summary()
        self.assertEqual(report["actual_usd"], 1.25)
        self.assertEqual(report["remaining_usd"], 38.75)
        self.assertEqual(report["reserved_unresolved_usd"], 0)

    def test_cost_overrun_is_recorded_and_stops_paid_work(self):
        self.reserve(amount=1)
        self.ledger.complete("first", 1.1, {"usage": {"cost": 1.1}}, {})
        self.assertEqual(self.ledger.summary()["actual_usd"], 1.1)
        with self.assertRaises(BudgetError):
            self.ledger.assert_ready()

    def test_saved_response_tamper_detected(self):
        self.reserve()
        self.ledger.complete("first", 0.1, {"meaning": "original"}, {})
        with sqlite3.connect(self.path) as connection:
            connection.execute("UPDATE requests SET response_json='{}'")
        with self.assertRaises(BudgetError):
            self.ledger.lookup("first")

    def test_bad_amounts_and_changed_request_ids_are_rejected(self):
        for amount in (float("nan"), float("inf"), -1, True):
            with self.assertRaises(BudgetError):
                self.reserve(amount=amount)
        self.reserve()
        with self.assertRaises(BudgetError):
            self.ledger.reserve("first", "chat", DEFAULT_MODELS["producer"], request_digest({"changed": True}), 1)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = BudgetLedger(Path(self.temp.name) / "budget.sqlite3")
        self.transport = FakeTransport(self.ledger)
        self.provider = OpenRouterProvider("CLOUD_SECRET_PLACEHOLDER", self.ledger, transport=self.transport)

    def chat(self, request_id="chat-1", messages=None):
        return self.provider.chat(request_id, DEFAULT_MODELS["producer"],
                                  messages or [{"role": "user", "content": "Return a decision."}], 1600,
                                  schema={"type": "object", "properties": {"decision": {"type": "string"}}, "required": ["decision"], "additionalProperties": False})

    def test_preflight_uses_free_gets_and_no_key_text_in_report(self):
        report = self.provider.preflight(DEFAULT_MODELS, minimum_remaining=40)
        self.assertEqual(report["status"], "ready")
        self.assertEqual(self.transport.posts, [])
        self.assertNotIn("CLOUD_SECRET_PLACEHOLDER", json.dumps(report))
        self.assertIn("unverified", report["key"]["wallet_funding"])
        self.assertEqual(self.ledger.summary()["request_count"], 0)

    def test_no_paid_request_without_successful_preflight(self):
        with self.assertRaises(PreflightError):
            self.chat()
        self.assertEqual(self.transport.posts, [])

    def test_2048_combined_reasoning_and_output_budget_accepted_above_rejected(self):
        self.provider.preflight()
        result = self.provider.chat("budget-2048", DEFAULT_MODELS["monitor"],
                                    [{"role": "user", "content": "Return JSON."}], 2048)
        self.assertEqual(result["content"]["decision"], "review")
        self.assertEqual(self.transport.posts[0][2]["max_tokens"], 2048)
        with self.assertRaises(ProviderError):
            self.provider.chat("budget-2049", DEFAULT_MODELS["monitor"],
                               [{"role": "user", "content": "Return JSON."}], 2049)
        self.assertEqual(len(self.transport.posts), 1)

    def test_agent_4096_high_reasoning_preserves_exposed_fields_and_exact_resume(self):
        self.provider.preflight()
        self.transport.reasoning = "Provider-exposed reasoning test fixture."
        self.transport.reasoning_details = [{"type": "reasoning.text", "text": "A synthetic trace.", "index": 0}]
        messages = [{"role": "user", "content": "Return the required JSON action."}]
        first = self.provider.chat("agent-high", DEFAULT_MODELS["producer"], messages, 4096, reasoning_effort="high")
        resumed = self.provider.chat("agent-high", DEFAULT_MODELS["producer"], messages, 4096, reasoning_effort="high")
        self.assertEqual(self.transport.posts[0][2]["reasoning"], {"effort": "high", "exclude": False})
        self.assertEqual(self.transport.posts[0][2]["max_tokens"], 4096)
        self.assertEqual(first["reasoning"], self.transport.reasoning)
        self.assertEqual(first["reasoning_details"], self.transport.reasoning_details)
        self.assertEqual(resumed["reasoning_details"], first["reasoning_details"])
        self.assertEqual(self.ledger.lookup("agent-high")["response"], first["raw"])
        self.assertTrue(resumed["cached"])
        self.assertEqual(len(self.transport.posts), 1)
        for limit, effort in ((4097, "high"), (4096, "low")):
            with self.assertRaises(ProviderError):
                self.provider.chat("bad-agent-limit", DEFAULT_MODELS["producer"], messages, limit, reasoning_effort=effort)
        with self.assertRaises(ProviderError):
            self.provider.chat("bad-auditor-effort", DEFAULT_MODELS["monitor"], messages, 2048, reasoning_effort="high")
        self.assertEqual(len(self.transport.posts), 1)

    def test_key_unlimited_resetting_insufficient_and_overcap_rejected(self):
        for mutation in ({"limit": None}, {"limit": 50}, {"limit_reset": "daily"},
                         {"limit_remaining": 10}, {"byok_usage": 0.1}, {"is_management_key": True}):
            with self.subTest(mutation=mutation):
                old = deepcopy(self.transport.key)
                self.transport.key.update(mutation)
                with self.assertRaises(PreflightError):
                    self.provider.preflight(minimum_remaining=40)
                self.transport.key = old
        self.assertEqual(self.transport.posts, [])

    def test_price_or_capability_change_blocks_before_post(self):
        self.transport.models["data"][0]["pricing"]["completion"] = "0.000011"
        with self.assertRaises(PreflightError):
            self.provider.preflight()
        self.transport.models = model_catalog()
        self.transport.models["data"][0]["architecture"]["input_modalities"] = ["text"]
        with self.assertRaises(PreflightError):
            self.provider.preflight()
        self.assertEqual(self.transport.posts, [])

    def test_wrong_image_pricing_or_missing_edit_capability_fails_closed(self):
        self.transport.endpoints["endpoints"][0]["pricing"][1]["cost_usd"] = 0.026
        with self.assertRaises(PreflightError):
            self.provider.preflight()
        self.transport.endpoints = image_endpoints()
        del self.transport.endpoints["endpoints"][0]["supported_parameters"]["input_references"]
        with self.assertRaises(PreflightError):
            self.provider.preflight()
        self.assertEqual(self.transport.posts, [])

    def test_real_inline_png_transport_and_exact_cached_resume(self):
        self.provider.preflight()
        image = png()
        messages = [{"role": "user", "content": [{"type": "text", "text": "Inspect this."}, image_content(image)]}]
        first = self.chat(messages=messages)
        second = self.chat(messages=messages)
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(first["content"], {"decision": "review"})
        self.assertEqual(len(self.transport.posts), 1)
        payload = self.transport.posts[0][2]
        url = payload["messages"][0]["content"][1]["image_url"]["url"]
        self.assertEqual(base64.b64decode(url.partition(",")[2]), image)
        self.assertFalse(payload["provider"]["allow_fallbacks"])
        self.assertEqual(payload["max_tokens"], 1600)
        self.assertEqual(first["input_estimate"]["image_count"], 1)

    def test_pending_failure_preserves_reservation_no_retry_no_secret_echo(self):
        self.provider.preflight()
        self.transport.failure = TimeoutError("DO_NOT_ECHO_SECRET")
        with self.assertRaises(ProviderError) as caught:
            self.chat()
        self.assertNotIn("DO_NOT_ECHO_SECRET", str(caught.exception))
        with self.assertRaises(PendingPaidRequest):
            self.chat()
        with self.assertRaises(PendingPaidRequest):
            self.chat("different")
        self.assertEqual(len(self.transport.posts), 1)
        self.assertGreater(self.ledger.summary()["reserved_unresolved_usd"], 0)

    def test_missing_cost_keeps_response_and_reservation_without_retry(self):
        self.provider.preflight()
        self.transport.cost_missing = True
        with self.assertRaises(ProviderError):
            self.chat()
        record = self.ledger.lookup("chat-1")
        self.assertEqual(record["status"], "uncertain")
        self.assertEqual(record["response"], self.transport.last_response)
        with self.assertRaises(PendingPaidRequest):
            self.chat()
        self.assertEqual(len(self.transport.posts), 1)

    def test_length_finish_is_billed_without_phantom_decision_or_retry(self):
        self.provider.preflight()
        self.transport.finish_reason = "length"
        with self.assertRaises(ResponseValidationError):
            self.chat()
        self.assertEqual(self.ledger.summary()["actual_usd"], 0.001)
        with self.assertRaises(ResponseValidationError):
            self.chat()
        self.assertEqual(len(self.transport.posts), 1)

    def test_key_cap_is_rechecked_before_every_new_paid_request(self):
        self.provider.preflight()
        self.transport.key["limit"] = 100
        with self.assertRaises(PreflightError):
            self.chat()
        self.assertEqual(self.transport.posts, [])

    def test_unexpected_returned_model_records_charge_and_no_retry(self):
        self.provider.preflight()
        self.transport.response_model = "other/model"
        with self.assertRaises(ProviderError):
            self.chat()
        self.assertEqual(self.ledger.summary()["actual_usd"], 0.001)
        with self.assertRaises(ProviderError):
            self.chat()
        self.assertEqual(len(self.transport.posts), 1)

    def test_image_generation_and_edit_use_one_endpoint_actual_bytes(self):
        self.provider.preflight()
        first = self.provider.image("image-1", "A benign diagram.")
        edited = self.provider.image("image-2", "Improve its labels.", input_reference=first["png_bytes"])
        cached = self.provider.image("image-2", "Improve its labels.", input_reference=first["png_bytes"])
        self.assertEqual(first["image_bytes"], self.transport.image_bytes)
        self.assertEqual(edited["media_type"], "image/png")
        self.assertTrue(cached["cached"])
        self.assertEqual(len(self.transport.posts), 2)
        self.assertEqual({p[1] for p in self.transport.posts}, {"/images"})
        self.assertNotIn("input_references", self.transport.posts[0][2])
        refs = self.transport.posts[1][2]["input_references"]
        self.assertEqual(len(refs), 1)
        self.assertTrue(refs[0]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertNotIn("detail", refs[0]["image_url"])
        self.assertEqual(self.transport.posts[0][2]["n"], 1)
        self.assertEqual(self.transport.posts[0][2]["resolution"], "1K")
        self.assertEqual(self.transport.posts[0][2]["aspect_ratio"], "1:1")
        self.assertNotIn("quality", self.transport.posts[0][2])
        self.assertEqual(self.transport.posts[0][2]["provider"]["only"], ["alibaba"])

    def test_paid_wrong_size_image_records_cost_and_cannot_fallback(self):
        self.provider.preflight()
        self.transport.image_bytes = png((128, 128))
        with self.assertRaises(ResponseValidationError):
            self.provider.image("image-1", "A benign diagram.")
        self.assertEqual(self.ledger.summary()["actual_usd"], 0.001)
        with self.assertRaises(ResponseValidationError):
            self.provider.image("image-1", "A benign diagram.")
        self.assertEqual(len(self.transport.posts), 1)

    def test_external_image_url_is_rejected_before_post(self):
        self.provider.preflight()
        messages = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.org/image.png"}}]}]
        with self.assertRaises(ProviderError):
            self.chat(messages=messages)
        self.assertEqual(self.transport.posts, [])

    def test_cost_reservation_includes_repeated_full_context_and_images(self):
        self.provider.preflight()
        plain = self.chat("one", [{"role": "user", "content": "x" * 1000}])
        visual = self.chat("two", [{"role": "user", "content": [{"type": "text", "text": "x" * 2000}, image_content(png())]}])
        self.assertGreater(visual["receipt"]["reserved_usd"], plain["receipt"]["reserved_usd"])
        self.assertGreater(plain["receipt"]["reserved_usd"], float(1600 * CHAT_PROFILES[DEFAULT_MODELS["producer"]]["completion_ceiling"]))

    def test_sixteen_image_history_preserves_each_image_and_prior_assistant_turn(self):
        self.provider.preflight()
        messages = [{"role": "system", "content": "Continue the fixed task."}]
        images = []
        for turn in range(16):
            image = png(color=(turn * 15, 180, 220))
            images.append(image)
            messages.append({"role": "user", "content": [{"type": "text", "text": "Received artifact."}, image_content(image)]})
            messages.append({"role": "assistant", "content": '{"status":"recorded"}'})
        earlier = self.chat("earlier-history", messages[:9])
        later = self.chat("complete-history", messages)
        restored = self.chat("complete-history", messages)
        self.assertTrue(restored["cached"])
        self.assertEqual(len(self.transport.posts), 2)
        self.assertEqual(self.transport.posts[-1][2]["messages"], messages)
        transmitted = [base64.b64decode(message["content"][1]["image_url"]["url"].partition(",")[2])
                       for message in self.transport.posts[-1][2]["messages"] if message["role"] == "user"]
        self.assertEqual(transmitted, images)
        self.assertEqual(later["input_estimate"]["image_count"], 16)
        self.assertGreater(later["receipt"]["reserved_usd"] - earlier["receipt"]["reserved_usd"],
                           float(4 * VISION_RESERVE_TOKENS * CHAT_PROFILES[DEFAULT_MODELS["producer"]]["input_reserve_rate"]))

    def test_declared_maximum_history_fits_and_extra_text_or_image_is_rejected(self):
        self.provider.preflight()
        schema = {"type": "object"}
        messages = [{"role": "user", "content": [image_content(png())]} for _ in range(MAX_CHAT_IMAGES)]
        messages.extend({"role": "assistant", "content": "x"} for _ in range(MAX_CHAT_MESSAGES - MAX_CHAT_IMAGES))
        text_length = MAX_CHAT_TEXT_BYTES - len(canonical(schema).encode()) - (MAX_CHAT_MESSAGES - MAX_CHAT_IMAGES)
        messages[0]["content"].insert(0, {"type": "text", "text": "x" * text_length})
        result = self.provider.chat("maximum-history", DEFAULT_MODELS["producer"], messages, 4096, schema=schema)
        self.assertEqual(result["input_estimate"], {"tokens": 205_824, "image_count": 16})
        self.assertEqual(result["receipt"]["reserved_usd"], 0.0472608)
        excess_text = deepcopy(messages)
        excess_text[0]["content"][0]["text"] += "é"
        with self.assertRaises(ProviderError):
            self.provider.chat("excess-text", DEFAULT_MODELS["producer"], excess_text, 2048, schema=schema)
        excess_images = deepcopy(messages)
        excess_images[1]["content"].append(image_content(png()))
        with self.assertRaises(ProviderError):
            self.provider.chat("excess-images", DEFAULT_MODELS["producer"], excess_images, 2048, schema=schema)
        self.assertEqual(len(self.transport.posts), 1)
        self.assertEqual(self.ledger.summary()["request_count"], 1)

    def test_auditor_has_smaller_history_limits_and_whole_plan_reserves_below_cap(self):
        self.provider.preflight()
        model = DEFAULT_MODELS["monitor"]
        profile = CHAT_PROFILES[model]
        schema = {"type": "object"}
        messages = [{"role": "user", "content": [image_content(png())]} for _ in range(profile["max_images"])]
        messages.extend({"role": "assistant", "content": "x"} for _ in range(profile["max_messages"] - profile["max_images"]))
        length = profile["max_text_bytes"] - len(canonical(schema).encode()) - (profile["max_messages"] - profile["max_images"])
        messages[0]["content"].insert(0, {"type": "text", "text": "x" * length})
        result = self.provider.chat("max-auditor", model, messages, 2048, schema=schema, reasoning_effort="low")
        self.assertEqual(result["input_estimate"], {"tokens": 88_064, "image_count": 8})
        self.assertEqual(result["receipt"]["reserved_usd"], 0.403216)
        self.assertEqual(self.transport.posts[0][2]["reasoning"], {"effort": "low", "exclude": True})
        self.assertIsNone(result["reasoning"])
        self.assertIsNone(result["reasoning_details"])
        for kind in ("text", "image", "message"):
            excess = deepcopy(messages)
            if kind == "text":
                excess[0]["content"][0]["text"] += "x"
            elif kind == "image":
                excess[0]["content"].append(image_content(png()))
            else:
                excess.append({"role": "user", "content": ""})
            with self.assertRaises(ProviderError):
                self.provider.chat("excess-auditor-" + kind, model, excess, 2048, schema=schema)
        maxima = {}
        for name, limits in CHAT_PROFILES.items():
            tokens = limits["max_text_bytes"] + 128 * limits["max_messages"] + 1024 + VISION_RESERVE_TOKENS * limits["max_images"]
            maxima[name] = (Decimal(tokens) * limits["input_reserve_rate"]
                            + Decimal(2 * limits["max_output_tokens"]) * limits["completion_ceiling"]
                            + limits["reservation_margin"])
        total = 432 * maxima[DEFAULT_MODELS["producer"]] + 32 * maxima[model] + 216 * IMAGE_OUTPUT_RESERVE_USD
        self.assertEqual(total, Decimal("39.3675776"))
        self.assertLess(total, Decimal("40"))
        self.assertEqual(len(self.transport.posts), 1)

    def test_unavailable_high_reasoning_or_expensive_cache_blocks_free_preflight(self):
        model = DEFAULT_MODELS["producer"]
        row = next(row for row in self.transport.models["data"] if row["id"] == model)
        row["reasoning"]["supported_efforts"].remove("high")
        with self.assertRaises(PreflightError):
            self.provider.preflight()
        row["reasoning"]["supported_efforts"].append("high")
        endpoint = self.transport.chat_endpoints[model]["data"]["endpoints"][0]
        for name in ("input_cache_read", "input_cache_write", "input_cache_write_1h"):
            endpoint["pricing"][name] = "0.000000151"
            with self.assertRaises(PreflightError):
                self.provider.preflight()
            del endpoint["pricing"][name]
        self.assertEqual(self.transport.posts, [])
        self.assertEqual(self.ledger.summary()["request_count"], 0)

    def test_preflight_records_pinned_endpoint_prices_instead_of_catalog_minimum(self):
        model = DEFAULT_MODELS["producer"]
        row = next(row for row in self.transport.models["data"] if row["id"] == model)
        row["pricing"] = {"prompt": "0.0000000171", "completion": "0.000000121"}
        endpoint = self.transport.chat_endpoints[model]["data"]["endpoints"][0]
        endpoint["pricing"] = {"prompt": "0.00000007835", "completion": "0.00000025"}
        report = self.provider.preflight()
        self.assertEqual(report["chat_metadata"][model]["pricing"], endpoint["pricing"])
        self.chat()
        routing = self.transport.posts[0][2]["provider"]
        self.assertEqual(routing["only"], ["sail-research/fp4"])
        self.assertFalse(routing["allow_fallbacks"])
        self.assertEqual(routing["max_price"], {"prompt": 0.15, "completion": 0.5, "request": 0})

    def test_endpoint_price_context_quantization_or_pin_change_fails_before_post(self):
        model = DEFAULT_MODELS["producer"]
        for mutation in ({"pricing": {"prompt": "0.00000016", "completion": "0.00000025"}},
                         {"context_length": 65_536}, {"max_completion_tokens": 1024},
                         {"quantization": "fp8"}, {"tag": "sail-research/us"}, {"status": -1}):
            with self.subTest(mutation=mutation):
                baseline = deepcopy(self.transport.chat_endpoints[model])
                self.transport.chat_endpoints[model]["data"]["endpoints"][0].update(mutation)
                with self.assertRaises(PreflightError):
                    self.provider.preflight()
                self.transport.chat_endpoints[model] = baseline
        self.assertEqual(self.transport.posts, [])

    def test_json_schema_only_if_the_pinned_endpoint_advertises_it(self):
        model = DEFAULT_MODELS["producer"]
        self.transport.chat_endpoints[model]["data"]["endpoints"][0]["supported_parameters"].remove("structured_outputs")
        report = self.provider.preflight()
        self.assertFalse(report["chat_metadata"][model]["structured_schema"])
        result = self.chat()
        self.assertEqual(self.transport.posts[0][2]["response_format"], {"type": "json_object"})
        self.assertEqual(result["response_format_mode"], "json_object")
        self.assertEqual(result["content"], {"decision": "review"})

    def test_schema_enforcement_cannot_silently_change_midrun(self):
        self.provider.preflight()
        model = DEFAULT_MODELS["producer"]
        self.transport.chat_endpoints[model]["data"]["endpoints"][0]["supported_parameters"].remove("structured_outputs")
        with self.assertRaises(BudgetError):
            self.provider.preflight()
        self.assertEqual(self.transport.posts, [])

    def test_returned_provider_mismatch_records_charge_and_halts(self):
        self.provider.preflight()
        self.transport.response_provider = "An unrequested provider"
        with self.assertRaises(ProviderError):
            self.chat()
        self.assertEqual(self.ledger.summary()["actual_usd"], 0.001)
        self.assertIsNotNone(self.ledger.summary()["halt_reason"])
        with self.assertRaises(BudgetError):
            self.chat("another-request")
        self.assertEqual(len(self.transport.posts), 1)

    def test_reference_image_price_ceiling_and_unknown_charge_unit_fail_closed(self):
        self.transport.endpoints["endpoints"][0]["pricing"][0]["cost_usd"] = 0.003
        report = self.provider.preflight()
        self.assertEqual(report["image_endpoint"]["checked_1k_rates_usd"], {"input_image": 0.003, "output_image": 0.0184})
        self.transport.endpoints["endpoints"][0]["pricing"][0]["cost_usd"] = 0.0031
        with self.assertRaises(PreflightError):
            self.provider.preflight()
        self.transport.endpoints = image_endpoints()
        self.transport.endpoints["endpoints"][0]["pricing"][1]["unit"] = "token"
        with self.assertRaises(PreflightError):
            self.provider.preflight()
        self.assertEqual(self.transport.posts, [])

    def test_developer_role_preserved_and_vendorless_same_model_accepted(self):
        self.provider.preflight()
        self.transport.response_model = DEFAULT_MODELS["producer"].split("/", 1)[1]
        messages = [{"role": "system", "content": "User service rules."},
                    {"role": "developer", "content": "Produce an illustration."},
                    {"role": "user", "content": "Return a JSON decision."}]
        result = self.chat(messages=messages)
        self.assertEqual(result["content"]["decision"], "review")
        self.assertEqual(self.transport.posts[0][2]["messages"], messages)

    def test_cloud_placeholder_change_does_not_falsely_claim_key_identity(self):
        self.provider.preflight()
        first = self.chat()
        resumed = OpenRouterProvider("DIFFERENT_CLOUD_PLACEHOLDER", self.ledger, transport=self.transport)
        report = resumed.preflight(minimum_remaining=self.ledger.summary()["remaining_usd"])
        self.assertIn("unverified", report["key"]["key_identity"])
        with sqlite3.connect(self.ledger.path) as connection:
            self.assertIsNone(connection.execute("SELECT value FROM settings WHERE key='key_fingerprint'").fetchone())
        second = resumed.chat("chat-1", DEFAULT_MODELS["producer"], [{"role": "user", "content": "Return a decision."}], 1600,
                              schema={"type": "object", "properties": {"decision": {"type": "string"}}, "required": ["decision"], "additionalProperties": False})
        self.assertTrue(second["cached"])
        self.assertEqual(first["text"], second["text"])


if __name__ == "__main__":
    unittest.main()
