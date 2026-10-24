"""A user stop boundary must not prepare the following paid round."""
import unittest
from experiments.triad_repaired_v7.tests import test_workflow as workflow
from experiments.triad_repaired_v7.contracts import ContractError
from experiments.triad_repaired_v7 import coordinator as coordination


class AdvanceBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.helper = workflow.WorkflowTests()
        self.helper.setUp()

    def tearDown(self):
        self.helper.tearDown()

    def test_boundary_retains_feedback_and_stops_before_next_dispatch(self):
        coordinator, backend = self.helper.make()
        job = coordinator.advance(through_round=1)
        self.assertEqual(job["status"], "awaiting_codex_reviewer")
        self.helper.service(coordinator, job)
        result = coordinator.advance(through_round=1)
        if result["status"] == "live_round_gate_passed":
            result = coordinator.advance(through_round=1)
        self.assertEqual(result["status"], "stopped_at_round_boundary")
        self.assertEqual(result["next"]["round"], 2)
        self.assertTrue(all(row["round"] == 1 for row in backend.calls))
        engine = coordinator._engine(coordinator.selected_cases[0])
        self.assertEqual(len(engine._previous_outcomes()), 8)
        self.assertFalse(list((engine.directory / "rounds/r02").glob("*")))

    def test_invalid_boundary_cannot_start_a_paid_stage(self):
        coordinator, backend = self.helper.make()
        for value in (True, 0, 13, "8", 8.5):
            with self.assertRaises(ContractError):
                coordinator.advance(through_round=value)
        self.assertEqual(backend.calls, [])

    def test_declared_stopped_source_metadata_resolves_without_backend(self):
        config = coordination.read_json(coordination.HERE / "config.json")
        plan = coordination.continuation_plan(config)
        old = coordination.read_json(config["continuation"]["source_out"] + "/plan.json")
        self.assertEqual(plan["cases"], old["cases"])
        self.assertEqual(plan["continuation"]["new_paid_rounds"], [9, 10, 11, 12])
        self.assertFalse(plan["fresh_memory"])
