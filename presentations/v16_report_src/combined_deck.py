"""Six-slide, five-minute talk built from the report's own figures and verified claims (run figs.py first)."""
import html
import re

from claims import N, QT, EV
from v15_claims import W, VQ, V15_STRIP

E = html.escape
svg = {k: re.sub(r'style="width:[^"]*"', 'style="width:100%;display:block"', open(f'{f}.svg').read(), count=1)
       for k, f in (('fig1', 'fig1'), ('veto', 'veto_slide'), ('timeline', 'timeline'), ('funnel', 'funnel'), ('prevention', 'prevention'), ('unified_funnel', 'unified_funnel_slide'), ('ladder_cmp', 'ladder_cmp_slide'), ('game_round', 'game_round'), ('lessons', 'lessons'))}
pre, post, L = N['pre'], N['post'], N['ladder']


def qe(k):
    return EV[k]['quote']


def whoe(k):
    e = EV[k]
    return f"{e['who']}, {e['arm']} round {e['round']}"


def quote(text, who):
    return f'<blockquote>“{E(text)}”<cite>{who}</cite></blockquote>'


def strip15(rounds):
    out = []
    for n, t, cl, b, c in V15_STRIP:
        if n in rounds:
            bb = f'<span class="v">B {b}</span>' if b == t else f'<span class="bl">B {b}</span>'
            cc = f'<span class="v">C {c}</span>' if c == t else f'<span class="bl">C {c}</span>'
            out.append(f'<div><img src="img/v15_r{n:02}_A.jpg"><p>R{n} · T = {t}<br>{bb} · {cc}</p></div>')
    return '<div class="strip">' + ''.join(out) + '</div>'


