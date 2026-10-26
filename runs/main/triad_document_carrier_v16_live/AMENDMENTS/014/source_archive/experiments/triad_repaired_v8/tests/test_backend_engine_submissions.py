"""Offline native finals must survive the backend-to-engine boundary unchanged.

These tests use the real backend with scripted native provider replies and the
real engine validator. They are technical fixtures, never communication results.
"""
from copy import deepcopy
from pathlib import Path
import unittest

from experiments.triad_repaired_v8.engine import InvalidObservedOutput
from experiments.triad_repaired_v8.tests import test_backend as backend_fixture
from experiments.triad_repaired_v8.tests import test_engine as engine_fixture


class BackendEngineSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.wire = backend_fixture.BackendTests()
        self.wire.setUp()
        self.addCleanup(self.wire.tearDown)
        self.engine_fixture = engine_fixture.EngineTests()
        self.engine_fixture.setUp()
        self.addCleanup(self.engine_fixture.tearDown)
        self.serial = 0

    def terminal(self, mode, name, *, draft=False, disposition=None):
        """Submit actual native fixture calls, then validate their real output."""
        self.serial += 1
        actions = ([{"action": "generate_image", "prompt": "OFFLINE_FIXTURE private draft.",
                     "reference": "none"}] if draft else [])
        final = {"action": name}
        if name == "submit_ballot":
            final.update(decision="P1", decoded_target="unknown")
        elif name in {"abstain", "decline"}:
            final["reason"] = "OFFLINE_FIXTURE explicit terminal choice."
        if disposition is not None:
            final["image_disposition"] = disposition
        actions.append(final)
        backend = self.wire.backend(actions)
        request = self.wire.request(mode, "C" if mode == "image_only" else "A",
                                    "boundary" + str(self.serial))
        result = backend.run_actor(request)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(len(result["action_log"]), len(actions))
        chats = self.wire.chats()
        self.assertEqual(len(chats), len(actions))
        self.assertIn(name, {tool["function"]["name"] for tool in chats[-1]["tools"]})
        self.assertEqual(chats[-1]["tool_choice"], "required")
        output = self.engine_fixture.engine._validate_actor(result["output"], mode)
        # Engine normalization can relocate copied PNGs, but cannot change the
        # explicit ballot, final state, or choice of whether to deliver pixels.
        for key in ("decision", "decoded_target", "submission_state"):
            self.assertEqual(output[key], result["output"][key])
        self.assertEqual(output["image"] is None, result["output"]["image"] is None)
        if output["image"] is not None:
            self.assertEqual(output["image"]["sha256"], result["output"]["image"]["sha256"])
            copied = self.engine_fixture.engine.memory_directory / output["image"]["path"]
            self.assertEqual(copied.read_bytes(), Path(result["output"]["image"]["path"]).read_bytes())
        return result, output

    def assert_no_ballot(self, output):
        self.assertIsNone(output["decision"])
        self.assertIsNone(output["decoded_target"])

    def test_image_only_abstention_with_or_without_explicit_image_is_valid(self):
        for draft, disposition in ((False, "none"), (True, "none"), (True, "latest_draft")):
            with self.subTest(draft=draft, disposition=disposition):
                _, output = self.terminal("image_only", "abstain", draft=draft, disposition=disposition)
                self.assertEqual(output["submission_state"], "abstained")
                self.assert_no_ballot(output)
                self.assertEqual(output["image"] is not None, disposition == "latest_draft")

    def test_image_only_delivery_and_decline_never_create_a_ballot(self):
        _, delivered = self.terminal("image_only", "deliver_image", draft=True)
        self.assertEqual(delivered["submission_state"], "image_delivered")
        self.assert_no_ballot(delivered)
        self.assertIsNotNone(delivered["image"])
        for draft in (False, True):
            with self.subTest(decline_after_draft=draft):
                result, declined = self.terminal("image_only", "decline", draft=draft)
                self.assertEqual(declined["submission_state"], "declined")
                self.assert_no_ballot(declined)
                self.assertIsNone(declined["image"])
                self.assertEqual(any("generated_image" in row for row in result["action_log"]), draft)

    def test_image_vote_submitted_ballot_is_independent_of_image_delivery(self):
        for draft, disposition in ((False, "none"), (True, "none"), (True, "latest_draft"),
                                   (False, "latest_draft")):
            with self.subTest(draft=draft, disposition=disposition):
                result, output = self.terminal("image_vote", "submit_ballot", draft=draft,
                                               disposition=disposition)
                self.assertEqual(output["submission_state"], "submitted")
                self.assertEqual((output["decision"], output["decoded_target"]), ("P1", "unknown"))
                self.assertEqual(output["image"] is not None, draft and disposition == "latest_draft")
                if not draft and disposition == "latest_draft":
                    self.assertIn("image_delivery_error", result["action_log"][-1])

    def test_image_vote_abstention_can_deliver_only_the_explicitly_selected_image(self):
        for draft, disposition in ((False, "none"), (True, "none"), (True, "latest_draft")):
            with self.subTest(draft=draft, disposition=disposition):
                _, output = self.terminal("image_vote", "abstain", draft=draft, disposition=disposition)
                self.assertEqual(output["submission_state"], "abstained")
                self.assert_no_ballot(output)
                self.assertEqual(output["image"] is not None, disposition == "latest_draft")

    def test_image_vote_decline_is_distinct_from_invalid_output_and_withholds_drafts(self):
        for draft in (False, True):
            with self.subTest(draft=draft):
                result, output = self.terminal("image_vote", "decline", draft=draft)
                self.assertEqual(result["status"], "valid")
                self.assertEqual(output["submission_state"], "declined")
                self.assert_no_ballot(output)
                self.assertIsNone(output["image"])

    def test_mode_specific_submission_states_remain_rejected_across_the_boundary(self):
        submitted, _ = self.terminal("image_vote", "submit_ballot", disposition="none")
        delivered, _ = self.terminal("image_only", "deliver_image", draft=True)
        for result, wrong_mode in ((submitted, "image_only"), (delivered, "image_vote")):
            with self.subTest(wrong_mode=wrong_mode):
                with self.assertRaises(InvalidObservedOutput):
                    self.engine_fixture.engine._validate_actor(deepcopy(result["output"]), wrong_mode)


if __name__ == "__main__":
    unittest.main()
