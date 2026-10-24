"""Administrative-only tests; never dispatch real CLI processes or APIs."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("administrative_driver_v2_under_test", HERE / "administrative_driver_v2.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def evidence(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"body": body, "sha256": m.sha(m.canonical(body).encode())}))


class AdministrativeDriverV2Tests(unittest.TestCase):
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
        statuses = {arm: 0 for arm in m.ARMS}
        self.assertEqual(m.matched_round(statuses), 1)
        statuses["b01"] = 1
        self.assertEqual(m.matched_round(statuses), 1)
        for arm in statuses: statuses[arm] = 1
        self.assertEqual(m.matched_round(statuses), 2)
        statuses["b01"] = 3
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

    def test_first_main_assessment_resumes_while_other_four_producers_are_active_once(self):
        driver = m.Driver(self.out)
        (self.out / "driver" / "START_ALLOWED.json").write_text("{}")
        job = "b01.r01.a02.MAIN"
        packet = self.out / "reviews" / job / "public" / "packet.json"
        evidence(packet, {"kind": "MAIN", "instructions": "OFFLINE fixture", "schema": {}})
        (packet.parent.parent / "assessment.json").write_text("{}")
        ready = {"arm": "b01", "committed_rounds": 0, "round": 1, "attempt": 2,
                 "stage": "MAIN_REVIEW", "status": "awaiting_review", "job_id": job,
                 "public_packet": str(packet)}
        self.mock_runner.status.return_value = {"arms": [ready]}
        for arm in m.ARMS[1:]:
            process = Mock(); process.poll.return_value = None
            driver.active[arm] = {"process": process, "record": {"arm": arm, "round": 1}}
        resumed = Mock(); resumed.pid = 99999999; resumed.poll.return_value = None
        with patch.object(m, "producer_busy", return_value=False), patch.object(m.subprocess, "Popen", return_value=resumed) as popen:
            self.assertFalse(driver.tick())
            self.assertEqual(len(driver.active), 5)
            popen.assert_called_once()
            command = popen.call_args.args[0]
            self.assertEqual(command[command.index("--arm") + 1], "b01")
            self.assertEqual(command[command.index("--through-round") + 1], "1")
            self.mock_runner.status.assert_called_once_with("b01")
            self.assertFalse(driver.tick())
            popen.assert_called_once()  # No duplicate dispatch while all five run.
        driver.active["b01"]["stream"].close()
        self.assertEqual(driver.round, 1)
        self.assertEqual(driver.checkpoint_counts, {arm: 0 for arm in m.ARMS})

    def test_next_round_requires_every_checkpoint_even_when_statuses_are_missing(self):
        driver = m.Driver(self.out)
        (self.out / "driver" / "START_ALLOWED.json").write_text("{}")
        directory = self.out / "arms" / "b01" / "checkpoints"
        directory.mkdir(parents=True); (directory / "r01.json").write_text("OFFLINE filename fixture")
        driver.statuses["b01"] = {"arm": "b01", "committed_rounds": 1, "round": 2, "status": "ready"}
        for arm in m.ARMS[1:]:
            process = Mock(); process.poll.return_value = None
            driver.active[arm] = {"process": process, "record": {"arm": arm, "round": 1}}
        with patch.object(driver, "dispatch") as dispatch:
            self.assertFalse(driver.tick())
            dispatch.assert_not_called()
            self.assertEqual(driver.round, 1)
            # Atomic checkpoint presence alone releases the matched-round barrier.
            for arm in m.ARMS[1:]:
                directory = self.out / "arms" / arm / "checkpoints"
                directory.mkdir(parents=True); (directory / "r01.json").write_text("OFFLINE filename fixture")
            self.assertFalse(driver.tick())
            self.assertEqual(driver.round, 2)
            dispatch.assert_called_once_with("b01", 2)

    def test_checkpoint_barrier_reads_filenames_and_rejects_gaps(self):
        directory = self.out / "arms" / "b01" / "checkpoints"
        directory.mkdir(parents=True)
        (directory / "r01.json").write_text("OFFLINE deliberately not a checkpoint body")
        with patch.object(Path, "read_text", side_effect=AssertionError("Barrier must not inspect checkpoint content")):
            self.assertEqual(m.checkpoint_counts(self.out)["b01"], 1)
        (directory / "r03.json").write_text("OFFLINE gap fixture")
        with self.assertRaises(ValueError): m.checkpoint_counts(self.out)

    def test_recovered_arm_is_skipped_before_status_lookup_and_cannot_dispatch_twice(self):
        driver = m.Driver(self.out)
        (self.out / "driver" / "START_ALLOWED.json").write_text("{}")
        record = {"arm": "b01", "pid": 123, "pid_start_ticks": "ticks"}
        driver.recovered = [(self.out / "driver" / "invocations" / "prior.json", record)]
        for arm in m.ARMS[1:]:
            process = Mock(); process.poll.return_value = None
            driver.active[arm] = {"process": process, "record": {"arm": arm, "round": 1}}
        with patch.object(m, "process_ticks", return_value="ticks"), patch.object(m.subprocess, "Popen") as popen:
            self.assertFalse(driver.tick())
            driver.dispatch("b01", 1)
            popen.assert_not_called()
        self.mock_runner.status.assert_not_called()

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
