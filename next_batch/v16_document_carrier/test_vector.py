"""Offline safe analytical-SVG fixtures, never supplied to experimental actors."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from .vector import validate_svg, rasterize_svg, audit_svg_literal_text, ocr_literal_payload, _local_tool_environment

OFFLINE_SVG = '''<svg xmlns="http://www.w3.org/2000/svg" width="640" height="320" viewBox="0 0 640 320">
<title>PRIVATE_OFFLINE_SOURCE_DESCRIPTION</title>
<rect width="640" height="320" fill="white"/>
<text x="25" y="38" font-family="sans-serif" font-size="24" fill="#142c3c">OFFLINE analytical fixture</text>
<line x1="70" y1="260" x2="610" y2="260" stroke="#142c3c" stroke-width="2"/>
<rect x="100" y="150" width="80" height="110" fill="#557f98"/>
<rect x="230" y="110" width="80" height="150" fill="#557f98"/>
<rect x="360" y="80" width="80" height="180" fill="#557f98"/>
<rect x="490" y="180" width="80" height="80" fill="#557f98"/>
<g font-family="sans-serif" font-size="22" text-anchor="middle" fill="#142c3c">
<text x="140" y="294">P1</text><text x="270" y="294">P2</text>
<text x="400" y="294">P3</text><text x="530" y="294">P4</text></g>
</svg>'''


class SafeVectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='v16-vector-offline-')
        self.root = Path(self.temp.name)
    def tearDown(self):
        self.temp.cleanup()

    def test_hex_colours_are_not_scientific_notation_but_real_geometry_stays_bounded(self):
        from . import vector
        for colour in ('#2E7D32','#1e8a4c','#3D6E9E','#6B4E93','#e3e8ec','#2e7'):
            svg='<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20"><rect width="10" height="10" fill="'+colour+'" stroke="'+colour+'"/></svg>'
            self.assertTrue(vector.validate_svg(svg)['valid'],colour)
        huge='<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20"><rect width="2E7" height="10" fill="#2E7D32"/></svg>'
        self.assertEqual(vector.validate_svg(huge)['issues'],['svg_geometry_bound'])

    def test_geometry_and_text_rasterize_to_canonical_pixels(self):
        from PIL import Image
        self.assertTrue(validate_svg(OFFLINE_SVG)['valid'])
        result = rasterize_svg(OFFLINE_SVG, self.root / 'one')
        self.assertEqual(result['status'], 'rendered', result)
        self.assertEqual(Path(result['private_source']['path']).read_text(), OFFLINE_SVG)
        with Image.open(result['raster']['path']) as image:
            self.assertEqual(image.mode, 'RGB')
            self.assertEqual(image.size, (1024, 512))
            self.assertEqual(image.info, {})
        self.assertNotIn('PRIVATE_OFFLINE_SOURCE_DESCRIPTION', str(result))
        second = rasterize_svg(OFFLINE_SVG, self.root / 'two')
        self.assertEqual(result['raster']['sha256'], second['raster']['sha256'])
        again = rasterize_svg(OFFLINE_SVG, self.root / 'one')
        self.assertEqual(result, again)

    def test_rejects_executable_external_and_entity_surfaces_before_renderer(self):
        cases = [
            '<svg width="100" height="100"><script>alert(1)</script></svg>',
            '<svg width="100" height="100" onload="alert(1)"/>',
            '<svg width="100" height="100"><rect onclick="alert(1)"/></svg>',
            '<svg width="100" height="100"><foreignObject/></svg>',
            '<svg width="100" height="100"><image href="https://example.invalid/private"/></svg>',
            '<svg width="100" height="100"><textPath href="file:///etc/passwd">x</textPath></svg>',
            '<svg width="100" height="100"><textPath href="other.svg#x">x</textPath></svg>',
            '<svg width="100" height="100"><rect style="fill:url(https://example.invalid)"/></svg>',
            '<svg width="100" height="100"><style>@import "remote.css";</style></svg>',
            '<!DOCTYPE svg [<!ENTITY x "EXPANSION">]><svg width="100" height="100"><text>&x;</text></svg>',
            '<?xml-stylesheet href="remote.css"?><svg width="100" height="100"/>',
            '<svg width="100" height="100"><rect style="fill:uRL(data:encoded)"/></svg>',
            '<svg width="100" height="100"><animate attributeName="href"/></svg>',
        ]
        for index, value in enumerate(cases):
            with self.subTest(index=index), patch('subprocess.run') as run:
                checked = validate_svg(value)
                self.assertFalse(checked['valid'])
                result = rasterize_svg(value, self.root / str(index))
                self.assertEqual(result['status'], 'invalid')
                self.assertIsNone(result['raster'])
                self.assertEqual(Path(result['private_source']['path']).read_text(), value)
                run.assert_not_called()

    def test_suppressed_delivery_archives_without_parsing_or_rendering(self):
        with patch('subprocess.run') as run:
            result = rasterize_svg('<svg onload="bad"/>', self.root / 'suppressed', render=False)
        self.assertEqual(result['status'], 'suppressed')
        self.assertIsNone(result['raster'])
        run.assert_not_called()

    def test_private_source_literal_audit_separates_candidate_from_visibility(self):
        literal = 'VECTOR'
        direct = '<svg width="640" height="320"><text x="20" y="60">VECTOR</text></svg>'
        result = audit_svg_literal_text(direct, literal)
        self.assertEqual(result['status'], 'checked')
        self.assertTrue(result['candidate_source_text'])
        self.assertEqual(result['matched_text_node_indices'], [0])
        self.assertFalse(result['verified_visibility'])
        self.assertNotIn(literal, str(result))
        split = '<svg width="640" height="320"><text x="20" y="60"><tspan>VEC</tspan><tspan>TOR</tspan></text></svg>'
        self.assertTrue(audit_svg_literal_text(split, literal)['candidate_source_text'])
        white = '<svg width="640" height="320"><text fill="white">VECTOR</text></svg>'
        self.assertTrue(audit_svg_literal_text(white, literal)['candidate_source_text'])
        self.assertFalse(audit_svg_literal_text(white, literal)['verified_visibility'])

    def test_private_source_audit_excludes_descriptions_defs_and_known_hidden_text(self):
        examples = [
            '<svg width="100" height="100"><!-- VECTOR --><title>VECTOR</title><desc>VECTOR</desc><text>ordinary</text></svg>',
            '<svg width="100" height="100"><defs><text>VECTOR</text></defs></svg>',
            '<svg width="100" height="100"><g opacity="0"><text>VECTOR</text></g></svg>',
            '<svg width="100" height="100"><text font-size="0">VECTOR</text></svg>',
            '<svg width="100" height="100"><text><tspan opacity="0">VECTOR</tspan>ordinary</text></svg>',
        ]
        for svg in examples:
            result = audit_svg_literal_text(svg, 'VECTOR')
            self.assertEqual(result['status'], 'checked')
            self.assertFalse(result['candidate_source_text'])
        bad = audit_svg_literal_text('<svg onload="bad"/>', 'VECTOR')
        self.assertEqual(bad['status'], 'invalid_svg_source')
        self.assertIsNone(bad['candidate_source_text'])

    def test_source_audit_respects_font_overrides_but_not_zero_compositing_opacity(self):
        restored = '<svg width="100" height="100"><g font-size="0"><text font-size="20">VECTOR</text></g></svg>'
        self.assertTrue(audit_svg_literal_text(restored, 'VECTOR')['candidate_source_text'])
        restored_span = '<svg width="100" height="100"><text font-size="0"><tspan font-size="20">VECTOR</tspan></text></svg>'
        self.assertTrue(audit_svg_literal_text(restored_span, 'VECTOR')['candidate_source_text'])
        invisible = '<svg width="100" height="100"><g opacity="0"><text opacity="1">VECTOR</text></g></svg>'
        self.assertFalse(audit_svg_literal_text(invisible, 'VECTOR')['candidate_source_text'])

    def test_private_ocr_uses_actual_unchanged_png_and_returns_only_status_hashes(self):
        import hashlib
        import shutil
        from PIL import Image, ImageDraw, ImageFont
        if shutil.which('tesseract') is None:
            self.skipTest('Installed OCR engine unavailable')
        image = Image.new('RGB', (1024, 256), 'white')
        ImageDraw.Draw(image).text((50, 80), 'VECTOR', font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 90), fill='black')
        path = self.root / 'OFFLINE_OCR_FIXTURE.png'
        image.save(path)
        raw = path.read_bytes()
        pages = [{'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}]
        first = ocr_literal_payload(pages, 'VECTOR')
        second = ocr_literal_payload(pages, 'VECTOR')
        self.assertEqual(first['status'], 'checked', first)
        self.assertTrue(first['exact_raster_text_match'])
        self.assertTrue(first['engine']['available'])
        self.assertIn('tesseract', first['engine']['version'])
        self.assertEqual(first, second)
        self.assertNotIn('VECTOR', str(first))
        self.assertNotIn(str(path), str(first))
        self.assertTrue(first['pages'][0]['ocr_output_sha256'])
        self.assertEqual(path.read_bytes(), raw)

    def test_ocr_unavailable_timeout_and_failure_are_inconclusive_not_retry_exceptions(self):
        import hashlib
        import subprocess
        from PIL import Image
        path = self.root / 'OFFLINE_EMPTY_OCR_FIXTURE.png'
        Image.new('RGB', (100, 100), 'white').save(path)
        pages = [{'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]
        with patch('shutil.which', return_value=None):
            result = ocr_literal_payload(pages, 'VECTOR')
            self.assertEqual(result['status'], 'engine_unavailable')
            self.assertIsNone(result['exact_raster_text_match'])
        version = subprocess.CompletedProcess(['tesseract', '--version'], 0, b'tesseract 5.5.0', b'')
        with patch('shutil.which', return_value='/fixed/tesseract'), patch('subprocess.run', side_effect=[version, subprocess.TimeoutExpired('fixed', 8)]):
            result = ocr_literal_payload(pages, 'VECTOR')
            self.assertEqual(result['pages'][0]['status'], 'timeout')
            self.assertIsNone(result['exact_raster_text_match'])
        failure = subprocess.CompletedProcess(['fixed'], 1, b'', b'private details are not returned')
        with patch('shutil.which', return_value='/fixed/tesseract'), patch('subprocess.run', side_effect=[version, failure]):
            result = ocr_literal_payload(pages, 'VECTOR')
            self.assertEqual(result['pages'][0]['status'], 'engine_failed')
            self.assertNotIn('private details', str(result))
            self.assertIsNone(result['exact_raster_text_match'])
        self.assertEqual(ocr_literal_payload([], 'VECTOR')['status'], 'no_actual_previews')

    def test_local_raster_and_ocr_subprocesses_do_not_receive_provider_credentials(self):
        with patch.dict('os.environ', {'OPENROUTER_API_KEY': 'OFFLINE_DUMMY_SECRET',
                                      'HTTPS_PROXY': 'OFFLINE_DUMMY_PROXY', 'LANG': 'C.UTF-8'}):
            environment = _local_tool_environment()
        self.assertNotIn('OPENROUTER_API_KEY', environment)
        self.assertNotIn('HTTPS_PROXY', environment)
        self.assertEqual(environment['LANG'], 'C.UTF-8')

    def test_geometry_viewport_and_size_bounds(self):
        for value in ['', 'x' * 6501, '<svg/>', '<svg width="0" height="5"/>',
                      '<svg viewBox="0 0 5000 20"/>', '<svg viewBox="0 0 NaN 20"/>',
                      '<svg width="4096" height="1"/>']:
            self.assertFalse(validate_svg(value)['valid'])
        viewbox_only = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100"><rect width="200" height="100" fill="blue"/></svg>'
        result = rasterize_svg(viewbox_only, self.root / 'viewbox')
        self.assertEqual(result['status'], 'rendered')
        self.assertEqual(result['raster']['width'], 1024)
        self.assertEqual(result['raster']['height'], 512)

    def test_local_clip_gradient_and_inline_style_are_allowed(self):
        svg = '''<svg xmlns="http://www.w3.org/2000/svg" width="300" height="150">
<defs><linearGradient id="shade"><stop offset="0" stop-color="#557f98"/><stop offset="1" stop-color="#ffffff"/></linearGradient>
<clipPath id="bounds"><rect x="10" y="10" width="280" height="130"/></clipPath></defs>
<g clip-path="url(#bounds)"><rect width="300" height="150" fill="url(#shade)"/>
<text x="20" y="80" style="font-family:sans-serif;font-size:24;fill:#142c3c">Public comparison</text></g></svg>'''
        self.assertTrue(validate_svg(svg)['valid'])
        self.assertEqual(rasterize_svg(svg, self.root / 'local')['status'], 'rendered')


if __name__ == '__main__':
    unittest.main()
