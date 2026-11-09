"""Actual PNG delivery; source briefs are separate private evidence."""
import base64
import io
from PIL import Image

from .events import IntegrityError


def validate_png(raw):
    if not isinstance(raw, bytes) or len(raw) > 8 * 1024 * 1024 or not raw.startswith(b'\x89PNG\r\n\x1a\n'):
        raise IntegrityError('A bounded actual PNG is required')
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != 'PNG' or image.size != (1024, 1024) or getattr(image, 'n_frames', 1) != 1:
                raise IntegrityError('One 1024x1024 PNG required')
            image.verify()
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as error:
        raise IntegrityError('Malformed image bytes') from error
    return raw


def image_block(raw):
    validate_png(raw)
    return {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(raw).decode(), 'detail': 'high'}}


def message_budget(messages, tools=None):
    """Bound text bytes and image tokens separately, not base64 as text tokens."""
    import json
    text, images = 0, 0
    if not isinstance(messages, list) or not 1 <= len(messages) <= 128:
        raise IntegrityError('Bounded history required')
    for message in messages:
        content = message.get('content')
        if isinstance(content, str):
            text += len(content.encode())
        elif isinstance(content, list):
            for part in content:
                if part.get('type') == 'text' and isinstance(part.get('text'), str):
                    text += len(part['text'].encode())
                elif part.get('type') == 'image_url':
                    url = part.get('image_url', {}).get('url', '')
                    if not url.startswith('data:image/png;base64,'):
                        raise IntegrityError('Remote image URLs forbidden')
                    validate_png(base64.b64decode(url.split(',', 1)[1], validate=True))
                    images += 1
                else:
                    raise IntegrityError('Unsupported message block')
        elif content is not None:
            raise IntegrityError('Unsupported message content')
        if message.get('tool_calls'):
            text += len(json.dumps(message['tool_calls'], ensure_ascii=False).encode())
    text += len(json.dumps(tools or [], ensure_ascii=False).encode()) + 128 * len(messages) + 2048
    if text > 81920 or images > 4:
        raise IntegrityError('History exceeds fixed input profile; no truncation')
    return text, images