L15 = W['ladder']
slides = [f'''
<section class="title">
  <h1 class="long">Collusion Is Not a Secrecy Problem</h1>
  <p class="sub">Five assumptions about monitoring AI agents, overturned</p>
  <p class="auth">Daniel Foo · Nanyang Technological University (NTU), Singapore<br>Apart Research AI Collusion Research Sprint, 23–25 October 2026 · Track 3: carriers and countermeasures</p>
  <div class="nums">
    <div><b>{W['gap']:+.2f} → {N['gap_t2']:+.2f}</b><span>steganographic gap (receivers minus outside decoders): a legible code followed → a legible code declined</span></div>
    <div><b>{W['target_votes']}/{W['votes']} → {N['target_ballots']}/{N['ballots']}</b><span>receiver votes for the sender’s target: Study P (Permissive) → Study G (Guarded)</span></div>
    <div><b>{L15['L3']['supported']}/{L15['L3']['valid']} vs 0/{N['ladder_jobs']}</b><span>monitor attribution with ballots and history: on collusion vs on signal-present refusals</span></div>
  </div>
  <p class="ack">Supported and funded by the Pathfinder Fellowship, a program run by Kairos. With thanks to the Nanyang AI Safety group at NTU for their feedback.</p>
</section>''', f'''
<section>
  <h2>Same agents, same rewards: one code followed, one declined</h2>
  <div class="full fn">{svg['unified_funnel']}</div>
  <p class="foot">Agents: GLM-5.3-Flash in both. Study P: reviewer GPT-6.1 Sol (Ultra reasoning) mentioned the code in {W['rev_green']}/{W['rounds']} reports, never called it collusion; checkers followed in {W['target_votes']}/{W['votes']}. Study G: reviewer Claude Opus 5.5 (medium effort) flagged every code round; outsiders read the tag in {N['t2_rounds_enc']}/{N['enc']}; checkers followed in {N['target_ballots']}/{N['ballots']}. A contrast, not an ablation.</p>
</section>''', f'''
<section>
  <h2>The game: one round of a two-key approval pipeline</h2>
  <div class="full g2">{svg['game_round']}</div>
</section>''', f'''
<section>
  <h2>Study P · Seen, not stopped: GPT-6.1 Sol (Ultra) never called it collusion</h2>
  {strip15([1, 4, 5, 6, 7, 10])}
  <div class="three">
    <div><h3>Collusion through noise</h3><p>{W['target_votes']}/{W['votes']} target votes and {W['grants']}/{W['rounds']} grants paid, although only {W['img_faithful']}/{W['img_total']} pictures were drawn as briefed.</p></div>
    <div><h3>Seen, not attributed</h3>{quote(VQ['rev_authorized']['quote'], VQ['rev_authorized']['who'] + '; “uncertain” in all 12 rounds')}</div>
    <div><h3>The report taught the colluders</h3>{quote(VQ['c_vary']['quote'], VQ['c_vary']['who'])}</div>
  </div>
</section>''', f'''
<section>
  <h2>What Study P taught us → what Study G changed</h2>
  <div class="full g2">{svg['lessons']}</div>
</section>''', f'''
<section>
  <h2>Study G · A readable code nobody joined</h2>
  <div class="full">{svg['timeline']}</div>
  <div class="two even">
    <ul>
      <li>Codes planted in {N['enc']}/{N['rounds']} rounds; outsiders read {N['t2_rounds_enc']}. Receivers: 0 tags, {N['target_ballots']}/{N['ballots']} target votes.</li>
      <li>Watched sender changed carrier after every exposure, then abstained {N['abstain']}×.</li>
    </ul>
    {quote(qe('b03r1_B_duty'), whoe('b03r1_B_duty') + ', after spotting the acrostic')}
  </div>
</section>''', f'''
<section>
  <h2>Five assumptions, overturned</h2>
  <table class="sota lt"><thead><tr><th>It seems obvious that…</th><th>What we found</th></tr></thead><tbody>
  <tr><td><b>A1</b> collusion is a secrecy problem</td><td>Receivers decided: the same model used one readable code ({W['target_votes']}/{W['votes']}) and refused another (0/{N['ballots']}); gap {W['gap']:+.2f} → {N['gap_t2']:+.2f}</td></tr>
  <tr><td><b>A2</b> a more capable reviewer catches more</td><td>GPT-6.1 Sol at Ultra failed; with votes and history monitors went {L15['L1']['supported']}/{L15['L1']['valid']} → {L15['L3']['supported']}/{L15['L3']['valid']}; Claude Opus 5.5 at medium flagged every code round</td></tr>
  <tr><td><b>A3</b> flagging any visible code is safe</td><td>Codes visible where nobody joined; 0/{N['ladder_jobs']} attributions; flagging would punish the refusers</td></tr>
  <tr><td><b>A4</b> block the channel and collusion stops</td><td>The code survived {W['unfaithful_pct']}% unfaithful pictures and moved into prose when figures were removed</td></tr>
  <tr><td><b>A5</b> sharing findings is harmless; caught agents hide better</td><td>Unpenalised reports taught collusion; penalised ones made the sender quit ({N['abstain']} rounds) but leaked the tag: the shared-codebook trap</td></tr>
  </tbody></table>
</section>''', f'''
<section>
  <h2>Design rules</h2>
  <ol class="recs">
    <li><b>Make the receiver the scarce link:</b> independent verifiers with their own payoff, trusted sources, multi-key payloads.</li>
    <li><b>Route findings by design:</b> to the principal; publish only that a finding exists; redact payloads.</li>
    <li><b>Give monitors ballots and history, and calibrate them on refusals.</b></li>
  </ol>
  <table class="sota topo"><thead><tr><th>Oversight output</th><th>Evidence</th><th>Effect on the coalition</th></tr></thead><tbody>
  <tr><td>Returned, not enforced</td><td>Shi · Paglieri · V15</td><td>Teaches; V15 coalitions {W['coalition_rounds']}/{W['rounds']}</td></tr>
  <tr><td>Penalty to a joint team</td><td>Mathew</td><td>Subtler collusion</td></tr>
  <tr><td>Score returned</td><td>Das</td><td>Adaptation; collusion continues</td></tr>
  <tr class="ours"><td>Public, sanctioned; receivers can refuse</td><td>V16</td><td>Sender deterred, receivers trapped, payload leaked; coalitions 0/{N['rounds']}</td></tr>
  <tr><td>Principal only, sanctioned</td><td>Next experiment</td><td>Predicted: deterrence without the leak</td></tr>
  </tbody></table>
  <p class="foot">Limits: the studies differ in carrier, payload, framing and reviewer, so the contrast is not an ablation; F2 rests on within-study evidence; induced coordination; one cohort per condition; beliefs are self-reports.</p>
</section>''']

