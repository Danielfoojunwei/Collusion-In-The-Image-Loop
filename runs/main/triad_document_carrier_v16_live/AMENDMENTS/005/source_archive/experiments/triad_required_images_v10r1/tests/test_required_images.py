"""Required-image and cancellation regressions; scripted callbacks, no APIs."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from .. import funding
from ..coordinator import CoordinatorError
from ..engine import Engine,RequiredOutputError,canonical
from . import test_coordinator as coordinator_fixtures
from .test_engine import mock_result,review_fixture
from . import test_funding as funding_fixtures
from research_plans.triad_required_images_v10r1.commitment import build_cases


REQUIRED={'all_selected_images_required':True,'all_structured_records_required':True,
          'restart_entire_episode_on_any_failure':True,'maximum_whole_episode_attempts':3}


class RequiredImagesTests(unittest.TestCase):
    def harness(self):
        h=coordinator_fixtures.CoordinatorTests(methodName='runTest');h.setUp();self.addCleanup(h.doCleanups)
        h.config['acceptance']=deepcopy(REQUIRED)
        h.config_path.write_text(canonical(h.config));h.write_freeze()
        h.coordinator=h.new_coordinator()
        return h

    def test_missing_A_image_aborts_before_proposal_or_peer_calls(self):
        h=self.harness();h.invalid.add('r01.a01.A_IMAGE')
        with h.coordinator.locked():
            h.coordinator.init();r=h.coordinator.advance()
            self.assertEqual(r['status'],'episode_aborted')
            self.assertEqual(r['restart_from_round'],1)
            self.assertEqual(len(h.posts),1)
            self.assertEqual(h.coordinator.engine.status()['committed_rounds'],0)
            self.assertTrue((h.out/'failed_required_outputs/r01.a01.A_IMAGE.json').exists())
            with self.assertRaises(CoordinatorError):h.coordinator.advance()
            self.assertEqual(len(h.posts),1)

    def test_missing_C_image_cannot_commit_after_review_and_A_B_images(self):
        h=self.harness();h.invalid.add('r01.a01.POST_C_IMAGE')
        with h.coordinator.locked():
            h.coordinator.init();self.assertEqual(h.coordinator.advance()['status'],'awaiting_review')
            review_fixture(h.coordinator.engine)
            r=h.coordinator.advance()
            self.assertEqual(r['status'],'episode_aborted')
            self.assertEqual(r['stage'],'POST_C_IMAGE')
            self.assertEqual(h.coordinator.engine.status()['committed_rounds'],0)
            self.assertEqual(len(h.posts),8)

    def test_valid_three_images_commit_and_later_failure_requires_round_one(self):
        h=self.harness()
        with h.coordinator.locked():
            h.coordinator.init();h.coordinator.advance();review_fixture(h.coordinator.engine)
            r=h.coordinator.advance(through_round=1)
            self.assertEqual(r['committed_rounds'],1)
            for role in 'ABC':self.assertIsNotNone(h.coordinator.engine._image(1,role))
            h.invalid.add('r02.a01.A_IMAGE')
            r=h.coordinator.advance()
            self.assertEqual(r['status'],'episode_aborted')
            self.assertEqual(r['restart_from_round'],1)
            self.assertTrue(r['previous_commits_are_excluded_from_accepted_episode'])
            self.assertEqual(r['committed_rounds_in_aborted_episode'],1)
            self.assertFalse(any(q['request_id']=='r02.a01.A_PROPOSAL' for q in h.performs))

    def test_transport_fault_aborts_episode_without_round_retry(self):
        h=self.harness();h.failures['r01.a01.A_IMAGE']='transport'
        with h.coordinator.locked():
            h.coordinator.init();r=h.coordinator.advance()
            self.assertEqual(r['status'],'episode_aborted')
            self.assertEqual(len(h.posts),1)
            self.assertEqual(h.coordinator.ledger.namespace_summary()['unresolved_reserved_usd'],.02)
            self.assertFalse((h.out/'attempts/r01/a02').exists())

    def test_valid_policy_choice_and_unknown_interpretation_remain_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            plan=build_cases();e=Engine(d,{'acceptance':REQUIRED},plan)
            req={'role':'B','mode':'vote','round':1,'request_id':'scripted.B_VOTE'}
            result=mock_result(req,Path(d),plan)
            result['output']['decision']=plan[0]['oracle']
            result['output']['raw_proposal'].update(decision=plan[0]['oracle'],inferred_target='unknown',confidence=0)
            result['output']['private_interpretation'].update(inferred_target='unknown',confidence=0)
            e._validate_result(req,result)

    def test_invalid_carryover_record_cannot_advance(self):
        with tempfile.TemporaryDirectory() as d:
            plan=build_cases();e=Engine(d,{'acceptance':REQUIRED},plan)
            req={'role':'A','mode':'log','round':1,'request_id':'scripted.POST_A_LOG'}
            result=mock_result(req,Path(d),plan,invalid=True)
            with self.assertRaises(RequiredOutputError):e._validate_result(req,result)

    def test_operator_interrupt_cannot_turn_into_another_paid_call(self):
        h=funding_fixtures.FundingTests(methodName='runTest');h.setUp();self.addCleanup(h.doCleanups)
        calls=[]
        def interrupted(body,binding):calls.append(binding);raise KeyboardInterrupt()
        with funding_fixtures.locked(h.out):
            with self.assertRaises(funding.OperatorStop):
                h.guard().execute('r01.a01.A.image','chat',funding.MODELS['chat'],{'model':funding.MODELS['chat']},.02,interrupted)
            self.assertEqual(len(calls),1)
            self.assertTrue((h.out/'operator_stop.requested.json').exists())
            with self.assertRaises(funding.OperatorStop):
                h.guard('r01.a02').execute('r01.a02.A.image','chat',funding.MODELS['chat'],{'model':funding.MODELS['chat']},.02,interrupted)
            self.assertEqual(len(calls),1)
            self.assertEqual(h.ledger.namespace_summary()['request_count'],1)

    def test_twelve_round_required_image_fixture_contains_exactly36_PNGs(self):
        h=self.harness()
        with h.coordinator.locked():
            h.coordinator.init()
            for n in range(1,13):
                r=h.coordinator.advance()
                self.assertEqual(r['status'],'awaiting_review')
                review_fixture(h.coordinator.engine)
                r=h.coordinator.advance(through_round=n)
                self.assertEqual(r['committed_rounds'],n)
            self.assertEqual(sum(h.coordinator.engine._image(n,role) is not None for n in range(1,13) for role in 'ABC'),36)
            self.assertIsNone(h.coordinator.engine.next_stage())


if __name__=='__main__':unittest.main()
