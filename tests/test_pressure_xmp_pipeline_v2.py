"""XMP amendment plumbing checks only; all model responses are test fixtures."""
import base64
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, PngImagePlugin

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import pressure_png_xmp_v2 as codec
from experiments.pressure_interview_v1 import live_provider
from experiments.pressure_interview_v1.tests.test_live_pipeline_audit import LivePipelineAuditTests
from experiments.pressure_interview_v1.tests.test_live_provider import ScriptedWireTransport
from experiments.pressure_interview_v1.planning import canonical, load_config
from visual_collusion.archive import store_pixels
from visual_collusion.budget import BudgetLedger


def xmp_fixture():
    image = Image.new("RGBA", (1024, 1024), (30, 120, 210, 235))
    metadata = PngImagePlugin.PngInfo()
    metadata.add_itxt("XML:com.adobe.xmp", '<x:xmpmeta xmlns:x="adobe:ns:meta/">OFFLINE_FIXTURE</x:xmpmeta>')
    stream = io.BytesIO()
    image.save(stream, format="PNG", pnginfo=metadata)
    return stream.getvalue()


class XmpPipelineTests(unittest.TestCase):
    def test_amended_analysis_binds_XMP_wire_to_actual_receipt(self):
        from run_pressure_xmp_v2 import build_analysis_verifier
        verify = build_analysis_verifier(codec)
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            evidence = out / "provider_evidence"
            config = load_config()
            ledger = BudgetLedger(out / "aggregate.sqlite3", 40)
            config["budget"]["aggregate_ledger"] = str(ledger.path)
            original = xmp_fixture()
            payload = {"model": "z-ai/glm-5.3-flash", "messages": [{"role": "user", "content": [
                {"type": "text", "text": "Offline wire-verification fixture."},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(original).decode()}}
            ]}]}
            wire, mapping = codec.prepare_wire(evidence, "fixture.xmp", "/chat/completions", payload)
            self.assertEqual(mapping["version"], 2)
            request_hash = hashlib.sha256(canonical({"endpoint": "/chat/completions",
                "wire_body_sha256": hashlib.sha256(canonical(wire).encode()).hexdigest()}).encode()).hexdigest()
            identifier = config["budget"]["namespace"] + ":fixture.xmp"
            ledger.reserve(identifier, "chat", payload["model"], request_hash, .1)
            ledger.complete(identifier, .01, {"fixture": True}, {"request_id": identifier,
                "request_hash": request_hash, "kind": "chat", "requested_model": payload["model"], "cost_usd": .01})
            logical = json.loads(json.dumps(payload))
            logical["messages"][0]["content"][1]["image_url"]["url"] = store_pixels(evidence, original)
            folder = evidence / "transport_requests"
            folder.mkdir()
            record = {"request_id": "fixture.xmp", "endpoint": "/chat/completions", "logical_payload": logical, "mapping": mapping}
            path = folder / "fixture.json"
            path.write_text(json.dumps(record))
            self.assertEqual(verify(out, config)["paid_intents"], 1)
            record["mapping"]["images"][0]["xmp_sha256"] = "0" * 64
            path.write_text(json.dumps(record))
            with self.assertRaises(Exception):
                verify(out, config)

    def test_actual_backend_guard_engine_branches_and_neutrals_with_XMP(self):
        # Reuse the full independently audited 118-request / 32-C-job fixture,
        # changing only the simulated image service's actual PNG bytes.
        original_init = ScriptedWireTransport.__init__
        observed_posts = []
        original_call = ScriptedWireTransport.__call__

        def initialize(transport, *args, **kwargs):
            original_init(transport, *args, **kwargs)
            transport.image_bytes = xmp_fixture()

        def observe(transport, method, endpoint, payload):
            result = original_call(transport, method, endpoint, payload)
            if method == "POST":
                observed_posts.append((endpoint, hashlib.sha256(payload).hexdigest()))
            return result

        with patch.object(live_provider, "codec", codec), \
                patch.object(ScriptedWireTransport, "__init__", initialize), \
                patch.object(ScriptedWireTransport, "__call__", observe):
            case = LivePipelineAuditTests("test_real_backend_engine_codex_packets_neutral_forks_and_resume")
            case.test_real_backend_engine_codex_packets_neutral_forks_and_resume()
        self.assertEqual(len(observed_posts), 118)
        self.assertEqual(sum(endpoint == "/images" for endpoint, _ in observed_posts), 34)


if __name__ == "__main__":
    unittest.main()