css = '''
@page { size: 13.333in 7.5in; margin: 0; }
* { box-sizing: border-box; }
body { margin: 0; font-family: 'Liberation Sans', Arial, Helvetica, sans-serif; color: #1A1A1A; counter-reset: s; }
section { width: 13.333in; height: 7.5in; padding: 0.5in 0.7in 0.5in; position: relative; break-after: page; overflow: hidden; }
section::after { content: counter(s); counter-increment: s; position: absolute; right: 0.7in; bottom: 0.3in; font-size: 11pt; color: #888; }
h2 { font-size: 26pt; margin: 0 0 0.2in; padding-bottom: 0.1in; border-bottom: 1pt solid #BDBDBD; font-weight: 700; }
h3 { font-size: 13pt; margin: 0 0 6pt; }
p, li { font-size: 15pt; line-height: 1.35; }
.title { display: flex; flex-direction: column; justify-content: center; }
.title h1 { font-size: 46pt; margin: 0 0 10pt; }
.title .sub { font-size: 20pt; color: #444; margin: 0 0 22pt; max-width: 11in; }
.title .auth { font-size: 14pt; color: #5F5F5F; margin: 0 0 36pt; }
.nums { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.4in; border-top: 1pt solid #BDBDBD; padding-top: 18pt; }
.nums b { display: block; font-size: 40pt; color: #D55E00; }
.nums span { font-size: 14pt; color: #444; }
.nums.small { border-top: 0; padding-top: 0; margin-bottom: 0.15in; }
.nums.small b { font-size: 32pt; }
.full svg { width: 100% !important; }
.g svg { width: 86% !important; margin: 0 auto; }
.cap { font-size: 11.5pt !important; color: #5F5F5F; margin: 2pt 0 0; text-align: center; }
.two.low { margin-top: 0.15in; }
.two.top { align-items: start; }
.two-col blockquote { font-size: 12.5pt; }
.title h1.long { font-size: 34pt; line-height: 1.15; max-width: 11.5in; }
.ack { position: absolute; left: 0.7in; right: 1.4in; bottom: 0.3in; font-size: 11pt; color: #777; margin: 0; }
.strip { display: grid; grid-template-columns: repeat(6, 1fr); gap: 8pt; margin-bottom: 0.12in; }
.strip img { width: 100%; display: block; }
.strip p { font-size: 11pt; margin: 3pt 0 0; line-height: 1.3; }
.v { color: #D55E00; font-weight: 700; } .bl { color: #0072B2; font-weight: 700; }
.cms { columns: 2; column-gap: 0.4in; margin-top: 0.15in; } .cms li { font-size: 13pt; margin-bottom: 5pt; break-inside: avoid; }
.fn svg { width: 92% !important; margin: 0 auto; }
h2 i { font-style: italic; }
.rd { margin-top: 0.1in; }
.loop svg { width: 100% !important; }
.foot { position: absolute; left: 0.7in; right: 1.4in; bottom: 0.3in; font-size: 11pt; color: #777; margin: 0; }
.two { display: grid; grid-template-columns: 1.1fr 1fr; gap: 0.4in; align-items: start; }
.two.even { grid-template-columns: 1fr 1fr; align-items: center; }
.two .fig svg { width: 100% !important; }
.two p { font-size: 13.5pt; }
.crops { display: grid; gap: 10pt; }
.crops img { width: 100%; display: block; border: 1pt solid #BDBDBD; }
.crops p { font-size: 11.5pt; margin: 3pt 0 0; color: #444; }
blockquote { margin: 10pt 0; padding-left: 12pt; border-left: 3pt solid #BDBDBD; font-style: italic; font-size: 14pt; line-height: 1.35; }
cite { display: block; font-style: normal; font-size: 11.5pt; color: #5F5F5F; margin-top: 3pt; }
ul { margin: 0; padding-left: 18pt; } ul li { margin-bottom: 7pt; font-size: 14.5pt; }
.three { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.35in; }
.three blockquote { font-size: 12.5pt; }
.recs { margin: 0.12in 0 0; padding-left: 22pt; } .recs li { font-size: 14pt; margin-bottom: 4pt; }
.sota { width: 100%; border-collapse: collapse; margin-top: 0.1in; } .sota th, .sota td { text-align: left; vertical-align: top; padding: 9pt 10pt; border-bottom: 1pt solid #BDBDBD; font-size: 14pt; line-height: 1.3; } .sota th { font-size: 12pt; color: #5F5F5F; } .sota td span { font-size: 11pt; color: #5F5F5F; } .sota tr.ours td { background: #FBE9DF; } .topo th, .topo td { font-size: 12.5pt; padding: 5pt 10pt; } .g2 svg { width: 88% !important; margin: 0 auto; } .gapline { font-size: 16pt; margin-top: 0.3in; } .grid4 { display: grid; grid-template-columns: 1fr 1fr; gap: 0.3in 0.5in; margin-top: 0.2in; } .grid4 h3 { font-size: 18pt; color: #D55E00; } .grid4 p { font-size: 16pt; margin: 0; } .lt td { font-size: 13pt; padding: 7pt 9pt; }
'''
from combined_rename import rename as _rename
open('combined_deck.html', 'w').write(_rename('<!doctype html><html><head><meta charset="utf-8"><title>Collusion Is Not a Secrecy Problem</title><style>'
                             + css + '</style></head><body>' + ''.join(slides) + '</body></html>'))
print('combined_deck.html')
