"""Administrative-only tests; never dispatch real CLI processes or APIs."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("administrative_driver_under_test", HERE / "administrative_driver.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def evidence(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"body": body, "sha256": m.sha(m.canonical(body).encode())}))


class AdministrativeDriverTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=HERE)
        self.out = Path(self.temporary.name)
        order = [{"round": n, "dispatch_order": list(m.ARMS)} for n in range(1, 13)]
        evidence(self.out / "DISPATCH_ORDER.json", {"order": order})
        self.mock_runner = Mock()
        self.patcher = patch.object(m, "Runner", return_value=self.mock_runner)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.temporary.cleanup()

    def test_start_gate_does_not_inspect_or_dispatch(self):
        driver = m.Driver(self.out)
        with patch.object(driver, "dispatch") as dispatch, patch.object(m, "producer_busy") as busy:
            self.assertFalse(driver.tick())
            dispatch.assert_not_called()
            busy.assert_not_called()
        self.mock_runner.status.assert_not_called()
        self.assertEqual(json.loads((self.out / "driver" / "STATE.json").read_text())["phase"], "waiting_for_start")

    def test_stop_before_start_never_dispatches(self):
        driver = m.Driver(self.out)
        (self.out / "STOP_REQUESTED.json").write_text("{}")
        with patch.object(driver, "dispatch") as dispatch:
            self.assertTrue(driver.tick())
            dispatch.assert_not_called()
        self.mock_runner.status.assert_not_called()

    def test_synchronous_round_selection_and_gap_rejection(self):
        statuses = {arm: {"committed_rounds": 0} for arm in m.ARMS}
        self.assertEqual(m.matched_round(statuses), 1)
        statuses["b01"]["committed_rounds"] = 1
        self.assertEqual(m.matched_round(statuses), 1)
        for value in statuses.values(): value["committed_rounds"] = 1
        self.assertEqual(m.matched_round(statuses), 2)
        statuses["b01"]["committed_rounds"] = 3
        with self.assertRaises(ValueError): m.matched_round(statuses)
        self.assertIsNone(m.matched_round({}))

    def test_predeclared_order_controls_dispatch(self):
        order = [{"round": n, "dispatch_order": list(reversed(m.ARMS))} for n in range(1, 13)]
        evidence(self.out / "DISPATCH_ORDER.json", {"order": order})
        driver = m.Driver(self.out)
        (self.out / "driver" / "START_ALLOWED.json").write_text("{}")
        driver.statuses = {arm: {"arm": arm, "committed_rounds": 0, "round": 1, "status": "ready"} for arm in m.ARMS}
        with patch.object(driver, "dispatch") as dispatch:
            self.assertFalse(driver.tick())
            self.assertEqual([call.args for call in dispatch.call_args_list], [(arm, 1) for arm in reversed(m.ARMS)])

    def test_queue_uses_exact_public_schema_and_instructions(self):
        driver = m.Driver(self.out)
        job = "b01.r01.a01.MAIN"
        path = self.out / "reviews" / job / "public" / "packet.json"
        packet = {"kind": "MAIN", "instructions": "Exact genuine instructions", "schema": {"type": "object"}}
        evidence(path, packet)
        driver.statuses["b01"] = {"arm": "b01", "round": 1, "attempt": 1, "committed_rounds": 0, "status": "awaiting_review", "job_id": job, "public_packet": str(path)}
        queue = driver.pending()
        self.assertEqual(queue[0]["instructions"], packet["instructions"])
        self.assertEqual(queue[0]["schema"], packet["schema"])
        self.assertEqual(queue[0]["packet_sha256"], m.sha(m.canonical(packet).encode()))
        (path.parent.parent / "assessment.json").write_text("{}")
        self.assertEqual(driver.pending(), [])

    def test_return_two_freezes_new_dispatches_without_terminating_children(self):
        driver = m.Driver(self.out)
        log = self.out / "driver" / "example.log"
        log.write_text('{"status":"held","code":"permanent"}\n')
        process = Mock()
        process.poll.return_value = 2
        record = {"sequence": 1, "arm": "b01", "pid": 123, "log_path": str(log)}
        driver.active["b01"] = {"process": process, "stream": Mock(), "record": record, "record_path": self.out / "driver" / "invocations" / "1.json"}
        other = Mock()
        other.poll.return_value = None
        driver.active["b02"] = {"process": other}
        driver.poll_children()
        self.assertIsNotNone(driver.hold)
        self.assertIn("b02", driver.active)
        other.terminate.assert_not_called()
        other.kill.assert_not_called()
        with patch.object(m.subprocess, "Popen") as popen:
            driver.dispatch("b03", 1)
            popen.assert_not_called()

    def test_funding_barrier_remains_safe_to_resume(self):
        driver = m.Driver(self.out)
        log = self.out / "driver" / "barrier.log"
        log.write_text('{"status":"funding_barrier","blocked_request_reserved":false}\n')
        process = Mock()
        process.poll.return_value = 0
        record = {"sequence": 1, "arm": "b01", "pid": 123, "log_path": str(log)}
        driver.active["b01"] = {"process": process, "stream": Mock(), "record": record, "record_path": self.out / "driver" / "invocations" / "1.json"}
        driver.poll_children()
        self.assertIsNone(driver.hold)
        self.assertEqual(record["final_output"]["status"], "funding_barrier")

    def test_final_json_keeps_actual_last_pretty_output(self):
        raw = '{"event":"stage_complete"}\n' + json.dumps({"status": "in_progress", "arms": [{"arm": "b01"}]}, indent=2) + "\n"
        self.assertEqual(m.final_json(raw)["status"], "in_progress")
        self.assertIsNone(m.final_json("incomplete {"))

    def test_project_root_and_dispatch_plan_tamper_detection(self):
        self.assertEqual(m.PROJECT, Path("/workspace/visual_collusion_stress_v4"))
        data = json.loads((self.out / "DISPATCH_ORDER.json").read_text())
        data["body"]["order"][0]["dispatch_order"][0] = "b05"
        (self.out / "DISPATCH_ORDER.json").write_text(json.dumps(data))
        with self.assertRaises(m.IntegrityError): m.read_plan(self.out)


if __name__ == "__main__": unittest.main()
