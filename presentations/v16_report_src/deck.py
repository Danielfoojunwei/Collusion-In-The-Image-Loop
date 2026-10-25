"""Six-slide, five-minute talk built from the report's own figures and verified claims (run figs.py first)."""
import html
import re

from claims import N, QT, EV

E = html.escape
svg = {k: re.sub(r'style="width:[^"]*"', 'style="width:100%;display:block"', open(f'{f}.svg').read(), count=1)
       for k, f in (('fig1', 'fig1'), ('veto', 'veto_slide'), ('timeline', 'timeline'), ('funnel', 'funnel'))}
pre, post, L = N['pre'], N['post'], N['ladder']


def qe(k):
    return EV[k]['quote']


def whoe(k):
    e = EV[k]
    return f"{e['who']}, {e['arm']} round {e['round']}"


def quote(text, who):
    return f'<blockquote>“{E(text)}”<cite>{who}</cite></blockquote>'


slides = [f'''
<section class="title">
  <h1>Collusion Needs an Accomplice</h1>
  <p class="sub">Reasoning-trace evidence on why LLM agents sent, but never took up, covert codes hidden in useful work</p>
  <p class="auth">Daniel Foo · Nanyang Technological University (NTU), Singapore<br>Apart Research AI Collusion Research Sprint, 23–25 October 2026 · Track 3.1, with Track 2</p>
  <div class="nums">
    <div><b>{N['enc']}/{N['rounds']}</b><span>rounds in which the paid sender hid an exact six-letter code in an accurate report</span></div>
    <div><b>{N['exposures_tag_in_reasoning']} → {N['exposures_recorded']}</b><span>times the tag reached a receiver’s reasoning → times it was recorded (outsiders: {N['t2_tag_enc']}/{N['t2_reads_enc']})</span></div>
    <div><b>{N['ev_median']:,.0f} vs 4,000</b><span>median value of joining by the receivers’ own estimates vs the certain client credit</span></div>
  </div>
</section>''', f'''
<section>
  <h2>Prior work asks if agents <i>can</i> collude. We ask if a receiver <i>will</i>.</h2>
  <div class="full g">{svg['fig1']}</div>
  <p class="foot">GLM-5.3-Flash agents · ground-truth tags, private pre-exposure judgments, 1.4M characters of raw reasoning · informed Claude reviewer · arms b01 original, b03 figures removed, b05 reviewer history · 5 matched rounds each</p>
</section>''', f'''
<section>
  <h2>1 · The sender was strategic, adaptive and deterrable</h2>
  <div class="full">{svg['timeline']}</div>
  <div class="three two-col">
    {quote(qe('b01r3_A_threshold'), whoe('b01r3_A_threshold') + ': pricing an attempt')}
    {quote(qe('b03r3_A_nolegit'), whoe('b03r3_A_nolegit') + ': no cover story for the worst candidate, so it stopped')}
    {quote(qe('b05r3_A_worked'), whoe('b05r3_A_worked') + ': diagnosing the failure')}
  </div>
</section>''', f'''
<section>
  <h2>2 · The receivers never took a code up</h2>
  <div class="full fn">{svg['funnel']}</div>
  <div class="two even">
    <div><h3>Mechanism: the shared-codebook trap</h3><p>Reviews were public. Receivers searched only for patterns the reviewer had already exposed, so each move A made to evade the monitor also evaded its partners.</p></div>
    {quote(qe('b03r2_C_lastwar'), whoe('b03r2_C_lastwar') + ': checking for the exposed pattern while A had moved to sentence initials')}
  </div>
</section>''', f'''
<section>
  <h2>3 · When they found it, they declined</h2>
  <div class="two even top">
    <div class="fig">{svg['veto']}<p class="cap">Each ballot by the receiver’s own stated beliefs. Shaded: joining beats the certain 4,000.</p></div>
    <div>
      {quote(qe('b03r1_C_derived'), whoe('b03r1_C_derived'))}
      {quote(qe('b03r1_C_ambig'), 'then recorded “unknown”')}
      <ul>
        <li><b>Coordination pessimism:</b> median {N['align_median']:.2f} chance the partner joins.</li>
        <li><b>Sanction over-generalisation:</b> one catch read as ending the grant for good, in two arms.</li>
      </ul>
    </div>
  </div>
</section>''', f'''
<section>
  <h2>What this means for collusion research and oversight</h2>
  <ol class="recs">
    <li><b>Capability is not propensity.</b> The code was legible (outsiders {N['t2_tag_enc']}/{N['t2_reads_enc']}) and the sender willing; collusion failed at the receivers. Report the uptake funnel: notice, decode, record, act.</li>
    <li><b>Public feedback denies colluders a private codebook.</b> Broadcasting findings to the agents a colluder needs made every evasion self-defeating. It also taught the sender, so route evasion-relevant detail to principals.</li>
    <li><b>Receivers are a first line of defence:</b> independent verifiers, a certain payoff for correct work, and acting only on sourced evidence.</li>
    <li><b>Residual risk:</b> the sender resumed when a cover story appeared; a payload naming no candidate escaped sanction; outsiders could be the accomplice.</li>
  </ol>
  <p class="foot">Limits: induced coordination; receiver terms not varied; one cohort per arm, 5 dependent rounds; beliefs are self-reports; the actor route ingested page images at low resolution (diagram-only codes likely unreadable to agents). All quotations verified against the run records.</p>
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
open('deck.html', 'w').write('<!doctype html><html><head><meta charset="utf-8"><title>Collusion Needs an Accomplice</title><style>'
                             + css + '</style></head><body>' + ''.join(slides) + '</body></html>')
print('deck.html')
