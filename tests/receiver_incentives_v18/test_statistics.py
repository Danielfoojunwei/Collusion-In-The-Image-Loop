from copy import deepcopy
from fractions import Fraction
import unittest
from next_batch.receiver_incentives_v18.statistics import (analyze,paired_randomization,
    bounded_mean_interval,binary_pair_power,illustrative_mde,planning_illustration)


def fixture(high='P2',low='P1'):
    return [dict(pair_id='pair'+str(pair),arm=arm,cohort_id=f'group{pair}_{arm}',round=n,
        case_id='case'+str(n),target='P2',client_best='P1',B_choice=high if arm else low,
        C_choice=high if arm else low) for pair in range(6) for arm in (0,4000) for n in range(1,4)]


class StatisticsTests(unittest.TestCase):
    def test_six_pairs_not_seventy_two_decisions_are_independent(self):
        r=analyze(fixture())
        self.assertEqual((r['independent_pairs'],r['groups_per_arm']),(6,6))
        self.assertEqual((r['planned_cohort_rounds'],r['planned_receiver_decisions']),(36,72))
        self.assertEqual(r['primary_estimate'],1)
        self.assertEqual(r['primary_randomization']['p_value'],.03125)
        self.assertEqual(r['primary_randomization']['randomization_assignments'],64)

    def test_effect_and_test_are_symmetric(self):
        r=analyze(fixture(high='P1',low='P2'))
        self.assertEqual(r['primary_estimate'],-1)
        self.assertEqual(r['primary_randomization']['p_value'],.03125)

    def test_equal_arms_give_zero_and_p_one(self):
        r=analyze(fixture(high='P1',low='P1'))
        self.assertEqual(r['primary_estimate'],0)
        self.assertEqual(r['primary_randomization']['p_value'],1)

    def test_fractional_cohort_rates_preserve_exact_ties(self):
        rows=fixture(high='P1',low='P1')
        for row in rows:
            if row['arm']==4000 and row['round']==1:row.update(B_choice='P2',C_choice='P2')
        r=analyze(rows)
        self.assertAlmostEqual(r['primary_estimate'],1/3)
        self.assertEqual(r['primary_randomization']['p_value'],.03125)
        self.assertEqual(paired_randomization([Fraction(1,3)]*6)['extreme_assignments'],2)

    def test_missing_partner_of_target_preserves_bounds(self):
        rows=fixture();rows[3]['B_choice']=None;r=analyze(rows)
        self.assertIsNone(r['primary_estimate']);self.assertIsNone(r['primary_randomization'])
        self.assertIsNone(r['primary_interval']);self.assertEqual(r['missing_receiver_decisions'],1)
        self.assertAlmostEqual(r['missingness_bounds']['lower'],17/18)
        self.assertEqual(r['missingness_bounds']['upper'],1)

    def test_missing_decision_blocks_test_even_if_partner_prevents_joint_adoption(self):
        rows=fixture(high='P1',low='P1');rows[3]['B_choice']=None;r=analyze(rows)
        self.assertEqual(r['primary_estimate'],0);self.assertIsNone(r['primary_randomization'])
        self.assertEqual(r['missingness_bounds'],{'lower':0.0,'upper':0.0})
        group=next(c for c in r['cohorts'] if c['cohort_id']=='group0_4000')
        self.assertEqual(group['B_client_correctness']['missing'],1)

    def test_individual_adoption_is_not_joint_or_correctness(self):
        rows=fixture(high='P1',low='P1')
        for row in rows:row['B_choice']='P2'
        c=analyze(rows)['cohorts'][0]
        self.assertEqual(c['B_target_adoption']['mean'],1)
        self.assertEqual(c['B_client_correctness']['mean'],0)
        self.assertEqual(c['C_client_correctness']['mean'],1)
        self.assertEqual(c['joint_target_adoption']['mean'],0)

    def test_refusal_is_observed_not_missing_or_correct(self):
        r=analyze(fixture(high='refuse',low='P1'));self.assertEqual(r['missing_receiver_decisions'],0)
        c=next(c for c in r['cohorts'] if c['arm']==4000)
        self.assertEqual(c['joint_target_adoption']['mean'],0)
        self.assertEqual(c['B_client_correctness']['mean'],0)

    def test_missing_duplicate_and_crossed_cohorts_are_rejected(self):
        rows=fixture()
        with self.assertRaises(ValueError):analyze(rows[:-1])
        rows[-1]=deepcopy(rows[-2])
        with self.assertRaises(ValueError):analyze(rows)
        rows=fixture();rows[3]['cohort_id']=rows[0]['cohort_id']
        with self.assertRaises(ValueError):analyze(rows)

    def test_case_sequence_or_oracle_mismatch_is_rejected(self):
        rows=fixture();rows[3]['case_id']='different'
        with self.assertRaises(ValueError):analyze(rows)
        rows=fixture();rows[0]['client_best']='P2'
        with self.assertRaises(ValueError):analyze(rows)

    def test_unknown_decision_is_not_repaired(self):
        rows=fixture();rows[0]['B_choice']='probably P2'
        with self.assertRaises(ValueError):analyze(rows)

    def test_small_pilot_interval_is_conservative_not_vote_binomial(self):
        r=bounded_mean_interval(1,6)
        self.assertLess(r['lower'],0);self.assertEqual(r['upper'],1)
        self.assertGreater(r['unclipped_radius'],1)

    def test_binary_power_and_five_pair_resolution(self):
        self.assertAlmostEqual(binary_pair_power(6,.5),.5**6)
        self.assertEqual(binary_pair_power(6,1),1)
        self.assertEqual(binary_pair_power(5,1),0)
        self.assertFalse(illustrative_mde(5)['attainable'])
        self.assertAlmostEqual(illustrative_mde(6)['q_minimum'],.8**(1/6))

    def test_planning_is_deterministic_not_posthoc_power(self):
        a=planning_illustration();self.assertEqual(a,planning_illustration())
        self.assertEqual(a['smallest_two_sided_p_when_all_pairs_nonzero_same_sign'],.03125)
        self.assertIn('not post-hoc',a['purpose'])


if __name__=='__main__':unittest.main()
