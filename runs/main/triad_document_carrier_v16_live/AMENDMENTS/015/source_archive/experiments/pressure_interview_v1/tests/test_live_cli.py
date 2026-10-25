"""No live command may bypass the exact offline workflow gate."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.pressure_interview_v1 import live_cli
from experiments.pressure_interview_v1.planning import load_config


class WorkflowGateTests(unittest.TestCase):
    def _gate(self):
        value = json.dumps(load_config(), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
        return {"status": "passed", "source_files": {"engine.py": "bound-source"},
                "config_sha256": hashlib.sha256(value).hexdigest(), "live_compatibility_probe_calls": 0}

    def test_missing_gate_rejects_before_live_initialization(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(live_cli, "GATE", Path(tmp) / "missing.json"):
            with self.assertRaises(RuntimeError):
                live_cli.require_workflow_gate()

    def test_exact_source_and_configuration_gate_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gate.json"
            path.write_text(json.dumps(self._gate()))
            with patch.object(live_cli, "GATE", path), patch.object(live_cli, "source_files", return_value={"engine.py": "bound-source"}):
                self.assertEqual(live_cli.require_workflow_gate()["status"], "passed")

    def test_source_or_configuration_change_invalidates_gate(self):
        for key in ("source_files", "config_sha256"):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as tmp:
                gate = self._gate()
                gate[key] = {} if key == "source_files" else "changed"
                path = Path(tmp) / "gate.json"
                path.write_text(json.dumps(gate))
                with patch.object(live_cli, "GATE", path), patch.object(live_cli, "source_files", return_value={"engine.py": "bound-source"}):
                    with self.assertRaises(RuntimeError):
                        live_cli.require_workflow_gate()


if __name__ == "__main__":
    unittest.main()
