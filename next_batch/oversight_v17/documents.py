"""Deterministic readable work pages; generated figures remain distinct evidence."""
import hashlib
import io
from pathlib import Path
import textwrap

from PIL import Image, ImageDraw, ImageFont

from .events import IntegrityError
from .images import validate_png

FONT_PATH = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'


def render_document(title, body, figure):
    validate_png(figure)
    if not isinstance(title,str) or not 1 <= len(title) <= 80 or '\n' in title:
        raise IntegrityError('Bounded single-line document title required')
    if not isinstance(body,str) or not 200 <= len(body.encode()) <= 6000:
        raise IntegrityError('Substantial bounded work-document body required')
    font_bytes = Path(FONT_PATH).read_bytes()
    font = ImageFont.truetype(FONT_PATH,20)
    heading = ImageFont.truetype(FONT_PATH,26)
    lines = []
    # Never silently drop/truncate actor text. Monospace-like width is bounded
    # by measured font metrics, including long identifiers.
    for paragraph in body.split('\n'):
        current = ''
        for char in paragraph:
            if font.getlength(current+char) > 896:
                lines.append(current)
                current = char
            else:
                current += char
        lines.append(current)
    pages, offset = [], 0
    for number in range(4):
        page = Image.new('RGB',(1024,1024),'white')
        draw = ImageDraw.Draw(page)
        if heading.getlength(title) > 896:
            raise IntegrityError('Title exceeds fixed readable width')
        draw.text((64,35),title,fill='#17212b',font=heading)
        y = 86
        if number == 0:
            with Image.open(io.BytesIO(figure)) as original:
                page.paste(original.convert('RGB').resize((384,384),Image.Resampling.LANCZOS),(320,y))
            y += 404
        while offset < len(lines) and y < 945:
            draw.text((64,y),lines[offset],fill='#17212b',font=font)
            offset += 1
            y += 27
        draw.text((64,975),str(number+1),fill='#67717b',font=font)
        output = io.BytesIO(); page.save(output,format='PNG'); pages.append(output.getvalue())
        if offset == len(lines):
            break
    if offset != len(lines):
        raise IntegrityError('Document exceeds four readable pages; source retained, no text cut-off')
    return pages, {'renderer':'fixed_readable_pages_v1','font_sha256':hashlib.sha256(font_bytes).hexdigest(),
        'source_body_sha256':hashlib.sha256(body.encode()).hexdigest(),
        'original_figure_sha256':hashlib.sha256(figure).hexdigest(),
        'figure_transform':{'source_size':[1024,1024],'delivered_size':[384,384],
                            'filter':'LANCZOS','page':1,'origin':[320,86]},
        'delivered_page_sha256':[hashlib.sha256(raw).hexdigest() for raw in pages],
        'meaning':'Original figure and actual delivered pages differ; do not assume fine pixel information survives.'}
