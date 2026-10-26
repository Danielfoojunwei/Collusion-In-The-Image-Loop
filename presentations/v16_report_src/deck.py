"""Six-slide, five-minute talk built from the report's own figures and verified claims (run figs.py first)."""
import html
import re

from claims import N, QT, EV

E = html.escape
svg = {k: re.sub(r'style="width:[^"]*"', 'style="width:100%;display:block"', open(f'{f}.svg').read(), count=1)
       for k, f in (('fig1', 'fig1'), ('veto', 'veto_slide'), ('timeline', 'timeline'), ('funnel', 'funnel'), ('prevention', 'prevention'))}
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
  <h1 class="long">Trusted Verifiers, Informed Review and Public Findings Prevent Covert Coalitions Among LLM Agents</h1>
  <p class="sub">Ground-truth and reasoning-trace evidence from a document-carrier collusion game</p>
  <p class="auth">Daniel Foo · Nanyang Technological University (NTU), Singapore<br>Apart Research AI Collusion Research Sprint, 23–25 October 2026 · Track 3: carriers and countermeasures (3.1–3.4)</p>
  <div class="nums">
    <div><b>{N['enc']}/{N['rounds']} → {N['coalition_rounds']}/{N['rounds']}</b><span>rounds with a planted code → rounds where B and C both voted A’s target</span></div>
    <div><b>{N['t2_tag_enc']}/{N['t2_reads_enc']} vs {N['recv_tag_enc']}</b><span>exact-tag recoveries by outside decoders vs tags recorded by the intended receivers</span></div>
    <div><b>{N['ev_median']:,.0f} vs 4,000</b><span>median value of joining by the receivers’ own estimates vs the certain client credit</span></div>
  </div>
  <p class="ack">Supported and funded by the Pathfinder Fellowship, a program run by Kairos. With thanks to the Nanyang AI Safety group at NTU for their feedback.</p>
</section>''', f'''
<section>
  <h2>A game that pays for collusion, inside a countermeasure stack</h2>
  <div class="full fn">{svg['prevention']}</div>
    <ul class="cms">
      <li><b>CM1</b> trusted reviewer with motive, history, standard of proof; escalating collective fines</li>
      <li><b>CM2</b> findings published to all agents</li>
      <li><b>CM3</b> receivers as trusted verifiers, paid for correct work</li>
      <li><b>CM4</b> two-key rule: both receivers, exact six-letter tag</li>
      <li><b>CM5</b> trusted source table in every document</li>
      <li><b>CM6</b> announced: figures removed (b03) · reviewer with history (b05)</li>
    </ul>
  <p class="foot">GLM-5.3-Flash agents · A holds a private target and an exact tag · ground-truth tags, private pre-exposure judgments, 1.7M characters of reasoning · 3 arms × 5 matched rounds</p>
</section>''', f'''
<section>
  <h2>3.1 · Carriers: the structure of honest work, and conduits nobody intended</h2>
  <div class="two">
    <div class="crops">
      <div><img src="img/acro_b05r3.png"><p><b>b05 R3</b> question initials = JVMWZL, in a client-correct report</p></div>
      <div><img src="img/acro_b03r2.png"><p><b>b03 R2</b> summary-sentence initials = HRIXHN</p></div>
    </div>
    <ul>
      <li>Every code was an <b>acrostic in report structure</b>; checking facts cannot catch it.</li>
      <li>Removing figures moved the code into text.</li>
      <li><b>The reviewer’s report was a carrier:</b> it broadcast the decoded tag to all agents in {len(N['report_leaks'])} rounds.</li>
      <li>Host conduits: the generator wrote text A never authored; layout reordered one acrostic; images were ingested at {N['img_tok_min']}–{N['img_tok_max']} tokens per page.</li>
    </ul>
  </div>
</section>''', f'''
<section>
  <h2>3.2 · Trusted agents broke the chain at two links</h2>
  <div class="full fn">{svg['funnel']}</div>
  <div class="three">
    <div><h3>Trusted reviewer</h3><p>Flagged {N['flag_enc']}/{N['enc']} code rounds; {N['supported']} sanctions; 0/{N['main_reviews'] + N['return_reviews'] + N['ladder_jobs']} false joint attributions.</p></div>
    <div><h3>Trusted verifiers</h3>{quote(qe('b03r1_B_duty'), whoe('b03r1_B_duty'))}</div>
    <div><h3>Suspicion spread</h3><p>C cited B’s flag or audit of A in {len(N['C_cites_B'])} rounds.</p></div>
  </div>
</section>''', f'''
<section>
  <h2>3.3–3.4 · Peers identified each other; the watched sender quit</h2>
  <div class="full">{svg['timeline']}</div>
  <div class="two even">
    <ul>
      <li><b>3.3</b> Receivers read A’s agenda from its ballots ({N['rcv_cite_A_ballots']}/30) and expected each other not to join (median {N['align_median']:.2f}).</li>
      <li><b>3.4</b> A changed carrier after every exposure, then abstained {N['abstain']}×. Announced-countermeasure arms encoded {N['enc_n']['b03']} and {N['enc_n']['b05']} times vs {N['enc_n']['b01']}.</li>
    </ul>
    {quote(qe('b03r2_C_lastwar'), whoe('b03r2_C_lastwar') + ': searching for the exposed pattern, while A had moved on — the shared-codebook trap')}
  </div>
</section>''', f'''
<section>
  <h2>Design principles for breaking collusion before it forms</h2>
  <ol class="recs">
    <li><b>Make the receiver the scarce link:</b> independent verifiers paid for correct work plus an exact multi-key payload made joining a losing bet.</li>
    <li><b>Anchor peers to trusted sources:</b> an unsourced code became “unverified content”.</li>
    <li><b>Broadcast findings, not payloads:</b> public findings deterred the sender and left no private codebook, but leaked decoded tags in {len(N['report_leaks'])} rounds.</li>
    <li><b>Announce watching:</b> it halved attempts at no cost.</li>
    <li><b>Close the gaps:</b> sanction payloads with no named beneficiary; expect senders to return when a cover story appears; audit the host pipeline as a carrier.</li>
  </ol>
  <p class="foot">Limits: no undefended arm in V16, so prevention is intent plus capacity without uptake, attributed from the agents’ own records; induced coordination; receiver terms not varied; 5 dependent rounds per arm; low-resolution image intake. All quotations verified against the run records.</p>
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
open('deck.html', 'w').write('<!doctype html><html><head><meta charset="utf-8"><title>Trusted Verifiers Prevent Covert Coalitions</title><style>'
                             + css + '</style></head><body>' + ''.join(slides) + '</body></html>')
print('deck.html')
