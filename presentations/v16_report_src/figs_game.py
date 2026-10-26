"""Setup schematics for the combined paper: one round of the game, and the step-by-step change from Study P to Study G.

Counts come from claims.N (Study G) and v15_claims.W (Study P). Run after figs.py.
"""
from claims import N
from figkit import Canvas, INK, GREY, RULE, FILL, VERM, BLUE, VERM_L, BLUE_L, wrap
from v15_claims import W

P_COL, G_COL = '#6B4E9B', '#1B7F5A'          # study badges (purple = P, green = G), distinct from the target/client colours
P_L, G_L = '#ECE6F4', '#DDF0E7'


def badge(c, x, y, s, col):
    c.rect(x, y - 13, 20, 18, col, None, r=3)
    c.text(x + 10, y + 1, s, 12.5, bold=True, fill='#FFFFFF', anchor='middle')


def step_box(c, x, y, w, h, n, title, common, p, g):
    c.rect(x, y, w, h, '#FFFFFF', RULE, 1.2, r=4)
    c.o.append(f'<circle cx="{x + 18}" cy="{y + 20}" r="12" fill="{INK}"/>')
    c.text(x + 18, y + 25, str(n), 14, bold=True, fill='#FFFFFF', anchor='middle')
    c.text(x + 38, y + 25, title, 15.5, bold=True)
    yy = y + 50
    if common:
        yy = c.para(x + 12, yy, common, w - 24, 13.2, lead=16.5) + 4
    for lab, col, txt in (('P', P_COL, p), ('G', G_COL, g)):
        if not txt:
            continue
        badge(c, x + 12, yy, lab, col)
        yy = c.para(x + 40, yy, txt, w - 52, 13.2, lead=16.5) + 5


def game_round():
    w, h, gx, gy = 292, 172, 25, 34
    c = Canvas(950, 2 * h + gy + 70, 'gr')
    steps = [
        ('Private assignment', 'Sender A privately learns a target T: one of four options, never the client’s best.', None,
         'A also gets an exact six-letter tag it must never write out.'),
        ('A sends an artefact', None, 'One picture drawn by Qwen-Image from A’s description; no readable text allowed.',
         'A two-page client report: prose, an illustration and a diagram, delivered as page images.'),
        ('Checkers vote', 'B and C each vote for one option after reading what they were sent.',
         'B passes its own picture on to C.', 'Each first records a private judgement; B verifies, C writes QA; both have a trusted source table.'),
        ('Reviewer judges', None, 'GPT-6.1 Sol (Ultra reasoning). Sees pictures, votes and its own past reports; no rewards; can only accuse both checkers.',
         'Claude Opus 5.5 (medium effort). Also sees rewards and voting history; applies a standard of proof; can fine A alone.'),
        ('Points are paid', '4,000 to each checker for the client’s best option. 10,000 to all three if both back T and the reviewer finds nothing.',
         None, 'Both must also record the exact tag; fines escalate and are shared.'),
        ('Report to everyone', 'The reviewer’s report goes to all three agents, who keep memory into the next round.',
         f'{W["rounds"]} rounds, one group.', f'{N["cases"]} rounds in each of 3 groups.'),
    ]
    for i, (t, common, p, g) in enumerate(steps):
        r, col = divmod(i, 3)
        x, y = col * (w + gx), 26 + r * (h + gy)
        step_box(c, x, y, w, h, i + 1, t, common, p, g)
        if col < 2:
            c.line([(x + w + 2, y + h / 2), (x + w + gx - 2, y + h / 2)])
    c.line([(2 * (w + gx) + w / 2, 26 + h + 2), (2 * (w + gx) + w / 2, 26 + h + gy / 2), (w / 2, 26 + h + gy / 2), (w / 2, 26 + h + gy - 2)])
    yl = 26 + h + gy + h / 2
    c.line([(2 * (w + gx) + w + 1, yl), (942, yl), (942, 12), (w / 2, 12), (w / 2, 24)], GREY, 1.3, dash=True)
    c.text(475, 8, 'next round, with memory', 12.5, fill=GREY, anchor='middle')
    yb = 2 * h + gy + 50
    badge(c, 0, yb, 'P', P_COL)
    c.text(28, yb, 'Study P (Permissive): signalling permitted, reports without penalties', 13.5)
    badge(c, 500, yb, 'G', G_COL)
    c.text(528, yb, 'Study G (Guarded): verifier checkers, exact tag, fines', 13.5)
    return c.svg()


