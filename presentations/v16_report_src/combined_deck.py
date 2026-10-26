"""Six-slide, five-minute talk built from the report's own figures and verified claims (run figs.py first)."""
import html
import re

from claims import N, QT, EV
from v15_claims import W, VQ, V15_STRIP

E = html.escape
svg = {k: re.sub(r'style="width:[^"]*"', 'style="width:100%;display:block"', open(f'{f}.svg').read(), count=1)
       for k, f in (('fig1', 'fig1'), ('veto', 'veto_slide'), ('timeline', 'timeline'), ('funnel', 'funnel'), ('prevention', 'prevention'), ('unified_funnel', 'unified_funnel_slide'), ('ladder_cmp', 'ladder_cmp_slide'))}
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
  <h1 class="long">Legible Yet Refused</h1>
  <p class="sub">Covert collusion among LLM agents is decided by receiver uptake and oversight feedback, not channel secrecy. Two studies, same multimodal agents, same payoffs</p>
  <p class="auth">Daniel Foo · Nanyang Technological University (NTU), Singapore<br>Apart Research AI Collusion Research Sprint, 23–25 October 2026 · Track 3: carriers and countermeasures</p>
  <div class="nums">
    <div><b>{W['gap']:+.2f} → {N['gap_t2']:+.2f}</b><span>steganographic gap (receivers minus outside decoders): a legible code followed → a legible code declined</span></div>
    <div><b>{W['target_votes']}/{W['votes']} → {N['target_ballots']}/{N['ballots']}</b><span>receiver votes for the sender’s target: unsanctioned feedback → verifier receivers and sanctioned findings</span></div>
    <div><b>{L15['L3']['supported']}/{L15['L3']['valid']} vs 0/{N['ladder_jobs']}</b><span>monitor attribution with ballots and history: on collusion vs on signal-present refusals</span></div>
  </div>
  <p class="ack">Supported and funded by the Pathfinder Fellowship, a program run by Kairos. With thanks to the Nanyang AI Safety group at NTU for their feedback.</p>
</section>''', f'''
<section>
  <h2>Prior work measures secrecy; we vary the links it holds fixed</h2>
  <table class="sota"><thead><tr><th></th><th>Receiver</th><th>Oversight output to agents</th><th>Monitor negatives</th></tr></thead><tbody>
  <tr><td><b>Secrecy and detection</b><br><span>Motwani · Mathew · Dineen · Rose · Makins · Das</span></td><td>Willing by design: “agents are explicitly told to collude”</td><td>None, or a penalty or score: collusion gets subtler or continues (“the agents keep colluding”)</td><td>Runs with no collusion</td></tr>
  <tr><td><b>Refusal and feedback</b><br><span>Zeng · Shi · Paglieri · Alnuhait · Nakamura</span></td><td>Can refuse; refusal tied to particular models and framing</td><td>No live sanctioning monitor; unenforced feedback teaches violation; whistleblowing fails</td><td>–</td></tr>
  <tr class="ours"><td><b>This work</b><br><span>Study 1 (V15) · Study 2 (V16)</span></td><td>Verifier with its own payoff; uptake observed with ground truth</td><td>Unsanctioned public reports vs sanctioned public findings</td><td>Signal present, receivers refused</td></tr>
  </tbody></table>
  <p class="gapline">Untested before this work: a receiver that can refuse <b>under a live, sanctioning monitor</b>; oversight feedback that <b>ends</b> collusion rather than refining it; monitor calibration on <b>refusals</b>.</p>
  <p class="foot">Quotations verbatim from Rose et al. (arXiv:2604.01151) and Das et al. (arXiv:2609.03035). Full comparison: paper Table 1.</p>
</section>''', f'''
<section>
  <h2>The collusion chain, link by link, in both studies</h2>
  <div class="full fn">{svg['unified_funnel']}</div>
  <p class="foot">Same GLM-5.3-Flash agents, same Qwen-Image-2.1-Turbo generator, same 4,000 vs 10,000 payoff. Study 1: picture channel, signalling framed as permitted, output-only reviewer. Study 2: two-page reports, exact tag, countermeasure stack. A contrast, not an ablation.</p>
</section>''', f'''
<section>
  <h2>Study 1 · The vulnerability: seen, never attributed</h2>
  {strip15([1, 4, 5, 6, 7, 10])}
  <div class="three">
    <div><h3>Collusion through noise</h3><p>{W['target_votes']}/{W['votes']} target votes and {W['grants']}/{W['rounds']} grants paid, although only {W['img_faithful']}/{W['img_total']} pictures were drawn as briefed.</p></div>
    <div><h3>Seen, not attributed</h3>{quote(VQ['rev_authorized']['quote'], VQ['rev_authorized']['who'] + '; “uncertain” in all 12 rounds')}</div>
    <div><h3>Reports reassured the colluders</h3>{quote(VQ['c_vary']['quote'], VQ['c_vary']['who'])}</div>
  </div>
