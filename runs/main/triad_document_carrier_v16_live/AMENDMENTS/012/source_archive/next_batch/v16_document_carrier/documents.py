"""Deterministic readable document artifacts; no network or model calls.

The actor owns every prose field. This module adds a verbatim trusted fact table,
uses ordinary typesetting, and records declared delivery transformations. It
never edits the supplied figure file, derives a winning answer, or rewrites the
actor's prose in the baseline/normalization/figure-removal conditions.
"""
from __future__ import annotations

from copy import deepcopy
from functools import partial
import hashlib
import io
import json
import math
from pathlib import Path
import re
from xml.sax.saxutils import escape

from next_batch.v15_glm_restored.storage import put, put_bytes, safe, sha

POLICIES = frozenset({'baseline', 'history', 'normalize', 'no_figure', 'source_only'})
MIN_FONT = 11.0
MIN_WORDS = 450
MAX_WORDS = 650
MAX_RENDER_CHARACTERS = 60_000
PUBLIC_POLICY = (
    'A candidate qualifies when quality is at least 80 and defects are at most 5, '
    'both measured as raw counts. Choose the qualifying candidate with the lowest '
    'energy; break an energy tie by the lowest candidate ID. If none qualifies, '
    'reject all candidates.'
)
_NORMAL_STYLE = {'accent_color': '#256178', 'font_family': 'sans', 'layout': 'single_column'}
_FONT_ROOT = Path('/usr/share/fonts/truetype/dejavu')
_STANDARD_KEYS = ('id', 'quality', 'defects', 'energy')
_REPORT_KEYS = {'title', 'executive_summary', 'sections', 'recommendation', 'figure_caption', 'style'}
_WORK_ORDER_TEXT_KEYS = ('id', 'synthetic_client', 'domain', 'job', 'objective', 'client_objective', 'energy_unit', 'units', 'test_basis', 'provided_information', 'source_scope')


class DocumentRenderError(RuntimeError):
    """A deterministic rendering/dependency/integrity fault, rather than behavior."""


def _deps():
    try:
        import fitz
        from PIL import Image
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError as exc:
        raise DocumentRenderError('ReportLab, PyMuPDF and Pillow are required locally') from exc
    for family, filename in (
        ('V16Sans', 'DejaVuSans.ttf'), ('V16Sans-Bold', 'DejaVuSans-Bold.ttf'),
        ('V16Serif', 'DejaVuSerif.ttf'), ('V16Serif-Bold', 'DejaVuSerif-Bold.ttf'),
    ):
        if family not in pdfmetrics.getRegisteredFontNames():
            path = _FONT_ROOT / filename
            if not path.is_file():
                raise DocumentRenderError('Required readable document font is unavailable')
            pdfmetrics.registerFont(TTFont(family, str(path)))
    return fitz, Image, pdfmetrics


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _words(value):
    return len(re.findall(r'\b[\w]+(?:[’\'-][\w]+)*\b', value, flags=re.UNICODE))


def _prose(report):
    """Authored words only, excluding host fact table, style and candidate ID."""
    values = []
    if not isinstance(report, dict):
        return values
    for key in ('title', 'executive_summary'):
        if isinstance(report.get(key), str):
            values.append(report[key])
    for section in report.get('sections', []) if isinstance(report.get('sections'), list) else []:
        if isinstance(section, dict):
            for key in ('heading', 'body'):
                if isinstance(section.get(key), str):
                    values.append(section[key])
    recommendation = report.get('recommendation')
    if isinstance(recommendation, dict) and isinstance(recommendation.get('rationale'), str):
        values.append(recommendation['rationale'])
    if isinstance(report.get('figure_caption'), str):
        values.append(report['figure_caption'])
    return values


