"""Focused offline checks; no credentials or live requests are used."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from run_authorized_key import AuthorizedKeyProvider
from visual_collusion.budget import BudgetError, BudgetLedger, PendingPaidRequest
from visual_collusion.providers import PreflightError


class AuthorizedKeyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.ledger = BudgetLedger(Path(self.temporary.name) / "budget.sqlite3", 40)
        self.key = {"limit": 60, "limit_remaining": 60, "usage": 9.52863545,
                    "limit_reset": "weekly", "byok_usage": 0,
                    "is_management_key": False, "is_free_tier": False}
        self.calls = []
        def transport(method, path, payload):
            self.calls.append((method, path))
            self.assertEqual((method, path), ("GET", "/key"))
            return {"data": deepcopy(self.key)}
        self.provider = AuthorizedKeyProvider("offline-fixture", self.ledger, transport=transport)

    def test_observed_weekly_metadata_is_reported_truthfully(self):
        status = self.provider._key_check(40)
        self.assertEqual(status["limit_usd"], 60)
        self.assertEqual(status["limit_reset"], "weekly")
        self.assertTrue(status["provider_lifetime_guard_waived"])

    def test_insufficient_remaining_is_rejected(self):
        self.key["limit_remaining"] = 0.01
        with self.assertRaisesRegex(PreflightError, "remaining allowance"):
            self.provider._key_check(0.02)

    def test_local_forty_dollar_ledger_still_enforces_ceiling(self):
        self.provider._key_check(40)
        with self.assertRaisesRegex(BudgetError, "cannot cover"):
            self.ledger.reserve("over-cap", "chat", "fixture", "a" * 64, 40.000001)
        self.assertEqual(self.ledger.summary()["request_count"], 0)

    def test_unresolved_ledger_blocks_even_key_get(self):
        self.ledger.reserve("unresolved", "chat", "fixture", "b" * 64, 0.01)
        with self.assertRaises(PendingPaidRequest):
            self.provider._key_check(0.01)
        self.assertEqual(self.calls, [])

    def test_canonical_compliant_key_uses_normal_path(self):
        self.key.update(limit=40, limit_remaining=40, usage=0, limit_reset=None)
        status = self.provider._key_check(40)
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("provider_lifetime_guard_waived", status)

    def test_unrelated_key_change_is_not_authorized(self):
        self.key["limit"] = 80
        with self.assertRaisesRegex(PreflightError, "only to the observed"):
            self.provider._key_check(40)

    def test_nonfinite_and_byok_fields_are_rejected(self):
        for field, value in (("usage", float("nan")), ("limit_remaining", float("inf")), ("byok_usage", 1)):
            with self.subTest(field=field):
                before = deepcopy(self.key)
                self.key[field] = value
                with self.assertRaises(PreflightError):
                    self.provider._key_check(0.01)
                self.key = before


if __name__ == "__main__":
    unittest.main()
