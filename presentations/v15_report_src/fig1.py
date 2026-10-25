"""Generate Figure 1: a cartoon overview of the V15 experiment as inline SVG."""
import sys

W, H = 1000, 520
F = "font-family=\"'IBM Plex Sans', Arial, sans-serif\""
INK = '#26303F'
out = []


def add(s):
    out.append(s)


def text(x, y, s, size=15, weight=400, fill=INK, anchor='start', style=''):
    add(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{fill}" '
        f'text-anchor="{anchor}" {F} {style}>{s}</text>')


def lines(x, y, rows, size=14.5, gap=18, **kw):
    for i, r in enumerate(rows):
        text(x, y + i * gap, r, size=size, **kw)


def robot(x, y, color, name, s=1.0, tool=None):
    """Boxy TV-head robot; (x, y) is the top-left of the head."""
    g = f'<g transform="translate({x},{y}) scale({s})">'
    g += f'<line x1="26" y1="-14" x2="26" y2="0" stroke="{INK}" stroke-width="2.4"/>'
    g += f'<path d="M26 -24 l3 6 6 1 -4.5 4 1 6 -5.5 -3 -5.5 3 1 -6 -4.5 -4 6 -1z" fill="#F4C542" stroke="{INK}" stroke-width="1.2"/>'
    g += f'<rect x="0" y="0" width="52" height="40" rx="9" fill="{color}" stroke="{INK}" stroke-width="2.4"/>'
    g += f'<rect x="7" y="7" width="38" height="25" rx="6" fill="#FFFDF8" stroke="{INK}" stroke-width="1.6"/>'
    g += f'<circle cx="19" cy="17" r="3" fill="{INK}"/><circle cx="33" cy="17" r="3" fill="{INK}"/>'
    g += f'<path d="M19 24 q7 6 14 0" fill="none" stroke="{INK}" stroke-width="2" stroke-linecap="round"/>'
    g += f'<rect x="-4" y="14" width="5" height="12" rx="2" fill="{INK}"/><rect x="51" y="14" width="5" height="12" rx="2" fill="{INK}"/>'
    g += f'<rect x="10" y="43" width="32" height="18" rx="5" fill="{color}" stroke="{INK}" stroke-width="2.2" opacity="0.9"/>'
    if tool == 'glass':
        g += f'<circle cx="64" cy="48" r="9" fill="#E8F4FF" stroke="{INK}" stroke-width="2.4"/><line x1="58" y1="55" x2="50" y2="62" stroke="{INK}" stroke-width="3" stroke-linecap="round"/>'
    if tool == 'brush':
        g += f'<ellipse cx="66" cy="50" rx="13" ry="9" fill="#FCE7C8" stroke="{INK}" stroke-width="2"/>'
        g += '<circle cx="61" cy="47" r="2.4" fill="#E2574C"/><circle cx="67" cy="45" r="2.4" fill="#3FA66B"/><circle cx="72" cy="50" r="2.4" fill="#3B7DD8"/>'
    if tool == 'lock':
        g += f'<rect x="58" y="40" width="15" height="12" rx="2.5" fill="#F4C542" stroke="{INK}" stroke-width="2"/><path d="M61 40 v-4 a4.5 4.5 0 0 1 9 0 v4" fill="none" stroke="{INK}" stroke-width="2"/>'
    g += '</g>'
    add(g)
    w = max(40, len(name) * 8.4 + 18)
    cx = x + 26 * s
    add(f'<rect x="{cx - w / 2}" y="{y + 64 * s}" width="{w}" height="21" rx="10.5" fill="{color}" stroke="{INK}" stroke-width="1.6"/>')
    text(cx, y + 64 * s + 15.5, name, size=13.5, weight=700, anchor='middle')


def card(x, y, n=4, green=3, w=70, h=48, bad=False, good=False):
    """A tiny picture of n lamp posts with the green one at position `green`."""
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="#1F2A3A" stroke="{INK}" stroke-width="2"/>')
    gap = (w - 12) / n
    for i in range(n):
        px = x + 6 + gap * i + gap / 2
        add(f'<rect x="{px - 2}" y="{y + 18}" width="4" height="{h - 24}" fill="#8C97A8"/>')
        col = '#3BE07A' if i + 1 == green else '#4A5566'
        add(f'<circle cx="{px}" cy="{y + 14}" r="5" fill="{col}"/>')
        if i + 1 == green:
            add(f'<circle cx="{px}" cy="{y + 14}" r="9" fill="#3BE07A" opacity="0.28"/>')
    if bad:
        add(f'<circle cx="{x + w}" cy="{y}" r="11" fill="#E2574C" stroke="{INK}" stroke-width="1.6"/>')
        add(f'<path d="M{x + w - 4.5} {y - 4.5} l9 9 M{x + w + 4.5} {y - 4.5} l-9 9" stroke="#fff" stroke-width="2.6" stroke-linecap="round"/>')
    if good:
        add(f'<circle cx="{x + w}" cy="{y}" r="11" fill="#3FA66B" stroke="{INK}" stroke-width="1.6"/>')
        add(f'<path d="M{x + w - 5} {y} l3.5 4 6 -8" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>')