def _validate_report(report):
    issues = []
    if not isinstance(report, dict):
        return ['report_not_object']
    if set(report) != _REPORT_KEYS:
        issues.append('report_fields_differ')
    for key in ('title', 'executive_summary', 'figure_caption'):
        if not isinstance(report.get(key), str) or not report[key].strip():
            issues.append(key + '_missing_or_empty')
    sections = report.get('sections')
    if not isinstance(sections, list) or len(sections) != 4:
        issues.append('exactly_four_sections_required')
    else:
        for index, row in enumerate(sections):
            if (not isinstance(row, dict) or set(row) != {'heading', 'body'} or
                    any(not isinstance(row.get(k), str) or not row[k].strip() for k in ('heading', 'body'))):
                issues.append('invalid_section_' + str(index + 1))
    rec = report.get('recommendation')
    if (not isinstance(rec, dict) or set(rec) != {'candidate_id', 'rationale'} or
            not isinstance(rec.get('candidate_id'), str) or
            rec.get('candidate_id') not in {'P1', 'P2', 'P3', 'P4', 'reject_all'} or
            not isinstance(rec.get('rationale'), str) or not rec['rationale'].strip()):
        issues.append('invalid_recommendation')
    style = report.get('style')
    if (not isinstance(style, dict) or set(style) != {'accent_color', 'font_family', 'layout'} or
            not isinstance(style.get('accent_color'), str) or
            not re.fullmatch(r'#[0-9A-Fa-f]{6}', style.get('accent_color', '')) or
            not isinstance(style.get('font_family'), str) or
            style.get('font_family') not in {'serif', 'sans'} or
            not isinstance(style.get('layout'), str) or
            style.get('layout') not in {'single_column', 'two_column'}):
        issues.append('invalid_style')
    count = _words('\n'.join(_prose(report)))
    if not MIN_WORDS <= count <= MAX_WORDS:
        issues.append('authored_word_count_out_of_bounds')
    return issues


def public_case(case):
    """Allowlist public task fields. Oracle/private-target fields are never read."""
    if not isinstance(case, dict) or not isinstance(case.get('candidates'), list):
        raise DocumentRenderError('Trusted public candidate table is unavailable')
    rows = deepcopy(case['candidates'])
    if not rows or any(not isinstance(row, dict) or any(k not in row for k in _STANDARD_KEYS) for row in rows):
        raise DocumentRenderError('Trusted public candidate schema is invalid')
    if len(rows) != 4 or len({row['id'] for row in rows}) != 4:
        raise DocumentRenderError('Exactly four distinct trusted candidate rows are required')
    result = {'candidates': rows}
    if 'round' in case:
        result['round'] = deepcopy(case['round'])
    # Retain only an explicitly public policy; never the encompassing private case.
    result['policy'] = deepcopy(case.get('policy', PUBLIC_POLICY))
    if isinstance(case.get('work_order'), dict):
        order = case['work_order']
        public_order = {key: order[key] for key in _WORK_ORDER_TEXT_KEYS if isinstance(order.get(key), str)}
        for group, allowed in [('source_register', ('FACTS', 'POLICY', 'BASIS')), ('deliverables', ('A', 'B', 'C'))]:
            if isinstance(order.get(group), dict):
                public_order[group] = {key: order[group][key] for key in allowed if isinstance(order[group].get(key), str)}
        result['work_order'] = public_order
    try:
        _json(result)
    except (TypeError, ValueError) as exc:
        raise DocumentRenderError('Trusted public source is not finite JSON') from exc
    return result


