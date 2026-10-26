"""Pure prospective native-function state contracts, no providers or secrets."""
from copy import deepcopy
import unittest

from experiments.triad_emergent_v9.backend import (
    legal_visual_tools, _validate_arguments, vote_schema, log_schema,
    _tool, _semantic_call_present,
)


class NativeToolTests(unittest.TestCase):
    def names(self, slot, count, draft):
        return {t["function"]["name"] for t in legal_visual_tools(slot, count, draft)}

    def test_generation_has_one_required_prompt_and_no_reference_or_ballot(self):
        tools = legal_visual_tools(1, 0, False)
        self.assertEqual(self.names(1, 0, False), {"create_visual", "decline_visual"})
        create = next(t for t in tools if t["function"]["name"] == "create_visual")
        schema = create["function"]["parameters"]
        self.assertEqual(schema["required"], ["prompt"])
        self.assertEqual(schema["properties"]["prompt"]["maxLength"], 1200)
        self.assertNotIn("record_vote", repr(tools))
        self.assertNotIn("decoded_target", repr(vote_schema()))

    def test_revision_requires_current_draft_and_final_slot_never_generates(self):
        self.assertEqual(self.names(2, 1, True),
                         {"revise_visual", "accept_visual", "decline_visual"})
        self.assertEqual(self.names(3, 2, True), {"accept_visual", "decline_visual"})
        self.assertEqual(self.names(4, 0, False), {"decline_visual"})
        self.assertEqual(self.names(4, 1, True), {"accept_visual", "decline_visual"})
        self.assertEqual(self.names(2, 1, False), {"create_visual", "decline_visual"})

    def test_bad_arguments_never_receive_defaults_or_normalization(self):
        tools = legal_visual_tools(1, 0, False)
        for args in [{}, {"prompt": "x", "reference": "none"},
                     {"prompt": "x" * 1201}, {"prompt": 1}, {"prompt": ""},
                     {"prompt": "invalid\u0001control"}]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                _validate_arguments("create_visual", args, tools)
        with self.assertRaises(ValueError):
            _validate_arguments("accept_visual", {}, tools)

    def test_flat_log_and_ballot_schema_have_explicit_small_bounds(self):
        self.assertEqual(vote_schema()["required"], ["decision"])
        self.assertEqual(log_schema()["properties"]["carryover_note"]["maxLength"], 400)
        self.assertEqual(log_schema()["properties"]["image_reflection"]["maxLength"], 800)
        vote = _tool("record_vote", vote_schema()["properties"], "fixture")
        for args in [{"decision": "p1"}, {"decision": None}, {"decision": "P1", "decoded_target": "P4"}]:
            with self.assertRaises(ValueError):
                _validate_arguments("record_vote", args, [vote])

    def test_valid_business_arguments_in_bad_envelope_are_recognized_for_hold(self):
        tools = [_tool("record_vote", vote_schema()["properties"], "fixture")]
        raw = {"choices": [{"finish_reason": "tool_calls", "message": {
            "tool_calls": [{"function": {"name": "record_vote", "arguments": '{"decision":"P4"}'},
                            "unexpected": "transport metadata"}]}}]}
        self.assertTrue(_semantic_call_present(raw, tools))
        bad = deepcopy(raw)
        bad["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = '{"decision":"wrong"}'
        self.assertFalse(_semantic_call_present(bad, tools))
        bad["choices"][0]["finish_reason"] = "length"
        self.assertFalse(_semantic_call_present(bad, tools))


if __name__ == "__main__":
    unittest.main()
