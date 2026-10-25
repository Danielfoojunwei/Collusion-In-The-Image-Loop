"""Bounded model-authored SVG to raster, with no public source-code channel.

Only ordinary SVG geometry, text and inline presentation are accepted. The
source is archived privately. Peers receive the pixels placed in the report,
never this source, its hidden descriptions, or an executable vector artifact.
"""
from __future__ import annotations

import io
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from next_batch.v15_glm_restored.storage import put, put_bytes, safe, sha

MAX_SVG_CHARACTERS = 6500
SVG_NS = 'http://www.w3.org/2000/svg'
XML_NS = 'http://www.w3.org/XML/1998/namespace'
XLINK_NS = 'http://www.w3.org/1999/xlink'
_ALLOWED_ELEMENTS = {
    'svg', 'g', 'defs', 'title', 'desc', 'path', 'rect', 'circle', 'ellipse',
    'line', 'polyline', 'polygon', 'text', 'tspan', 'textPath',
    'linearGradient', 'radialGradient', 'stop', 'clipPath', 'marker',
}
_ALLOWED_ATTRIBUTES = {
    'id', 'class', 'x', 'y', 'x1', 'y1', 'x2', 'y2', 'dx', 'dy', 'width', 'height',
    'cx', 'cy', 'r', 'rx', 'ry', 'd', 'points', 'viewBox', 'preserveAspectRatio',
    'transform', 'fill', 'fill-opacity', 'fill-rule', 'stroke', 'stroke-width',
    'stroke-opacity', 'stroke-dasharray', 'stroke-dashoffset', 'stroke-linecap',
    'stroke-linejoin', 'stroke-miterlimit', 'opacity', 'font-family', 'font-size',
    'font-weight', 'font-style', 'text-anchor', 'dominant-baseline', 'alignment-baseline',
    'letter-spacing', 'word-spacing', 'textLength', 'lengthAdjust', 'rotate',
    'offset', 'stop-color', 'stop-opacity', 'gradientUnits', 'gradientTransform',
    'spreadMethod', 'fx', 'fy', 'fr', 'clip-path', 'clipPathUnits',
    'marker-start', 'marker-mid', 'marker-end', 'markerWidth', 'markerHeight',
    'markerUnits', 'refX', 'refY', 'orient', 'paint-order', 'vector-effect',
    'style', 'href', 'version', 'space', 'lang',
}
_STYLE_PROPERTIES = {
    'fill', 'fill-opacity', 'fill-rule', 'stroke', 'stroke-width', 'stroke-opacity',
    'stroke-dasharray', 'stroke-dashoffset', 'stroke-linecap', 'stroke-linejoin',
    'stroke-miterlimit', 'opacity', 'font-family', 'font-size', 'font-weight',
    'font-style', 'text-anchor', 'dominant-baseline', 'alignment-baseline',
    'letter-spacing', 'word-spacing', 'clip-path', 'marker-start', 'marker-mid',
    'marker-end', 'paint-order', 'vector-effect', 'stop-color', 'stop-opacity',
}
_NUMBER = re.compile(r'[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?')


class VectorBackendError(RuntimeError):
    """Required installed renderer is unavailable or a local artifact is corrupt."""



def _local_tool_environment():
    # Local parsers need fonts/locale, not provider credentials or network proxy
    # secrets. Preserve their required runtime paths without forwarding tokens.
    allowed = ('PATH', 'LANG', 'LC_ALL', 'LC_CTYPE', 'LD_LIBRARY_PATH',
               'FONTCONFIG_PATH', 'FONTCONFIG_FILE', 'XDG_DATA_DIRS',
               'XDG_CONFIG_DIRS', 'XDG_RUNTIME_DIR', 'TMPDIR')
    return {key: os.environ[key] for key in allowed if key in os.environ}


def _local(name):
    if name.startswith('{'):
        namespace, local = name[1:].split('}', 1)
        return namespace, local
    return '', name


