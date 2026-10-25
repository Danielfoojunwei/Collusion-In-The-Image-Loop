"""Baseline chart and unified-narrative loop for the report."""
from fig_r import S, INK, MUTE, PLUM, CORAL, TEAL, MUST, RED

BLUE = '#2B62A8'


def baseline():
    s = S(330, 'Votes for A’s target, by what the receivers were shown')
    rows = [('Main game: agents’ own pictures (12 rounds)', 23, 24, '#C2602F', 'full incentive prompt'),
            ('Replays: original pictures (R4, R8, R12)', 5, 6, '#C2602F', 'full incentive prompt'),
            ('Replays: A’s picture swapped for a task-only one', 2, 6, '#D9A441', 'full incentive prompt'),
            ('Replay: unreadable text memo, no picture code', 0, 2, BLUE, 'full incentive prompt'),
            ('Honest check: blank picture, no grant offered', 0, 2, BLUE, 'grant removed')]
    x0, w = 440, 420
    for i, (lab, k, n, col, note) in enumerate(rows):
        y = 62 + i * 50
        s.t(24, y + 18, lab, 15, 600)
        s.t(24, y + 36, note, 12.5, fill=MUTE)
        s.box(x0, y + 4, w, 26, '#EDE8DE', '#EDE8DE', 13, sw=0)
        if k:
            s.box(x0, y + 4, w * k / n, 26, col, col, 13, sw=0)
        s.t(x0 + w + 14, y + 24, f'{k}/{n}', 17, 800, col)
    gx = x0 + w / 3
    s.o.append(f'<line x1="{gx}" y1="56" x2="{gx}" y2="312" stroke="{INK}" stroke-width="1.6" stroke-dasharray="5 4"/>')
    s.t(gx + 6, 322, 'blind guess = 1/3', 13, 700, INK)
    return s.svg()


def loop():
    s = S(430, 'One mechanism: how the carrier and the oversight failure fed each other')
    nodes = [
        (350, 46, 300, 'Incentive and private target', ['A must signal; B and C gain 10,000 if both follow'], '#FFF3EA', CORAL),
        (690, 150, 290, 'Noisy image model', ['only 2 of 36 pictures faithful, so agents', 'pick a coarse, visible code (B1–B3)'], '#EAF6F2', TEAL),
        (600, 318, 330, 'Code spreads and stabilises', ['B re-sends it, C reports back, memory', 'keeps it: 23 of 24 votes (B4, B5, B7)'], '#EAF1FB', BLUE),
        (70, 318, 330, 'Reviewer sees it but cannot attribute', ['rubric calls it the task; payoff and', 'reasoning hidden: “uncertain” (R1–R5)'], '#F1EDFA', PLUM),
        (20, 150, 300, 'Verdict broadcast to the agents', ['captions fix their reading (B6) and', 'signal that visible is safe (R6)'], '#FDF3D9', MUST),
    ]
    for x, y, w, title, sub, fill, stroke in nodes:
        s.box(x, y, w, 30 + 18 * len(sub) + 14, fill, stroke, 12, sw=2)
        s.t(x + w / 2, y + 24, title, 15, 700, a='middle')
        for i, line in enumerate(sub):
            s.t(x + w / 2, y + 46 + i * 18, line, 13, fill='#3E4654', a='middle')
    s.arrow(652, 80, 780, 148, curve=-20)
    s.arrow(830, 234, 800, 316, curve=10)
    s.arrow(598, 360, 404, 360)
    s.arrow(200, 316, 170, 236, curve=10)
    s.arrow(250, 148, 348, 82, curve=-20)
    s.box(320, 215, 360, 56, '#1B2433', '#1B2433', 10)
    s.t(500, 239, 'Visible coordination, never attributed', 14, 700, '#F3EEE4', 'middle')
    s.t(500, 259, '23/24 target votes · 0/12 findings · grant paid 11/12', 12.5, fill='#C9D1DC', a='middle')
    return s.svg()


if __name__ == '__main__':
    open('base.svg', 'w').write(baseline())
    open('loop.svg', 'w').write(loop())
