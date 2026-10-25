"""Offline, pixel-exact transport encoding for new canonical requests.

Logical PNG inputs and request hashes remain unchanged. Only a deep-copied wire
chat payload receives lossless WebP data URLs; /images stays entirely unchanged.
The caller must anchor the returned
mapping immutably, reserve both budgets, and dispatch these exact wire bytes.
This module never opens a network connection or reads or writes a paid ledger.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from functools import lru_cache
import hashlib
import io
from pathlib import Path
import re
import struct
import zlib

import PIL
from PIL import Image, features

from visual_collusion.common import atomic_bytes, canonical, digest


MAX_WIRE_BYTES = 10 * 1024 * 1024
_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_PNG_PREFIX = 'data:image/png;base64,'
_WEBP_PREFIX = 'data:image/webp;base64,'
_PARAMETERS = {'lossless': True, 'quality': 100, 'method': 6, 'exact': True}
_MAPPING_KEYS = {
    'version', 'request_id', 'endpoint', 'logical_request_hash',
    'logical_body_utf8_bytes', 'wire_request_hash', 'wire_body_sha256',
    'wire_body_utf8_bytes', 'max_bytes', 'encoder', 'images',
}
_IMAGE_KEYS = {
    'message_index', 'content_index', 'source_sha256', 'wire_sha256',
    'wire_blob', 'source_mime', 'wire_mime', 'source_byte_count',
    'wire_byte_count', 'mode', 'wire_mode', 'size', 'frame_count', 'rgba_sha256',
}


class WireEncodingError(ValueError):
    """Local preparation/verification failed; do not reserve or send a POST."""


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _body(payload):
    try:
        return canonical(payload).encode('utf-8')
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise WireEncodingError('Payload is not finite canonical JSON') from None


def _limit(max_bytes):
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_WIRE_BYTES:
        raise WireEncodingError('Wire limit must be a positive integer at most 10 MiB')


def _encoder():
    if not features.check('webp'):
        raise WireEncodingError('Pillow lossless WebP support is unavailable')
    webp_version = features.version('webp')
    if not isinstance(webp_version, str) or not webp_version:
        raise WireEncodingError('Pillow lossless WebP encoder version is unavailable')
    return {
        'name': 'pillow_lossless_webp_v1',
        'pillow_version': PIL.__version__,
        'libwebp_version': webp_version,
        'parameters': dict(_PARAMETERS),
        'source_metadata_policy': 'PNG IHDR/IDAT/IEND only; RGB or RGBA; no color or ancillary metadata',
        'pixel_comparison': 'Exact decoded RGBA bytes, dimensions, and one frame',
    }


def _locations(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('messages'), list):
        raise WireEncodingError('Expected a chat object with an ordered messages list')
    values = []
    for message_index, message in enumerate(payload['messages']):
        if not isinstance(message, dict):
            raise WireEncodingError('Chat message must be an object')
        content = message.get('content')
        if not isinstance(content, list):
            continue
        for content_index, block in enumerate(content):
            if not isinstance(block, dict):
                raise WireEncodingError('Chat content block must be an object')
            if block.get('type') != 'image_url':
                continue
            image = block.get('image_url')
            if not isinstance(image, dict) or not isinstance(image.get('url'), str):
                raise WireEncodingError('Image block requires an inline PNG URL')
            values.append((message_index, content_index, image['url']))
    return values


def _source_bytes(url):
    if not url.startswith(_PNG_PREFIX):
        raise WireEncodingError('Logical image must be an original inline PNG')
    try:
        encoded = url[len(_PNG_PREFIX):]
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise WireEncodingError('Logical PNG base64 is invalid') from None
    if (not data or len(data) > _MAX_IMAGE_BYTES or
            base64.b64encode(data).decode('ascii') != encoded):
        raise WireEncodingError('Logical PNG is oversized or noncanonical base64')
    return data


def _png_chunks(data):
    """Reject even unknown PNG chunks rather than discard color/other context."""
    if not data.startswith(b'\x89PNG\r\n\x1a\n'):
        raise WireEncodingError('Logical image does not contain PNG bytes')
    position, kinds = 8, []
    while position < len(data):
        if position + 12 > len(data):
            raise WireEncodingError('PNG chunk is truncated')
        count = struct.unpack('>I', data[position:position + 4])[0]
        end = position + 12 + count
        if end > len(data):
            raise WireEncodingError('PNG chunk is truncated')
        kind = data[position + 4:position + 8]
        value = data[position + 8:position + 8 + count]
        crc = struct.unpack('>I', data[position + 8 + count:end])[0]
        if crc != zlib.crc32(kind + value) & 0xffffffff:
            raise WireEncodingError('PNG chunk checksum is invalid')
        if kind not in {b'IHDR', b'IDAT', b'IEND'}:
            raise WireEncodingError('PNG metadata/color context or unknown chunk is unsupported')
        kinds.append(kind)
        position = end
        if kind == b'IEND':
            if count or position != len(data):
                raise WireEncodingError('PNG has invalid trailing data')
            break
    if (not kinds or kinds[0] != b'IHDR' or kinds[-1] != b'IEND' or
            kinds.count(b'IHDR') != 1 or kinds.count(b'IEND') != 1 or
            b'IDAT' not in kinds or any(kind != b'IDAT' for kind in kinds[1:-1])):
        raise WireEncodingError('PNG chunk sequence is invalid')


def _webp_chunks(data):
    if (len(data) < 20 or data[:4] != b'RIFF' or data[8:12] != b'WEBP' or
            struct.unpack('<I', data[4:8])[0] != len(data) - 8):
        raise WireEncodingError('Wire image is not a complete WebP container')
    position, kinds = 12, []
    while position < len(data):
        if position + 8 > len(data):
            raise WireEncodingError('WebP chunk is truncated')
        kind = data[position:position + 4]
        count = struct.unpack('<I', data[position + 4:position + 8])[0]
        end = position + 8 + count
        padded = end + (count & 1)
        if padded > len(data) or (count & 1 and data[end] != 0):
            raise WireEncodingError('WebP chunk/padding is invalid')
        if kind not in {b'VP8L', b'VP8X'}:
            raise WireEncodingError('Wire WebP contains metadata, animation, or lossy content')
        if kind == b'VP8X':
            value = data[position + 8:end]
            if count != 10 or value[0] & ~0x10 or value[1:4] != b'\0\0\0':
                raise WireEncodingError('Wire WebP has unsupported extended context')
        kinds.append(kind)
        position = padded
    if kinds not in ([b'VP8L'], [b'VP8X', b'VP8L']):
        raise WireEncodingError('Wire WebP must contain exactly one lossless still image')


def _raster(data, image_format):
    if image_format == 'PNG':
        _png_chunks(data)
    else:
        _webp_chunks(data)
    try:
        with Image.open(io.BytesIO(data)) as image:
            if (image.format != image_format or image.mode not in {'RGB', 'RGBA'} or
                    getattr(image, 'n_frames', 1) != 1 or
                    not all(1 <= side <= 1024 for side in image.size)):
                raise WireEncodingError('Image mode, dimensions, frame count, or format is unsupported')
            image.load()
            if image_format == 'PNG' and (image.info or image.getexif()):
                raise WireEncodingError('PNG metadata/color context is unsupported')
            if image_format == 'WEBP' and set(image.info) - {'loop', 'background', 'timestamp', 'duration'}:
                raise WireEncodingError('Wire WebP metadata/color context is unsupported')
            return image.mode, list(image.size), image.convert('RGBA').tobytes(), image.copy()
    except WireEncodingError:
        raise
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        raise WireEncodingError('Image cannot be decoded exactly') from None


@lru_cache(maxsize=16)
def _encode(source, encoder_profile):
    # Cache keys contain exact immutable PNG bytes and the complete encoder
    # profile. Cached values are immutable; no returned mapping owns this state.
    mode, size, rgba, image = _raster(source, 'PNG')
    output = io.BytesIO()
    try:
        image.save(output, format='WEBP', **_PARAMETERS)
    except (OSError, ValueError, TypeError):
        raise WireEncodingError('Lossless WebP encoding failed') from None
    wire = output.getvalue()
    wire_mode, wire_size, wire_rgba, _ = _raster(wire, 'WEBP')
    if size != wire_size or rgba != wire_rgba:
        raise WireEncodingError('Lossless encoder changed decoded pixels or dimensions')
    return wire, mode, wire_mode, tuple(size), _sha(rgba)


def _blob_path(out, relative, wire_sha):
    if not isinstance(wire_sha, str) or re.fullmatch(r'[0-9a-f]{64}', wire_sha) is None:
        raise WireEncodingError('Wire blob path/hash is invalid')
    expected = 'transport_blobs/' + wire_sha + '.webp'
    if relative != expected:
        raise WireEncodingError('Wire blob path/hash is invalid')
    directory = Path(out) / 'transport_blobs'
    path = directory / (wire_sha + '.webp')
    if directory.is_symlink() or path.is_symlink():
        raise WireEncodingError('Wire blob must not be a symlink')
    return path


def _only_urls_changed(logical, wire, locations):
    restored = deepcopy(wire)
    for mi, ci, original_url in locations:
        restored['messages'][mi]['content'][ci]['image_url']['url'] = original_url
    if _body(restored) != _body(logical):
        raise WireEncodingError('Transport encoding changed a field other than an image URL')


def prepare_wire(out, request_id, endpoint, payload, max_bytes=MAX_WIRE_BYTES):
    """Prepare exact wire JSON and separate immutable-content chat image blobs.

    Call before reservations. Existing paid request IDs must use their original
    cached/missing path instead. No prepared mapping is proof that a POST occurred.
    """
    _limit(max_bytes)
    if (not isinstance(request_id, str) or
            re.fullmatch(r'[A-Za-z0-9_.:-]{1,180}', request_id) is None or
            endpoint not in {'/chat/completions', '/images'} or not isinstance(payload, dict)):
        raise WireEncodingError('Expected a stable new request ID and fixed endpoint')
    encoder = _encoder()
    logical_body = _body(payload)
    locations = _locations(payload) if endpoint == '/chat/completions' else []
    wire_payload, entries, blobs, cache = deepcopy(payload), [], {}, {}
    for mi, ci, url in locations:
        source = _source_bytes(url)
        source_sha = _sha(source)
        if source_sha not in cache:
            cache[source_sha] = _encode(source, canonical(encoder))
        wire, mode, wire_mode, size, rgba_sha = cache[source_sha]
        wire_sha = _sha(wire)
        relative = 'transport_blobs/' + wire_sha + '.webp'
        entries.append({
            'message_index': mi, 'content_index': ci,
            'source_sha256': source_sha, 'wire_sha256': wire_sha,
            'wire_blob': relative, 'source_mime': 'image/png', 'wire_mime': 'image/webp',
            'source_byte_count': len(source), 'wire_byte_count': len(wire),
            'mode': mode, 'wire_mode': wire_mode, 'size': list(size),
            'frame_count': 1, 'rgba_sha256': rgba_sha,
        })
        wire_payload['messages'][mi]['content'][ci]['image_url']['url'] = (
            _WEBP_PREFIX + base64.b64encode(wire).decode('ascii'))
        blobs[wire_sha] = wire
    _only_urls_changed(payload, wire_payload, locations)
    wire_body = _body(wire_payload)
    if len(wire_body) >= max_bytes:
        raise WireEncodingError('Complete canonical wire JSON meets or exceeds its byte limit; no reservation permitted')
    mapping = {
        'version': 1, 'request_id': request_id, 'endpoint': endpoint,
        'logical_request_hash': digest({'endpoint': endpoint, 'payload': payload}),
        'logical_body_utf8_bytes': len(logical_body),
        'wire_request_hash': digest({'endpoint': endpoint, 'payload': wire_payload}),
        'wire_body_sha256': _sha(wire_body), 'wire_body_utf8_bytes': len(wire_body),
        'max_bytes': max_bytes, 'encoder': encoder, 'images': entries,
    }
    # Size/pixel validation completes before any blob write. Original PNGs and
    # logical archives are never replaced or re-encoded by this module.
    for wire_sha, wire in blobs.items():
        path = _blob_path(out, 'transport_blobs/' + wire_sha + '.webp', wire_sha)
        try:
            if path.exists():
                if path.read_bytes() != wire:
                    raise WireEncodingError('Content-addressed wire blob changed')
            else:
                atomic_bytes(path, wire)
        except OSError:
            raise WireEncodingError('Wire blob could not be persisted') from None
    return wire_payload, mapping


def verify_wire(out, endpoint, logical_payload, mapping):
    """Reconstruct and verify exact wire bytes without recompressing any image.

    The caller separately verifies the immutable mapping digest and request-ID
    binding. Logical request hashes are not hashes of transformed wire JSON.
    """
    if not isinstance(mapping, dict) or set(mapping) != _MAPPING_KEYS:
        raise WireEncodingError('Wire mapping schema is invalid')
    if (type(mapping['version']) is not int or mapping['version'] != 1 or
            not isinstance(mapping['request_id'], str) or
            re.fullmatch(r'[A-Za-z0-9_.:-]{1,180}', mapping['request_id']) is None or
            endpoint not in {'/chat/completions', '/images'} or mapping['endpoint'] != endpoint or
            _body(mapping['encoder']) != _body(_encoder())):
        raise WireEncodingError('Wire mapping endpoint, version, or encoder policy differs')
    _limit(mapping['max_bytes'])
    logical_body = _body(logical_payload)
    if (type(mapping['logical_body_utf8_bytes']) is not int or
            type(mapping['wire_body_utf8_bytes']) is not int or
            mapping['logical_request_hash'] != digest({'endpoint': endpoint, 'payload': logical_payload}) or
            mapping['logical_body_utf8_bytes'] != len(logical_body)):
        raise WireEncodingError('Wire mapping does not bind the logical request')
    if not isinstance(logical_payload, dict):
        raise WireEncodingError('Expected a logical request object')
    locations = _locations(logical_payload) if endpoint == '/chat/completions' else []
    entries = mapping['images']
    if not isinstance(entries, list) or len(entries) != len(locations):
        raise WireEncodingError('Wire mapping image count differs')
    wire_payload = deepcopy(logical_payload)
    for (mi, ci, url), entry in zip(locations, entries):
        if (not isinstance(entry, dict) or set(entry) != _IMAGE_KEYS or
                type(entry['message_index']) is not int or type(entry['content_index']) is not int or
                (entry['message_index'], entry['content_index']) != (mi, ci) or
                type(entry['source_byte_count']) is not int or type(entry['wire_byte_count']) is not int or
                not 0 < entry['wire_byte_count'] <= _MAX_IMAGE_BYTES or
                not isinstance(entry['size'], list) or len(entry['size']) != 2 or
                any(type(side) is not int for side in entry['size']) or
                entry['source_mime'] != 'image/png' or entry['wire_mime'] != 'image/webp' or
                type(entry['frame_count']) is not int or entry['frame_count'] != 1):
            raise WireEncodingError('Wire mapping image positions or encoding differ')
        source = _source_bytes(url)
        mode, size, rgba, _ = _raster(source, 'PNG')
        if (entry['source_sha256'] != _sha(source) or entry['source_byte_count'] != len(source) or
                entry['mode'] != mode or entry['size'] != size or entry['rgba_sha256'] != _sha(rgba)):
            raise WireEncodingError('Wire mapping source image differs')
        try:
            path = _blob_path(out, entry['wire_blob'], entry['wire_sha256'])
            if path.stat().st_size != entry['wire_byte_count']:
                raise WireEncodingError('Mapped wire blob byte count differs')
            wire = path.read_bytes()
        except OSError:
            raise WireEncodingError('Mapped wire blob is missing or unreadable') from None
        if entry['wire_sha256'] != _sha(wire) or entry['wire_byte_count'] != len(wire):
            raise WireEncodingError('Mapped wire blob content/hash differs')
        wire_mode, wire_size, wire_rgba, _ = _raster(wire, 'WEBP')
        if (entry['wire_mode'] != wire_mode or size != wire_size or rgba != wire_rgba or
                entry['rgba_sha256'] != _sha(wire_rgba)):
            raise WireEncodingError('Wire blob changed exact decoded RGBA pixels')
        wire_payload['messages'][mi]['content'][ci]['image_url']['url'] = (
            _WEBP_PREFIX + base64.b64encode(wire).decode('ascii'))
    _only_urls_changed(logical_payload, wire_payload, locations)
    wire_body = _body(wire_payload)
    if (len(wire_body) >= mapping['max_bytes'] or
            mapping['wire_body_utf8_bytes'] != len(wire_body) or
            mapping['wire_body_sha256'] != _sha(wire_body) or
            mapping['wire_request_hash'] != digest({'endpoint': endpoint, 'payload': wire_payload})):
        raise WireEncodingError('Reconstructed canonical wire JSON differs or exceeds its limit')
    return wire_payload
