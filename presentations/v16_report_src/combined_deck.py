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
  <h1 class="long">From Legible Collusion to No Coalition</h1>
  <p class="sub">Diagnosing and breaking covert coordination among multimodal LLM agents: two studies, same agents, same payoffs</p>
  <p class="auth">Daniel Foo · Nanyang Technological University (NTU), Singapore<br>Apart Research AI Collusion Research Sprint, 23–25 October 2026 · Track 3: carriers and countermeasures</p>
  <div class="nums">
    <div><b>{W['target_votes']}/{W['votes']} → {N['target_ballots']}/{N['ballots']}</b><span>receiver votes for the sender’s target: output-only oversight → countermeasure stack</span></div>
    <div><b>{L15['L1']['supported']}/{L15['L1']['valid']} → {L15['L3']['supported']}/{L15['L3']['valid']}</b><span>monitor attribution of Study 1’s collusion: pictures only → plus ballots and history</span></div>
    <div><b>0/{N['ladder_jobs']}</b><span>same monitors on Study 2’s refusals: the diagnostic separates collusion from refusal</span></div>
  </div>
  <p class="ack">Supported and funded by the Pathfinder Fellowship, a program run by Kairos. With thanks to the Nanyang AI Safety group at NTU for their feedback.</p>
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
  <h2>What the two studies show together</h2>
  <ol class="recs">
    <li><b>The feedback paradox.</b> Unsanctioned reports reassured colluders (“keep encoding but vary framing”). Sanctioned reports deterred the sender and trapped receivers in the last exposed pattern (the <i>shared-codebook trap</i>), but leaked decoded tags in {len(N['report_leaks'])} rounds. Send findings to the principal; redact payloads.</li>
    <li><b>Defend the receiver side.</b> Removing the signal moved the code (b03 wrote it in text); verifier roles, a prohibiting contract and exact two-key payloads stopped uptake. Study 1’s receivers followed targets with up to {W['max_ratio_followed']:.0f}× the client answer’s energy.</li>
    <li><b>Calibrate monitors on refusal as well as collusion.</b> The same ladder said 12/12 on Study 1’s collusion and 0/{N['ladder_jobs']} on Study 2’s refusals.</li>
    <li><b>Audit the pipeline as a carrier:</b> generated text, layout and image resolution all changed what agents could see.</li>
  </ol>
  <p class="foot">Limits: the studies differ in carrier, payload, framing and reviewer, so the contrast is not an ablation; induced coordination; one cohort per condition; beliefs are self-reports. Next: a no-countermeasure arm on Study 2’s carrier and single-countermeasure removals.</p>
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
'''
open('combined_deck.html', 'w').write('<!doctype html><html><head><meta charset="utf-8"><title>From Legible Collusion to No Coalition</title><style>'
                             + css + '</style></head><body>' + ''.join(slides) + '</body></html>')
print('combined_deck.html')