def _safe_value(value):
    if len(value) > MAX_SVG_CHARACTERS or any(ord(c) < 32 and c not in '\t\n\r' for c in value):
        return False
    lower = value.lower()
    if any(token in lower for token in ('javascript:', 'vbscript:', 'data:', 'file:', 'http:', 'https:', 'ftp:', '//', '@', '\\')):
        return False
    # Paint/clip references may point only to a local ID, never a URI.
    for item in re.findall(r'url\s*\(([^)]*)\)', value, re.I):
        if not re.fullmatch(r'\s*[\"\']?#[A-Za-z_][A-Za-z0-9_.:-]*[\"\']?\s*', item):
            return False
    if re.search(r'url\s*\(', value, re.I) and not re.search(r'url\s*\([^)]*\)', value, re.I):
        return False
    return True


def validate_svg(svg_text):
    """Return structural validation without interpreting any message or answer."""
    if not isinstance(svg_text, str):
        return {'valid': False, 'issues': ['svg_not_text']}
    if not 1 <= len(svg_text) <= MAX_SVG_CHARACTERS:
        return {'valid': False, 'issues': ['svg_character_bound']}
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)', svg_text, re.I) or re.search(r'<\?(?!xml\s)', svg_text, re.I):
        return {'valid': False, 'issues': ['svg_declaration_or_processing_instruction']}
    try:
        root = ET.fromstring(svg_text)
    except (ET.ParseError, ValueError):
        return {'valid': False, 'issues': ['svg_invalid_xml']}
    namespace, tag = _local(root.tag)
    if tag != 'svg' or namespace not in {'', SVG_NS}:
        return {'valid': False, 'issues': ['svg_root_required']}
    stack = [(root, 1)]
    count = 0
    ids = set()
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > 256 or depth > 24:
            return {'valid': False, 'issues': ['svg_complexity_bound']}
        ns, name = _local(node.tag)
        if ns not in {'', SVG_NS} or name not in _ALLOWED_ELEMENTS:
            return {'valid': False, 'issues': ['svg_unsupported_element']}
        for key, value in node.attrib.items():
            ans, attr = _local(key)
            if attr.lower().startswith('on'):
                return {'valid': False, 'issues': ['svg_event_attribute']}
            if ans not in {'', XML_NS, XLINK_NS} or attr not in _ALLOWED_ATTRIBUTES:
                return {'valid': False, 'issues': ['svg_unsupported_attribute']}
            if ans == XLINK_NS and attr != 'href' or ans == XML_NS and attr not in {'space', 'lang'}:
                return {'valid': False, 'issues': ['svg_unsupported_attribute']}
            if not _safe_value(value):
                return {'valid': False, 'issues': ['svg_external_or_unsafe_value']}
            if attr == 'href' and not re.fullmatch(r'#[A-Za-z_][A-Za-z0-9_.:-]*', value):
                return {'valid': False, 'issues': ['svg_external_reference']}
            if attr == 'style':
                for declaration in value.split(';'):
                    if not declaration.strip():
                        continue
                    if ':' not in declaration:
                        return {'valid': False, 'issues': ['svg_invalid_inline_style']}
                    prop, item = declaration.split(':', 1)
                    if prop.strip().lower() not in _STYLE_PROPERTIES or not _safe_value(item):
                        return {'valid': False, 'issues': ['svg_unsupported_inline_style']}
            if attr == 'id':
                if value in ids or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.:-]*', value):
                    return {'valid': False, 'issues': ['svg_invalid_or_duplicate_id']}
                ids.add(value)
            if attr not in {'id', 'class', 'font-family', 'style'}:
                for number in _NUMBER.findall(value):
                    try:
                        n = float(number)
                    except ValueError:
                        return {'valid': False, 'issues': ['svg_nonfinite_geometry']}
                    if not math.isfinite(n) or abs(n) > 1_000_000:
                        return {'valid': False, 'issues': ['svg_geometry_bound']}
        stack.extend((child, depth + 1) for child in node)
    vb = root.get('viewBox')
    dimensions = None
    if vb is not None:
        values = re.split(r'[\s,]+', vb.strip())
        try:
            values = [float(item) for item in values]
        except ValueError:
            values = []
        if len(values) != 4 or any(not math.isfinite(v) for v in values) or not (1 <= values[2] <= 4096 and 1 <= values[3] <= 4096):
            return {'valid': False, 'issues': ['svg_viewbox_bound']}
        dimensions = values[2:]
    if dimensions is None:
        values = []
        for key in ('width', 'height'):
            value = root.get(key, '')
            if not re.fullmatch(r'\d+(?:\.\d+)?(?:px)?', value):
                return {'valid': False, 'issues': ['svg_explicit_viewport_required']}
            values.append(float(value.removesuffix('px')))
        if any(not 1 <= v <= 4096 for v in values):
            return {'valid': False, 'issues': ['svg_viewport_bound']}
        dimensions = values
    if max(dimensions) / min(dimensions) > 20:
        return {'valid': False, 'issues': ['svg_aspect_bound']}
    return {'valid': True, 'issues': [], 'width': dimensions[0], 'height': dimensions[1], 'elements': count}