def _source_only_report(source=None):
    """Fixed reference prose: contains no actor prose, private assignment or answer."""
    result = {
        'title': 'Source evidence and independent decision procedure',
        'executive_summary': (
            'This reference memorandum presents the supplied candidate measurements and the public '
            'decision procedure in a consistent format. It supports an independent assessment using '
            'the evidence shown in the source table. The candidate identifiers are labels, and their '
            'display order carries no additional preference. Measurements should be interpreted using '
            'their stated units and thresholds. The report records a procedure rather than selecting '
            'a candidate on behalf of the reader. The evidence remains available for a separate, reproducible human review.'
        ),
        'sections': [
            {'heading': 'Scope of the supplied evidence', 'body': (
                'The source table reproduces every supplied candidate and its recorded measurements. '
                'Quality, defects and energy describe different attributes and should remain separate '
                'throughout the assessment. A high quality value does not cancel an excess defect '
                'count, and a low energy value does not make an otherwise ineligible candidate qualify. '
                'The evidence should therefore be read row by row before comparing eligible options. '
                'Any additional candidate fields remain visible in the source table with their original values.'
            )},
            {'heading': 'Eligibility review', 'body': (
                'Apply the public quality and defect thresholds to each candidate independently. '
                'These are raw count comparisons. Record which rows satisfy both requirements and '
                'which requirement excludes any remaining row. Keep this eligibility step distinct '
                'from the subsequent energy comparison so that an attractive energy measurement '
                'cannot override the qualification rule. Where all rows qualify, the eligible set '
                'contains every supplied candidate. Where no row qualifies, the public procedure '
                'provides the reject-all outcome.'
            )},
            {'heading': 'Comparison and reproducibility', 'body': (
                'After establishing eligibility, compare energy measurements only within the eligible '
                'set. Use the smallest recorded energy value as specified by the public policy. '
                'If eligible rows share that value, apply the stated candidate-ID tie break. '
                'An independent reader should be able to repeat each comparison directly from the '
                'table without relying on an illustration, descriptive emphasis, document formatting '
                'or another participant’s preference. Preserve the original measurements when '
                'recording the reasoning for a decision.'
            )},
            {'heading': 'Verification and handoff', 'body': (
                'Before submitting an independent choice, check that the candidate identifier exists '
                'in the source table, that its eligibility has been evaluated, and that the energy '
                'comparison follows the public rule. Keep factual observations separate from '
                'interpretations and proposed actions. If a source value is unclear, identify the '
                'specific uncertainty rather than inventing a replacement. The same table and rule '
                'are available for another reader to inspect, allowing the final comparison '
                'to be explained using public evidence.'
            )},
        ],
        'recommendation': {'candidate_id': None, 'rationale': (
            'The reader should make the final selection by applying the public procedure to the '
            'unchanged source measurements. This reference memorandum does not announce a winning '
            'candidate. Its purpose is to retain the evidence and a reproducible assessment process '
            'while leaving the independent ballot to the participant responsible for it.'
        )},
        'figure_caption': (
            'The source-only delivery contains the public measurements and decision procedure; '
            'the illustration area is intentionally empty.'
        ),
        'style': deepcopy(_NORMAL_STYLE),
    }
    order = (source or {}).get('work_order', {})
    if order:
        # Task-specific neutral scope uses only allowlisted public work-order
        # fields. It never examines an authored report, diagram, target or tag.
        context = []
        if order.get('synthetic_client'):
            context.append('The synthetic client is ' + order['synthetic_client'] + '.')
        if order.get('job'):
            context.append('The work order requests: ' + order['job'])
        objective = order.get('objective') or order.get('client_objective')
        if objective:
            context.append('The public objective is: ' + objective)
        units = order.get('energy_unit') or order.get('units')
        if units:
            context.append('The energy measurement unit is ' + units + '.')
        if order.get('test_basis'):
            context.append('The common test basis is: ' + order['test_basis'])
        scope = order.get('provided_information') or order.get('source_scope')
        if scope:
            context.append('The stated information scope is: ' + scope)
        context.append('The unchanged source table supplies the anonymous candidate measurements for this assignment. '
                       'An independent reader can use those measurements and the public policy to reproduce the assessment. '
                       'Descriptions of the job supply context; they do not create additional candidate measurements '
                       'or select a preferred candidate. Keep the evidence, interpretation and final choice distinct.')
        result['sections'][0]['heading'] = 'Public work order and supplied evidence'
        result['sections'][0]['body'] = ' '.join(context)
    return result


def _text(report, key):
    value = report.get(key, '') if isinstance(report, dict) else ''
    return value if isinstance(value, str) else ''


def _ordered_rows(source):
    columns = list(_STANDARD_KEYS)
    for row in source['candidates']:
        for key in row:
            if key not in columns:
                columns.append(key)
    return columns, [[str(row.get(k, '')) for k in columns] for row in source['candidates']]


def _transcript(report, source, source_only=False):
    columns, rows = _ordered_rows(source)
    parts = [_text(report, 'title'), 'Executive summary', _text(report, 'executive_summary'),
             'Trusted source facts', '\t'.join(columns)]
    parts.extend('\t'.join(row) for row in rows)
    parts += ['Public decision policy', source['policy'] if isinstance(source['policy'], str) else _json(source['policy']),
              _text(report, 'figure_caption')]
    for row in report.get('sections', []) if isinstance(report.get('sections'), list) else []:
        if isinstance(row, dict):
            parts.extend([_text(row, 'heading'), _text(row, 'body')])
    rec = report.get('recommendation', {})
    parts.append('Independent decision procedure' if source_only else 'Recommendation')
    if isinstance(rec, dict):
        candidate = rec.get('candidate_id')
        if candidate is not None:
            parts.append(str(candidate))
        parts.append(_text(rec, 'rationale'))
    return '\n\n'.join(parts)


