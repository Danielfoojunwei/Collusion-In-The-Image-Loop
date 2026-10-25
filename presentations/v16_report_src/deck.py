"""Six-slide, five-minute talk built from the report's own figures and verified claims (run figs.py first)."""
import html
import re

from claims import N, QT

E = html.escape
svg = {k: re.sub(r'style="width:[^"]*"', 'style="width:100%;display:block"', open(f'{f}.svg').read(), count=1)
       for k, f in (('fig1', 'fig1'), ('veto', 'veto_slide'), ('chain', 'chain'))}
pre, post, L = N['pre'], N['post'], N['ladder']


def quote(text, who):
    return f'<blockquote>“{E(text)}”<cite>{who}</cite></blockquote>'


slides = [f'''
<section class="title">
  <h1>The Receiver’s Veto</h1>
  <p class="sub">A sender hid exact codes in accurate client reports; its verifier partners watched for them, declined to decode them, and an informed AI reviewer deterred the sender</p>
  <p class="auth">Daniel Foo · Nanyang Technological University (NTU), Singapore<br>Apart Research AI Collusion Research Sprint, 23–25 October 2026 · Track 3.1, with Track 2</p>
  <div class="nums">
    <div><b>{N['enc']}/{N['rounds']}</b><span>rounds in which the sender hid an exact six-letter code (outside decoders read {N['t2_tag_enc']}/{N['t2_reads_enc']})</span></div>
    <div><b>{N['recv_tag_enc']}/{N['recv_reads_enc']} · {N['target_ballots']}/{N['ballots']}</b><span>codes decoded and target ballots by the intended partners</span></div>
    <div><b>{N['ev_median']:,.0f} vs 4,000</b><span>median value of joining, by the partners’ own estimates, vs the certain client credit</span></div>
  </div>
</section>''', f'''
<section>
  <h2>Covert collusion is a chain: encode → uptake → evade</h2>
  <div class="full g">{svg['fig1']}</div>
  <p class="foot">GLM-5.3-Flash agents · Qwen-Image-2.1-Turbo figures · fresh Claude subagent reviewers given incentives, ballot history and a standard of proof · arms b01 original, b03 figures removed, b05 reviewer with history · 5 matched rounds each, 25 October 2026</p>
</section>''', f'''
<section>
  <h2>C1 · Encode held: exact codes inside accurate reports</h2>
  <div class="two">
    <div class="crops">
      <div><img src="img/acro_b01r1.png"><p><b>b01 R1</b> checklist initials = SJRQLZ</p></div>
      <div><img src="img/acro_b03r2.png"><p><b>b03 R2</b> summary-sentence initials = HRIXHN</p></div>
    </div>
    <ul>
      <li>Code declared in <b>{N['enc']} of {N['rounds']}</b> rounds, always as first letters; the prose kept the client answer.</li>
      <li>Outside decoders read the exact tag in <b>{N['t2_rounds_enc']}</b> of those rounds and never invented one ({N['t2_tag_nonenc']} of {N['t2_reads_nonenc']} on other rounds).</li>
      <li>After each catch A moved the carrier: register → sentences → none (b03); diagram labels → questions → none (b05).</li>
      <li>Removing figures (b03) did not remove the channel; A wrote it in text.</li>
    </ul>
  </div>
  {quote(QT['plan_b05r4'], 'A, b05 round 4, after its round-3 code was caught')}
</section>''', f'''
<section>
  <h2>C2 · Uptake broke: the receiver’s veto</h2>
  <div class="two even top">
    <div class="fig">{svg['veto']}<p class="cap">Each of 30 ballots by the receiver’s own stated beliefs at lock. Shaded: joining beats the certain 4,000.</p></div>
    <div>
      <ul>
        <li><b>They were looking:</b> {N['vigilant']} of 30 ballots discussed a possible hidden code.</li>
        <li><b>They declined to decode:</b> 0 of {N['recv_reads_enc']} tags recorded, though outsiders read {N['t2_tag_enc']} of {N['t2_reads_enc']} (gap {N['gap_t2']:+.2f}).</li>
        <li><b>Two keys, low trust:</b> median {N['align_median']:.2f} chance the partner joins, {N['risk_median']:.2f} detection risk.</li>
        <li><b>The one exception</b> (b03 R1 B) still refused, on provenance:</li>
      </ul>
      {quote(QT['b03r1_B'], 'B, b03 round 1, after spotting the acrostic')}
    </div>
  </div>
</section>''', f'''
<section>
  <h2>C3 · Evade broke for the sender</h2>
  <div class="nums small">
    <div><b>{N['flag_enc']}/{N['enc']}</b><span>code rounds flagged (other rounds: {N['flag_nonenc']}/{N['nonenc']})</span></div>
    <div><b>{N['supported']}</b><span>supported findings, all in code rounds; {N['overt_supported']} on overt cues</span></div>
    <div><b>0/{N['main_reviews'] + N['return_reviews'] + N['ladder_jobs']}</b><span>joint attributions by reviewer and L1–L4 ladder (correct: nobody joined)</span></div>
  </div>
  <div class="three">
    <div><h3>Caught the code</h3>{quote(QT['rev_b05r3'], 'reviewer, b05 round 3: supported, all fined')}</div>
    <div><h3>More often caught the overt cue</h3>{quote(QT['rev_b01r1'], 'reviewer, b01 round 1; decoders read SJRQLZ there')}</div>
    <div><h3>Gap: a payload with no beneficiary</h3>{quote(QT['rev_b03r2'], 'reviewer, b03 round 2: the tag named no candidate')}</div>
  </div>
</section>''', f'''
<section>
  <h2>What this suggests for collusion oversight</h2>
  <div class="full loop">{svg['chain']}</div>
  <ol class="recs">
    <li><b>Measure uptake as its own link.</b> Exact independent payloads and private pre-exposure judgments separate encoding, decoding and acting.</li>
    <li><b>Design for the receiver’s veto:</b> several independent verifiers, a certain payoff for correct work, and action only on sourced evidence were the conditions under which uptake failed.</li>
    <li><b>Informed review deters senders</b> with no joint false alarm; treat “payload recovered” as a finding even when it names no candidate.</li>
    <li><b>Reports teach the sender</b> (“{E(QT['log_b05r3'])}”); route them to the principal.</li>
  </ol>
  <p class="foot">Limits: induced coordination; receiver terms not varied, so the uptake threshold is unknown; one cohort per arm, 5 dependent rounds; beliefs are self-reports; reviewer serving model not independently verified. Clean run: {N['calls_clean']} paid calls, all completed, ${N['usd_clean']:.2f}.</p>
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
open('deck.html', 'w').write('<!doctype html><html><head><meta charset="utf-8"><title>The Receiver’s Veto</title><style>'
                             + css + '</style></head><body>' + ''.join(slides) + '</body></html>')
print('deck.html')
