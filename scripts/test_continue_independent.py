"""Offline invariants for explicitly authorized independent-case continuation."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from run_continue_independent import ContinuationLedger, IndependentCaseProvider, LOSS_REASON
from visual_collusion.budget import BudgetError, PendingPaidRequest, request_digest
from visual_collusion.providers import ProviderError
from visual_collusion.runtime import InvalidAgentOutput


class IndependentContinuationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.ledger = ContinuationLedger(Path(temporary.name) / "budget.sqlite3", 40)
        self.payload = {"model": "fixture"}
        self.hash = request_digest({"endpoint": "/chat/completions", "payload": self.payload})
        self.posts = 0
        self.response = None
        def transport(method, path, payload):
            if method == "POST":
                self.posts += 1
                if self.response is None:
                    raise ProviderError("API HTTP 502; no automatic retry")
                return deepcopy(self.response)
            self.fail("No metadata GET was expected")
        self.provider = IndependentCaseProvider("offline-fixture", self.ledger, transport=transport)
        self.provider._preflight = {"status": "offline-fixture"}
        import time
        self.provider._preflight_time = time.monotonic()
        self.provider._key_check = lambda required: {"remaining_usd": 60}

    def reserve(self, request_id="lost", amount=0.0098964):
        self.ledger.reserve(request_id, "chat", "fixture", self.hash, amount)

    def test_sealed_original_uncertain_retains_every_field_and_reservation(self):
        self.reserve()
        self.ledger.uncertain("lost", LOSS_REASON)
        before = self.ledger.lookup("lost")
        self.ledger.seal_transport_loss("lost")
        self.ledger.assert_ready()
        self.assertEqual(self.ledger.lookup("lost"), before)
        self.assertEqual(self.ledger.summary()["reserved_unresolved_usd"], 0.0098964)
        with self.assertRaises(InvalidAgentOutput):
            self.provider._paid("lost", "chat", "/chat/completions", self.payload, 0.0098964)
        self.assertEqual(self.posts, 0)

    def test_pending_and_unsealed_uncertain_block(self):
        self.reserve()
        with self.assertRaises(PendingPaidRequest):
            self.ledger.assert_ready()
        self.ledger.uncertain("lost", LOSS_REASON)
        with self.assertRaises(PendingPaidRequest):
            self.ledger.assert_ready()

    def test_new_transport_loss_posts_once_and_then_is_terminal_missing(self):
        with self.assertRaises(InvalidAgentOutput):
            self.provider._paid("new", "chat", "/chat/completions", self.payload, 0.01)
        self.assertEqual(self.posts, 1)
        self.assertTrue(self.ledger.is_terminal_loss("new"))
        with self.assertRaises(InvalidAgentOutput):
            self.provider._paid("new", "chat", "/chat/completions", self.payload, 0.01)
        self.assertEqual(self.posts, 1)
        self.ledger.assert_ready()

    def test_missing_cost_response_is_not_sealed_and_still_blocks(self):
        self.response = {"id": "fixture-generation", "usage": {}}
        with self.assertRaisesRegex(ProviderError, "no usable cost receipt"):
            self.provider._paid("no-cost", "chat", "/chat/completions", self.payload, 0.01)
        self.assertFalse(self.ledger.is_terminal_loss("no-cost"))
        with self.assertRaises(PendingPaidRequest):
            self.ledger.assert_ready()
        self.assertEqual(self.posts, 1)

    def test_cap_includes_sealed_unknown_reservation(self):
        self.reserve(amount=39.99)
        self.ledger.uncertain("lost", LOSS_REASON)
        self.ledger.seal_transport_loss("lost")
        with self.assertRaisesRegex(BudgetError, "cannot cover"):
            self.ledger.reserve("other", "chat", "fixture", "a" * 64, 0.02)
        self.assertEqual(self.ledger.summary()["request_count"], 1)

    def test_changed_payload_does_not_skip_or_post(self):
        self.reserve()
        self.ledger.uncertain("lost", LOSS_REASON)
        self.ledger.seal_transport_loss("lost")
        with self.assertRaisesRegex(BudgetError, "Stable request ID"):
            self.provider._paid("lost", "chat", "/chat/completions", {"model": "different"}, 0.01)
        self.assertEqual(self.posts, 0)

    def test_loss_records_cannot_be_rewritten(self):
        import sqlite3
        self.reserve()
        self.ledger.uncertain("lost", LOSS_REASON)
        self.ledger.seal_transport_loss("lost")
        with self.assertRaises(sqlite3.IntegrityError):
            with self.ledger._connect() as connection:
                connection.execute("DELETE FROM terminal_transport_losses")

    def test_halt_reason_is_honored(self):
        self.ledger.halt("fixture budget halt")
        with self.assertRaisesRegex(BudgetError, "fixture budget halt"):
            self.ledger.assert_ready()


if __name__ == "__main__":
    unittest.main()