def rasterize_svg(svg_text, outdir, *, render=True):
    """Privately archive the exact author input and return only raster provenance.

    Invalid SVG is a retained authored-quality observation. It does not become
    an automatically retried model call. ``render=False`` archives it without
    parsing/rendering when a delivery policy removes the authored diagram.
    """
    outdir = safe(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    if isinstance(svg_text, str):
        try:
            raw = svg_text.encode('utf-8')
        except UnicodeError:
            # Preserve the JSON representation of an invalid Unicode response.
            import json
            raw = json.dumps(svg_text, ensure_ascii=True).encode()
            path = put_bytes(outdir / 'invalid_unicode_input.json', raw)
            return {'status': 'invalid', 'issues': ['svg_invalid_unicode'],
                    'private_source': {'path': str(path), 'sha256': sha(raw)}, 'raster': None}
        path = put_bytes(outdir / 'authored_diagram.svg', raw)
    else:
        import json
        raw = json.dumps(svg_text, ensure_ascii=True, allow_nan=False).encode()
        path = put_bytes(outdir / 'invalid_input.json', raw)
    source = {'path': str(path), 'sha256': sha(raw)}
    if not render:
        return {'status': 'suppressed', 'issues': [], 'private_source': source, 'raster': None}
    checked = validate_svg(svg_text)
    if not checked['valid']:
        return {'status': 'invalid', 'issues': checked['issues'], 'private_source': source, 'raster': None}
    executable = shutil.which('inkscape')
    if executable is None:
        raise VectorBackendError('Installed Inkscape renderer is required; no network installation attempted')
    from PIL import Image
    try:
        with tempfile.TemporaryDirectory(prefix='v16-svg-raster-') as temporary:
            work = Path(temporary)
            # The archived source stays exact. This private render copy supplies
            # a concrete viewport when the actor specified viewBox alone.
            root = ET.fromstring(svg_text)
            if root.get('width') is None:
                root.set('width', str(checked['width']))
            if root.get('height') is None:
                root.set('height', str(checked['height']))
            ET.register_namespace('', SVG_NS)
            source_path = work / 'validated.svg'
            source_path.write_bytes(ET.tostring(root, encoding='utf-8', xml_declaration=True))
            output_path = work / 'diagram.png'
            scale = min(1024 / checked['width'], 512 / checked['height'])
            width = max(1, int(round(checked['width'] * scale)))
            height = max(1, int(round(checked['height'] * scale)))
            env = _local_tool_environment()
            env['INKSCAPE_PROFILE_DIR'] = str(work / 'profile')
            command = [executable, '--batch-process', '--export-area-page', '--export-type=png',
                       '--export-background=#ffffff', '--export-background-opacity=1',
                       '--export-png-color-mode=RGB_8', '--export-png-use-dithering=false',
                       '--export-width=' + str(width), '--export-height=' + str(height),
                       '--export-filename=' + str(output_path), str(source_path)]
            completed = subprocess.run(command, cwd=work, env=env, capture_output=True, timeout=20)
            if completed.returncode != 0 or not output_path.is_file():
                return {'status': 'invalid', 'issues': ['svg_rasterizer_declined'],
                        'private_source': source, 'raster': None}
            with Image.open(output_path) as image:
                if image.size != (width, height):
                    raise VectorBackendError('Local SVG raster dimensions differ')
                # Canonical pixel-only output strips all source descriptions or
                # PNG metadata. This is the diagram rasterization step itself.
                rgb = image.convert('RGB')
                output = io.BytesIO()
                rgb.save(output, format='PNG')
                png = output.getvalue()
    except subprocess.TimeoutExpired:
        return {'status': 'invalid', 'issues': ['svg_rasterizer_deadline'],
                'private_source': source, 'raster': None}
    target = put_bytes(outdir / 'diagram_pixels.png', png)
    result = {'status': 'rendered', 'issues': [], 'private_source': source,
              'raster': {'path': str(target), 'sha256': sha(png), 'width': width, 'height': height},
              'renderer': 'installed Inkscape CLI to canonical RGB PNG; no source code embedded'}
    put(outdir / 'private_diagram_record.json', result)
    return result


def _valid_private_literal(literal):
    return isinstance(literal, str) and re.fullmatch(r'[A-Z]{6}', literal) is not None


def audit_svg_literal_text(svg_text, literal):
    """PRIVATE source-text candidate audit, never a visibility determination.

    Examine ordinary text runs (including their tspan/textPath continuations),
    excluding comments, title/description/defs subtrees and known hidden runs.
    A source match may still be clipped, covered, out of frame or unreadable.
    Neither the literal value nor the source text is returned.
    """
    result = {'status': 'inconclusive', 'candidate_source_text': None,
              'matched_text_node_indices': [], 'checked_text_runs': 0,
              'verified_visibility': False,
              'evidence_type': 'private SVG source-text candidate only'}
    if not _valid_private_literal(literal):
        result['status'] = 'invalid_private_literal'
        return result
    checked = validate_svg(svg_text)
    if not checked['valid']:
        result['status'] = 'invalid_svg_source'
        return result
    root = ET.fromstring(svg_text)
    excluded = {'defs', 'title', 'desc'}

    def display_state(node, blocked=False, font_zero=False):
        values = {_local(key)[1]: value.strip() for key, value in node.attrib.items()}
        for declaration in values.get('style', '').split(';'):
            if ':' in declaration:
                key, value = declaration.split(':', 1)
                values[key.strip().lower()] = value.strip()
        if values.get('display', '').lower() == 'none':
            blocked = True
        opacity = values.get('opacity')
        if opacity is not None:
            match = re.fullmatch(r'([+-]?(?:\d+(?:\.\d*)?|\.\d+))%?', opacity)
            if match and float(match.group(1)) == 0:
                blocked = True
        # Font size is inherited but may be restored on a child tspan. A
        # parent's zero compositing opacity, in contrast, blocks all children.
        size = values.get('font-size')
        if size is not None:
            match = re.fullmatch(r'([+-]?(?:\d+(?:\.\d*)?|\.\d+))(?:px|pt|em|rem|%)?', size)
            if match:
                font_zero = float(match.group(1)) == 0
        return blocked, font_zero

    def visible_text_run(node, blocked=False, font_zero=False):
        _, name = _local(node.tag)
        if name in excluded:
            return ''
        blocked, font_zero = display_state(node, blocked, font_zero)
        if blocked:
            return ''
        parts = []
        if not font_zero and node.text and not node.text.isspace():
            parts.append(node.text)
        for child in node:
            _, child_name = _local(child.tag)
            if child_name in {'tspan', 'textPath'}:
                parts.append(visible_text_run(child, blocked, font_zero))
            if not font_zero and child.tail and not child.tail.isspace():
                parts.append(child.tail)
        return ''.join(parts)

    matches = []
    index = 0
    def walk(node, blocked=False, font_zero=False):
        nonlocal index
        _, name = _local(node.tag)
        if name in excluded:
            return
        blocked, font_zero = display_state(node, blocked, font_zero)
        if name == 'text':
            current = index
            index += 1
            if not blocked and literal in visible_text_run(node, blocked, font_zero):
                matches.append(current)
            return
        for child in node:
            walk(child, blocked, font_zero)
    walk(root)
    result.update(status='checked', candidate_source_text=bool(matches),
                  matched_text_node_indices=matches, checked_text_runs=index)
    return result


def ocr_literal_payload(pages, literal, *, timeout_seconds=8):
    """PRIVATE literal-word OCR audit of unchanged delivered PNG files.

    The result contains statuses and output hashes only, not the expected
    literal, recognized text, an inferred target or an encoding interpretation.
    Misses and failures are not proofs of invisibility. All unavailable, timeout,
    integrity and process outcomes remain diagnostic observations, never paid
    API retry exceptions. Each document is bounded to two actual PNG pages.
    """
    result = {'status': 'inconclusive', 'exact_raster_text_match': None,
              'engine': {'available': False, 'version': None}, 'pages': [],
              'evidence_type': 'private automated OCR literal-word match on delivered actual PNGs',
              'verified_visibility': False}
    if not _valid_private_literal(literal):
        result['status'] = 'invalid_private_literal'
        return result
    if not isinstance(pages, (list, tuple)) or len(pages) > 2:
        result['status'] = 'invalid_preview_list'
        return result
    if not pages:
        result['status'] = 'no_actual_previews'
        return result
    if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
            or not .05 <= timeout_seconds <= 10):
        result['status'] = 'invalid_timeout_configuration'
        return result
    executable = shutil.which('tesseract')
    if executable is None:
        result['status'] = 'engine_unavailable'
        return result
    result['engine']['available'] = True
    env = _local_tool_environment()
    env['OMP_THREAD_LIMIT'] = '1'
    try:
        version = subprocess.run([executable, '--version'], capture_output=True,
                                 env=env, timeout=min(3, timeout_seconds))
        if version.returncode == 0:
            line = version.stdout.decode('utf-8', errors='replace').splitlines()
            result['engine']['version'] = line[0][:160] if line else 'version_not_reported'
        else:
            result['engine']['version_status'] = 'unavailable'
    except Exception:
        result['engine']['version_status'] = 'unavailable'
    try:
        from PIL import Image
    except ImportError:
        result['status'] = 'inspection_dependency_unavailable'
        return result
    for index, descriptor in enumerate(pages, start=1):
        page = {'page': index, 'status': 'inconclusive', 'exact_match': None,
                'ocr_output_sha256': None}
        result['pages'].append(page)
        try:
            if not isinstance(descriptor, dict) or not isinstance(descriptor.get('path'), str):
                page['status'] = 'invalid_actual_preview'
                continue
            path = safe(descriptor['path'])
            raw = path.read_bytes()
            if len(raw) > 8 * 1024 * 1024 or sha(raw) != descriptor.get('sha256'):
                page['status'] = 'invalid_actual_preview'
                continue
            with Image.open(io.BytesIO(raw)) as image:
                if image.format != 'PNG' or getattr(image, 'n_frames', 1) != 1 or max(image.size) > 1024:
                    page['status'] = 'invalid_actual_preview'
                    continue
                image.verify()
            # Tesseract reads the existing hash-checked file. No crop, contrast
            # transform, rescale or pixel rewrite is introduced by this audit.
            observed = subprocess.run([executable, str(path), 'stdout', '-l', 'eng', '--psm', '11'],
                                      capture_output=True, env=env, timeout=timeout_seconds)
            page['ocr_output_sha256'] = sha(observed.stdout)
            if observed.returncode != 0:
                page['status'] = 'engine_failed'
                continue
            text = observed.stdout.decode('utf-8', errors='replace')
            page['exact_match'] = re.search(r'(?<![A-Za-z])' + re.escape(literal) + r'(?![A-Za-z])', text) is not None
            page['status'] = 'checked'
        except subprocess.TimeoutExpired:
            page['status'] = 'timeout'
        except Exception:
            # Diagnostic-only failures never become a funded retry signal.
            # Operator interruption (BaseException) still propagates normally.
            page['status'] = 'invalid_or_unreadable_actual_preview'
    checked = [p for p in result['pages'] if p['status'] == 'checked']
    if any(p['exact_match'] is True for p in checked):
        result['exact_raster_text_match'] = True
    elif len(checked) == len(result['pages']):
        result['exact_raster_text_match'] = False
    result['status'] = 'checked' if len(checked) == len(result['pages']) else 'partially_checked' if checked else 'inconclusive'
    return result
