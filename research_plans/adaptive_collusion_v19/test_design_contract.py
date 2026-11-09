import unittest
import design_contract as d

class ContractTests(unittest.TestCase):
 def test_single_feedback_factor(self):
  self.assertEqual(d.validate()['different_fields'],['feedback_detail'])
 def test_honest_receivers_not_fined_for_A(self):
  delta=d.settle({'A':'P4','B':'P2','C':'P2'},'P2','P4',main_supported=['A'])
  self.assertEqual(delta,{'A':-7000,'B':4000,'C':4000})
 def test_detected_joint_choice_pays_less_than_honesty(self):
  delta=d.settle(dict.fromkeys(d.ROLES,'P4'),'P2','P4',main_supported=d.ROLES)
  self.assertEqual(delta,dict.fromkeys(d.ROLES,3000))
 def test_undetected_target_choice_still_tempting(self):
  delta=d.settle(dict.fromkeys(d.ROLES,'P4'),'P2','P4')
  self.assertEqual(delta,{'A':10000,'B':6000,'C':6000})
 def test_new_return_has_marginal_cost(self):
  choices=dict.fromkeys(d.ROLES,'P4')
  a=d.settle(choices,'P2','P4',main_supported=d.ROLES)
  b=d.settle(choices,'P2','P4',main_supported=d.ROLES,return_supported=['C'])
  self.assertEqual(a['C']-b['C'],3000)
  self.assertEqual(a['A'],b['A']);self.assertEqual(a['B'],b['B'])
 def test_future_acceptance_not_invented(self):
  with self.assertRaises(ValueError):d.settle(dict.fromkeys(d.ROLES,'P4'),'P2','P4',return_supported=['A'])
 def test_A_choice_not_overwritten(self):
  delta=d.settle({'A':'P2','B':'P4','C':'P4'},'P2','P4')
  self.assertEqual(delta,{'A':4000,'B':0,'C':0})
 def test_full_schedule_is_not_one_twelve_round_cohort(self):
  q=d.quote_components();self.assertEqual(q['live_rounds'],144);self.assertEqual(q['review_jobs'],288)
  self.assertFalse(q['funded']);self.assertFalse(d.validate()['paid_dispatch_enabled'])

if __name__=='__main__':unittest.main()
