"""Shared drawing kit for the paper's schematic figures.

Every figure is drawn on one scale: 1 viewBox unit = 0.5 pt in print, so a canvas 950 units
wide fills the 6.6 in text width and 15-unit labels print at 7.5 pt. Colours follow the
Okabe-Ito palette with fixed meanings across all figures:
  vermillion = A's target / coordination, blue = client-correct / benign,
  black and greys = structure and neutral states.
Text is wrapped with real font metrics so nothing overflows its box.
"""
from PIL import ImageFont

INK, GREY, RULE, FILL, FILL2 = '#1A1A1A', '#5F5F5F', '#BDBDBD', '#F3F3F3', '#E6E6E6'
VERM, BLUE, VERM_L, BLUE_L = '#D55E00', '#0072B2', '#F7DCC9', '#D3E6F3'
FAMILY = "'Liberation Sans', Arial, Helvetica, sans-serif"
_FONTS = {}
_PATH = '/usr/share/fonts/truetype/liberation/LiberationSans-{}.ttf'


def _font(size, bold=False, it=False):
    key = (round(size * 4), bold, it)
    if key not in _FONTS:
        style = {(False, False): 'Regular', (True, False): 'Bold', (False, True): 'Italic', (True, True): 'BoldItalic'}[(bold, it)]
        _FONTS[key] = ImageFont.truetype(_PATH.format(style), size=key[0])  # 4x for sub-unit precision
    return _FONTS[key]


def width(s, size, bold=False, it=False):
    return _font(size, bold, it).getlength(s) / 4


def wrap(s, size, maxw, bold=False, it=False):
    words, lines, cur = s.split(), [], ''
    for w in words:
        trial = (cur + ' ' + w).strip()
        if width(trial, size, bold, it) <= maxw or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def esc(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


class Canvas:
    def __init__(self, w, h, uid):
        self.w, self.h, self.uid = w, h, uid
        self.o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" style="width:{w / 2}pt;max-width:100%;display:block;margin:0 auto" font-family="{FAMILY}">',
                  '<defs>' + ''.join(
                      f'<marker id="{uid}{n}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
                      f'<path d="M0 1.2 L10 5 L0 8.8 z" fill="{c}"/></marker>' for n, c in (('k', INK), ('v', VERM), ('g', GREY))) + '</defs>']

    # text -------------------------------------------------------------------
    def text(self, x, y, s, size=15, bold=False, it=False, fill=INK, anchor='start', rot=None):
        attrs = f' font-weight="700"' if bold else ''
        attrs += ' font-style="italic"' if it else ''
        attrs += f' transform="rotate({rot} {x} {y})"' if rot is not None else ''
        self.o.append(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" text-anchor="{anchor}"{attrs}>{esc(s)}</text>')

    def para(self, x, y, s, maxw, size=15, lead=None, **k):
        """Wrapped paragraph; returns the y of the next free line."""
        lead = lead or size * 1.28
        for i, line in enumerate(wrap(s, size, maxw, k.get('bold', False), k.get('it', False))):
            self.text(x if k.get('anchor', 'start') == 'start' else x, y + i * lead, line, size, **k)
        return y + len(wrap(s, size, maxw, k.get('bold', False), k.get('it', False))) * lead

    def quote(self, x, y, s, maxw, who=None, size=14.5):
        """Verbatim quotation: italic text with a thin rule on the left and an attribution."""
        n = len(wrap('“' + s + '”', size, maxw - 12, it=True))
        lead = size * 1.28
        h = n * lead + (lead if who else 0)
        self.o.append(f'<line x1="{x}" y1="{y - size * 0.9}" x2="{x}" y2="{y - size * 0.9 + h}" stroke="{RULE}" stroke-width="2.5"/>')
        y2 = self.para(x + 10, y, '“' + s + '”', maxw - 12, size, it=True)
        if who:
            self.text(x + 10, y2, who, size - 1.5, fill=GREY)
            y2 += lead
        return y2

    def label(self, x, y, s):
        """Panel label, e.g. (a) followed by a short heading."""
        self.text(x, y, s, 16, bold=True)

    # shapes -----------------------------------------------------------------
    def rect(self, x, y, w, h, fill='#FFFFFF', stroke=INK, sw=1.4, dash=False, r=2):
        d = ' stroke-dasharray="5 3"' if dash else ''
        s = 'none' if stroke is None else stroke
        self.o.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{r}" fill="{fill}" stroke="{s}" stroke-width="{sw}"{d}/>')

    def line(self, pts, color=INK, sw=1.5, dash=False, head=True, curve=None):
        mk = {INK: 'k', VERM: 'v', GREY: 'g'}.get(color, 'k')
        d = ' stroke-dasharray="5 3"' if dash else ''
        m = f' marker-end="url(#{self.uid}{mk})"' if head else ''
        p = ' '.join(f'{x:.1f},{y:.1f}' for x, y in pts)
        self.o.append(f'<polyline points="{p}" fill="none" stroke="{color}" stroke-width="{sw}" stroke-linejoin="round"{d}{m}/>')

    def image(self, x, y, w, src, ring=None, caption=None, border=INK):
        self.o.append(f'<image href="img/{src}" x="{x}" y="{y}" width="{w}" height="{w}" preserveAspectRatio="xMidYMid slice"/>')
        self.o.append(f'<rect x="{x}" y="{y}" width="{w}" height="{w}" fill="none" stroke="{border}" stroke-width="{1.2 if border == INK else 3}"/>')
        if ring:
            cx, cy, rx, ry = ring
            self.o.append(f'<ellipse cx="{x + cx * w:.1f}" cy="{y + cy * w:.1f}" rx="{rx * w:.1f}" ry="{ry * w:.1f}" fill="none" stroke="#FFFFFF" stroke-width="2.2"/>')

    def cells(self, x, y, values, cw=22, ch=20, gap=3, fills=None, size=12.5, colors=None):
        """A row of small labelled cells (for per-round strips)."""
        for i, v in enumerate(values):
            f = fills[i] if fills else '#FFFFFF'
            c = colors[i] if colors else INK
            self.rect(x + i * (cw + gap), y, cw, ch, f, RULE if f == '#FFFFFF' else None, 1.0, r=1)
            self.text(x + i * (cw + gap) + cw / 2, y + ch / 2 + size * 0.36, v, size, fill=c, anchor='middle')
        return x + len(values) * (cw + gap) - gap

    def svg(self):
        return '\n'.join(self.o + ['</svg>'])
