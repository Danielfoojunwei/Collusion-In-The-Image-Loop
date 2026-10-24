"""Offline transport amendment checks; never send or reserve a request."""
from copy import deepcopy
import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("tested_pressure_xmp_codec", ROOT / "scripts/pressure_png_xmp_v2.py")
codec = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(codec)


def chunk(kind, value):
    return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value) & 0xffffffff)


def png(*, mode="RGBA", xmp=None, extra_kind=None, extra_payload=None):
    image = Image.new(mode, (32, 32), (30, 80, 150, 96) if mode == "RGBA" else (30, 80, 150))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    data = buf.getvalue()
    end = 8 + 12 + struct.unpack(">I", data[8:12])[0]
    addition = b""
    if xmp is not None:
        addition += chunk(b"iTXt", codec._XMP_HEADER + xmp)
    if extra_kind is not None:
        addition += chunk(extra_kind, extra_payload)
    return data[:end] + addition + data[end:]


def payload(*sources):
    return {"model": "z-ai/glm-5.3-flash", "provider": {"only": ["open-inference/fp4"], "allow_fallbacks": False},
            "messages": [{"role": "system", "content": "Exact test instructions"}, {"role": "user", "content": [
                {"type": "text", "text": "Observed artifacts"},
                *[{"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(source).decode(), "detail": "high"}}
                  for source in sources]]}], "max_tokens": 4096}


class PressureXmpCodecTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.out = Path(self.temp.name)
        self.xmp = b'<?xpacket begin=""?><x:xmpmeta xmlns:x="adobe:ns:meta/">fixture</x:xmpmeta>'

    def tearDown(self):
        self.temp.cleanup()

    def prepare(self, logical):
        return codec.prepare_wire(self.out, "offline.fixture.step2", "/chat/completions", logical)

    def test_exact_XMP_pixels_alpha_and_mixed_legacy_image(self):
        original = png(xmp=self.xmp)
        logical = payload(original, png(mode="RGB"))
        saved = deepcopy(logical)
        wire, mapping = self.prepare(logical)
        self.assertEqual(logical, saved)
        self.assertEqual(mapping["version"], 2)
        self.assertEqual(mapping["images"][0]["xmp_sha256"], hashlib.sha256(self.xmp).hexdigest())
        self.assertEqual(mapping["images"][0]["xmp_byte_count"], len(self.xmp))
        self.assertIsNone(mapping["images"][1]["xmp_sha256"])
        self.assertEqual(mapping["images"][1]["xmp_byte_count"], 0)
        reconstructed = codec.verify_wire(self.out, "/chat/completions", logical, mapping)
        self.assertEqual(wire, reconstructed)
        url = wire["messages"][1]["content"][1]["image_url"]["url"]
        raw = base64.b64decode(url.split(",", 1)[1])
        with Image.open(io.BytesIO(original)) as first, Image.open(io.BytesIO(raw)) as second:
            self.assertEqual(first.convert("RGBA").tobytes(), second.convert("RGBA").tobytes())
            self.assertEqual(first.size, second.size)
            self.assertEqual(second.info["xmp"], self.xmp)
        self.assertEqual(wire["provider"], logical["provider"])
        self.assertEqual(wire["messages"][0], logical["messages"][0])

    def test_metadata_free_and_images_endpoint_delegate_exact_original_mapping(self):
        for endpoint, logical in (("/chat/completions", payload(png())),
                                  ("/chat/completions", payload()),
                                  ("/images", {"model": "qwen/qwen-image-2.1-turbo", "prompt": "Unchanged PNG reference", "input_references": []})):
            with self.subTest(endpoint=endpoint):
                old_wire, old_map = codec.base.prepare_wire(self.out, "offline.original", endpoint, logical)
                new_wire, new_map = codec.prepare_wire(self.out, "offline.original", endpoint, logical)
                self.assertEqual((new_wire, new_map), (old_wire, old_map))
                self.assertEqual(new_map["version"], 1)
                self.assertEqual(codec.verify_wire(self.out, endpoint, logical, old_map), old_wire)

    def test_original_codec_remains_pinned_and_still_rejects_XMP(self):
        self.assertEqual(hashlib.sha256((ROOT / "scripts/lossless_wire_codec.py").read_bytes()).hexdigest(), codec._BASE_SHA256)
        with self.assertRaises(codec.WireEncodingError):
            codec.base.prepare_wire(self.out, "offline.original", "/chat/completions", payload(png(xmp=self.xmp)))

    def test_real_first_provider_PNG_roundtrip(self):
        source = ROOT / "runs/main/pressure_interview_live/provider_evidence/blobs/3261793ad855cf89885d5a2df99e747d1a46d04d064538111c0e6ded61358d98.png"
        if not source.exists():
            self.skipTest("Actual preserved provider PNG is not present in this checkout")
        raw = source.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        logical = payload(raw)
        wire, mapping = self.prepare(logical)
        self.assertEqual(codec.verify_wire(self.out, "/chat/completions", logical, mapping), wire)
        self.assertEqual(source.read_bytes(), raw)
        self.assertEqual(sha, "3261793ad855cf89885d5a2df99e747d1a46d04d064538111c0e6ded61358d98")
        self.assertEqual(mapping["images"][0]["xmp_byte_count"], 608)
        self.assertEqual(mapping["images"][0]["xmp_sha256"], "f8115d9ad66d68de705329ac8f5be1206ddd2165ef3119a781f2798e8248594b")
        self.assertLess(mapping["wire_body_utf8_bytes"], 10 * 1024 * 1024)

    def test_other_metadata_color_and_alternate_itxt_are_rejected(self):
        for kind, value in ((b"iCCP", b"profile\0\0" + zlib.compress(b"fixture")),
                            (b"gAMA", struct.pack(">I", 45455)), (b"sRGB", b"\0"),
                            (b"tEXt", b"other\0fixture"), (b"iTXt", b"other\0\0\0\0\0fixture"),
                            (b"iTXt", b"XML:com.adobe.xmp\0\1\0\0\0" + zlib.compress(self.xmp)),
                            (b"iTXt", b"XML:com.adobe.xmp\0\0\0en\0\0" + self.xmp),
                            (b"iTXt", b"XML:com.adobe.xmp\0\0\0\0translated\0" + self.xmp)):
            with self.subTest(kind=kind, value=value[:30]):
                with self.assertRaises(codec.WireEncodingError):
                    self.prepare(payload(png(extra_kind=kind, extra_payload=value)))

    def test_malformed_crc_duplicate_XMP_and_trailing_bytes_are_rejected(self):
        raw = png(xmp=self.xmp)
        broken_crc = bytearray(raw)
        broken_crc[45] ^= 1
        bads = [bytes(broken_crc), raw + b"tail",
                png(xmp=self.xmp, extra_kind=b"iTXt", extra_payload=codec._XMP_HEADER + self.xmp)]
        for bad in bads:
            with self.subTest(length=len(bad)):
                with self.assertRaises(codec.WireEncodingError):
                    self.prepare(payload(bad))

    def test_mapping_or_logical_tamper_is_rejected(self):
        logical = payload(png(xmp=self.xmp))
        _, mapping = self.prepare(logical)
        for key, changed in (("xmp_byte_count", 1), ("xmp_sha256", "f" * 64),
                             ("rgba_sha256", "f" * 64), ("source_sha256", "f" * 64),
                             ("wire_mode", "RGB"), ("frame_count", 2), ("message_index", 0)):
            with self.subTest(key=key):
                bad = deepcopy(mapping)
                bad["images"][0][key] = changed
                with self.assertRaises(codec.WireEncodingError):
                    codec.verify_wire(self.out, "/chat/completions", logical, bad)
        bad = deepcopy(mapping)
        bad["encoder"]["metadata_comparison"] = "Changed policy"
        with self.assertRaises(codec.WireEncodingError):
            codec.verify_wire(self.out, "/chat/completions", logical, bad)
        altered = deepcopy(logical)
        altered["max_tokens"] = 10
        with self.assertRaises(codec.WireEncodingError):
            codec.verify_wire(self.out, "/chat/completions", altered, mapping)

    def test_wire_XMP_must_match_even_if_wire_digest_is_rebound(self):
        logical = payload(png(xmp=self.xmp))
        _, mapping = self.prepare(logical)
        source = png(xmp=self.xmp.replace(b"fixture", b"changed"))
        encoded = codec._encode(source, "offline.changed")[0]
        sha = hashlib.sha256(encoded).hexdigest()
        path = self.out / "transport_blobs" / (sha + ".webp")
        path.write_bytes(encoded)
        bad = deepcopy(mapping)
        bad["images"][0].update({"wire_sha256": sha, "wire_blob": "transport_blobs/" + sha + ".webp", "wire_byte_count": len(encoded)})
        with self.assertRaisesRegex(codec.WireEncodingError, "XMP"):
            codec.verify_wire(self.out, "/chat/completions", logical, bad)

    def test_verify_uses_stored_wire_without_recompression(self):
        logical = payload(png(xmp=self.xmp))
        expected, mapping = self.prepare(logical)
        with patch.object(codec, "_encode", side_effect=AssertionError("Verification recompressed")):
            self.assertEqual(codec.verify_wire(self.out, "/chat/completions", logical, mapping), expected)

    def test_strict_whole_JSON_size_guard_precedes_blob_writes(self):
        logical = payload(png(xmp=self.xmp))
        expected, _ = self.prepare(logical)
        size = len(codec.base._body(expected))
        empty = self.out / "empty"
        with self.assertRaises(codec.WireEncodingError):
            codec.prepare_wire(empty, "offline.limit", "/chat/completions", logical, max_bytes=size)
        self.assertFalse(empty.exists())
        _, mapping = codec.prepare_wire(empty, "offline.limit", "/chat/completions", logical, max_bytes=size + 1)
        self.assertEqual(mapping["wire_body_utf8_bytes"], size)
        self.assertEqual(codec.verify_wire(empty, "/chat/completions", logical, mapping), expected)

    def test_wire_blob_or_symlink_tamper_is_rejected(self):
        logical = payload(png(xmp=self.xmp))
        _, mapping = self.prepare(logical)
        entry = mapping["images"][0]
        file = self.out / entry["wire_blob"]
        original = file.read_bytes()
        file.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
        with self.assertRaises(codec.WireEncodingError):
            codec.verify_wire(self.out, "/chat/completions", logical, mapping)
        file.unlink()
        actual = self.out / "actual.webp"
        actual.write_bytes(original)
        file.symlink_to(actual)
        with self.assertRaises(codec.WireEncodingError):
            codec.verify_wire(self.out, "/chat/completions", logical, mapping)


if __name__ == "__main__":
    unittest.main()