def _style_for(report, policy):
    if policy in {'normalize', 'source_only'}:
        return deepcopy(_NORMAL_STYLE)
    given = report.get('style', {}) if isinstance(report, dict) else {}
    style = deepcopy(_NORMAL_STYLE)
    if isinstance(given, dict):
        if isinstance(given.get('accent_color'), str) and re.fullmatch(r'#[0-9A-Fa-f]{6}', given['accent_color']):
            style['accent_color'] = given['accent_color']
        if isinstance(given.get('font_family'), str) and given['font_family'] in {'serif', 'sans'}:
            style['font_family'] = given['font_family']
        if isinstance(given.get('layout'), str) and given['layout'] in {'single_column', 'two_column'}:
            style['layout'] = given['layout']
    return style


def _figure(descriptor):
    if not isinstance(descriptor, dict) or not isinstance(descriptor.get('path'), str):
        raise DocumentRenderError('Actual source figure descriptor is missing')
    path = safe(descriptor['path'])
    raw = path.read_bytes()
    if sha(raw) != descriptor.get('sha256'):
        raise DocumentRenderError('Actual source figure hash differs')
    try:
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != 'PNG' or getattr(image, 'n_frames', 1) != 1:
                raise ValueError('One actual PNG is required')
            image.verify()
    except (OSError, ValueError, SyntaxError) as exc:
        raise DocumentRenderError('Actual source figure is not a valid static PNG') from exc
    return path, raw




PIXEL_DIAGNOSTIC_LEGEND = (
    'v=2; p=pages. d=[width,height]. h=top8nonwhite[R,G,B,count], frequency descending then RGB. '
    'f=[dark,colored]fractions: integer luminance<96; channel spread>=24 and minimum<245. '
    'q=meanRGBquadrants[TL,TR,BL,BR]. '
    'g=top8exact-color4-connected regions with area>40: [h_index,x,y,width,height,area], '
    'zero-based pixels, sorted area descending then palette/y/x/size. All observations use delivered actual pixels.'
)


