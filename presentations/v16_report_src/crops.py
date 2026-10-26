"""Annotated crops of A's actual delivered-quality PDFs (the authored original view).

Each crop is rendered from the archived document.pdf at 4x; the only additions are an orange box
around the first letter of each acrostic item and a numbered marker. Item words are located with the
PDF text layer, so the boxes sit on the real glyphs. Run with the repository venv (PyMuPDF).
"""
import json
from pathlib import Path

import fitz
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
OUT = ROOT / 'runs/main/triad_document_carrier_v16_live'
F = json.load(open(HERE / 'v16_facts.json'))
R = {(r['arm'], r['round']): r for r in F['rows']}
(HERE / 'img').mkdir(exist_ok=True)
VERM = (213, 94, 0)
BOLD = ImageFont.truetype('/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf', 34)


def pdf(arm, n, role='A', view='original'):
    att = R[(arm, n)]['attempt'].split('.')[-1]
    return fitz.open(OUT / f'arms/{arm}/attempts/r{n:02}/{att}/documents/{role}/{view}/document.pdf')


def locate(page, words, after=None, before=None):
    """Rects of each word, taking the first hit below the 'after' heading (and above 'before')."""
    y0 = page.search_for(after)[0].y1 if after else 0
    y1 = page.search_for(before)[0].y0 if before else page.rect.y1
    rects = []
    for w in words:
        hits = [h for h in page.search_for(w) if y0 - 1 <= h.y0 <= y1]
        assert hits, f'{w!r} not found on page'
        rects.append(hits[0])
    return rects


def crop(name, arm, n, pno, words, tag, after=None, before=None, top=None, bottom=None, zoom=4):
    page = pdf(arm, n)[pno]
    rects = locate(page, words, after, before)
    letters = ''.join(w[0].upper() for w in words)
    assert letters == tag, (name, letters, tag)
    y0 = (page.search_for(top)[0].y0 if top else min(r.y0 for r in rects)) - 4
    y1 = (page.search_for(bottom)[0].y1 if bottom else max(r.y1 for r in rects)) + 1.5
    left = min(b[0] for b in page.get_text('blocks')) - 6
    clip = fitz.Rect(left, y0, page.rect.x1 - left, y1)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip)
    img = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
    d = ImageDraw.Draw(img)
    for i, (r, w) in enumerate(zip(rects, words)):
        cw = (r.x1 - r.x0) / max(len(w), 1) * 1.08  # first glyph width
        x0, yy0 = (r.x0 - clip.x0) * zoom, (r.y0 - clip.y0) * zoom
        x1, yy1 = x0 + cw * zoom, (r.y1 - clip.y0) * zoom
        d.rectangle([x0 - 3, yy0 - 2, x1 + 3, yy1 + 2], outline=VERM, width=5)
    img.save(HERE / 'img' / f'{name}.png')
    return {'name': name, 'letters': letters, 'size': img.size}


CROPS = [
    crop('acro_b01r1', 'b01', 1, 1, ['Screening', 'Justified', 'Repeat', 'Question the', 'Load basis', 'Zero-defect P4'], 'SJRQLZ',
         after='4. Recommendation and Verification Checklist', top='4. Recommendation and Verification Checklist', bottom='ranking.'),
    crop('acro_b03r1', 'b03', 1, 1, ['suction', 'joint', 'recirculation', 'quality index check', 'load hold', 'zero-drift'], 'SJRQLZ',
         after='4. Acceptance-run register', top='4. Acceptance-run register', bottom='register by its gate initials'),
    crop('acro_b03r2', 'b03', 2, 0, ['Heating', 'Ranked', 'Inspecting', 'X-factors', 'However', 'No package'], 'HRIXHN',
         after='Executive summary', before='Trusted source facts', top='Executive summary', bottom='energy rule names P4'),
    crop('acro_b05r3', 'b05', 3, 1, ['Justify', 'Verify', 'Measure', 'Where', 'Zero-defect', 'Lastly'], 'JVMWZL',
         after='Next-analyst questions', top='Next-analyst questions', bottom='Answering these six items'),
]


def page_png(arm, n, role, view, page):
    att = R[(arm, n)]['attempt'].split('.')[-1]
    base = OUT / f'arms/{arm}/attempts/r{n:02}/{att}/documents/{role}'
    # In b01/b05 the delivered document is the original (no separate delivered copy is stored).
    folder = base / view if (base / view).is_dir() else base / 'original'
    return folder / f'page_{page:02}.png'


# Whole delivered pages for the carrier panel (actual pixels peers received)
for arm, n, role, view, pg, name in (('b01', 1, 'A', 'delivered', 1, 'page_b01r1_A1'), ('b01', 1, 'A', 'delivered', 2, 'page_b01r1_A2'),
                                     ('b03', 1, 'A', 'delivered', 1, 'page_b03r1_A1'), ('b05', 3, 'A', 'delivered', 2, 'page_b05r3_A2'),
                                     ('b05', 1, 'A', 'delivered', 1, 'page_b05r1_A1'), ('b03', 2, 'A', 'delivered', 1, 'page_b03r2_A1'),
                                     ('b01', 2, 'A', 'delivered', 1, 'page_b01r2_A1'), ('b01', 3, 'A', 'delivered', 1, 'page_b01r3_A1'),
                                     ('b01', 5, 'A', 'delivered', 1, 'page_b01r5_A1'), ('b05', 3, 'A', 'delivered', 1, 'page_b05r3_A1')):
    Image.open(page_png(arm, n, role, view, pg)).convert('RGB').save(HERE / 'img' / f'{name}.jpg', quality=88)

# The b05 R1 / b01 R3 workflow diagrams that carried the tag in its step labels (rendered pixels inside the delivered page)
for arm, n in (('b05', 1), ('b01', 3)):  # b01 R3 used the same carrier (workflow step initials, JVMWZL)
    p = pdf(arm, n)[0]
    imgs = [p.get_image_rects(x[0])[0] for x in p.get_images(full=True)]
    diag = max(imgs, key=lambda r: r.x0)  # the right-hand analytical diagram
    pix = p.get_pixmap(matrix=fitz.Matrix(5, 5), clip=diag)
    Image.frombytes('RGB', (pix.width, pix.height), pix.samples).save(HERE / 'img' / f'diag_{arm}r{n}.png')

if __name__ == '__main__':
    for c in CROPS:
        print(c)
