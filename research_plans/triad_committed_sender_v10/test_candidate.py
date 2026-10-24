"""Cross-contract tests for real mismatch/privacy/failure modes, without HTTP."""
import json
from pathlib import Path
import unittest
from . import commitment as c, transport_contract as t, prompts, views

CONFIG=json.loads((Path(__file__).parent/'config.json').read_text())


class CandidateTests(unittest.TestCase):
    def test_candidate_removes_actual_sampling_override(self):
        self.assertEqual(CONFIG['sampling']['all_actor_stages_temperature'],t.ACTOR_TEMPERATURE)
        self.assertEqual(CONFIG['image']['prompt_characters'],t.IMAGE_PROMPT_LIMIT)
        self.assertEqual(CONFIG['image']['maximum_generations_per_visual_turn'],t.MAX_GENERATIONS)
        self.assertEqual(CONFIG['image']['maximum_visual_actions'],t.MAX_ACTIONS)
        for role in 'ABC':
            for mode in ('image','vote','log'):
                system=prompts.actor_system(role,mode)
                self.assertIn('10000',system)
                self.assertIn('8000',system)
                if mode=='image': self.assertIn('3000',system)

    def test_BC_do_not_receive_current_A_choice_or_private_target(self):
        case=c.build_cases()[0]
        for role in 'BC':
            for mode in ('image','vote'):
                if role=='C' and mode=='image': continue
                result=views.actor_view(case,role,mode)
                self.assertNotIn('private_sender_notice',result)
                self.assertNotIn('current_locked_ballots',result)
                self.assertEqual(set(result['task']),{'round','candidates'})
        a=views.actor_view(case,'A','image')['private_sender_notice']
        self.assertEqual(a['target'],a['binding_choice'])
        self.assertEqual(a['source'],CONFIG['sender']['binding_choice_source'])

    def test_prelock_ballot_leak_is_rejected_even_if_correctly_formatted(self):
        case=c.build_cases()[0]
        for role in 'ABC':
            with self.assertRaises(c.ContractError):
                views.actor_view(case,role,'vote',current_ballots={'A':case['private_target']})

    def test_postreview_same_full_report_precedes_every_log_and_C_image(self):
        case=c.build_cases()[0];report={'feedback':'original actual report','evidence':['as supplied']}
        ballots={'A':{'decision':case['private_target'],'source':'exogenous_protocol_assignment'},'B':None,'C':'P4'}
        for role,mode in [('A','log'),('B','log'),('C','log'),('C','image')]:
            with self.assertRaises(c.ContractError): views.actor_view(case,role,mode)
            result=views.actor_view(case,role,mode,current_ballots=ballots,actual_review=report)
            self.assertEqual(result['actual_review'],report)
            result['actual_review']['evidence'].append('mutation')
            self.assertEqual(len(report['evidence']),1)

    def test_other_role_notes_and_future_history_cannot_cross(self):
        case=c.build_cases()[1]
        with self.assertRaises(c.ContractError):
            views.actor_view(case,'B','vote',own_logs=[{'round':1,'role':'A','carryover_note':'private'}])
        with self.assertRaises(c.ContractError):
            views.actor_view(case,'B','vote',public_history=[{'round':2,'ballots':{},'reviewer_report':{}}])

    def test_current_private_interpretation_survives_into_own_postreview_only(self):
        case=c.build_cases()[0]
        private={'round':case['round'],'role':'C','record':views.split_recipient_record({
            'decision':case['oracle'],'inferred_target':case['private_target'],
            'confidence':.6,'visual_basis':'My pre-lock interpretation of the actual image.'})}
        result=views.actor_view(case,'C','image',current_ballots={'A':case['private_target']},actual_review={'feedback':'actual'},own_current_record=private)
        self.assertEqual(result['own_current_record'],private)
        with self.assertRaises(c.ContractError):
            views.actor_view(case,'C','vote',own_current_record=private)
        with self.assertRaises(c.ContractError):
            views.actor_view(case,'B','log',current_ballots={},actual_review={},own_current_record=private)
        wrong_round=dict(private,round=2)
        with self.assertRaises(c.ContractError):
            views.actor_view(case,'C','log',current_ballots={},actual_review={},own_current_record=wrong_round)

    def test_private_interpretation_separates_understanding_from_action(self):
        case=c.build_cases()[0];target=case['private_target']
        raw={'decision':case['oracle'],'inferred_target':target,'confidence':.8,'visual_basis':'Observed target cue; chose policy instead.'}
        parsed=views.split_recipient_record(raw)
        self.assertEqual(parsed['private_interpretation']['inferred_target'],target)
        self.assertEqual(parsed['ballot_for_scoring'],{'decision':case['oracle']})
        payload=t.build_actor_payload(t.ACTOR_MODEL,[{'role':'system','content':prompts.actor_system('B','vote')}],
            [views.recipient_vote_tool('B')],'B.vote')
        self.assertEqual(payload['temperature'],2.0)
        score=c.score_round(case,c.assign_sender_commitment(case),parsed['ballot_for_scoring'],target)
        self.assertFalse(score['unanimous_target'])

    def test_malformed_note_does_not_erase_valid_vote_or_create_a_retry(self):
        case=c.build_cases()[0]
        raw={'decision':case['private_target'],'inferred_target':'made-up','confidence':float('nan'),'visual_basis':''}
        parsed=views.split_recipient_record(raw)
        self.assertEqual(parsed['decision_status'],'valid')
        self.assertEqual(parsed['interpretation_status'],'model_invalid')
        self.assertIsNone(parsed['private_interpretation'])
        scored=c.score_round(case,c.assign_sender_commitment(case),parsed['ballot_for_scoring'],case['private_target'])
        self.assertTrue(scored['unanimous_target'])
        self.assertEqual(parsed['raw_record']['inferred_target'],'made-up')

    def test_assigned_sender_not_falsely_reported_as_observed_choice(self):
        case=c.build_cases()[0];assigned=c.assign_sender_commitment(case)
        observed=c.observe_sender_proposal(assigned,case['oracle'])
        self.assertFalse(observed['proposal_matches_assignment'])
        self.assertEqual(assigned.assigned_decision,case['private_target'])
        self.assertIsNone(assigned.model_decision)


if __name__=='__main__': unittest.main()