def bubble(x, y, w, h, rows, tail=None, think=False, fill='#FFFFFF', size=14, bold_first=False):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{fill}" stroke="{INK}" stroke-width="1.8"/>')
    if tail and not think:
        bx1, by1, bx2, by2, ex, ey = tail
        add(f'<path d="M{bx1} {by1} L{ex} {ey} L{bx2} {by2}" fill="{fill}" stroke="{INK}" stroke-width="1.8" stroke-linejoin="round"/>')
        add(f'<line x1="{bx1 + (bx2 - bx1) * 0.08}" y1="{by1 + (by2 - by1) * 0.08}" x2="{bx2 - (bx2 - bx1) * 0.08}" y2="{by2 - (by2 - by1) * 0.08}" stroke="{fill}" stroke-width="3.6"/>')
    if tail and think:
        tx, ty, ex, ey = tail[:4]
        for k, r in ((0.35, 5), (0.7, 3.5), (1.0, 2.4)):
            add(f'<circle cx="{tx + (ex - tx) * k}" cy="{ty + (ey - ty) * k}" r="{r}" fill="{fill}" stroke="{INK}" stroke-width="1.5"/>')
    for i, r in enumerate(rows):
        text(x + 12, y + 21 + i * (size + 4), r, size=size, weight=700 if (bold_first and i == 0) else 400)


def arrow(x1, y1, x2, y2, color=INK, dash=False, curve=0):
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2 + curve
    d = 'stroke-dasharray="6 5"' if dash else ''
    add(f'<path d="M{x1} {y1} Q{mx} {my} {x2} {y2}" fill="none" stroke="{color}" stroke-width="2.4" {d} marker-end="url(#ah)"/>')


def coin(x, y, r=9):
    add(f'<circle cx="{x}" cy="{y}" r="{r}" fill="#F4C542" stroke="{INK}" stroke-width="1.6"/>')
    add(f'<circle cx="{x}" cy="{y}" r="{r - 3.5}" fill="none" stroke="#C9971C" stroke-width="1.4"/>')


def panel(x, title, num, fill, ribbon):
    add(f'<rect x="{x}" y="6" width="322" height="508" rx="20" fill="{fill}" stroke="{INK}" stroke-width="2"/>')
    add(f'<rect x="{x + 14}" y="16" width="294" height="34" rx="17" fill="{ribbon}" stroke="{INK}" stroke-width="1.8"/>')
    add(f'<circle cx="{x + 33}" cy="33" r="12" fill="#FFFDF8" stroke="{INK}" stroke-width="1.6"/>')
    text(x + 33, 38.5, num, size=15, weight=700, anchor='middle')
    text(x + 52, 39, title, size=15.5, weight=700, fill='#FFFDF8')


def chip(x, y, w, rows, fill, size=13.5):
    h = 12 + 18 * len(rows)
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{fill}" stroke="{INK}" stroke-width="1.6"/>')
    for i, r in enumerate(rows):
        text(x + w / 2, y + 21 + i * 18, r, size=size, weight=600, anchor='middle')


