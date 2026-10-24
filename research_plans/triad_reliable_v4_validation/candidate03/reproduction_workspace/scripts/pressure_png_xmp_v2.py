"""Strict, pixel- and XMP-exact transport amendment for provider PNGs.

The original codec remains immutable. Metadata-free requests and all /images
requests delegate to it unchanged. Version 2 adds only an uncompressed PNG iTXt
XML:com.adobe.xmp chunk, carried byte-for-byte into lossless WebP XMP. Other
metadata, color context, animation and lossy image content remain unsupported.
No network or paid-ledger operation occurs here; logical PNGs never change.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from functools import lru_cache
import hashlib
import importlib.util
import io
from pathlib import Path
import re
import struct
import zlib

from PIL import Image
from visual_collusion.common import atomic_bytes, canonical, digest


_BASE_PATH = Path(__file__).resolve().with_name("lossless_wire_codec.py")
_BASE_SHA256 = "d1ecf05b90f34ae99952d09771539253f7a181706a5d7ad6d75098d3dbd15247"
if _BASE_PATH.is_symlink() or hashlib.sha256(_BASE_PATH.read_bytes()).hexdigest() != _BASE_SHA256:
    raise RuntimeError("The original lossless wire codec changed")
_SPEC = importlib.util.spec_from_file_location("_pressure_xmp_original_codec", _BASE_PATH)
base = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(base)

WireEncodingError = base.WireEncodingError
MAX_WIRE_BYTES = base.MAX_WIRE_BYTES
_XMP_HEADER = b"XML:com.adobe.xmp\0\0\0\0\0"
_IMAGE_KEYS = base._IMAGE_KEYS | {"xmp_sha256", "xmp_byte_count"}


def _encoder():
    result = base._encoder()
    result.update({
        "name": "pillow_lossless_webp_xmp_v2",
        "source_metadata_policy": "PNG IHDR/[one uncompressed empty-language XML:com.adobe.xmp iTXt]/IDAT/IEND only; RGB or RGBA; no color or other metadata",
        "metadata_comparison": "Exact source iTXt XMP payload equals WebP XMP chunk and decoder metadata bytes",
    })
    return result


def _png_xmp(data):
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise WireEncodingError("Logical image does not contain PNG bytes")
    position, kinds, xmp = 8, [], None
    while position < len(data):
        if position + 12 > len(data):
            raise WireEncodingError("PNG chunk is truncated")
        count = struct.unpack(">I", data[position:position + 4])[0]
        end = position + 12 + count
        if end > len(data):
            raise WireEncodingError("PNG chunk is truncated")
        kind = data[position + 4:position + 8]
        value = data[position + 8:position + 8 + count]
        crc = struct.unpack(">I", data[position + 8 + count:end])[0]
        if crc != zlib.crc32(kind + value) & 0xffffffff:
            raise WireEncodingError("PNG chunk checksum is invalid")
        if kind == b"iTXt":
            if (xmp is not None or kinds != [b"IHDR"] or
                    not value.startswith(_XMP_HEADER) or len(value) <= len(_XMP_HEADER)):
                raise WireEncodingError("Only one exact uncompressed empty-language PNG XMP chunk is supported")
            xmp = value[len(_XMP_HEADER):]
        elif kind not in {b"IHDR", b"IDAT", b"IEND"}:
            raise WireEncodingError("PNG color context or other metadata is unsupported")
        kinds.append(kind)
        position = end
        if kind == b"IEND":
            if count or position != len(data):
                raise WireEncodingError("PNG has invalid trailing data")
            break
    plain = [kind for kind in kinds if kind != b"iTXt"]
    if (not plain or plain[0] != b"IHDR" or plain[-1] != b"IEND" or
            plain.count(b"IHDR") != 1 or plain.count(b"IEND") != 1 or
            b"IDAT" not in plain or any(kind != b"IDAT" for kind in plain[1:-1])):
        raise WireEncodingError("PNG chunk sequence is invalid")
    return xmp


def _webp_xmp(data):
    if (len(data) < 20 or data[:4] != b"RIFF" or data[8:12] != b"WEBP" or
            struct.unpack("<I", data[4:8])[0] != len(data) - 8):
        raise WireEncodingError("Wire image is not a complete WebP container")
    position, kinds, xmp, flags = 12, [], None, 0
    while position < len(data):
        if position + 8 > len(data):
            raise WireEncodingError("WebP chunk is truncated")
        kind = data[position:position + 4]
        count = struct.unpack("<I", data[position + 4:position + 8])[0]
        end = position + 8 + count
        padded = end + (count & 1)
        if padded > len(data) or (count & 1 and data[end] != 0):
            raise WireEncodingError("WebP chunk/padding is invalid")
        value = data[position + 8:end]
        if kind == b"VP8X":
            if count != 10 or value[0] & ~0x14 or value[1:4] != b"\0\0\0":
                raise WireEncodingError("Wire WebP has unsupported extended context")
            flags = value[0]
        elif kind == b"XMP ":
            if xmp is not None or not value:
                raise WireEncodingError("Wire WebP XMP must occur once and be nonempty")
            xmp = value
        elif kind != b"VP8L":
            raise WireEncodingError("Wire WebP contains other metadata, animation, or lossy content")
        kinds.append(kind)
        position = padded
    if kinds not in ([b"VP8L"], [b"VP8X", b"VP8L"], [b"VP8X", b"VP8L", b"XMP "]):
        raise WireEncodingError("Wire WebP chunk sequence is unsupported")
    if bool(flags & 0x04) != (xmp is not None):
        raise WireEncodingError("Wire WebP XMP flag differs from its actual chunk")
    return xmp


def _raster(data, image_format):
    xmp = _png_xmp(data) if image_format == "PNG" else _webp_xmp(data)
    try:
        with Image.open(io.BytesIO(data)) as image:
            if (image.format != image_format or image.mode not in {"RGB", "RGBA"} or
                    getattr(image, "n_frames", 1) != 1 or
                    not all(1 <= side <= 1024 for side in image.size)):
                raise WireEncodingError("Image mode, dimensions, frame count, or format is unsupported")
            image.load()
            allowed = ({"xmp", "XML:com.adobe.xmp"} if image_format == "PNG" else
                       {"loop", "background", "timestamp", "duration", "xmp"})
            if set(image.info) - allowed or image.getexif():
                raise WireEncodingError("Image contains color context or other metadata")
            if image.info.get("xmp") != xmp:
                raise WireEncodingError("Decoded XMP differs from the exact container payload")
            if image_format == "PNG" and xmp is not None:
                text = image.info.get("XML:com.adobe.xmp")
                if not isinstance(text, str) or text.encode("utf-8") != xmp:
                    raise WireEncodingError("Decoded PNG text differs from the exact XMP bytes")
            return image.mode, list(image.size), image.convert("RGBA").tobytes(), image.copy(), xmp
    except WireEncodingError:
        raise
    except (OSError, ValueError, SyntaxError, UnicodeError, Image.DecompressionBombError):
        raise WireEncodingError("Image cannot be decoded exactly") from None


@lru_cache(maxsize=16)
def _encode(source, encoder_profile):
    mode, size, rgba, image, xmp = _raster(source, "PNG")
    if xmp is None:
        wire, mode, wire_mode, size, rgba_sha = base._encode(source, canonical(base._encoder()))
        return wire, mode, wire_mode, size, rgba_sha, None
    output = io.BytesIO()
    try:
        image.save(output, format="WEBP", xmp=xmp, **base._PARAMETERS)
    except (OSError, ValueError, TypeError):
        raise WireEncodingError("Lossless XMP-preserving WebP encoding failed") from None
    wire = output.getvalue()
    wire_mode, wire_size, wire_rgba, _, wire_xmp = _raster(wire, "WEBP")
    if size != wire_size or rgba != wire_rgba or xmp != wire_xmp:
        raise WireEncodingError("Lossless encoding changed pixels, dimensions, or XMP")
    return wire, mode, wire_mode, tuple(size), base._sha(rgba), xmp


def prepare_wire(out, request_id, endpoint, payload, max_bytes=MAX_WIRE_BYTES):
    base._limit(max_bytes)
    if (not isinstance(request_id, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,180}", request_id) is None or
            endpoint not in {"/chat/completions", "/images"} or not isinstance(payload, dict)):
        raise WireEncodingError("Expected a stable new request ID and fixed endpoint")
    locations = base._locations(payload) if endpoint == "/chat/completions" else []
    sources = [base._source_bytes(url) for _, _, url in locations]
    has_xmp = any(_png_xmp(source) is not None for source in sources)
    if not has_xmp:
        return base.prepare_wire(out, request_id, endpoint, payload, max_bytes)
    encoder = _encoder()
    wire_payload, entries, blobs = deepcopy(payload), [], {}
    for (mi, ci, _), source in zip(locations, sources):
        wire, mode, wire_mode, size, rgba_sha, xmp = _encode(source, canonical(encoder))
        wire_sha = base._sha(wire)
        if not 0 < len(wire) <= base._MAX_IMAGE_BYTES:
            raise WireEncodingError("Encoded wire image exceeds its fixed byte bound")
        entries.append({
            "message_index": mi, "content_index": ci,
            "source_sha256": base._sha(source), "wire_sha256": wire_sha,
            "wire_blob": "transport_blobs/" + wire_sha + ".webp",
            "source_mime": "image/png", "wire_mime": "image/webp",
            "source_byte_count": len(source), "wire_byte_count": len(wire),
            "mode": mode, "wire_mode": wire_mode, "size": list(size),
            "frame_count": 1, "rgba_sha256": rgba_sha,
            "xmp_sha256": base._sha(xmp) if xmp is not None else None,
            "xmp_byte_count": len(xmp) if xmp is not None else 0,
        })
        wire_payload["messages"][mi]["content"][ci]["image_url"]["url"] = base._WEBP_PREFIX + base64.b64encode(wire).decode("ascii")
        blobs[wire_sha] = wire
    base._only_urls_changed(payload, wire_payload, locations)
    wire_body = base._body(wire_payload)
    if len(wire_body) >= max_bytes:
        raise WireEncodingError("Complete canonical wire JSON meets or exceeds its byte limit; no reservation permitted")
    mapping = {
        "version": 2, "request_id": request_id, "endpoint": endpoint,
        "logical_request_hash": digest({"endpoint": endpoint, "payload": payload}),
        "logical_body_utf8_bytes": len(base._body(payload)),
        "wire_request_hash": digest({"endpoint": endpoint, "payload": wire_payload}),
        "wire_body_sha256": base._sha(wire_body), "wire_body_utf8_bytes": len(wire_body),
        "max_bytes": max_bytes, "encoder": encoder, "images": entries,
    }
    for wire_sha, wire in blobs.items():
        path = base._blob_path(out, "transport_blobs/" + wire_sha + ".webp", wire_sha)
        try:
            if path.exists():
                if path.read_bytes() != wire:
                    raise WireEncodingError("Content-addressed wire blob changed")
            else:
                atomic_bytes(path, wire)
        except OSError:
            raise WireEncodingError("Wire blob could not be persisted") from None
    return wire_payload, mapping


def verify_wire(out, endpoint, logical_payload, mapping):
    if isinstance(mapping, dict) and type(mapping.get("version")) is int and mapping["version"] == 1:
        return base.verify_wire(out, endpoint, logical_payload, mapping)
    if not isinstance(mapping, dict) or set(mapping) != base._MAPPING_KEYS:
        raise WireEncodingError("Wire mapping schema is invalid")
    if (type(mapping["version"]) is not int or mapping["version"] != 2 or
            not isinstance(mapping["request_id"], str) or
            re.fullmatch(r"[A-Za-z0-9_.:-]{1,180}", mapping["request_id"]) is None or
            endpoint != "/chat/completions" or mapping["endpoint"] != endpoint or
            base._body(mapping["encoder"]) != base._body(_encoder())):
        raise WireEncodingError("Wire mapping endpoint, version, or XMP policy differs")
    base._limit(mapping["max_bytes"])
    logical_body = base._body(logical_payload)
    if (not isinstance(logical_payload, dict) or type(mapping["logical_body_utf8_bytes"]) is not int or
            type(mapping["wire_body_utf8_bytes"]) is not int or
            mapping["logical_request_hash"] != digest({"endpoint": endpoint, "payload": logical_payload}) or
            mapping["logical_body_utf8_bytes"] != len(logical_body)):
        raise WireEncodingError("Wire mapping does not bind the logical request")
    locations = base._locations(logical_payload)
    entries = mapping["images"]
    if not isinstance(entries, list) or len(entries) != len(locations):
        raise WireEncodingError("Wire mapping image count differs")
    wire_payload, has_xmp = deepcopy(logical_payload), False
    for (mi, ci, url), entry in zip(locations, entries):
        if (not isinstance(entry, dict) or set(entry) != _IMAGE_KEYS or
                type(entry["message_index"]) is not int or type(entry["content_index"]) is not int or
                (entry["message_index"], entry["content_index"]) != (mi, ci) or
                type(entry["source_byte_count"]) is not int or type(entry["wire_byte_count"]) is not int or
                not 0 < entry["wire_byte_count"] <= base._MAX_IMAGE_BYTES or
                not isinstance(entry["size"], list) or len(entry["size"]) != 2 or
                any(type(side) is not int for side in entry["size"]) or
                entry["source_mime"] != "image/png" or entry["wire_mime"] != "image/webp" or
                type(entry["frame_count"]) is not int or entry["frame_count"] != 1 or
                type(entry["xmp_byte_count"]) is not int):
            raise WireEncodingError("Wire mapping image positions or encoding differ")
        source = base._source_bytes(url)
        mode, size, rgba, _, xmp = _raster(source, "PNG")
        has_xmp |= xmp is not None
        if (entry["source_sha256"] != base._sha(source) or entry["source_byte_count"] != len(source) or
                entry["mode"] != mode or entry["size"] != size or entry["rgba_sha256"] != base._sha(rgba) or
                entry["xmp_byte_count"] != (len(xmp) if xmp is not None else 0) or
                entry["xmp_sha256"] != (base._sha(xmp) if xmp is not None else None)):
            raise WireEncodingError("Wire mapping source pixels or XMP differ")
        try:
            path = base._blob_path(out, entry["wire_blob"], entry["wire_sha256"])
            if path.stat().st_size != entry["wire_byte_count"]:
                raise WireEncodingError("Mapped wire blob byte count differs")
            wire = path.read_bytes()
        except OSError:
            raise WireEncodingError("Mapped wire blob is missing or unreadable") from None
        if entry["wire_sha256"] != base._sha(wire) or entry["wire_byte_count"] != len(wire):
            raise WireEncodingError("Mapped wire blob content/hash differs")
        wire_mode, wire_size, wire_rgba, _, wire_xmp = _raster(wire, "WEBP")
        if (entry["wire_mode"] != wire_mode or size != wire_size or rgba != wire_rgba or
                entry["rgba_sha256"] != base._sha(wire_rgba) or xmp != wire_xmp):
            raise WireEncodingError("Wire blob changed exact pixels, dimensions, or XMP")
        wire_payload["messages"][mi]["content"][ci]["image_url"]["url"] = base._WEBP_PREFIX + base64.b64encode(wire).decode("ascii")
    if not has_xmp:
        raise WireEncodingError("Metadata-free requests must use the unchanged version 1 codec")
    base._only_urls_changed(logical_payload, wire_payload, locations)
    wire_body = base._body(wire_payload)
    if (len(wire_body) >= mapping["max_bytes"] or mapping["wire_body_utf8_bytes"] != len(wire_body) or
            mapping["wire_body_sha256"] != base._sha(wire_body) or
            mapping["wire_request_hash"] != digest({"endpoint": endpoint, "payload": wire_payload})):
        raise WireEncodingError("Reconstructed wire JSON differs or exceeds its limit")
    return wire_payload
