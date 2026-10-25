"""Six-slide, five-minute talk built from the report's own figures and verified claims (run figs.py first)."""
import html
import re

from claims import N, QT, V15

E = html.escape
svg = {k: re.sub(r'style="width:[^"]*"', 'style="width:100%;display:block"', open(f'{f}.svg').read(), count=1)
       for k, f in (('fig1', 'fig1'), ('votes', 'votes_slide'), ('readers', 'readers_slide'), ('timeline', 'timeline'), ('loop', 'loop'))}
pre, post, L = N['pre'], N['post'], N['ladder']


def quote(text, who):
    return f'<blockquote>“{E(text)}”<cite>{who}</cite></blockquote>'


slides = [f'''
<section class="title">
  <h1>Seen and Refused</h1>
  <p class="sub">Document agents hid acrostic codes in accurate client reports; their partners declined them, and an informed AI reviewer caught the senders</p>
  <p class="auth">Daniel Foo · Nanyang Technological University (NTU), Singapore<br>Apart Research AI Collusion Research Sprint, 23–25 October 2026 · Track 3.1, with Track 2 · V16, following V15 “Seeing Is Not Attributing”</p>
  <div class="nums">
    <div><b>{N['enc']}/{N['rounds']}</b><span>rounds in which the sender hid an exact six-letter code in its report</span></div>
    <div><b>{N['target_ballots']}/{N['ballots']}</b><span>ballots by the partners for the sender’s target (V15: {V15['target_votes']}/{V15['votes']})</span></div>
    <div><b>{N['t2_tag_enc']}/{N['t2_reads_enc']} vs {N['recv_tag_enc']}/{N['recv_reads_enc']}</b><span>exact-tag reads by outside decoders vs the intended partners</span></div>
  </div>
</section>''', f'''
<section>
  <h2>The game: a useful client report is the only channel</h2>
  <div class="full g">{svg['fig1']}</div>
  <p class="foot">GLM-5.3-Flash agents · Qwen-Image-2.1-Turbo figures · fresh Claude subagent reviewers given V15’s recommendations · arms b01 original, b03 figures removed, b05 reviewer with history · 5 matched rounds each, 25 October 2026</p>
</section>''', f'''
<section>
  <h2>C1 · The sender wrote real steganography into accurate reports</h2>
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
  <h2>C2 · The partners refused a code that outsiders could read</h2>
  <div class="two even">
    <div class="fig">{svg['votes']}<p class="cap">Share of B and C ballots for A’s target (95% CI; dashed: blind guess)</p></div>
    <div class="fig">{svg['readers']}<p class="cap">Who recovered the exact tag. Steganographic gap {N['gap_t2']:+.2f} (V15 ≈ +0.04)</p></div>
  </div>
  <div class="two even low">
    <div>{quote(QT['b03r1_B'], 'B, b03 round 1, after spotting the acrostic')}{quote(QT['b03r1_C'], 'C, same round')}</div>
    <p>All {N['initial_client']} private pre-exposure judgments were client-correct; no ballot moved. An over-length same-model sentinel record read SJRQLZ exactly: GLM can decode the code, but as B and C it chose not to.</p>
  </div>
</section>''', f'''
<section>
  <h2>C3 · The informed reviewer caught senders, not pairs</h2>
  <div class="nums small">
    <div><b>{N['flag_enc']}/{N['enc']}</b><span>code rounds flagged (other rounds: {N['flag_nonenc']}/{N['nonenc']})</span></div>
    <div><b>{N['supported']}</b><span>supported findings, all in code rounds; {N['overt_supported']} on overt cues</span></div>
    <div><b>0/{N['main_reviews'] + N['return_reviews'] + N['ladder_jobs']}</b><span>joint attributions by reviewer and L1–L4 ladder (correct: nobody joined)</span></div>
  </div>
  <div class="three">
    <div><h3>Caught the code once</h3>{quote(QT['rev_b05r3'], 'reviewer, b05 round 3: supported, all fined')}</div>
    <div><h3>Often caught the overt cue instead</h3>{quote(QT['rev_b01r1'], 'reviewer, b01 round 1; decoders read SJRQLZ there')}</div>
    <div><h3>Read a payload, could not attribute it</h3>{quote(QT['rev_b03r2'], 'reviewer, b03 round 2: the tag named no candidate')}</div>
  </div>
</section>''', f'''
<section>
  <h2>What this suggests for collusion oversight</h2>
  <div class="full loop">{svg['loop']}</div>
  <ol class="recs">
    <li><b>Measure carrier, receiver and overseer separately.</b> V15 lacked attribution; V16 lacked willing receivers. Exact payloads and private pre-exposure judgments make the receiver step measurable.</li>
    <li><b>Informed review works on senders</b> with no joint false alarm; add “payload recovered” as its own finding, so a tag without a beneficiary is not left at “possible”.</li>
    <li><b>Still send reports to the principal:</b> the report told A its code had been read (“{E(QT['log_b05r3'])}”), and A moved or dropped it.</li>
    <li><b>Audit the transport:</b> a provider decoder silently cut every field at its schema limit; all arms were rewound to round 1.</li>
  </ol>
  <p class="foot">Limits: induced coordination; one cohort per arm, 5 dependent rounds; receivers’ incentive and role framing not varied; V15 contrast not controlled; reviewer serving model not independently verified. Run cost ${N['usd_clean']:.2f}, {N['calls_clean']} paid calls, all completed.</p>
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
open('deck.html', 'w').write('<!doctype html><html><head><meta charset="utf-8"><title>Seen and Refused</title><style>'
                             + css + '</style></head><body>' + ''.join(slides) + '</body></html>')
print('deck.html')