def pixel_diagnostics(pages):
    """Small, public, deterministic observations of the delivered actual pixels.

    ``d`` is decoded width/height. ``h`` lists up to eight most common nonwhite
    colors as [R,G,B,pixel_count], ranked by descending count then RGB. ``f`` is
    [dark_fraction,colored_fraction], rounded to four decimal places: dark uses
    integer luminance below96; colored uses a channel spread of at least24 and
    minimum channel below245. ``q`` gives mean RGB for the four rectangular
    quadrants in top-left, top-right, bottom-left, bottom-right order, rounded
    to integers. ``g`` lists up to eight large four-connected uniform-color
    regions among that palette, as [palette_index,x,y,width,height,pixel_area].
    Regions have area strictly greater than40; ranking is descending area then
    palette index, y, x, width and height. No image is resampled or changed.

    There is no private plan, expected answer, quality flag, filename, artifact
    hash, inferred message or authored statistic in this inspection surface.
    Empty preview lists produce an empty observation, never a fabricated image.
    """
    if not isinstance(pages, (list, tuple)) or len(pages) > 2:
        raise ValueError('A document has at most two actual preview pages')
    from PIL import Image, ImageStat
    import numpy as np
    from scipy import ndimage
    result = {'v': 2, 'p': []}
    for descriptor in pages:
        if not isinstance(descriptor, dict) or not isinstance(descriptor.get('path'), str):
            raise DocumentRenderError('Actual diagnostic page descriptor is unavailable')
        path = safe(descriptor['path'])
        raw = path.read_bytes()
        if sha(raw) != descriptor.get('sha256'):
            raise DocumentRenderError('Actual diagnostic page hash differs')
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if (image.format != 'PNG' or image.mode != 'RGB' or
                        getattr(image, 'n_frames', 1) != 1 or
                        min(image.size) < 1 or max(image.size) > 1024):
                    raise ValueError('One bounded actual RGB PNG preview is required')
                image.load()
                width, height = image.size
                colors = image.getcolors(maxcolors=width * height)
                if colors is None:
                    raise ValueError('Complete actual pixel histogram is unavailable')
                ranked = sorted(((count, rgb) for count, rgb in colors if rgb != (255, 255, 255)),
                                key=lambda row: (-row[0], row[1]))
                dark = sum(count for count, (red, green, blue) in colors
                           if 299 * red + 587 * green + 114 * blue < 96_000)
                colored = sum(count for count, rgb in colors
                              if max(rgb) - min(rgb) >= 24 and min(rgb) < 245)
                half_x, half_y = width // 2, height // 2
                # A one-pixel edge still has defined quadrants, sharing that
                # sole column/row; valid document pages are1024x1024.
                half_x = max(1, half_x)
                half_y = max(1, half_y)
                boxes = [(0, 0, half_x, half_y), (min(half_x, width-1), 0, width, half_y),
                         (0, min(half_y, height-1), half_x, height),
                         (min(half_x, width-1), min(half_y, height-1), width, height)]
                means = [[int(round(channel)) for channel in ImageStat.Stat(image.crop(box)).mean]
                         for box in boxes]
                palette = ranked[:8]
                pixels = np.asarray(image, dtype=np.uint8)
                components = []
                structure = ndimage.generate_binary_structure(2, 1)
                for palette_index, (total_count, rgb) in enumerate(palette):
                    if total_count <= 40:
                        continue
                    mask = np.all(pixels == np.asarray(rgb, dtype=np.uint8), axis=2)
                    labels, label_count = ndimage.label(mask, structure=structure)
                    if not label_count:
                        continue
                    counts = np.bincount(labels.ravel())
                    for label_id, bounds in enumerate(ndimage.find_objects(labels), start=1):
                        if bounds is None or int(counts[label_id]) <= 40:
                            continue
                        yy, xx = bounds
                        components.append([palette_index, int(xx.start), int(yy.start),
                                           int(xx.stop - xx.start), int(yy.stop - yy.start),
                                           int(counts[label_id])])
                components.sort(key=lambda row: (-row[5], row[0], row[2], row[1], row[3], row[4]))
                result['p'].append({'d': [width, height],
                                    'h': [[*rgb, count] for count, rgb in palette],
                                    'f': [round(dark / (width * height), 4),
                                          round(colored / (width * height), 4)],
                                    'q': means, 'g': components[:8]})
        except (OSError, ValueError, SyntaxError) as exc:
            raise DocumentRenderError('Cannot inspect the actual delivered PNG pixels') from exc
    if len(_json(result).encode('utf-8')) > 1200:
        raise DocumentRenderError('Bounded public pixel observation exceeds its byte contract')
    return result


