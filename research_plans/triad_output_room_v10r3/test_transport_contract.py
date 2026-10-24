"""Meaningful offline contract checks; no provider SDK or network access."""
import unittest

from .transport_contract import (
    ACTOR_MODEL, ACTOR_TEMPERATURE, IMAGE_PROMPT_LIMIT, MAX_ACTIONS,
    MAX_GENERATIONS, STAGES, ContractError, VisualTurn,
    build_actor_payload, validate_actor_payload,
)


def tool(name):
    return {"type": "function", "function": {"name": name, "strict": True,
        "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}}}


def payload(stage="A.image"):
    mode=stage.split(".")[1]
    tools=[tool("create_visual"),tool("decline_visual")] if mode=="image" else [tool("record_vote" if mode=="vote" else "record_log")]
    return build_actor_payload(ACTOR_MODEL,[{"role":"system","content":"offline fixture"},{"role":"user","content":"task"}],tools,stage)


class PayloadTests(unittest.TestCase):
    def test_all_nine_stages_have_actual_authorized_sampling(self):
        self.assertEqual(len(STAGES),9)
        for stage in STAGES:
            with self.subTest(stage=stage):
                p=payload(stage)
                self.assertEqual(p["temperature"],1.0)
                self.assertEqual(p["reasoning"],{"effort":"high","exclude":False})
                self.assertEqual(p["max_tokens"],16384)
                self.assertEqual(p["provider"]["only"],["open-inference/fp4"])
                self.assertFalse(p["provider"]["allow_fallbacks"])
                validate_actor_payload(p,stage)

    def test_rejects_unconfigured_temperature_and_invalid_value(self):
        for stage in STAGES:
            for t in [2.0,0.0,0.99,1.01,True,"1.0",None,float("nan")]:
                p=payload(stage);p["temperature"]=t
                with self.subTest(stage=stage,t=t),self.assertRaises(ContractError):validate_actor_payload(p,stage)

    def test_rejects_extra_or_missing_sampling_even_if_temperature_correct(self):
        for field in ["top_p","top_k","min_p","seed","frequency_penalty","presence_penalty"]:
            p=payload();p[field]=1
            with self.subTest(field=field),self.assertRaises(ContractError):validate_actor_payload(p,"A.image")
        for field in ["temperature","reasoning","max_tokens"]:
            p=payload();del p[field]
            with self.subTest(field=field),self.assertRaises(ContractError):validate_actor_payload(p,"A.image")

    def test_rejects_route_fallback_reasoning_drift_and_bad_stage(self):
        for field,value in [("provider",{"only":["other"],"allow_fallbacks":True}),
                            ("reasoning",{"effort":"low","exclude":False}),
                            ("model","some-other-model"),("max_tokens",4096),("tool_choice","auto")]:
            p=payload();p[field]=value
            with self.subTest(field=field),self.assertRaises(ContractError):validate_actor_payload(p,"A.image")
        for stage in ["D.image","A.chat","image",None]:
            with self.subTest(stage=stage),self.assertRaises(ContractError):validate_actor_payload(payload(),stage)

    def test_stage_tools_and_forced_single_function(self):
        p=payload("C.vote")
        self.assertEqual(p["tool_choice"],{"type":"function","function":{"name":"record_vote"}})
        with self.assertRaises(ContractError):validate_actor_payload(p,"C.image")
        p=payload();p["tools"][0]["function"]["strict"]=False
        with self.assertRaises(ContractError):validate_actor_payload(p,"A.image")

    def test_malformed_builder_inputs_fail_as_contract_errors(self):
        for messages,tools in [([None],[tool("record_vote")]),([],[]),([{"role":"user"}],[]),
                               ([{"role":"system"}],[{}])]:
            with self.subTest(messages=messages,tools=tools),self.assertRaises(ContractError):
                build_actor_payload(ACTOR_MODEL,messages,tools,"A.vote")
        p=payload();p["provider"]["allow_fallbacks"]=0
        with self.assertRaises(ContractError):validate_actor_payload(p,"A.image")
        p=payload();p["reasoning"]["exclude"]=0
        with self.assertRaises(ContractError):validate_actor_payload(p,"A.image")

    def test_inputs_and_returned_payload_do_not_share_mutable_state(self):
        messages=[{"role":"system","content":"original"}];tools=[tool("record_log")]
        p=build_actor_payload(ACTOR_MODEL,messages,tools,"B.log")
        messages[0]["content"]="changed";tools[0]["function"]["name"]="wrong"
        self.assertEqual(p["messages"][0]["content"],"original")
        self.assertEqual(p["tools"][0]["function"]["name"],"record_log")
        p["provider"]["only"].append("other")
        self.assertEqual(payload()["provider"]["only"],["open-inference/fp4"])


class VisualTurnTests(unittest.TestCase):
    def test_two_fresh_alternatives_can_accept_the_first(self):
        initial=VisualTurn()
        state=initial.create("first","A landscape").create("second","A different visual metaphor")
        self.assertEqual([d.revision_of for d in state.drafts],[None,None])
        accepted=state.accept("first")
        self.assertEqual(accepted.selected_draft_id,"first")
        self.assertEqual(accepted.status,"accepted")
        self.assertEqual(accepted.actions,3)
        self.assertEqual(initial.drafts,())

    def test_revision_explicitly_binds_source_and_does_not_force_acceptance(self):
        state=VisualTurn().create("first","Scene").revise("first","revised","Revised scene")
        self.assertEqual(state.drafts[1].revision_of,"first")
        self.assertEqual(state.accept("first").selected_draft_id,"first")
        self.assertEqual(state.accept("revised").selected_draft_id,"revised")

    def test_generation_and_action_limits_reserve_a_terminal_action(self):
        self.assertEqual((MAX_GENERATIONS,MAX_ACTIONS),(2,4))
        state=VisualTurn().create("first","One").create("second","Two")
        with self.assertRaises(ContractError):state.create("third","Three")
        with self.assertRaises(ContractError):VisualTurn(actions=3).create("late","No room to submit")

    def test_cannot_submit_unknown_or_revise_unseen_drafts(self):
        state=VisualTurn().create("first","One")
        with self.assertRaises(ContractError):state.accept("unseen")
        with self.assertRaises(ContractError):state.revise("unseen","new","Prompt")
        with self.assertRaises(ContractError):state.create("first","Overwrite")

    def test_character_budget_including_unicode_and_invalid_controls(self):
        self.assertEqual(IMAGE_PROMPT_LIMIT,3000)
        self.assertEqual(len(VisualTurn().create("unicode","图"*3000).drafts[0].prompt),3000)
        for p in ["x"*3001,"", "   ","abc\x00", "bad\ud800"]:
            with self.subTest(prompt_length=len(p)),self.assertRaises(ContractError):VisualTurn().create("id",p)

    def test_no_delivery_without_accept_and_no_retry_after_failure_or_decline(self):
        drafted=VisualTurn().create("first","One")
        self.assertEqual(drafted.status,"open");self.assertIsNone(drafted.selected_draft_id)
        for terminal in [drafted.accept("first"),drafted.decline("Unsuitable pixels"),drafted.fail("API fault")]:
            with self.subTest(status=terminal.status):
                for operation in [lambda:terminal.create("another","No retry"),lambda:terminal.revise("first","revision","No retry"),
                                  lambda:terminal.accept("first"),lambda:terminal.decline("Again"),lambda:terminal.fail("Again")]:
                    with self.assertRaises(ContractError):operation()


if __name__ == "__main__":
    unittest.main()