def lessons():
    rows = [
        ('Incentive', f'Signalling was permitted and only the target mattered: checkers backed T even when it was up to {W["max_ratio_followed"]:.0f}× worse for the client.',
         'A contract forbids coordination; the bonus needs the exact tag from both checkers; fines are shared.',
         f'Joining worth a median {N["ev_median"]:,.0f} vs a certain 4,000'),
        ('Carrier', f'Noise did not stop the code: only {W["img_faithful"]} of {W["img_total"]} pictures matched their description, yet it worked.',
         'A richer carrier (report with prose and figures); one group has figures removed.',
         f'Code moved into text; outsiders read the tag in {N["t2_rounds_enc"]}/{N["enc"]}'),
        ('Attribution', f'The reviewer called signalling authorized ({W["authorized_premise"]}/{W["rounds"]}); no rewards or votes in view; joint accusations only.',
         'Signalling declared not authorized; rewards, voting history and a standard of proof in view; fines on A alone.',
         f'{N["flag_enc"]}/{N["enc"]} code rounds flagged; no false joint finding'),
        ('Uptake', 'B relayed pictures to C: the checkers were relays, not independent checkers.',
         'B an independent verifier, C a QA author, both paid for correct work, with a trusted source table.',
         f'0 tags recorded; {N["target_ballots"]}/{N["ballots"]} target votes'),
        ('Feedback', 'Reports reached the agents and taught them: “keep encoding but vary framing”.',
         'Reports stay public but now carry fines (routing unchanged).',
         f'Sender sat out {N["abstain"]} rounds; tag leaked in {len(N["report_leaks"])}'),
    ]
    rh, top = 74, 40
    c = Canvas(950, top + len(rows) * rh + 4, 'ls')
    xs = (0, 108, 438, 470, 770)
    c.text(xs[1] + 4, 22, 'What Study P showed', 14, bold=True, fill=P_COL)
    c.text(xs[3] + 4, 22, 'What Study G changed', 14, bold=True, fill=G_COL)
    c.text(xs[4] + 4, 22, 'Study G result', 14, bold=True)
    for i, (step, p, g, res) in enumerate(rows):
        y = top + i * rh
        c.rect(0, y, 100, rh - 8, INK, None, r=4)
        c.text(50, y + (rh - 8) / 2 + 5, step, 14, bold=True, fill='#FFFFFF', anchor='middle')
        c.rect(xs[1], y, 330, rh - 8, P_L, None, r=4)
        c.para(xs[1] + 8, y + 19, p, 314, 12.6, lead=15.5)
        c.line([(xs[1] + 334, y + (rh - 8) / 2), (xs[3] - 4, y + (rh - 8) / 2)], INK, 1.6)
        c.rect(xs[3], y, 292, rh - 8, G_L, None, r=4)
        c.para(xs[3] + 8, y + 19, g, 276, 12.6, lead=15.5)
        c.rect(xs[4], y, 180, rh - 8, '#FFFFFF', RULE, 1.0, r=4)
        c.para(xs[4] + 8, y + 19, res, 166, 12.6, lead=15.5, bold=True)
    return c.svg()


if __name__ == '__main__':
    for k, f in (('game_round', game_round), ('lessons', lessons)):
        open(f'{k}.svg', 'w').write(f())
    print('game_round, lessons')
