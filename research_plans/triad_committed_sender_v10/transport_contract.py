"""OFFLINE CANDIDATE ONLY: not connected to a provider, SDK, or live runner.

These pure request/state contracts do no I/O, spend no money, inspect no secrets,
and implement no retry or fallback. A future runner must fund/freeze requests,
archive raw outputs, bind actual PNGs to draft IDs, and enforce round order.
C.image denotes C's post-verdict next-A handoff, not a current-round image.
The image-generator API is deliberately not given an invented temperature knob.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any

ACTOR_MODEL = "z-ai/glm-5.3-flash"
ACTOR_TEMPERATURE = 2.0
ACTOR_REASONING_EFFORT = "high"
MAX_OUTPUT_TOKENS = 8192
IMAGE_PROMPT_LIMIT = 3000
MAX_GENERATIONS = 2
MAX_ACTIONS = 4
STAGES = frozenset(f"{role}.{mode}" for role in "ABC" for mode in ("image", "vote", "log"))
IMAGE_NAMES = frozenset(("create_visual", "revise_visual", "accept_visual", "decline_visual"))
PROVIDER = {"only": ["open-inference/fp4"], "allow_fallbacks": False, "require_parameters": True}
PAYLOAD_FIELDS = frozenset(("model", "messages", "tools", "tool_choice", "max_tokens", "stream", "temperature", "reasoning", "provider"))


class ContractError(ValueError):
    """Candidate contract violation; never permission to retry a paid call."""


def _stage(stage: str) -> str:
    if not isinstance(stage, str) or stage not in STAGES:
        raise ContractError("Expected a declared A/B/C image, vote, or log stage")
    return stage.split(".")[1]


def _validate_messages(messages: Any) -> None:
    if not isinstance(messages, list) or not messages or not isinstance(messages[0], dict) or messages[0].get("role") != "system":
        raise ContractError("A nonempty message list beginning with a system message is required")
    if any(not isinstance(m, dict) or m.get("role") not in {"system", "user", "assistant", "tool"} for m in messages):
        raise ContractError("Invalid actor message")


def _validate_tools(tools: Any, mode: str) -> None:
    if not isinstance(tools, list) or not tools:
        raise ContractError("An explicit nonempty current function list is required")
    names = []
    for t in tools:
        if not isinstance(t, dict) or t.get("type") != "function" or not isinstance(t.get("function"), dict):
            raise ContractError("Only native function tools are accepted")
        f = t["function"]
        if not isinstance(f.get("name"), str) or f.get("strict") is not True:
            raise ContractError("Functions must be named and strict")
        schema = f.get("parameters", {})
        if not isinstance(schema, dict) or schema.get("type") != "object" or schema.get("additionalProperties") is not False:
            raise ContractError("Exact object argument schemas are required")
        if not isinstance(schema.get("properties"), dict) or set(schema.get("required", [])) != set(schema["properties"]):
            raise ContractError("Every advertised argument must be required")
        names.append(f["name"])
    if len(names) != len(set(names)):
        raise ContractError("Duplicate current function names")
    allowed = IMAGE_NAMES if mode == "image" else {"record_vote"} if mode == "vote" else {"record_log"}
    if not set(names) <= allowed:
        raise ContractError("A tool does not belong to this stage")


def _choice(tools: list[dict]) -> Any:
    return {"type": "function", "function": {"name": tools[0]["function"]["name"]}} if len(tools) == 1 else "required"


def validate_actor_payload(payload: dict, stage: str) -> None:
    """Fail closed on actual outgoing fields, not merely a configuration label."""
    mode = _stage(stage)
    if not isinstance(payload, dict) or set(payload) != PAYLOAD_FIELDS:
        raise ContractError("Outgoing fields differ; extra or omitted sampling/transport fields are forbidden")
    if payload["model"] != ACTOR_MODEL:
        raise ContractError("The candidate retains its declared actor model")
    if type(payload["temperature"]) not in {int, float} or payload["temperature"] != ACTOR_TEMPERATURE:
        raise ContractError("Actual outgoing actor temperature must equal 2.0")
    if type(payload["max_tokens"]) is not int or payload["max_tokens"] != MAX_OUTPUT_TOKENS:
        raise ContractError("The declared output allowance is 8192 tokens")
    reasoning=payload["reasoning"]
    if not isinstance(reasoning, dict) or set(reasoning) != {"effort", "exclude"} or reasoning.get("effort") != ACTOR_REASONING_EFFORT or reasoning.get("exclude") is not False:
        raise ContractError("Actual outgoing actor reasoning must be high")
    provider=payload["provider"]
    if (payload["stream"] is not False or not isinstance(provider, dict) or set(provider) != set(PROVIDER)
            or provider.get("only") != PROVIDER["only"] or provider.get("allow_fallbacks") is not False
            or provider.get("require_parameters") is not True):
        raise ContractError("The fixed nonstreaming provider route cannot fall back")
    _validate_messages(payload["messages"])
    _validate_tools(payload["tools"], mode)
    if payload["tool_choice"] != _choice(payload["tools"]):
        raise ContractError("Exactly one currently supplied native function is required")


def build_actor_payload(model: str, messages: list[dict], tools: list[dict], stage: str) -> dict:
    """Build an offline actor payload; this function never dispatches it."""
    mode=_stage(stage)
    _validate_messages(messages)
    _validate_tools(tools, mode)
    payload = {"model": model, "messages": deepcopy(messages), "tools": deepcopy(tools),
        "tool_choice": _choice(tools),
        "max_tokens": MAX_OUTPUT_TOKENS, "stream": False, "temperature": ACTOR_TEMPERATURE,
        "reasoning": {"effort": ACTOR_REASONING_EFFORT, "exclude": False}, "provider": deepcopy(PROVIDER)}
    validate_actor_payload(payload, stage)
    return payload


def _text(value: str, limit: int, label: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ContractError(f"{label} must contain 1-{limit} Unicode characters")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ContractError(f"{label} must be valid Unicode") from None
    if any(ord(c) < 32 and c not in "\r\n\t" for c in value):
        raise ContractError(f"{label} contains an unsupported control character")


@dataclass(frozen=True)
class Draft:
    """An ID the future runner must bind to an actual successful PNG receipt."""
    draft_id: str
    prompt: str
    revision_of: str | None = None


@dataclass(frozen=True)
class VisualTurn:
    """Pure bounded state transitions, with no inferred image delivery.

    Caller must persist the returned state before advancing. create/revise mean
    an actual completed generation is being registered; failed calls must use
    fail(), whose terminal state has no subsequent action. There is no resample,
    format-retry, automatic acceptance, or network implementation here.
    """
    drafts: tuple[Draft, ...] = ()
    actions: int = 0
    status: str = "open"
    selected_draft_id: str | None = None
    reason: str | None = None

    def _open(self) -> None:
        if self.status != "open" or self.actions >= MAX_ACTIONS:
            raise ContractError("This visual turn is terminal or exhausted")

    def _generate(self, draft_id: str, prompt: str, revision_of: str | None) -> VisualTurn:
        self._open()
        if len(self.drafts) >= MAX_GENERATIONS:
            raise ContractError("The two-generation allowance is exhausted")
        if self.actions >= MAX_ACTIONS - 1:
            raise ContractError("The final action is reserved for accept or decline")
        _text(draft_id, 100, "Draft ID")
        _text(prompt, IMAGE_PROMPT_LIMIT, "Image prompt")
        if draft_id in {d.draft_id for d in self.drafts}:
            raise ContractError("An existing draft ID cannot be overwritten")
        if revision_of is not None and revision_of not in {d.draft_id for d in self.drafts}:
            raise ContractError("Revision requires an existing draft ID")
        return replace(self, drafts=self.drafts + (Draft(draft_id, prompt, revision_of),), actions=self.actions + 1)

    def create(self, draft_id: str, prompt: str) -> VisualTurn:
        """Register a fresh alternative; a second generation may also be fresh."""
        return self._generate(draft_id, prompt, None)

    def revise(self, source_draft_id: str, draft_id: str, prompt: str) -> VisualTurn:
        """Register a revision of an explicitly identified existing draft."""
        return self._generate(draft_id, prompt, source_draft_id)

    def accept(self, draft_id: str) -> VisualTurn:
        self._open()
        if draft_id not in {d.draft_id for d in self.drafts}:
            raise ContractError("Only an explicitly identified existing draft can be accepted")
        return replace(self, actions=self.actions + 1, status="accepted", selected_draft_id=draft_id)

    def decline(self, reason: str) -> VisualTurn:
        self._open()
        _text(reason, 800, "Decline reason")
        return replace(self, actions=self.actions + 1, status="declined", reason=reason)

    def fail(self, reason: str) -> VisualTurn:
        """Terminal failure; never a request for another sample or API attempt."""
        self._open()
        _text(reason, 800, "Failure reason")
        return replace(self, actions=self.actions + 1, status="failed", reason=reason)