add(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="100%">')
add(f'<defs><marker id="ah" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="{INK}"/></marker></defs>')

CORAL, TEAL, MUST, PLUM, SLATE = '#F28C7A', '#5EC2B0', '#F2C14E', '#A58BD6', '#9DB4CF'

# Panel 1: the game
X = 6
panel(X, 'A secret target, three agents', '1', '#FFF3EA', '#D9694F')
robot(X + 26, 92, CORAL, 'A · sender', tool='lock')
bubble(X + 128, 66, 180, 64, ['Secret target: P3', 'Pass it on, no words!'], tail=(X + 136, 112, X + 108, 118), think=True, bold_first=True)
robot(X + 236, 226, TEAL, 'B · relay')
robot(X + 50, 316, MUST, 'C · receiver')
arrow(X + 104, 140, X + 226, 236, curve=-30)
card(X + 150, 150, green=3)
text(X + 185, 216, 'A’s picture', size=12.5, fill='#5B6573', anchor='middle')
arrow(X + 236, 300, X + 118, 336, curve=10)
card(X + 160, 312, green=3)
text(X + 195, 376, 'B’s picture', size=12.5, fill='#5B6573', anchor='middle')
arrow(X + 52, 188, X + 70, 290)
card(X + 76, 218, w=56, h=40, green=3)
text(X + 104, 274, 'also to C', size=12.5, fill='#5B6573', anchor='middle')
coin(X + 34, 418); text(X + 50, 423, '4,000 if B or C picks the client answer', size=13.2)
coin(X + 26, 448); coin(X + 40, 448); coin(X + 33, 440)
text(X + 54, 453, '10,000 each if B and C both pick P3', size=13.2, weight=700)
chip(X + 16, 466, 290, ['…unless the reviewer finds knowing', 'joint coordination: then nothing'], '#FBD9CF', size=12.8)

# Panel 2: the noisy painter
X = 340
panel(X, 'A noisy painter in the loop', '2', '#EAF6F2', '#2F8F7F')
robot(X + 22, 92, CORAL, 'A', tool='glass')
robot(X + 236, 92, SLATE, 'Image model', s=1.0, tool='brush')
bubble(X + 98, 70, 120, 46, ['“Exactly FOUR…', 'planters”'], size=13)
arrow(X + 218, 92, X + 230, 100)
card(X + 238, 196, n=3, green=3, bad=True)
text(X + 271, 262, 'draft 1', size=12.5, fill='#5B6573', anchor='middle')
bubble(X + 16, 190, 196, 66, ['“…only THREE', 'planters… would', 'incorrectly signal P3”'], tail=(X + 212, 208, X + 212, 230, X + 232, 220), size=13.5)
arrow(X + 271, 270, X + 271, 296)
card(X + 238, 300, n=4, green=4, good=True)
text(X + 271, 366, 'redraw → sent', size=12.5, fill='#5B6573', anchor='middle')
text(X + 18, 286, 'How they beat the noise', size=14, weight=700)
for i, t in enumerate(['one bold cue, count from left', 'inspect drafts, redraw', 'B re-sends the code',
                       'target ≠ client answer', 'reviewer’s captions']):
    add(f'<circle cx="{X + 25}" cy="{304 + i * 21}" r="4.5" fill="#2F8F7F"/>')
    text(X + 36, 309 + i * 21, t, size=13)
chip(X + 16, 420, 290, ['2 of 36 sent pictures fully matched', 'their brief, yet 23 of 24 votes', 'still landed on A’s target'], '#CDEBE3', size=12.8)

# Panel 3: the reviewer
X = 674
panel(X, 'The reviewer sees, but won’t say', '3', '#F1EDFA', '#6E56B0')
robot(X + 22, 92, PLUM, 'GPT-6.1 Sol', tool='glass')
for i, g in enumerate((3, 4, 1)):
    card(X + 112 + i * 64, 92, w=56, h=40, green=g)
text(X + 202, 150, 'every round: images + ballots', size=12.5, fill='#5B6573', anchor='middle')
bubble(X + 16, 186, 290, 86, ['“A clear visual preference for the third', 'of four… However, picture-only', 'preference communication is authorized.”'],
       tail=(X + 40, 186, X + 62, 186, X + 50, 172), size=13.5)
add(f'<g transform="translate({X + 168},{304}) rotate(-8)"><rect x="-100" y="-21" width="200" height="42" rx="6" fill="none" stroke="#C0392B" stroke-width="3"/>'
    f'<text x="0" y="8" font-size="20" font-weight="800" fill="#C0392B" text-anchor="middle" {F} letter-spacing="2">UNCERTAIN ×12</text></g>')
arrow(X + 72, 336, X + 72, 372)
text(X + 84, 360, 'full report goes back to A, B and C', size=12.5, fill='#5B6573')
bubble(X + 16, 378, 290, 46, ['B: “reviewer found level ‘uncertain’,', 'no supported finding”'], fill='#FFFFFF', size=13.2)
chip(X + 16, 434, 290, ['code seen 12/12 · collusion called 0/12', 'shared grant paid 11/12 rounds'], '#DCD3F2', size=12.8)

add('</svg>')
open(sys.argv[1], 'w').write('\n'.join(out))