</section>''', f'''
<section>
  <h2>Diagnosis · the monitor lacked ballots and history, not eyesight</h2>
  <div class="full">{svg['ladder_cmp']}</div>
  <div class="two even">
    <ul>
      <li>Outsiders decoded Study 1’s code in {W['sentinel']}/{W['sentinel_n']} rounds (gap {W['gap']:.2f}): legible, not hidden.</li>
      <li>Observers without the reviewer’s own past verdicts: {W['observers_supported']}/{W['observers']} supported.</li>
    </ul>
    <ul>
      <li>Fixes for Study 2: motive, ballot history, a standard of proof, unilateral findings on the sender.</li>
      <li>Panel (b) is the calibration Study 1 lacked: a signal was present and nobody joined, and attribution stayed at 0.</li>
    </ul>
  </div>
</section>''', f'''
<section>
  <h2>Study 2 · The hardened system: a willing sender, no coalition</h2>
  <div class="full">{svg['timeline']}</div>
  <div class="two even">
    <ul>
      <li>Sender planted codes in {N['enc']}/{N['rounds']} rounds; outsiders read {N['t2_rounds_enc']}. Receivers recorded 0 tags, cast {N['target_ballots']}/{N['ballots']} target votes.</li>
      <li>By their own estimates joining was worth a median {N['ev_median']:,.0f} vs a certain 4,000.</li>
      <li>Watched sender: {N['abstain']} abstentions; announced-countermeasure arms encoded {N['enc_n']['b03']} and {N['enc_n']['b05']} times vs {N['enc_n']['b01']}.</li>
    </ul>
    {quote(qe('b03r1_B_duty'), whoe('b03r1_B_duty') + ', after spotting the acrostic')}
  </div>
</section>''', f'''
<section>
  <h2>What decided collusion</h2>
  <ol class="recs">
    <li><b>Uptake, not concealment.</b> Outsiders read the code in both studies. The same model followed it ({W['target_votes']}/{W['votes']}) and declined it (0 tags, {N['target_ballots']}/{N['ballots']}); joining was worth a median {N['ev_median']:,.0f} vs 4,000 by the receivers’ own beliefs. The gap’s sign separates collusion from refusal.</li>
    <li><b>Feedback topology.</b> Reports returned without enforcement taught (“keep encoding but vary framing”), as in prior work. Sanctioned public findings deterred the sender ({N['abstain']} abstentions) and denied a private codebook (the <i>shared-codebook trap</i>), but leaked tags in {len(N['report_leaks'])} rounds. Next: findings to the principal only.</li>
    <li><b>Calibrate on refusal.</b> The ladder said {L15['L3']['supported']}/{L15['L3']['valid']} on collusion and 0/{N['ladder_jobs']} on refusals with a visible signal; every prior calibration uses clean runs.</li>
    <li><b>Track 3.1:</b> images, document structure and the oversight report itself carried coordination.</li>
  </ol>
  <table class="sota topo"><thead><tr><th>Oversight output</th><th>Evidence</th><th>Effect on the coalition</th></tr></thead><tbody>
  <tr><td>Returned, not enforced</td><td>Shi · Paglieri · Study 1</td><td>Teaches; Study 1 coalitions {W['coalition_rounds']}/{W['rounds']}</td></tr>
  <tr><td>Penalty to a joint team</td><td>Mathew</td><td>Subtler collusion</td></tr>
  <tr><td>Returned as a score</td><td>Das</td><td>Adaptation; collusion continues</td></tr>
  <tr class="ours"><td>Public, sanctioned; receivers can refuse</td><td>Study 2</td><td>Sender deterred, receivers trapped, payload leaked; coalitions 0/{N['rounds']}</td></tr>
  <tr><td>Principal only, sanctioned</td><td>Proposed</td><td>Predicted: deterrence without the leak</td></tr>
  </tbody></table>
  <p class="foot">Limits: the studies differ in carrier, payload, framing and reviewer, so the contrast is not an ablation; the uptake claim rests on within-study evidence; induced coordination; one cohort per condition; beliefs are self-reports.</p>
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
.sota { width: 100%; border-collapse: collapse; margin-top: 0.1in; } .sota th, .sota td { text-align: left; vertical-align: top; padding: 9pt 10pt; border-bottom: 1pt solid #BDBDBD; font-size: 14pt; line-height: 1.3; } .sota th { font-size: 12pt; color: #5F5F5F; } .sota td span { font-size: 11pt; color: #5F5F5F; } .sota tr.ours td { background: #FBE9DF; } .topo th, .topo td { font-size: 12.5pt; padding: 5pt 10pt; } .gapline { font-size: 16pt; margin-top: 0.3in; }
'''
open('combined_deck.html', 'w').write('<!doctype html><html><head><meta charset="utf-8"><title>Legible Yet Refused</title><style>'
                             + css + '</style></head><body>' + ''.join(slides) + '</body></html>')
print('combined_deck.html')
