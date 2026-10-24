"""Offline pixel/hash/immutability regressions; no paid calls or real ledgers."""
from copy import deepcopy
import base64
import hashlib
import io
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from PIL import Image, PngImagePlugin

import lossless_wire_codec as codec
from lossless_wire_codec import MAX_WIRE_BYTES, WireEncodingError, prepare_wire, verify_wire
from visual_collusion.common import canonical, digest


ENDPOINT = '/chat/completions'


def png(mode='RGB'):
    image = Image.new(mode, (17, 13))
    for y in range(image.height):
        for x in range(image.width):
            rgb = ((x * 29 + y * 3) % 256, (x * 7 + y * 41) % 256, (x * 53 + y * 17) % 256)
            image.putpixel((x, y), rgb + ((0 if (x + y) % 3 == 0 else (x * 19 + y * 23) % 256),) if mode == 'RGBA' else rgb)
    output = io.BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()


def block(data):
    return {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(data).decode(), 'detail': 'high'}}


def payload(images):
    return {
        'model': 'locked-model',
        'provider': {'only': ['locked-route'], 'allow_fallbacks': False, 'max_price': {'prompt': 0.15}},
        'max_tokens': 4096, 'reasoning': {'effort': 'high', 'exclude': False},
        'response_format': {'type': 'json_schema', 'json_schema': {'name': 'experiment_response', 'strict': True, 'schema': {'type': 'object'}}},
        'messages': [{'role': 'system', 'content': 'Exact policy — unchanged.'},
                     {'role': 'user', 'content': [{'type': 'text', 'text': 'Before image'}, *[block(data) for data in images], {'type': 'text', 'text': 'After image'}]}],
    }


class LosslessWireCodecTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.out = Path(temporary.name)

    def prepare(self, logical):
        return prepare_wire(self.out, 'episode.fixture:round1:A:step1', ENDPOINT, logical)

    def test_rgb_preserves_logical_payload_all_fields_and_hashes(self):
        source = png()
        logical = payload([source])
        original = deepcopy(logical)
        wire, mapping = self.prepare(logical)
        self.assertEqual(logical, original)
        restored = deepcopy(wire)
        restored['messages'][1]['content'][1]['image_url']['url'] = logical['messages'][1]['content'][1]['image_url']['url']
        self.assertEqual(restored, logical)
        self.assertEqual(mapping['logical_request_hash'], digest({'endpoint': ENDPOINT, 'payload': logical}))
        self.assertEqual(mapping['wire_request_hash'], digest({'endpoint': ENDPOINT, 'payload': wire}))
        self.assertEqual(mapping['wire_body_sha256'], hashlib.sha256(canonical(wire).encode()).hexdigest())
        self.assertEqual(mapping['wire_body_utf8_bytes'], len(canonical(wire).encode()))
        self.assertLess(mapping['wire_body_utf8_bytes'], MAX_WIRE_BYTES)
        self.assertEqual(verify_wire(self.out, ENDPOINT, logical, mapping), wire)
        self.assertEqual(list(self.out.glob('*.png')), [])
        self.assertEqual(len(list((self.out / 'transport_blobs').glob('*.webp'))), 1)

    def test_rgba_retains_nonzero_hidden_rgb_under_zero_alpha(self):
        source = png('RGBA')
        logical = payload([source])
        wire, mapping = self.prepare(logical)
        encoded = wire['messages'][1]['content'][1]['image_url']['url'].split(',', 1)[1]
        with Image.open(io.BytesIO(source)) as before, Image.open(io.BytesIO(base64.b64decode(encoded))) as after:
            self.assertEqual(before.convert('RGBA').tobytes(), after.convert('RGBA').tobytes())
            self.assertEqual(before.size, after.size)
            self.assertEqual(before.getpixel((3, 0))[3], 0)
            self.assertNotEqual(before.getpixel((3, 0))[:3], (0, 0, 0))
            self.assertEqual(before.getpixel((3, 0)), after.getpixel((3, 0)))
        self.assertEqual(mapping['images'][0]['mode'], 'RGBA')
        self.assertEqual(verify_wire(self.out, ENDPOINT, logical, mapping), wire)

    def test_repeated_images_keep_order_and_positions_and_deduplicate_blobs(self):
        rgb, rgba = png(), png('RGBA')
        logical = payload([rgb, rgba, rgb])
        logical['messages'].append({'role': 'assistant', 'content': [block(rgba)]})
        wire, mapping = self.prepare(logical)
        self.assertEqual([(x['message_index'], x['content_index']) for x in mapping['images']], [(1, 1), (1, 2), (1, 3), (2, 0)])
        self.assertEqual(mapping['images'][0]['wire_sha256'], mapping['images'][2]['wire_sha256'])
        self.assertEqual(mapping['images'][1]['wire_sha256'], mapping['images'][3]['wire_sha256'])
        self.assertEqual(len(list((self.out / 'transport_blobs').glob('*.webp'))), 2)
        self.assertEqual(verify_wire(self.out, ENDPOINT, logical, mapping), wire)

    def test_preparation_is_repeatable_and_verifier_never_recompresses(self):
        codec._encode.cache_clear()
        logical = payload([png('RGBA')])
        first = self.prepare(logical)
        self.assertEqual(self.prepare(logical), first)
        self.assertEqual(codec._encode.cache_info().misses, 1)
        self.assertEqual(codec._encode.cache_info().hits, 1)
        modified = deepcopy(first[1])
        modified['images'][0]['size'][0] = 1
        self.assertEqual(self.prepare(logical), first)
        with patch.object(Image.Image, 'save', side_effect=AssertionError('Verifier must not encode')):
            self.assertEqual(verify_wire(self.out, ENDPOINT, logical, first[1]), first[0])

    def test_images_endpoint_is_exact_identity_including_png_reference(self):
        logical = {
            'model': 'locked-image-model', 'prompt': 'Exact prompt',
            'provider': {'only': ['alibaba'], 'allow_fallbacks': False},
            'input_references': [block(png())], 'resolution': '1K',
        }
        original = deepcopy(logical)
        wire, mapping = prepare_wire(self.out, 'fixture:image1', '/images', logical)
        self.assertEqual(logical, original)
        self.assertEqual(wire, original)
        self.assertEqual(mapping['images'], [])
        self.assertEqual(mapping['logical_request_hash'], mapping['wire_request_hash'])
        self.assertEqual(verify_wire(self.out, '/images', logical, mapping), logical)
        self.assertEqual(list(self.out.iterdir()), [])
        with self.assertRaises(WireEncodingError):
            prepare_wire(self.out, 'fixture:image1', '/images', logical,
                         max_bytes=mapping['wire_body_utf8_bytes'])

    def test_tampered_blob_or_mapping_fails_closed(self):
        logical = payload([png()])
        wire, mapping = self.prepare(logical)
        for key, changed in [('logical_request_hash', '0' * 64), ('wire_request_hash', '0' * 64), ('wire_body_sha256', '0' * 64), ('wire_body_utf8_bytes', 1)]:
            altered = deepcopy(mapping)
            altered[key] = changed
            with self.subTest(key=key), self.assertRaises(WireEncodingError):
                verify_wire(self.out, ENDPOINT, logical, altered)
        for key, changed in [('content_index', 2), ('source_sha256', '0' * 64), ('rgba_sha256', '0' * 64), ('size', [1, 1]), ('size', [17.0, 13.0]), ('wire_sha256', None), ('wire_byte_count', True), ('wire_blob', '../outside.webp')]:
            altered = deepcopy(mapping)
            altered['images'][0][key] = changed
            with self.subTest(key=key), self.assertRaises(WireEncodingError):
                verify_wire(self.out, ENDPOINT, logical, altered)
        blob = self.out / mapping['images'][0]['wire_blob']
        blob.write_bytes(blob.read_bytes()[:-1] + b'X')
        with self.assertRaises(WireEncodingError):
            verify_wire(self.out, ENDPOINT, logical, mapping)

    def test_reordered_mapping_and_changed_text_or_provider_fail(self):
        logical = payload([png(), png('RGBA')])
        _, mapping = self.prepare(logical)
        altered = deepcopy(mapping)
        altered['images'].reverse()
        with self.assertRaises(WireEncodingError):
            verify_wire(self.out, ENDPOINT, logical, altered)
        for change in ('text', 'provider'):
            changed = deepcopy(logical)
            if change == 'text':
                changed['messages'][0]['content'] += ' Changed'
            else:
                changed['provider']['only'] = ['other-route']
            with self.subTest(change=change), self.assertRaises(WireEncodingError):
                verify_wire(self.out, ENDPOINT, changed, mapping)

    def test_exact_complete_wire_size_boundary_rejects_before_blob_writes(self):
        logical = payload([png()])
        wire, mapping = self.prepare(logical)
        with tempfile.TemporaryDirectory() as other:
            with self.assertRaises(WireEncodingError):
                prepare_wire(other, 'fixture', ENDPOINT, logical, max_bytes=mapping['wire_body_utf8_bytes'])
            self.assertEqual(list(Path(other).iterdir()), [])
            prepared, _ = prepare_wire(other, 'fixture', ENDPOINT, logical, max_bytes=mapping['wire_body_utf8_bytes'] + 1)
            self.assertEqual(prepared, wire)

    def test_large_nonimage_body_cannot_bypass_total_size_limit(self):
        logical = payload([])
        logical['messages'][0]['content'] = 'x' * MAX_WIRE_BYTES
        with self.assertRaises(WireEncodingError):
            self.prepare(logical)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_rejects_color_profiles_text_and_unknown_png_metadata(self):
        image = Image.new('RGB', (4, 4), (10, 20, 30))
        for metadata in ({'icc_profile': b'opaque-profile'}, {'gamma': 0.45455}):
            stream = io.BytesIO()
            if 'gamma' in metadata:
                info = PngImagePlugin.PngInfo()
                info.add(b'gAMA', struct.pack('>I', 45455))
                image.save(stream, format='PNG', pnginfo=info)
            else:
                image.save(stream, format='PNG', **metadata)
            with self.subTest(metadata=metadata.keys()), self.assertRaises(WireEncodingError):
                self.prepare(payload([stream.getvalue()]))
        stream = io.BytesIO()
        info = PngImagePlugin.PngInfo()
        info.add_text('description', 'context')
        image.save(stream, format='PNG', pnginfo=info)
        with self.assertRaises(WireEncodingError):
            self.prepare(payload([stream.getvalue()]))
        source = png()
        kind, value = b'vpAg', b'unknown-context'
        chunk = struct.pack('>I', len(value)) + kind + value + struct.pack('>I', zlib.crc32(kind + value) & 0xffffffff)
        unknown = source[:-12] + chunk + source[-12:]
        with self.assertRaises(WireEncodingError):
            self.prepare(payload([unknown]))
        self.assertEqual(list(self.out.iterdir()), [])

    def test_rejects_non_png_palette_animation_and_symlink_blob(self):
        logical = payload([png()])
        logical['messages'][1]['content'][1]['image_url']['url'] = 'https://example.invalid/image.png'
        with self.assertRaises(WireEncodingError):
            self.prepare(logical)
        stream = io.BytesIO()
        Image.new('P', (4, 4)).save(stream, format='PNG')
        with self.assertRaises(WireEncodingError):
            self.prepare(payload([stream.getvalue()]))
        stream = io.BytesIO()
        Image.new('RGB', (4, 4), 'red').save(stream, format='PNG', save_all=True, append_images=[Image.new('RGB', (4, 4), 'blue')], duration=10)
        with self.assertRaises(WireEncodingError):
            self.prepare(payload([stream.getvalue()]))
        logical = payload([png()])
        _, mapping = self.prepare(logical)
        blob = self.out / mapping['images'][0]['wire_blob']
        original = blob.read_bytes()
        blob.unlink()
        target = self.out / 'other.webp'
        target.write_bytes(original)
        blob.symlink_to(target)
        with self.assertRaises(WireEncodingError):
            verify_wire(self.out, ENDPOINT, logical, mapping)


if __name__ == '__main__':
    unittest.main()
