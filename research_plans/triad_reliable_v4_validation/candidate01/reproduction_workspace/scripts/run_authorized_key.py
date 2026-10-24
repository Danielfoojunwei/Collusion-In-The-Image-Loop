"""Host-only runner for the user's explicit provider-key guard waiver.

The canonical source/configuration are unchanged. Only the observed $60 weekly
OpenRouter key is admitted in addition to keys accepted by the canonical adapter.
The original $40 ledger, model pins, reservations and no-retry behavior remain.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

from visual_collusion import cli
from visual_collusion.providers import OpenRouterProvider, PreflightError, _number


AUTHORIZATION = {
    "user_budget_clarification": "the $50 is only for the openrouter, do not count codex",
    "user_proceed_instruction": "doesnt matter, just proceed now",
    "waived_guard": "Dedicated non-resetting provider lifetime key cap at most $40",
    "scope": "Accept only the observed $60 weekly provider key; preserve the $40 local inference ledger and $50 OpenRouter cash ceiling.",
}


class AuthorizedKeyProvider(OpenRouterProvider):
    """Retain all canonical transport and paid-call behavior except this guard."""

    def _key_check(self, minimum_remaining):
        self.ledger.assert_ready()
        if _number(self.ledger.summary()["cap_usd"], "local ledger cap") != Decimal("40"):
            raise PreflightError("Authorized host runner requires the unchanged $40 inference ledger")
        try:
            result = super()._key_check(minimum_remaining)
        except PreflightError as error:
            # Only canonical provider-cap/reset rejections fall under the waiver.
            if str(error) not in {
                "Use a dedicated lifetime key cap without daily/weekly/monthly resets",
                "Dedicated OpenRouter key must have a positive lifetime limit no greater than $40",
            }:
                raise
            result = self._authorized_weekly_check(minimum_remaining)
        self._last_authorized_key_status = deepcopy(result)
        return result

    def _authorized_weekly_check(self, minimum_remaining):
        data = self._request("GET", "/key").get("data")
        if not isinstance(data, dict):
            raise PreflightError("Free key metadata was unavailable")
        limit = _number(data.get("limit"), "key limit")
        remaining = _number(data.get("limit_remaining"), "key limit remaining")
        usage = _number(data.get("usage"), "key usage")
        byok = _number(data.get("byok_usage"), "BYOK usage")
        required = _number(minimum_remaining, "required allowance")
        if limit != Decimal("60") or data.get("limit_reset") != "weekly":
            raise PreflightError("User waiver applies only to the observed $60 weekly provider key")
        if remaining > limit:
            raise PreflightError("Inconsistent key remaining allowance metadata")
        if remaining < required:
            raise PreflightError("Key remaining allowance cannot cover the required budget/reservation")
        if byok != 0 or data.get("is_management_key") is not False or data.get("is_free_tier") is not False:
            raise PreflightError("Use a paid inference key without BYOK usage; management/free keys are not suitable")
        # `usage` can be lifetime usage while the limit resets weekly. Do not
        # falsely apply the canonical lifetime usage + remaining identity here.
        return {
            "limit_usd": float(limit), "remaining_usd": float(remaining),
            "usage_usd": float(usage), "limit_reset": "weekly", "byok_usage_usd": float(byok),
            "key_identity": "unverified: retain the same runtime secret; no credential or placeholder hash recorded",
            "wallet_funding": "unverified: ordinary inference-key metadata does not establish wallet funding",
            "provider_lifetime_guard_waived": True,
            "authorization": deepcopy(AUTHORIZATION),
            "budget_warning": "The provider key resets weekly and permits $60. It does not independently enforce the experiment's $40 lifetime limit; the unchanged local ledger enforces that limit.",
        }

    def preflight(self, *args, **kwargs):
        report = super().preflight(*args, **kwargs)
        if report["key"].get("provider_lifetime_guard_waived"):
            report["image_reservation_warning"] = "One 1K output and at most one reference are reserved at $0.028. The user waived the independent provider lifetime key cap; the unchanged $40 local inference ledger remains enforced."
            report["authorized_host_deviation"] = deepcopy(AUTHORIZATION)
            self._preflight = deepcopy(report)
        return report


def main(argv=None):
    cli.OpenRouterProvider = AuthorizedKeyProvider
    return cli.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