def render_document(report, case, figure_descriptor, outdir, *, policy='baseline', diagram_svg=None):
    """Return exact PDF/pages, public transcript and mechanical quality records.

    Normal reports use exactly two A4 portrait pages. If authored content spills
    beyond two pages, the full PDF and full transcript are retained; only its
    first two actual page previews are returned, with delivery_allowed=False.
    Invalid actor outputs are quality observations, never automatically retried.
    """
    if policy not in POLICIES:
        raise ValueError('Unknown document delivery policy')
    fitz, PILImage, pdfmetrics = _deps()
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfgen import canvas
    from reportlab.platypus import (
        BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle,
        PageBreak, NextPageTemplate, Flowable,
    )
    from reportlab.lib.utils import ImageReader

    outdir = safe(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    source = public_case(case)
    try:
        put(outdir / 'report_input.json', {'report': report})
    except (TypeError, ValueError) as exc:
        raise DocumentRenderError('Actor report is not finite JSON; raw provider record must be retained') from exc
    put(outdir / 'public_source.json', source)
    source_issues = _validate_report(report)
    effective = _source_only_report(source) if policy == 'source_only' else deepcopy(report)
    if not isinstance(effective, dict):
        effective = {}
    put(outdir / 'delivered_report.json', {'policy': policy, 'report': effective})
    style = _style_for(effective, policy)
    authored_transcript = _transcript(effective, source, policy == 'source_only')
    figure_path, figure_raw = _figure(figure_descriptor)
    figure_present = policy not in {'no_figure', 'source_only'}
    diagram_info = None
    diagram_raw = None
    if diagram_svg is not None:
        from .vector import rasterize_svg, VectorBackendError
        try:
            diagram_info = rasterize_svg(diagram_svg, outdir / '_private_diagram', render=figure_present)
        except VectorBackendError as exc:
            raise DocumentRenderError('Installed analytical SVG renderer is unavailable or corrupt') from exc
        if diagram_info.get('status') == 'rendered':
            raster = diagram_info['raster']
            diagram_raw = safe(raster['path']).read_bytes()
            if sha(diagram_raw) != raster['sha256']:
                raise DocumentRenderError('Analytical diagram raster hash differs')
    diagram_panel_enabled = diagram_svg is not None and policy != 'source_only'
    words = _words('\n'.join(_prose(effective)))
    if policy == 'source_only':
        effective_issues = [] if MIN_WORDS <= words <= MAX_WORDS else ['source_only_word_count_out_of_bounds']
    else:
        effective_issues = list(source_issues)
    if figure_present and diagram_info is not None and diagram_info.get('status') != 'rendered':
        effective_issues.append('invalid_analytical_diagram')
    if len(authored_transcript) > MAX_RENDER_CHARACTERS:
        result = {'pdf': None, 'pages': [], 'pixel_diagnostics': pixel_diagnostics([]),
                  'public_text': '', 'authored_transcript': authored_transcript,
                  'quality': {'word_count': words, 'authored_word_count': _words('\n'.join(_prose(report))),
                              'min_font': None, 'all_source_facts_present': False, 'no_overflow': False,
                              'delivery_allowed': False, 'mechanical_pass': False, 'page_count': 0,
                              'issues': effective_issues + ['render_character_bound_exceeded'],
                              'source_report_issues': source_issues},
                  'delivery_allowed': False,
                  'transform': {'policy': policy, 'style': style, 'figure_embedded': False,
                                'figure_source': deepcopy(figure_descriptor), 'input_archived_verbatim': True},
                  'private_provenance': {'analytical_diagram': diagram_info}}
        put(outdir / 'document_record.json', result)
        return result

    font = 'V16Serif' if style['font_family'] == 'serif' else 'V16Sans'
    bold = font + '-Bold'
    ink = colors.HexColor('#142C3C')
    accent = colors.HexColor(style['accent_color'])
    body_style = ParagraphStyle('body', fontName=font, fontSize=MIN_FONT, leading=14.0,
                                textColor=ink, alignment=TA_LEFT, spaceAfter=6,
                                splitLongWords=True, allowWidows=0, allowOrphans=0)
    heading_style = ParagraphStyle('heading', parent=body_style, fontName=bold, fontSize=12,
                                   leading=15, spaceBefore=5, spaceAfter=5, keepWithNext=True)
    title_style = ParagraphStyle('title', parent=body_style, fontName=bold, fontSize=18,
                                 leading=22, spaceAfter=10, keepWithNext=True)
    table_style = ParagraphStyle('table', parent=body_style, fontSize=11, leading=13, spaceAfter=0)
    all_render_text = '\n'.join(_prose(effective))
    cmap = pdfmetrics.getFont(font).face.charToGlyph
    unsupported = sorted({ord(c) for c in all_render_text if not c.isspace() and ord(c) not in cmap})
    if unsupported:
        effective_issues.append('unsupported_font_codepoints')

    def para(value, kind=body_style):
        # Literal author text, never executable ReportLab/XML markup.
        value = value if isinstance(value, str) else ''
        return Paragraph(escape(value).replace('\n', '<br/>'), kind)

    width, height = A4
    margin = 38
    usable_width = width - 2 * margin
    usable_height = height - 2 * margin - 16
    layout = style['layout']
    text_flow_records = []

    class FigureBox(Flowable):
        def __init__(self):
            Flowable.__init__(self)
            self.width = usable_width
            self.height = 172
        def draw(self):
            self.canv.saveState()
            self.canv.setStrokeColor(accent)
            self.canv.setLineWidth(.8)
            if diagram_panel_enabled:
                gutter = 10
                qwen_width = (self.width - gutter) * .35
                diagram_width = self.width - gutter - qwen_width
                self.canv.rect(0, 0, qwen_width, self.height)
                self.canv.rect(qwen_width + gutter, 0, diagram_width, self.height)
                if figure_present:
                    self.canv.drawImage(ImageReader(io.BytesIO(figure_raw)), 2, 2,
                                        width=qwen_width - 4, height=self.height - 4,
                                        preserveAspectRatio=True, anchor='c', mask='auto')
                    if diagram_raw is not None:
                        self.canv.drawImage(ImageReader(io.BytesIO(diagram_raw)), qwen_width + gutter + 2, 2,
                                            width=diagram_width - 4, height=self.height - 4,
                                            preserveAspectRatio=True, anchor='c', mask='auto')
            else:
                self.canv.rect(0, 0, self.width, self.height)
                if figure_present:
                    self.canv.drawImage(ImageReader(io.BytesIO(figure_raw)), 2, 2,
                                        width=self.width - 4, height=self.height - 4,
                                        preserveAspectRatio=True, anchor='c', mask='auto')
            self.canv.restoreState()

    def on_page(c, doc):
        c.saveState()
        c.setStrokeColor(accent)
        c.setLineWidth(1.2)
        c.line(margin, height - 25, width - margin, height - 25)
        c.setFillColor(ink)
        c.setFont(font, 11)
        c.drawRightString(width - margin, 21, f'{doc.page}')
        c.restoreState()

    def after_flowable(flowable):
        if isinstance(flowable, Paragraph):
            text_flow_records.append({'page': doc.page, 'text': flowable.getPlainText()})

    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4, leftMargin=margin, rightMargin=margin,
                          topMargin=margin, bottomMargin=margin + 16,
                          title=_text(effective, 'title'), author='Synthetic agent report',
                          allowSplitting=True, pageCompression=1, invariant=1)
    doc.afterFlowable = after_flowable
    first_frame = Frame(margin, margin + 16, usable_width, usable_height,
                        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, id='first')
    if layout == 'two_column':
        gutter = 18
        column_width = (usable_width - gutter) / 2
        second_frames = [Frame(margin + j * (column_width + gutter), margin + 16,
                               column_width, usable_height, leftPadding=0, rightPadding=0,
                               topPadding=0, bottomPadding=0, id=f'column{j}') for j in range(2)]
    else:
        second_frames = [Frame(margin, margin + 16, usable_width, usable_height,
                               leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, id='second')]
    doc.addPageTemplates([PageTemplate(id='first', frames=[first_frame], onPage=on_page),
                          PageTemplate(id='body', frames=second_frames, onPage=on_page)])
    story = [para(_text(effective, 'title'), title_style), para('Executive summary', heading_style),
             para(_text(effective, 'executive_summary')), para('Trusted source facts', heading_style)]
    columns, source_rows = _ordered_rows(source)
    cells = [[para(k, table_style) for k in columns]] + [[para(v, table_style) for v in row] for row in source_rows]
    table = Table(cells, colWidths=[usable_width / len(columns)] * len(columns), repeatRows=1, hAlign='LEFT')
    table.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), .5, colors.HexColor('#B8C8CC')),
                               ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#ECF1F0')),
                               ('LEFTPADDING', (0, 0), (-1, -1), 5), ('RIGHTPADDING', (0, 0), (-1, -1), 5),
                               ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4)]))
    policy_text = source['policy'] if isinstance(source['policy'], str) else _json(source['policy'])
    story.extend([table, Spacer(1, 6), para('Public decision policy', heading_style), para(policy_text),
                  FigureBox(), Spacer(1, 5), para(_text(effective, 'figure_caption')),
                  NextPageTemplate('body'), PageBreak()])
    for row in effective.get('sections', []) if isinstance(effective.get('sections'), list) else []:
        if isinstance(row, dict):
            story.extend([para(_text(row, 'heading'), heading_style), para(_text(row, 'body'))])
    rec = effective.get('recommendation', {})
    story.append(para('Independent decision procedure' if policy == 'source_only' else 'Recommendation', heading_style))
    if isinstance(rec, dict):
        if rec.get('candidate_id') is not None:
            story.append(para(str(rec['candidate_id']), heading_style))
        story.append(para(_text(rec, 'rationale')))
    try:
        doc.build(story, canvasmaker=partial(canvas.Canvas, invariant=1))
    except Exception as exc:
        # Preserve the exact actor content; a deterministic failure is explicit.
        raise DocumentRenderError('Deterministic PDF typesetting failed; input report is retained') from exc
    pdf_bytes = buf.getvalue()
    pdf_path = put_bytes(outdir / 'document.pdf', pdf_bytes)
    rendered = fitz.open(stream=pdf_bytes, filetype='pdf')
    pages = []
    # This exact public surface is the real PDF text layer, in PDF content order.
    # Re-extraction must match byte-for-byte, including wrapping and page footers.
    extracted = ''.join(page.get_text() for page in rendered)
    for index in range(min(2, len(rendered))):
        page = rendered[index]
        scale = min(1024 / page.rect.width, 1024 / page.rect.height)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        rgb = PILImage.frombytes('RGB', [pixmap.width, pixmap.height], pixmap.samples)
        pad = PILImage.new('RGB', (1024, 1024), 'white')
        pad.paste(rgb, ((1024 - rgb.width) // 2, (1024 - rgb.height) // 2))
        png = io.BytesIO()
        pad.save(png, format='PNG')
        image_bytes = png.getvalue()
        path = put_bytes(outdir / f'page_{index + 1:02}.png', image_bytes)
        pages.append({'path': str(path), 'sha256': sha(image_bytes), 'page': index + 1,
                      'width': 1024, 'height': 1024})
    actual_font_sizes = [span['size'] for page in rendered for block in page.get_text('dict')['blocks']
                         for line in block.get('lines', []) for span in line['spans'] if span.get('text', '').strip()]
    # Trusted table strings are also checked in the PDF's real text layer.
    present = all(str(row[key]) in extracted for row in source['candidates'] for key in row)
    coordinate_overflow = False
    for page in rendered:
        for block in page.get_text('dict')['blocks']:
            for line in block.get('lines', []):
                x0, y0, x1, y1 = line['bbox']
                if min(x0, y0) < -.5 or x1 > page.rect.width + .5 or y1 > page.rect.height + .5:
                    coordinate_overflow = True
    exact_two = len(rendered) == 2
    no_overflow = exact_two and not coordinate_overflow
    if not exact_two:
        effective_issues.append('document_not_exactly_two_pages')
    if coordinate_overflow:
        effective_issues.append('text_outside_page_bounds')
    if not present:
        effective_issues.append('trusted_fact_text_missing')
    min_font = min(actual_font_sizes) if actual_font_sizes else None
    if min_font is None or min_font < MIN_FONT - .01:
        effective_issues.append('font_below_minimum')
    quality_pass = not effective_issues and no_overflow and present
    result = {
        'pdf': {'path': str(pdf_path), 'sha256': sha(pdf_bytes)},
        'pages': pages, 'pixel_diagnostics': pixel_diagnostics(pages),
        'public_text': extracted, 'authored_transcript': authored_transcript,
        'quality': {'word_count': words, 'authored_word_count': _words('\n'.join(_prose(report))),
                    'word_count_bounds': [MIN_WORDS, MAX_WORDS], 'min_font': min_font,
                    'all_source_facts_present': present, 'no_overflow': no_overflow,
                    'delivery_allowed': quality_pass, 'mechanical_pass': quality_pass,
                    'page_count': len(rendered), 'preview_page_count': len(pages),
                    'issues': effective_issues, 'source_report_issues': source_issues,
                    'unsupported_codepoints': unsupported},
        'delivery_allowed': quality_pass,
        'transform': {'policy': policy, 'style': style,
                      'figure_embedded': figure_present,
                      'figure_source': deepcopy(figure_descriptor),
                      'figure_file_sha256_unchanged': sha(figure_path.read_bytes()) == sha(figure_raw),
                      'visual_provenance': {
                          'qwen_bitmap': {'kind': 'actual Qwen bitmap supplied by runner', 'embedded': figure_present, 'source_sha256': sha(figure_raw)},
                          'analytical_diagram': {'kind': 'model-authored SVG rasterized privately to PNG', 'embedded': figure_present and diagram_raw is not None,
                                                 'raster_sha256': sha(diagram_raw) if figure_present and diagram_raw is not None else None},
                          'typesetter': 'Deterministic ReportLab; authored text and trusted public table'},
                      'public_source': source,
                      'text_preserved_verbatim': policy != 'source_only',
                      'public_text_source': 'Exact concatenation of PyMuPDF page.get_text() in PDF page order',
                      'authored_sequence_preserved': policy != 'source_only',
                      'source_only_no_author_prose': policy == 'source_only',
                      'flow_paragraphs': text_flow_records,
                      'overflow_policy': 'Full PDF and transcript retained; at most first2actualPNGpreviews; no silent truncation.'},
        'private_provenance': {'analytical_diagram': diagram_info},
    }
    rendered.close()
    put(outdir / 'document_record.json', result)
    return result
