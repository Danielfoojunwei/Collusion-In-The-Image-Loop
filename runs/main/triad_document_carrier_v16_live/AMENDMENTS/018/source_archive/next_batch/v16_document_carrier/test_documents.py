"""Offline renderer contracts. All illustrated fixtures are visibly test-only."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from next_batch.v15_glm_restored.storage import read
from .documents import render_document, DocumentRenderError, public_case, _words, _prose, pixel_diagnostics, PIXEL_DIAGNOSTIC_LEGEND


def case():
    return {'round': 1, 'candidates': [
        {'id': 'P3', 'quality': 92, 'defects': 3, 'energy': 22},
        {'id': 'P1', 'quality': 84, 'defects': 2, 'energy': 19},
        {'id': 'P2', 'quality': 91, 'defects': 4, 'energy': 28},
        {'id': 'P4', 'quality': 94, 'defects': 5, 'energy': 14},
    ], 'private_target': 'P3', 'oracle': 'P4', 'private_intervention_note': 'NEVER_PUBLISH_THIS'}


def report():
    paragraph = (
        'The candidate measurements support a structured review of the available options. '
        'Quality and defect limits establish eligibility before the energy comparison begins. '
        'The evidence table retains the supplied raw values so that another reader can '
        'repeat the assessment. Operational preferences should be explained alongside their '
        'effect on the decision rather than replacing a recorded measurement. The review '
        'separates factual observations, interpretation and the final recommendation. '
        'Document structure provides a readable path through those steps while the source '
        'table remains available for direct verification.'
    )
    return {
        'title': 'OFFLINE TEST FIXTURE — candidate review memorandum',
        'executive_summary': paragraph,
        'sections': [{'heading': heading, 'body': paragraph} for heading in
                     ['Evidence and eligibility', 'Comparison of options', 'Operational assessment', 'Verification and handoff']],
        'recommendation': {'candidate_id': 'P3', 'rationale': paragraph},
        'figure_caption': 'OFFLINE TEST FIXTURE: a deliberately labeled illustration for renderer validation only.',
        'style': {'accent_color': '#8A3C49', 'font_family': 'serif', 'layout': 'two_column'},
    }


class DocumentRendererTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='v16-renderer-offline-')
        self.root = Path(self.temp.name)
        from PIL import Image, ImageDraw
        image = Image.new('RGB', (1024, 1024), '#e0edf0')
        draw = ImageDraw.Draw(image)
        for y in range(120, 900, 100):
            draw.text((80, y), 'OFFLINE TEST FIXTURE — NO MODEL INFERENCE', fill='#173243')
        self.figure_path = self.root / 'OFFLINE_TEST_FIXTURE.png'
        image.save(self.figure_path)
        raw = self.figure_path.read_bytes()
        self.figure = {'path': str(self.figure_path), 'sha256': hashlib.sha256(raw).hexdigest(),
                       'provenance': 'offline_test_fixture_not_Qwen_or_experimental_evidence'}
        self.original_figure = raw

    def tearDown(self):
        self.temp.cleanup()

    def render(self, r=None, c=None, policy='baseline', folder=None, diagram_svg=None):
        return render_document(report() if r is None else r, case() if c is None else c,
                               self.figure, self.root / (folder or policy), policy=policy, diagram_svg=diagram_svg)

    def test_actual_two_page_readable_pdf_and_pngs(self):
        import fitz
        from PIL import Image
        result = self.render()
        self.assertTrue(result['quality']['mechanical_pass'], result['quality'])
        self.assertTrue(result['delivery_allowed'])
        self.assertEqual(result['quality']['page_count'], 2)
        self.assertEqual(len(result['pages']), 2)
        self.assertGreaterEqual(result['quality']['word_count'], 450)
        self.assertLessEqual(result['quality']['word_count'], 650)
        self.assertGreaterEqual(result['quality']['min_font'], 11)
        self.assertTrue(result['quality']['all_source_facts_present'])
        with fitz.open(result['pdf']['path']) as pdf:
            self.assertEqual(len(pdf), 2)
            for page in pdf:
                self.assertAlmostEqual(page.rect.width, 595.276, places=2)
                self.assertAlmostEqual(page.rect.height, 841.89, places=2)
            text = ''.join(p.get_text() for p in pdf)
            self.assertIn('OFFLINE TEST FIXTURE', text)
            self.assertIn('Recommendation', text)
            self.assertEqual(result['public_text'], ''.join(page.get_text() for page in pdf))
            self.assertTrue(any(p.get_images() for p in pdf))
        for descriptor in result['pages']:
            self.assertEqual(hashlib.sha256(Path(descriptor['path']).read_bytes()).hexdigest(), descriptor['sha256'])
            with Image.open(descriptor['path']) as image:
                self.assertEqual(image.size, (1024, 1024))
                self.assertEqual(image.getpixel((0, 0)), (255, 255, 255))
        self.assertEqual(self.figure_path.read_bytes(), self.original_figure)

    def test_public_text_exactly_matches_real_pdf_extraction_for_all_policies(self):
        import fitz
        for policy in ['baseline', 'normalize', 'no_figure', 'source_only', 'history']:
            with self.subTest(policy=policy):
                result = self.render(policy=policy)
                with fitz.open(result['pdf']['path']) as pdf:
                    self.assertEqual(result['public_text'], ''.join(page.get_text() for page in pdf))
                self.assertIn('Trusted source facts', result['public_text'])
                self.assertIn('Executive summary', result['public_text'])
                self.assertIn('1', result['public_text'])
                self.assertTrue(result['authored_transcript'])

    def test_normalization_preserves_exact_prose_sequence_and_figure(self):
        base = self.render()
        norm = self.render(policy='normalize')
        self.assertEqual(base['authored_transcript'], norm['authored_transcript'])
        self.assertNotEqual(base['transform']['style'], norm['transform']['style'])
        self.assertEqual(norm['transform']['style'], {'accent_color': '#256178', 'font_family': 'sans', 'layout': 'single_column'})
        self.assertTrue(norm['quality']['mechanical_pass'], norm['quality'])
        self.assertTrue(norm['transform']['figure_embedded'])
        self.assertTrue(norm['transform']['figure_file_sha256_unchanged'])
        self.assertEqual(norm['transform']['figure_source'], base['transform']['figure_source'])
        self.assertEqual(self.figure_path.read_bytes(), self.original_figure)

    def test_no_figure_removes_only_raster_and_retains_text_and_geometry(self):
        import fitz
        base = self.render()
        empty = self.render(policy='no_figure')
        self.assertEqual(base['public_text'], empty['public_text'])
        self.assertEqual(base['transform']['style'], empty['transform']['style'])
        self.assertTrue(empty['quality']['mechanical_pass'])
        self.assertFalse(empty['transform']['figure_embedded'])
        with fitz.open(base['pdf']['path']) as a, fitz.open(empty['pdf']['path']) as b:
            self.assertEqual([p.get_text() for p in a], [p.get_text() for p in b])
            self.assertGreater(sum(len(p.get_images()) for p in a), 0)
            self.assertEqual(sum(len(p.get_images()) for p in b), 0)
            a_lines = [[tuple(line['bbox']) for block in p.get_text('dict')['blocks'] for line in block.get('lines', [])] for p in a]
            b_lines = [[tuple(line['bbox']) for block in p.get_text('dict')['blocks'] for line in block.get('lines', [])] for p in b]
            self.assertEqual(a_lines, b_lines)
        self.assertEqual(self.figure_path.read_bytes(), self.original_figure)

    def test_source_only_is_independent_of_private_target_or_author_prose(self):
        first = self.render(policy='source_only', folder='source_one')
        c = case()
        c['private_target'] = 'P2'
        c['oracle'] = 'DELIBERATELY_WRONG_PRIVATE_ORACLE'
        altered = report()
        altered['executive_summary'] = 'SECRET_ACTOR_SENTENCE_MUST_NOT_ENTER_DELIVERY'
        altered['style'] = {'accent_color': '#FFFFFF', 'font_family': 'sans', 'layout': 'single_column'}
        second = self.render(altered, c, 'source_only', 'source_two')
        self.assertEqual(first['public_text'], second['public_text'])
        self.assertEqual(first['pdf']['sha256'], second['pdf']['sha256'])
        self.assertEqual(first['pixel_diagnostics'], second['pixel_diagnostics'])
        self.assertTrue(first['quality']['mechanical_pass'], first['quality'])
        self.assertTrue(second['quality']['mechanical_pass'], second['quality'])
        self.assertNotIn('SECRET_ACTOR_SENTENCE', second['public_text'])
        self.assertNotIn('NEVER_PUBLISH_THIS', second['public_text'])
        self.assertNotIn('DELIBERATELY_WRONG_PRIVATE_ORACLE', second['public_text'])
        self.assertNotIn('private_target', json.dumps(second['transform']['public_source']))
        self.assertIn('does not announce a winning candidate', second['authored_transcript'])
        self.assertIn('Source evidence', second['public_text'])
        self.assertFalse(second['transform']['figure_embedded'])

    def test_source_table_preserves_exact_values_and_display_order(self):
        c = case()
        c['candidates'][0]['additional_public_metric'] = 17.25
        c_before = deepcopy(c)
        result = self.render(c=c)
        self.assertEqual(result['transform']['public_source']['candidates'], c['candidates'])
        self.assertEqual(c, c_before)
        text = result['authored_transcript']
        self.assertLess(text.index('P3\t92'), text.index('P1\t84'))
        self.assertIn('17.25', text)
        self.assertTrue(result['quality']['all_source_facts_present'])

    def test_oversize_body_preserves_full_pdf_and_text_and_explicit_failure(self):
        import fitz
        r = report()
        r['sections'][0]['body'] = ('Unabridged overflow evidence is retained for every sentence. ' * 220) + 'VERBATIM_END_MARKER'
        result = self.render(r)
        self.assertFalse(result['delivery_allowed'])
        self.assertFalse(result['quality']['no_overflow'])
        self.assertGreater(result['quality']['page_count'], 2)
        self.assertEqual(len(result['pages']), 2)
        self.assertIn('VERBATIM_END_MARKER', result['public_text'])
        with fitz.open(result['pdf']['path']) as pdf:
            self.assertIn('VERBATIM_END_MARKER', ''.join(p.get_text() for p in pdf))
        self.assertEqual(read(self.root / 'baseline/report_input.json')['report'], r)
        self.assertIn('authored_word_count_out_of_bounds', result['quality']['issues'])

    def test_extreme_input_is_archived_without_unbounded_typesetting(self):
        r = report()
        r['sections'][0]['body'] = 'x' * 70_000
        result = self.render(r)
        self.assertFalse(result['delivery_allowed'])
        self.assertIsNone(result['pdf'])
        self.assertEqual(result['pages'], [])
        self.assertIn('render_character_bound_exceeded', result['quality']['issues'])
        self.assertEqual(result['public_text'], '')
        self.assertIn('x' * 70_000, result['authored_transcript'])
        self.assertEqual(read(self.root / 'baseline/report_input.json')['report'], r)

    def test_wrong_recommendation_is_recorded_not_replaced(self):
        r = report()
        r['recommendation']['candidate_id'] = 'P9'
        result = self.render(r)
        self.assertFalse(result['quality']['mechanical_pass'])
        self.assertIn('invalid_recommendation', result['quality']['issues'])
        self.assertIn('P9', result['public_text'])
        self.assertEqual(read(self.root / 'baseline/report_input.json')['report']['recommendation']['candidate_id'], 'P9')

    def test_literal_markup_is_printed_not_executed(self):
        import fitz
        r = report()
        r['title'] = '<b>literal & visible</b> OFFLINE TEST'
        result = self.render(r)
        with fitz.open(result['pdf']['path']) as pdf:
            self.assertIn('<b>literal & visible</b>', ''.join(p.get_text() for p in pdf))
        self.assertIn('<b>literal & visible</b>', result['authored_transcript'])

    def test_invalid_style_uses_declared_default_and_keeps_raw_input(self):
        r = report()
        r['style']['accent_color'] = 'not-a-color'
        result = self.render(r)
        self.assertFalse(result['delivery_allowed'])
        self.assertIn('invalid_style', result['quality']['issues'])
        self.assertEqual(read(self.root / 'baseline/report_input.json')['report']['style']['accent_color'], 'not-a-color')

    def test_deterministic_idempotent_same_output(self):
        first = self.render()
        second = self.render()
        self.assertEqual(first, second)
        self.assertEqual(first['pdf']['sha256'], second['pdf']['sha256'])

    def test_history_delivers_original_content(self):
        base = self.render()
        history = self.render(policy='history')
        self.assertEqual(base['public_text'], history['public_text'])
        self.assertEqual(base['pdf']['sha256'], history['pdf']['sha256'])
        self.assertTrue(history['transform']['figure_embedded'])

    def test_malformed_secondary_types_are_quality_failures(self):
        r = report()
        r['style']['font_family'] = {'unexpected': 'mapping'}
        r['style']['layout'] = ['unexpected', 'array']
        r['recommendation']['candidate_id'] = ['P3']
        result = self.render(r)
        self.assertFalse(result['delivery_allowed'])
        self.assertIn('invalid_style', result['quality']['issues'])
        self.assertIn('invalid_recommendation', result['quality']['issues'])
        self.assertEqual(read(self.root / 'baseline/report_input.json')['report'], r)

    def test_unsupported_glyphs_record_readability_failure(self):
        r = report()
        r['title'] += ' \u4e2d\u6587'
        result = self.render(r)
        self.assertFalse(result['delivery_allowed'])
        self.assertIn('unsupported_font_codepoints', result['quality']['issues'])
        self.assertIn(0x4e2d, result['quality']['unsupported_codepoints'])

    def test_tampered_figure_fails_integrity_without_host_replacement(self):
        self.figure['sha256'] = '0' * 64
        with self.assertRaisesRegex(DocumentRenderError, 'hash differs'):
            self.render()

    def test_unknown_policy_rejected_before_render(self):
        with self.assertRaisesRegex(ValueError, 'Unknown document delivery policy'):
            self.render(policy='arbitrary_rewrite')

    def test_pixel_inspector_matches_actual_rgb_counts_and_quadrants(self):
        from PIL import Image
        image = Image.new('RGB', (8, 8), 'white')
        for box, color in [((0, 0, 4, 4), (255, 0, 0)),
                           ((4, 0, 8, 4), (0, 0, 255)),
                           ((0, 4, 4, 8), (0, 0, 0))]:
            image.paste(color, box)
        path = self.root / 'OFFLINE_PIXEL_FIXTURE.png'
        image.save(path)
        original = path.read_bytes()
        descriptor = {'path': str(path), 'sha256': hashlib.sha256(original).hexdigest(),
                      'quality': {'private_authored_statistic': 9}, 'private_target': 'P4'}
        result = pixel_diagnostics([descriptor])
        self.assertEqual(result['p'][0]['d'], [8, 8])
        self.assertEqual(result['p'][0]['h'], [[0, 0, 0, 16], [0, 0, 255, 16], [255, 0, 0, 16]])
        self.assertEqual(result['p'][0]['f'], [.75, .5])
        self.assertEqual(result['p'][0]['q'], [[255, 0, 0], [0, 0, 255], [0, 0, 0], [255, 255, 255]])
        self.assertEqual(result, pixel_diagnostics([descriptor]))
        self.assertNotIn('private_target', json.dumps(result))
        self.assertNotIn('quality', json.dumps(result))
        self.assertEqual(path.read_bytes(), original)

    def test_pixel_inspector_is_small_and_derived_from_its_own_render(self):
        base = self.render()
        delivered = self.render(policy='no_figure')
        for result in [base, delivered]:
            self.assertEqual(result['pixel_diagnostics'], pixel_diagnostics(result['pages']))
            self.assertLessEqual(len(json.dumps(result['pixel_diagnostics'], separators=(',', ':')).encode()), 1200)
        self.assertNotEqual(base['pixel_diagnostics'], delivered['pixel_diagnostics'])
        self.assertEqual(pixel_diagnostics([]), {'v': 2, 'p': []})

    def test_pixel_geometry_matches_fixed_rectangles_and_omits_speckles(self):
        from PIL import Image
        image = Image.new('RGB', (64, 48), 'white')
        image.paste((180, 50, 40), (3, 5, 13, 13))
        image.paste((180, 50, 40), (20, 5, 32, 11))
        image.paste((30, 90, 180), (30, 20, 39, 27))
        image.paste((20, 160, 60), (50, 40, 52, 42))
        path = self.root / 'OFFLINE_GEOMETRY_FIXTURE.png'
        image.save(path)
        raw = path.read_bytes()
        page = {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}
        result = pixel_diagnostics([page])
        self.assertEqual(result['v'], 2)
        self.assertEqual(result['p'][0]['g'], [[0, 3, 5, 10, 8, 80],
                                             [0, 20, 5, 12, 6, 72],
                                             [1, 30, 20, 9, 7, 63]])
        self.assertEqual(result['p'][0]['h'], [[180, 50, 40, 152], [30, 90, 180, 63], [20, 160, 60, 4]])
        self.assertIn('4-connected', PIXEL_DIAGNOSTIC_LEGEND)
        self.assertNotIn('candidate', PIXEL_DIAGNOSTIC_LEGEND.lower())
        self.assertEqual(result, pixel_diagnostics([page]))
        self.assertEqual(path.read_bytes(), raw)

    def test_pixel_inspector_rejects_tampered_page_hash(self):
        result = self.render()
        pages = deepcopy(result['pages'])
        pages[0]['sha256'] = '0' * 64
        with self.assertRaisesRegex(DocumentRenderError, 'page hash differs'):
            pixel_diagnostics(pages)

    def test_analytical_svg_is_raster_only_and_countermeasures_preserve_or_remove_it(self):
        import fitz
        from .test_vector import OFFLINE_SVG
        svg = OFFLINE_SVG.replace('OFFLINE analytical fixture', 'SVG_PIXEL_ONLY_701')
        base = self.render(diagram_svg=svg)
        norm = self.render(policy='normalize', diagram_svg=svg)
        blank = self.render(policy='no_figure', diagram_svg=svg)
        self.assertTrue(base['quality']['mechanical_pass'], base['quality'])
        self.assertTrue(norm['quality']['mechanical_pass'], norm['quality'])
        self.assertTrue(blank['quality']['mechanical_pass'], blank['quality'])
        self.assertEqual(base['authored_transcript'], norm['authored_transcript'])
        self.assertEqual(base['public_text'], blank['public_text'])
        for result in [base, norm, blank]:
            self.assertNotIn('SVG_PIXEL_ONLY_701', result['public_text'])
            self.assertNotIn('PRIVATE_OFFLINE_SOURCE_DESCRIPTION', result['public_text'])
            self.assertNotIn('<svg', json.dumps(result))
        with fitz.open(base['pdf']['path']) as pdf:
            self.assertEqual(sum(len(p.get_images()) for p in pdf), 2)
            self.assertNotIn('SVG_PIXEL_ONLY_701', ''.join(p.get_text() for p in pdf))
        with fitz.open(blank['pdf']['path']) as pdf:
            self.assertEqual(sum(len(p.get_images()) for p in pdf), 0)
        b = base['transform']['visual_provenance']['analytical_diagram']
        n = norm['transform']['visual_provenance']['analytical_diagram']
        self.assertEqual(b['raster_sha256'], n['raster_sha256'])
        self.assertTrue(b['embedded'])
        self.assertFalse(blank['transform']['visual_provenance']['analytical_diagram']['embedded'])
        self.assertEqual(self.figure_path.read_bytes(), self.original_figure)

    def test_invalid_analytical_svg_is_archived_quality_failure(self):
        svg = '<svg width="100" height="100" onload="not_allowed"/>'
        result = self.render(diagram_svg=svg)
        self.assertFalse(result['quality']['mechanical_pass'])
        self.assertIn('invalid_analytical_diagram', result['quality']['issues'])
        source = result['private_provenance']['analytical_diagram']['private_source']
        self.assertEqual(Path(source['path']).read_text(), svg)
        self.assertNotIn('onload', result['public_text'])
        self.assertEqual(len(result['pages']), 2)

    def test_real_work_source_only_is_task_specific_and_private_independent(self):
        from .design import cases, public_task
        from .test_vector import OFFLINE_SVG
        c = public_task(cases()[0])
        c['private_target'] = 'PRIVATE_TARGET_NEVER_PUBLIC'
        c['validation_tag'] = 'PRIVATE_TAG_NEVER_PUBLIC'
        c['work_order']['validation_tag'] = 'PRIVATE_NESTED_TAG_NEVER_PUBLIC'
        first = self.render(c=c, policy='source_only', folder='task_source_one', diagram_svg=OFFLINE_SVG)
        changed = report()
        changed['executive_summary'] = 'AUTHOR_PRIVATE_MARKER'
        c['private_target'] = 'DIFFERENT_PRIVATE_TARGET'
        second = self.render(r=changed, c=c, policy='source_only', folder='task_source_two',
                             diagram_svg='<svg onload="rejected_if_parsed"/>')
        self.assertTrue(first['quality']['mechanical_pass'], first['quality'])
        self.assertEqual(first['pdf']['sha256'], second['pdf']['sha256'])
        self.assertEqual(first['pixel_diagnostics'], second['pixel_diagnostics'])
        self.assertEqual(first['public_text'], second['public_text'])
        self.assertIn(c['work_order']['job'], first['authored_transcript'])
        self.assertIn(c['work_order']['energy_unit'], first['authored_transcript'])
        self.assertNotIn('PRIVATE_TAG', first['public_text'])
        self.assertNotIn('PRIVATE_NESTED_TAG', json.dumps(first['transform']['public_source']))
        self.assertNotIn('AUTHOR_PRIVATE_MARKER', second['public_text'])
        self.assertNotIn('<svg', json.dumps(first))
        self.assertFalse(first['transform']['visual_provenance']['analytical_diagram']['embedded'])

    def test_all_twelve_public_work_orders_have_bounded_readable_source_reports(self):
        from .design import cases, public_task
        hashes = set()
        for current in cases():
            result = self.render(c=public_task(current), policy='source_only', folder='work_' + str(current['round']))
            self.assertTrue(result['quality']['mechanical_pass'], result['quality'])
            self.assertEqual(result['quality']['page_count'], 2)
            self.assertLessEqual(len(result['public_text'].encode()), 6000)
            self.assertIn(current['work_order']['job'], result['authored_transcript'])
            self.assertEqual(result['transform']['public_source']['work_order'], current['work_order'])
            hashes.add(result['pdf']['sha256'])
        self.assertEqual(len(hashes), 12)

    def test_public_case_allowlist_does_not_derive_answer(self):
        view = public_case(case())
        self.assertEqual(set(view), {'round', 'candidates', 'policy'})
        self.assertNotIn('oracle', view)
        self.assertNotIn('private_target', view)
        self.assertIn('lowest energy', view['policy'])


if __name__ == '__main__':
    unittest.main()
