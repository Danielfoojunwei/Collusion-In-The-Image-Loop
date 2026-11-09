"""Meaningful guards for proposal arithmetic; no API or benchmark execution."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('paper_build', HERE / 'build.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class BuildContractTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads((HERE.parents[1] / 'research_plans/oversight_v17/STUDY_PLAN.json').read_text())
        self.source = (HERE / 'manuscript.tex').read_text()

    def test_actual_plan_matches_manuscript_contract(self):
        self.assertTrue(all(module.check_design(self.plan, self.source).values()))

    def test_old_round_budget_cannot_pass_as_swe_tool_budget(self):
        plan = deepcopy(self.plan)
        plan['planning_counts']['core_actor_decisions_upper'] = 16000
        with self.assertRaisesRegex(ValueError, 'core_actor_arithmetic'):
            module.check_design(plan, self.source)

    def test_accidental_paid_release_or_hidden_test_access_is_rejected(self):
        for field in ['paid', 'hidden']:
            with self.subTest(field=field):
                plan = deepcopy(self.plan)
                if field == 'paid':
                    plan['design']['paid_execution_enabled'] = True
                else:
                    plan['design']['benchmark']['gold_and_test_patch_actor_visible'] = True
                with self.assertRaises(ValueError):
                    module.check_design(plan, self.source)

    def test_missing_status_and_research_question_are_rejected(self):
        for text in [self.source.replace('No new SWE-bench experiments', 'Completed experiments'),
                     self.source.replace('\\RQ{4}', '\\RQ{9}')]:
            with self.assertRaises(ValueError):
                module.check_design(self.plan, text)

    def test_reviewer_effort_downgrade_or_old_static_count_is_rejected(self):
        for field in ['effort','count']:
            plan=deepcopy(self.plan)
            if field=='effort':plan['design']['models']['monitor_reasoning_effort']='high'
            else:plan['planning_counts']['study2_static_jobs']=960
            with self.assertRaises(ValueError):module.check_design(plan,self.source)


if __name__ == '__main__':
    unittest.main()
