"""One exact file-sealed exception; every original reserve remains counted."""
from experiments.triad_emergent_v9 import funding as base
from experiments.pressure_interview_v1.budget_guard import SharedBudgetGuard
from experiments.pressure_interview_v1.live_provider import _save_once
import hashlib
import re


class FundingLedger(base.FundingLedger):
    def __init__(self, path, cap_usd=40, *, authority):
        self.loss_authority = authority
        super().__init__(path, cap_usd)

    def _sealed(self, db, row):
        return base.FundingLedger._sealed(db, row) or self.loss_authority.matches(row)


class SingleAttemptFunding(base.SingleAttemptFunding):
    def execute(self, local_request_id, kind, model, wire_payload, reserve_usd, post):
        def diagnosed_post(body, binding):
            try:
                return post(body, binding)
            except BaseException as error:
                name = type(error).__name__
                safe_class = name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", name) else "UnclassifiedException"
                if self.evidence_dir is not None:
                    _save_once(self.evidence_dir / "callback_diagnostics" / (hashlib.sha256(binding["request_id"].encode()).hexdigest() + ".json"),
                        {**binding, "category": "paid_callback_exception", "exception_class": safe_class,
                         "callback_observation_returned": False, "provider_response_observed": "unknown",
                         "error_body_and_message_not_retained": True})
                raise
        return super().execute(local_request_id, kind, model, wire_payload, reserve_usd, diagnosed_post)


def create_guard(path, evidence_dir, authority, *, ledger=None):
    if ledger is None:
        if path.resolve() != base.DEFAULT_AGGREGATE_PATH.resolve():
            raise base.BudgetError("Production repair03 requires the original aggregate")
        ledger = FundingLedger(path, authority=authority)
    if not isinstance(ledger, FundingLedger) or ledger.loss_authority.expected_sha256 != authority.expected_sha256:
        raise base.BudgetError("Repair03 requires its exact capped funding authority")
    guard = SharedBudgetGuard(path, base.NAMESPACE, ledger=ledger)
    return SingleAttemptFunding(guard, evidence_dir)
