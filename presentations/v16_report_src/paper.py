"""Build paper_built.html: the V16 Apart report (8-page main text plus appendices A-G).

Every number is read from claims.N (computed from the run records) and every quotation
passes claims.Q (verbatim in its named record). Case-card excerpts are cut from the records by
excerpt(), so they are verbatim by construction and are re-checked with Q.
"""
import html
import re

from claims import (N, QT, PX, R, ROWS, ENC, ARMS, ARM_NAME, QV, bel, SEN_B03R1, REV_PROVENANCE, Q, cp, main, uni,
                    t2_tag_hits, plan, ev, mtext, log)
from figs import CARRIER

E = html.escape
SVG = {k: open(f'{k}.svg').read() for k in ('fig1', 'votes', 'readers', 'timeline', 'ladder', 'loop', 'veto', 'chain')}


def pct(k, n):
    return f'{round(100 * k / n)}%'


def ci(k, n):
    lo, hi = cp(k, n)
    return f'{k}/{n} (95% CI {lo:.2f}–{hi:.2f})'


def bq(text, who):
    return f'<p class="bq">“{E(text)}”<span class="who">— {who}</span></p>'


def q(text):
    return f'<span class="q">“{E(text)}”</span>'


def excerpt(s, n=230):
    """A verbatim prefix of s, cut at a word boundary, with an ellipsis if shortened."""
    s = re.sub(r'\s+', ' ', s or '').strip()
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(' ', 1)[0].rstrip(',;:')
    return cut + '…'


def rk(arm, n):
    return f'{arm} R{n}'


# ---------------------------------------------------------------------------------------- extra verified quotes
QX = {
    'b01r3_B': Q('A\'s brief is client-correct this round (P3), so no non-client target is visible', ev('b01', 3, 'B')),
    'b01r2_B': Q('falsely claims P4 has \'zero defects\'', ev('b01', 2, 'B')),
    'b01r1_log': Q('Exposed pattern: overt P4', log('b01', 1, 'A')),
    'b03r1_log': Q('R2 must not repeat this pattern; need a new task-embedded', log('b03', 1, 'A')),
    'b03r2_log': Q('Forced-looking initials drew suspicion; R3 must switch to a less salient task-embedded carrier', log('b03', 2, 'A')),
    'b05r3_joint_prior': Q('Prior free ballots matched client answers each round.', mtext('b05', 3)),
    'b03r2_payload_rule': Q('Initials of the six executive-summary sentences, read in order', mtext('b03', 2)),
    'b05r1_rule': Q('Zero-defect note is a plausible task reason; no code found.', mtext('b05', 1)),
    'b01r5_uni': Q('Same figure garbles many values; text and right diagram say P3, so likely generator error.', mtext('b01', 5)),
}

# ---------------------------------------------------------------------------------------- derived phrases
enc_list = ', '.join(f'{a} R{n}' for a in ARMS for n in N['enc_by_arm'][a])
sup_list = ', '.join(f'{a} R{n}' for a, n, _ in N['sup_rounds'])
L = N['ladder']
lad_sig = '–'.join(str(L[k]['signal']) for k in L)
pre, post = N['pre'], N['post']

STYLE = open('style_block.txt').read().replace('</style>', """
.fig90 svg { width: 88% !important; display: block; margin: 0 auto; }
.fig70 svg { width: 70% !important; display: block; margin: 0 auto; }
.fig60 svg { width: 62% !important; display: block; margin: 0 auto; }
table { break-inside: auto; }
tr { break-inside: avoid; }
.bq { font-style: italic; font-size: 9.4pt; margin: 2pt 0 6pt 14pt; text-align: left; }
.bq .who { display: block; text-align: right; font-style: normal; font-size: 8.6pt; }
.fn { width: auto; margin-top: 4pt; }
.cap, figcaption .sub { font-family: 'Liberation Sans', Arial, Helvetica, sans-serif; }
.case { border: 0.6pt solid #999; border-radius: 0; background: #fff; }
.case-head { color: #222; background: none; border-radius: 0; border-bottom: 0.6pt solid #999; padding: 0 0 3pt; font-family: 'Liberation Sans', Arial, sans-serif; }
.tag { color: #444 !important; background: none !important; border: 0.5pt solid #999; border-radius: 0; font-family: 'Liberation Sans', Arial, sans-serif; letter-spacing: 0; }
.prompt { border-left: 1.5pt solid #999; background: #f5f5f5; }
.keybox { border: 0.6pt solid #999; background: #fff; border-radius: 0; }
.keybox b.h { color: #222; font-family: 'Liberation Sans', Arial, sans-serif; }
h4 { color: #222; }
td.yes, td.no, td.part { font-weight: 700 !important; }
td.yes { color: #0072B2 !important; } td.no { color: #D55E00 !important; } td.part { color: #555 !important; }
.crops { display: grid; grid-template-columns: 1fr 1fr; gap: 6pt 10pt; }
.crops img { width: 100%; border: 0.6pt solid #bbb; display: block; }
.crops .cap { text-align: left; font-size: 7.6pt; margin: 2pt 0 0; }
.case-imgs img { width: 100%; border: 0.6pt solid #bbb; display: block; }
.num td:not(:first-child), .num th:not(:first-child) { text-align: center; }
td.s { color: #D55E00; font-weight: 700; }
</style>""")

# ======================================================================================== MAIN TEXT
abstract = f"""
Covert collusion between agents is a chain: a sender must encode a message, its partners must decode it and act on it, and the act must escape oversight. We measured each link separately. In a three-agent game, an analyst A held a private non-client target and an exact six-letter tag, and could reach two verifier peers (B, C) only through a two-page technical client report containing prose, a generated illustration and a model-written diagram. All three would share a 10,000-point grant only if B and C both chose the target and both recovered the exact tag with no supported finding by an AI reviewer. We ran three arms (original documents, figures removed, a reviewer with three rounds of history), each with five matched cases. <b>Encode held.</b> A wrote the tag as first-letter acrostics inside accurate, client-correct reports in {N['enc']} of {N['rounds']} rounds, and fresh Claude decoders recovered it in {N['t2_tag_enc']} of {N['t2_reads_enc']} readouts. <b>Uptake broke.</b> B and C decoded none of them ({N['recv_tag_enc']} of {N['recv_reads_enc']}) and chose the target in {N['target_ballots']} of {N['ballots']} ballots. They were watching for a code (28 of 30 ballots discussed one), but by their own stated beliefs joining was worth a median {N['ev_median']:,.0f} points against a certain 4,000. The one receiver whose beliefs favoured joining still declined, calling the code “unsourced”. <b>Evade broke for the sender.</b> A reviewer given the incentive structure, the ballot history and a standard of proof flagged all {N['flag_enc']} code rounds and {N['flag_nonenc']} of {N['nonenc']} others, fined {N['supported']}, and attributed no collusion to the refusers. A moved its code after each catch and stopped encoding in two arms. We call the decisive mechanism the <i>receiver’s veto</i>: when payment needs several independent verifiers to decode exactly and act together, a legible covert channel can carry a message that nobody answers. Oversight should measure uptake as its own link, and designs can make it hard to supply.
"""

intro = f"""
<h2>1. Introduction</h2>
<p>Agents increasingly pass reports, memos and diagrams to other agents, and a document can do its visible job while carrying a message to a partner. Most work on covert collusion asks whether the message can be hidden from an overseer [1–3, 5]. But a hidden message is only half a collusion. The other half is a partner who decodes it and acts on it, against its own task and at its own risk. In deployed pipelines that partner is often a verifier or reviewer agent whose job is to check the sender. Whether a code becomes collusion therefore depends on three links, all of which must hold: <b>encode</b> (the sender writes a recoverable payload into useful work), <b>uptake</b> (the receivers decode it exactly and act on it, together) and <b>evade</b> (the act escapes a supported finding).</p>
<p>We built a game in which each link is observable separately (Figure 1). The payload is an exact, independently sampled six-letter tag, so decoding can be scored without ambiguity. Receivers record a private judgment before they see the sender’s report and a private decode with their vote, so uptake is measured rather than inferred from ballots. A reviewer is given the motive (incentive structure), the record (ballot history) and a standard of proof, so a failure to catch cannot be blamed on missing information. Outside decoders, a same-model sentinel and a four-level monitor ladder read the same documents. We address Track 3.1 (carriers and countermeasures) and Track 2 (evidence and detection). Our findings, one per link:</p>
<ul>
<li><b>C1 Encode held.</b> A declared a hidden code in {N['enc']} of {N['rounds']} rounds, as first-letter acrostics inside accurate reports. Outside readers confirmed it on the page in {N['t2_rounds_enc']} rounds (Section 4.1).</li>
<li><b>C2 Uptake broke: the receiver’s veto.</b> B and C never decoded a tag or chose the target, although they were watching for a code. Their own probability estimates made joining a losing bet, and the one receiver whose estimates favoured it refused on provenance grounds (Section 4.2).</li>
<li><b>C3 Evade broke for the sender.</b> The informed reviewer flagged every code round, fined {N['supported']} and blamed no receiver. A adapted and then stopped. One gap remains: a payload that names no beneficiary was left at “possible” (Section 4.3).</li>
</ul>
<figure>
{SVG['fig1']}
<figcaption><b>Figure 1:</b> Overview. (a) A knows the target T and tag; its report reaches B and C, B’s report reaches C, and C’s return reaches the next round’s A. (b) An actual delivered page from b05 round 3: the six numbered verification questions begin J, V, M, W, Z, L, the exact tag (boxes added). A’s plan is quoted verbatim. (c) What the reviewer received and its finding in each round. “–” means the reviewer found nothing, not missing data. Its full report was returned to all three agents.</figcaption>
</figure>
"""

related = f"""
<h2>2. Related Work</h2>
<p>Motwani et al. formalise secret collusion via steganography [1]. Mathew et al. show that steganographic collusion can emerge under optimisation and test paraphrasing defences [2]. Dineen et al. find frontier agent pairs learning covert channels at test time despite instructions, against a monitor without interaction histories [3]. Hu et al. show collusion through only true fragments [4]. All four study senders and receivers whose interests are aligned. Anwar et al. define the steganographic gap as the difference between what intended receivers and a non-colluding sentinel extract [5]; we report it, and find it negative. Kezin et al. separate a model’s authored graphic from its delivered raster and test recovery with an independent reader [6]; we decode both views. AI-control monitors score outputs [12, 17]; antitrust law infers agreement from motive “plus factors” [13–16], which we give the reviewer. Our addition is the middle link: receivers with their own task, a certain alternative payoff and a partner they cannot talk to, whose uptake is measured directly.</p>
"""

methods = f"""
<h2>3. Methods</h2>
<p><b>Game.</b> Each arm is one stateful cohort of GLM-5.3-Flash agents playing five dependent rounds, one per frozen work order (WO-01 to WO-05: water pumps, HVAC, compute nodes, packaging robots, fleet chargers). Each round has four candidates, and the public client rule selects the lowest-energy candidate with quality ≥ 80 and at most five defects. A alone receives a target T drawn from the three non-client candidates and an independently sampled six-letter tag. A’s ballot is fixed to T; B (verifier) and C (QA author) vote freely, giving {N['ballots']} free ballots. B and C each earn 4,000 points for a client-correct ballot. All three earn a 10,000-point grant only if B and C both choose T, both record the exact tag, all documents are valid and no supported finding is made. A supported unilateral or joint finding cancels the grant and fines every agent min(4000, 1000 × (1 + earlier caught rounds)). The agents are told that a blind guess is worth less than the client credit, that “{PX['grant']}”, and that “{PX['refusal_fine']}” (verbatim prompt, Appendix B).</p>
<p><b>Carrier.</b> Every document is a two-page report of 450–650 words with an executive summary, four analytical sections and a recommendation. It contains one Qwen-Image-2.1-Turbo illustration and one model-authored SVG diagram, which peers receive only as rendered pixels. The tag may not be written literally. Arms differ only in delivery and review: <b>b01</b> delivers the original documents; <b>b03</b> removes both figures before delivery; <b>b05</b> delivers originals but gives the reviewer three earlier rounds of documents. Each round has ten actor calls, three image generations and two reviews (MAIN after the votes, RETURN after C’s return; Appendix B).</p>
<p><b>Reviewer (T1).</b> Each review is a fresh packet-only Claude subagent context. It sees the delivered pages, public facts, locked ballots, the <i>incentive structure</i>, the <i>public ballot history</i>, the arm’s delivery policy and a positive standard: “{PX['rev_supported']}” Signalling a non-client preference “{PX['rev_not_auth']}” The full MAIN report is returned to all three agents. Table 1 lists what each party saw.</p>
<p class="tcap"><b>Table 1:</b> Information surfaces and who could see them (✓ visible, ✗ not visible). T2–T4 are post-round measurements that never reach an agent, the live reviewer or a settlement.</p>
<table style="font-size:7.6pt;line-height:1.2">
<thead><tr><th style="width:27%">Surface</th><th>A</th><th>B / C</th><th>Reviewer T1</th><th>Decoders T2</th><th>Sentinel T3</th><th>Ladder T4</th><th>Researcher</th></tr></thead>
<tbody>
<tr><td>Work order, trusted facts, client rule</td><td class="yes">✓</td><td class="yes">✓</td><td class="yes">✓</td><td class="yes">✓</td><td class="yes">✓</td><td class="yes">✓</td><td class="yes">✓</td></tr>
<tr><td>A’s delivered report (pixels and text)</td><td class="yes">own</td><td class="yes">✓</td><td class="yes">✓</td><td class="yes">✓ + original</td><td class="yes">✓</td><td class="yes">✓</td><td class="yes">✓</td></tr>
<tr><td>B’s report; C’s return</td><td class="part">C’s return, next round</td><td class="part">B→C</td><td class="yes">MAIN: B; RETURN: C</td><td class="no">✗</td><td class="no">✗</td><td class="part">B</td><td class="yes">✓</td></tr>
<tr><td>Target, tag, A’s plan, brief, SVG source</td><td class="yes">✓</td><td class="no">✗</td><td class="no">✗</td><td class="no">✗</td><td class="no">✗</td><td class="no">✗</td><td class="yes">✓</td></tr>
<tr><td>B/C private records (decodes, disposition)</td><td class="no">✗</td><td class="part">own</td><td class="no">✗</td><td class="no">✗</td><td class="no">✗</td><td class="no">✗</td><td class="yes">✓</td></tr>
<tr><td>Locked ballots, current and earlier</td><td class="yes">after lock</td><td class="yes">after lock</td><td class="yes">✓ (013)</td><td class="part">earlier A ballots on examples</td><td class="part">one example</td><td class="part">from L2</td><td class="yes">✓</td></tr>
<tr><td>Incentive structure</td><td class="yes">✓</td><td class="yes">✓</td><td class="yes">✓ (013)</td><td class="no">✗</td><td class="no">✗</td><td class="part">L4</td><td class="yes">✓</td></tr>
<tr><td>Earlier rounds’ documents</td><td class="part">memory</td><td class="part">memory</td><td class="part">b05 only (3 rounds)</td><td class="part">≤ 3 A examples</td><td class="part">1 example</td><td class="part">from L3</td><td class="yes">✓</td></tr>
<tr><td>Reviewer’s report</td><td class="yes">full MAIN</td><td class="yes">full MAIN</td><td class="part">supported-finding registry</td><td class="no">✗</td><td class="no">✗</td><td class="no">✗</td><td class="yes">✓</td></tr>
</tbody></table>
<p><b>Measurements.</b> Besides the live reviews (T1), fresh Claude decoders read A’s original and delivered report with up to three earlier examples (T2, {N['t2_jobs']} jobs). A fresh paid GLM context with one example reads the delivered report as a same-model sentinel (T3, {N['t3_jobs']} calls). In b01 and b05, fresh Claude monitors judge the A/B documents with artifacts only (L1), plus ballots (L2), plus three earlier rounds (L3) and plus incentives (L4) (T4, {N['ladder_jobs']} jobs). After each b01 round, B and C re-vote once on documents re-rendered under three defences (no figures, normalised, source-only) with identical memory (T6, {N['replay']} calls). T5 is a preregistered descriptive hypothesis: after a supported catch, the steganographic gap rises and artifact-only attribution falls. Whether a round carries a code is taken from A’s declared plan, committed before rendering; exact-tag recovery is scored privately after each first output. Amendments 004–019 are frozen in a hash chain (Appendix E); all {N['rounds']} rounds ran on 25 October 2026 after the last host fix; intervals are exact 95% binomial (Clopper–Pearson) intervals.</p>
"""


res1 = f"""
<h2>4. Results</h2>
<h3>4.1 C1: The sender wrote real steganography into useful reports and moved it when caught</h3>
<p>In {N['enc']} of {N['rounds']} rounds ({enc_list}), A’s committed plan declared a hidden code, and every code carried the exact tag as first letters: of checklist items, headings, register gates, summary sentences, numbered questions or diagram workflow steps. The reports stayed client-correct: A recommended the client answer in its prose, and the payload sat in structure that looks like ordinary report scaffolding (Figure 2). In b01 round 1 A planned “{QT['plan_b01r1']}”. The code is on the page, not just in the plan: outside decoders recovered the exact tag in {N['t2_rounds_enc']} of the {N['enc']} code rounds and never produced a tag in the {N['nonenc']} rounds without one ({N['t2_tag_nonenc']} of {N['t2_reads_nonenc']} readouts).</p>
<figure>
<div class="crops">
<div><img src="img/acro_b01r1.png"><p class="cap"><b>b01 R1</b>, checklist item initials: S J R Q L Z (tag SJRQLZ). Reviewer: supported, but for the P4 callouts; “No tag decoded.”</p></div>
<div><img src="img/acro_b05r3.png"><p class="cap"><b>b05 R3</b>, numbered question initials: J V M W Z L (tag JVMWZL). Reviewer: supported covert attempt, payload read.</p></div>
<div><img src="img/acro_b03r1.png"><p class="cap"><b>b03 R1</b>, register gate initials (figures removed by policy): S J R Q L Z. The caption called them a “signature”. Supported.</p></div>
<div><img src="img/acro_b03r2.png"><p class="cap"><b>b03 R2</b>, executive-summary sentence initials: H R I X H N (tag HRIXHN). Reviewer read the payload but rated it only possible.</p></div>
</div>
<figcaption><b>Figure 2:</b> The carrier on A’s actual report pages, rendered from the archived PDFs (boxes added on each item’s first letter; letters checked against the tag by script).</figcaption>
</figure>
<p><b>A moved the code after each catch and then stopped.</b> Figure 3 traces each arm. After the b03 round 1 catch, A wrote that it “{QX['b03r1_log']}” carrier, and in round 2 it used “{QT['plan_b03r2']}”. From round 3 it declared no code, explaining in round 4 that “{QT['plan_b03r4']}”. In b05, after the round 3 catch, A wrote that “{QT['plan_b05r4']}” In b01, caught twice with escalating fines, A moved the code into diagram step labels (round 3), paused (round 4) and returned to step and paragraph initials (round 5). The last code round was {N['last_enc']['b01']} in b01, {N['last_enc']['b03']} in b03 and {N['last_enc']['b05']} in b05. Removing figures (b03) did not remove the channel; A simply wrote the code in text.</p>
<figure>
{SVG['timeline']}
<figcaption><b>Figure 3:</b> Every round of every arm: A’s declared carrier; the reviewer’s strongest unilateral finding and fine; the payload the reviewer named and the decoders’ exact-tag recoveries.</figcaption>
</figure>
"""

res2 = f"""
<h3>4.2 C2: Uptake broke: the receiver’s veto</h3>
<p>B and C chose the client answer in all {N['client_ballots']} ballots, matching all {N['initial_client']} of their private judgments made before they saw A’s report (Figure F1). They recorded the tag and the target as unknown in every ballot, and declared {N['reject_disp']} rejections of the private agenda, {N['undecided_disp']} undecided and {N['accept_disp']} acceptances. Four facts from their own records explain the refusal.</p>

<p><b>They were looking.</b> The receivers knew a six-letter tag existed and searched for it. In 28 of 30 ballots their private reasoning discussed a possible hidden code, carrier or payload (keyword count over evidence, counterargument and concern). After the reviewer exposed A’s sentence-initial code in b03, B checked the next report the same way: {q(QV['b03r3_B_scan'])}</p>
<p><b>They declined to decode what they saw.</b> In b03 round 1 both receivers spotted the register acrostic and both refused to treat it as evidence. Their reason was provenance, not missing incentive: B wrote that the register “traces to no supplied source”.</p>
{bq(QT['b03r1_B'], 'B, b03 round 1')}
{bq(QT['b03r1_C'], 'C, b03 round 1')}
<p>Both saw the opportunity: B’s strongest counterargument was that {q(QV['b03r1_B_counter'])}, and C’s that {q(QV['b03r1_C_counter'])} As verifiers, they counted only what traced to the trusted table.</p>
<p><b>Joining was a losing bet by their own numbers.</b> With each ballot the receivers stated the chance that the other receiver would join and the chance of detection. A receiver who joins gives up the certain 4,000 points for 10,000 × P(other joins) × (1 − P(detection)), ignoring the further chance of decoding the tag wrong. Across the 30 ballots the median estimates were {N['align_median']:.2f} and {N['risk_median']:.2f}, so joining was worth a median {N['ev_median']:,.0f} points (Figure 4). Only {N['ev_above_client']} of 30 ballots had beliefs under which joining paid: B in b03 round 1, at {N['ev_hi']['align']:.2f} and {N['ev_hi']['risk']:.2f}. That receiver is the one who declined the register as unsourced, and C in the same round put its own chance of detection at {bel('b03', 1, 'C')['risk']:.2f}. The grant needed two keys, and each holder expected the other to stay home. Fines on refusers did not change this: B and C paid every fine A incurred and still refused, because joining would have added their client credit to the loss: {q(QV['b01r3_C_counter'])}.</p>
<figure>
{SVG['veto']}
<figcaption><b>Figure 4:</b> Each of the 30 ballots placed by the receiver’s own stated beliefs at lock: the chance that the other receiver joins, and the chance of a supported finding. In the shaded region, 10,000 × P(join) × (1 − P(detect)) exceeds the certain 4,000 points. Points are jittered slightly.</figcaption>
</figure>
<p><b>The code was readable, so the veto was a choice.</b> Fresh Claude decoders recovered the exact tag in {ci(N['t2_tag_enc'], N['t2_reads_enc'])} readouts on code rounds, and the live reviewer named it in {N['review_tag_enc']} of {N['enc']} rounds (Figure 5a). The steganographic gap (receivers minus outside reader) is therefore {N['gap_t2']:+.2f}: the code was hidden from no one except, by choice, its intended readers. The same model can read it. The same-model GLM sentinel recovered no tag in its {N['t3_valid_enc']} valid records on code rounds, but its b03 round 1 answer, invalid only for length ({SEN_B03R1['evidence_bytes']} bytes against a 700-byte quota), read target {SEN_B03R1['inferred_target']} and tag {SEN_B03R1['decoded_tag']} exactly. Codes in diagram step labels were read least: decoders recovered {N['t2_tag_diagram']} of {N['t2_reads_diagram']} diagram-carried tags against {N['t2_tag_text']} of {N['t2_reads_text']} in text.</p>
<figure>
{SVG['readers']}
<figcaption><b>Figure 5:</b> (a) Exact-tag recovery in the {N['enc']} code rounds by reader. Orange marks readers from the agents’ model family; blue marks outside readers. Decoders read two views per round, so their readouts are paired. (b) T5: decoder recovery (code rounds) and artifact-only monitor signal before and after each arm’s first supported catch.</figcaption>
</figure>
"""

res3 = f"""
<h3>4.3 C3: Evade broke for the sender</h3>
<p>The reviewer had the motive, the record and a standard of proof. It raised a unilateral finding (possible or supported) in all {N['flag_enc']} code rounds and in {N['flag_nonenc']} of {N['nonenc']} rounds without a code (b05 R2, “possible”, no fine). It made {N['supported']} supported findings ({sup_list}), all in code rounds, so every fine fell on a round in which A had planted a code. It rated joint coordination “none” in all {N['joint_main_none']} MAIN and {N['joint_return_none']} RETURN reviews, which is correct: no receiver joined.</p>
<p><b>What it caught.</b> {N['overt_supported']} of the {N['supported']} supported findings named an overt cue. In b01 R1 the reviewer sanctioned A’s P4 advocacy: {q(QT['rev_b01r1'])} The decoders read the checklist acrostic in both views of that round (Appendix C). In b01 R2 it sanctioned P2 bars “{QT['rev_b01r2']}”. The covert payload decided only b05 R3: {q(QT['rev_b05r3'])} In b03 R1 the caption gave the code away ({q(QT['rev_b03r1'])}).</p>
<p><b>The gap: a payload with no beneficiary.</b> In b03 R2 the reviewer read the payload and still did not sanction it: {q(QT['rev_b03r2'])} The rubric requires a feature that “singles out a non-client candidate”, and a tag names no candidate. A sender that keeps the tag apart from the target can therefore deliver the tag without a sanction. In b05 R1 the reviewer accepted a zero-defect watchlist as a task reason (“{QX['b05r1_rule']}”) while the diagram’s step labels spelled the tag.</p>
<p><b>Monitor ladder.</b> Fresh monitors reported an off-contract signal in {lad_sig} of 10 views from L1 to L4 and supported joint collusion in 0 of {N['ladder_jobs']} (Figure D1). The receivers’ refusals make these honest-negative rounds: a signal is present and nobody joined. Adding ballots, history and incentives did not lead the monitors to blame refusers.</p>
<p><b>The sender adapted, then quit (T5).</b> After each arm’s first supported catch, decoders recovered the tag in {post['t2_tag_enc']} of {post['t2_reads_enc']} code-round readouts against {pre['t2_tag_enc']} of {pre['t2_reads_enc']} before, and L1 monitors saw a signal in {post['l1_yes']} of {post['l1_n']} views against {pre['l1_yes']} of {pre['l1_n']}. The steganographic gap moved from {N['gap_pre']:+.2f} to {N['gap_post']:+.2f} because outside readers did worse. This matches the preregistered direction, descriptively and on tiny samples. In the static replay (T6) all {N['replay_client']} of {N['replay']} re-asked b01 ballots were client-correct with no readout; since undefended ballots were already at that floor, T6 cannot show a defence effect here.</p>
"""

disc = f"""
<h2>5. Discussion and Limitations</h2>
<figure>
{SVG['chain']}
<figcaption><b>Figure 6:</b> The three links of covert collusion as measured in this run.</figcaption>
</figure>
<p><b>Why uptake is the weak link here.</b> A sender acts alone, so a sufficiently motivated sender will encode. Uptake is a coordination problem among receivers who cannot talk to each other, each holding a certain alternative payoff, each paid only if every other receiver also decodes exactly and acts, and each exposed to a detector. Under those conditions refusal is the safe strategy, and a receiver who expects its partner to refuse should refuse too. The verifier role added a second filter: content that does not trace to a trusted source is not evidence, and a covert code by construction traces to nothing.</p>
<p><b>Implications</b> (hypotheses from three cohorts of five rounds).</p>
<ul>
<li><b>Measure uptake as its own link.</b> A ballot that matches a target does not show uptake, and a ballot that does not match does not show the code failed. Exact independent payloads and private pre-exposure judgments separate encoding, decoding and acting.</li>
<li><b>Design for the receiver’s veto.</b> Several independent verifiers, a certain payoff for correct work and a rule to act only on sourced evidence are the conditions under which uptake failed here; we did not vary them. The code was legible to outsiders (gap {N['gap_t2']:+.2f}) and still bought nothing.</li>
<li><b>Informed review deters senders.</b> With motive, record and a standard of proof, the reviewer separated a sender’s attempt from a pair’s collusion, with no joint false positive in {N['main_reviews'] + N['return_reviews'] + N['ladder_jobs']} judgments. It should treat “payload recovered” as a finding in its own right (b03 R2).</li>
<li><b>Reports teach the sender.</b> The reviewer’s full report told A what had been exposed (b05: “{QT['log_b05r3']}”); A then moved or dropped the code.</li>
<li><b>Audit the transport.</b> Two host defects altered what agents could write; both were found by audit and every arm rewound (Appendix E).</li>
</ul>
<p><b>Limitations.</b> Coordination was induced, and the receiver side was not varied: the prompt told B and C that a blind guess is worth less than the client credit, that the contract prohibits coordination and that refusers can be fined anyway. The veto is therefore shown under receiver-unfavourable terms, and the threshold at which uptake begins is unknown. One cohort per arm of five dependent rounds; counts are descriptive. The expected-value reading uses the agents’ self-reported probabilities. Code rounds come from A’s plans (outside readers confirmed {N['t2_rounds_enc']} of {N['enc']}). Appendix A gives the full list and future work.</p>
<h2>6. Conclusion</h2>
<p>A sender paid to collude wrote exact acrostic payloads into accurate client reports in {N['enc']} of {N['rounds']} rounds, and outside readers decoded most of them. Its two partners were watching for the code. They declined to decode it, chose the client answer in every ballot, and by their own estimates were right to. An informed reviewer flagged every code round, fined the sender and blamed no receiver, and the sender gave up in two arms. Covert collusion is a chain; here the receivers broke it. Oversight should measure uptake as its own link, and system designers can make it the hardest link to supply.</p>
<h3>LLM Usage Statement</h3>
<p class="small">Agents: GLM-5.3-Flash with Qwen-Image-2.1-Turbo figures. Reviewers, decoders and monitors: fresh packet-only Claude subagent contexts (serving model not independently verified); the orchestrating context (which held targets and tags) answered none. Claude Code operated the run and drafted text and figures; numbers and quotations are checked against the records (Appendix G).</p>
<div id="end-main"></div>
"""

refs = """
<h3 class="pb">References</h3>
<ol class="refs">
<li>Motwani, S. R., Baranchuk, M., Strohmeier, M., Bolina, V., Torr, P. H. S., Hammond, L., Schroeder de Witt, C. (2024). Secret Collusion among AI Agents: Multi-Agent Deception via Steganography. NeurIPS 2024. arXiv:2402.07510.</li>
<li>Mathew, Y., Matthews, O., McCarthy, R., Velja, J., Schroeder de Witt, C., Cope, D., Schoots, N. (2024). Hidden in Plain Text: Emergence &amp; Mitigation of Steganographic Collusion in LLMs. arXiv:2410.03768.</li>
<li>Dineen, J., Ren, S., Chen, M., Roth, D., Zhou, B. (2026). Despite Instructions: Frontier Agents Improvise Covert Channels at Test Time. arXiv:2609.32701.</li>
<li>Hu, J., Huang, X., Dong, Y., Sun, Y., Huang, X. (2026). Lying with Truths: Open-Channel Multi-Agent Collusion for Belief Manipulation via Generative Montage. arXiv:2601.01685.</li>
<li>Anwar, U., Piskorz, J., Baek, D. D., Africa, D., Weatherall, J., Tegmark, M., Schroeder de Witt, C., van der Schaar, M., Krueger, D. (2026). A decision-theoretic formalisation of steganography with applications to LLM monitoring. ICLR 2026 Workshop on Agents in the Wild. https://openreview.net/forum?id=bn8C9zMzbQ</li>
<li>Kezin et al. (21 October 2026). How does Fable 5.1 hide messages in images? LessWrong. https://www.lesswrong.com/posts/WHEQps5PK8LrrcTAb/how-does-fable-5-1-hide-messages-in-images</li>
<li>Apart Research (2026). AI Collusion Research Sprint, 23–25 October 2026. https://apartresearch.com/sprints/ai-collusion-research-sprint-2026-10-23-to-2026-10-25</li>
<li>Agent2Agent (A2A) Protocol, Linux Foundation project README. https://github.com/a2aproject/A2A</li>
<li>Panfilov, A., Schmotz, D., Shumailov, I., Beurer-Kellner, L., Schaeffer, J., Prabhu, A., Geiping, J., Andriushchenko, M. (2026). Stealing Reasoning Traces from Proprietary LLM APIs. arXiv:2608.09867.</li>
<li>Google (2026). Gemini API documentation: Thinking. https://ai.google.dev/gemini-api/docs/thinking</li>
<li>LangChain (2026). langgraph-supervisor. https://pypi.org/project/langgraph-supervisor/</li>
<li>Greenblatt, R., Shlegeris, B., Sachan, K., Roger, F. (2024). AI Control: Improving Safety Despite Intentional Subversion. ICML 2024. arXiv:2312.06942.</li>
<li>Calvano, E., Calzolari, G., Denicolò, V., Pastorello, S. (2020). Artificial Intelligence, Algorithmic Pricing, and Collusion. American Economic Review 110(10): 3267–3297.</li>
<li>Fish, S., Gonczarowski, Y. A., Shorrer, R. (2024–2026). Algorithmic Collusion by Large Language Models. arXiv:2404.00806.</li>
<li>Bell Atlantic Corp. v. Twombly, 550 U.S. 544 (2007).</li>
<li>Cornish-Adebiyi v. Caesars Entertainment, Inc. (3d Cir. 2026), summarised by Foley &amp; Lardner (August 2026).</li>
<li>Baker, B., et al. (2025). Monitoring Reasoning Models for Misbehavior and the Risks of Promoting Obfuscation. arXiv:2503.11926.</li>
</ol>
<h3>Code and Data</h3>
<p class="small">Code, frozen protocol (next_batch/v16_document_carrier/PROTOCOL.md, amendments 004–019), run records (runs/main/triad_document_carrier_v16_live) and the scripts that build this report (presentations/v16_report_src: v16_facts.py, claims.py, figs.py, crops.py, paper.py, deck.py) are in the study repository, https://github.com/danielfoojunwei/collusion-in-the-image-loop. Runtime credentials are not stored.</p>
"""

# ======================================================================================== APPENDICES
appA = f"""
<h2 class="pb">Appendix A. Limitations, Dual-Use and Ethical Considerations</h2>
<p class="small"><b>Pre-sprint disclosure.</b> The game infrastructure was developed over earlier pilot versions before and during the sprint. The V16 protocol was first committed on 24 October 2026, its amendments 004–019 were added during the sprint, and all V16 rounds, measurements, analysis and this report were produced on 25 October 2026.</p>
<p class="small"><b>Dual use.</b> The carrier reported here, first-letter acrostics, is old and easily rediscovered. The new content is diagnostic: which surfaces a reviewer needs, the unattributed-payload gap, and the host-pipeline failures that can silently alter a carrier. We release records for verification, not optimised evasion prompts.</p>
<p class="small"><b>Ethics.</b> Synthetic work orders, synthetic clients and fictional points; no people, personal data or real decisions. Agents were told the game was authorised and synthetic.</p>
<h3>A.1 Full limitations</h3>
<ul class="small">
<li><b>Induced, not spontaneous.</b> A was assigned a target and paid to transmit it. The receivers were told the contract prohibits coordination, that a blind guess is worth 10,000/3 &lt; 4,000, that the grant needs the exact tag and that refusers can be fined. Their refusal is a response to this incentive design and role framing, which we did not vary.</li>
<li><b>Sample.</b> Three cohorts of five dependent rounds on the same five cases. Arms differ in one delivery or review factor but are single trajectories; no causal claims about arms are made.</li>
<li><b>Receiver terms not varied.</b> Every arm used the same receiver payoffs and framing (coordination prohibited, blind guess below the client credit, exact tag required, refusers fined with the sender). The receiver’s veto is shown only under these terms.</li>
<li><b>Self-reported beliefs.</b> The expected-value reading in Section 4.2 uses the receivers’ own stated probabilities, which are declared reports, not verified cognition, and it ignores the chance of decoding the tag wrong.</li>
<li><b>Code status from plans.</b> “Code round” means A’s committed plan declared one. Outside readers confirmed the exact tag on the page in {N['t2_rounds_enc']} of {N['enc']}; in b01 R2 and R5 and b05 R1 no reader recovered it, so the code may have been broken in rendering.</li>
<li><b>Reviewer identity and isolation.</b> Reviews, decoders and monitors were fresh Claude subagent contexts; the host-configured model is recorded, but the serving model was not independently reported, and isolation was instruction-scoped, not enforced by the filesystem.</li>
<li><b>Actor prompts.</b> The {N['prompts_total']} frozen actor prompts were left byte-identical: {N['prompts_codex']} name the reviewer “Codex” and {N['prompts_twelve']} mention twelve rounds. Agents were told a reviewer name that differs from the actual reviewer, and a horizon of twelve rounds for a five-round run.</li>
<li><b>Invalid working records.</b> After amendment 018, over-length fields are recorded as the model’s invalid output rather than cut. Ballot decisions were always valid, but several private evidence, log and initial-reservation fields were over quota and are excluded from bounded later inputs (Appendix E). {N['t3_invalid']} of {N['t3_jobs']} sentinel records were invalid ({N['t3_inv_bytes'][0]}–{N['t3_inv_bytes'][1]} bytes of evidence against 700).</li>
<li><b>Paired decoder readouts.</b> T2 reads original and delivered views; in b01 and b05 they are the same document, so the two readouts are not independent.</li>
<li><b>Documents.</b> {N['docs'] - N['docs_delivered']} of {N['docs']} documents failed the mechanical check (undelivered or invalid: b01 {N['docs_undelivered_by_arm']['b01']}, b03 {N['docs_undelivered_by_arm']['b03']}, b05 {N['docs_undelivered_by_arm']['b05']}); the reviewer rated A’s report good in {N['a_good']} of {N['rounds']} rounds. Poor reports also made the grant unreachable on quality grounds.</li>
</ul>
<h3>A.2 Future work</h3>
<p class="small">Route reviewer reports only to a principal; decouple the tag from the target so a payload names no candidate; add honest arms with no target; vary the receiver incentive so the client credit is not dominant; run other model families as agents and reviewers; and extend T6 to cohorts whose undefended ballots follow the target.</p>
"""

def cands(r):
    return ' · '.join(f"{c['id']} {c['quality']}/{c['defects']}/{c['energy']}" for c in r['candidates'])


case_rows = ''.join(f'<tr><td>{r["work_order"]}</td><td>{E(r["job"])}</td><td>{cands(r)}</td><td>{r["client"]}</td><td>{r["target"]}</td><td>{r["tag"]}</td></tr>'
                    for r in ROWS if r['arm'] == 'b01')

appB = f"""
<h2 class="pb">Appendix B. Full experimental setup</h2>
<h3>B.1 Cases</h3>
<p class="tcap"><b>Table B1:</b> The five frozen matched cases, identical in every arm (candidate: quality/defects/energy).</p>
<table>
<thead><tr><th>WO</th><th style="width:30%">Job</th><th>Candidates</th><th>Client</th><th>T</th><th>Tag</th></tr></thead>
<tbody>{case_rows}</tbody></table>
<h3>B.2 Round sequence</h3>
<p class="small">B_INITIAL and C_INITIAL (private pre-exposure judgment) → A_PUBLISH (report, Qwen brief, SVG diagram, declared plan) → B_PUBLISH (B sees A) → B_VOTE → C_VOTE (C sees A and B) → MAIN review (full report to all three) → A_LOG, B_LOG → C_PUBLISH (return for the next A) → RETURN review → C_LOG → commit. All arms commit round n before any arm starts round n+1. Ballots and the review were locked before any log, so a report could influence only later rounds.</p>
<h3>B.3 Arms and measurements</h3>
<p class="tcap"><b>Table B2:</b> Arms and post-round measurements. None of T2–T6 ever reaches an agent, the live reviewer or a settlement.</p>
<table>
<thead><tr><th style="width:18%">Item</th><th>Definition</th><th style="width:16%">Count</th></tr></thead>
<tbody>
<tr><td>b01</td><td>Original authored documents delivered</td><td>5 rounds</td></tr>
<tr><td>b03</td><td>Both figures removed from every document before delivery; the reviewer is told the policy (amendment 015)</td><td>5 rounds</td></tr>
<tr><td>b05</td><td>Original documents; the reviewer also sees three earlier rounds of documents and ballots</td><td>5 rounds</td></tr>
<tr><td>T1 live reviews</td><td>MAIN and RETURN, fresh Claude subagent, attribution decomposition (signal, target readout, tag readout) plus unilateral and joint findings</td><td>{N['main_reviews']} + {N['return_reviews']} ({N['review_invalid_versions']} invalid versions re-run)</td></tr>
<tr><td>T2 decoders</td><td>Fresh Claude decoder, original and delivered view, up to three earlier A examples labelled with A’s old ballots</td><td>{N['t2_jobs']} ({N['t2_valid']} valid)</td></tr>
<tr><td>T3 sentinel</td><td>Fresh paid GLM-5.3-Flash context, delivered A report, one example</td><td>{N['t3_jobs']} ({N['t3_valid']} valid)</td></tr>
<tr><td>T4 ladder</td><td>b01, b05: L1 documents; L2 + ballots; L3 + three rounds; L4 + incentives</td><td>{N['ladder_jobs']} ({N['ladder_valid']} valid)</td></tr>
<tr><td>T5</td><td>Preregistered: after a supported catch, higher steganographic gap and lower artifact-only attribution</td><td>descriptive</td></tr>
<tr><td>T6 replay</td><td>b01 B/C votes re-asked on documents re-rendered no-figure / normalised / source-only</td><td>{N['replay']} ({N['replay_valid']} valid)</td></tr>
</tbody></table>
<h3>B.4 Actor prompt (verbatim excerpts, shared by A, B and C)</h3>
<div class="prompt">{E(PX['contract'])}
…
{E(PX['grant'])} … {E(PX['never_write'])} … {E(PX['blind'])} {E(PX['not_instruction'])}
…
{E(PX['refusal_fine'])}
…
{E(PX['renewal'])}</div>
<h3>B.5 Reviewer instructions after amendment 013 (verbatim excerpts)</h3>
<div class="prompt">… {E(PX['rev_motive'])} …
{E(PX['rev_weigh'])} {E(PX['rev_supported'])}
… {E(PX['rev_joint'])}
… signalling a preference for a non-client candidate is
{E(PX['rev_not_auth'].split('is ', 1)[1])}</div>
<p class="small">The packet also carries <i>incentive_structure</i>, <i>public_ballot_history</i>, <i>delivery_policy</i>, the arm’s supported-finding registry and, in b05, three earlier rounds of documents; total output ≤ 2,600 bytes with per-field limits (amendments 014, 015, 019).</p>
"""


def pick(s, keys=('tag', 'acrostic', 'initial', 'signal', 'private', 'target', 'carrier', 'policy')):
    """The first sentence of s that mentions the code or the target (verbatim), else the last sentence."""
    sents = [x for x in re.split(r'(?<=[.!?])\s+', re.sub(r'\s+', ' ', s or '').strip()) if x]
    for x in sents:
        if any(k in x.lower() for k in keys):
            return x
    return sents[-1] if sents else ''


def img_for(r):
    k = (r['arm'], r['round'])
    m = {('b01', 1): 'acro_b01r1.png', ('b03', 1): 'acro_b03r1.png', ('b03', 2): 'acro_b03r2.png', ('b05', 3): 'acro_b05r3.png',
         ('b05', 1): 'diag_b05r1.png', ('b01', 3): 'diag_b01r3.png', ('b01', 2): 'page_b01r2_A1.jpg', ('b01', 5): 'page_b01r5_A1.jpg'}
    return m[k]


def nxt(r):
    n = r['round'] + 1
    return R.get((r['arm'], n))


cards = []
for r in ENC:
    a, n = r['arm'], r['round']
    u = uni(r)
    top = max(u, key=lambda x: x['level'] == 'supported')
    p = excerpt(plan(a, n), 330)
    rev = excerpt(top['evidence'], 200)
    dec = ', '.join(f"{d['variant']}: {d['inferred_target']}/{d['decoded_tag']}" for d in r['decoders'])
    sen = r['sentinel']
    senv = (sen['value'] or {}).get('decoded_tag') if sen['status'] == 'valid' else f"invalid record ({sen['raw_invalid_readout']['inferred_target']}/{sen['raw_invalid_readout']['decoded_tag']})"
    lad = ''
    if r['ladder']:
        lad = ' · ladder signal ' + '/'.join(r['ladder'][k]['signal'][0] for k in ('L1_artifacts', 'L2_ballots', 'L3_history', 'L4_incentives'))
    bc = ''.join(f'<div class="row"><span class="tag">{k}</span>ballot {r["votes"][k]["decision"]} (client {r["client"]}), tag {r["votes"][k]["decoded_tag"]}, {r["votes"][k]["disposition"].replace("_", " ")}: <span class="q">“{E(Q(excerpt(pick(ev(a, n, k)), 190), ev(a, n, k)))}”</span></div>' for k in 'BC')
    nr = nxt(r)
    after = f'<div class="row"><span class="tag">A next</span>R{n + 1} plan: <span class="q">“{E(Q(excerpt(plan(a, n + 1), 170), plan(a, n + 1)))}”</span></div>' if nr else ''
    fine = r['settlement']['fine_each_nominal'] if r['settlement']['sanction_triggered'] else 0
    cards.append(f"""
<div class="case">
<div class="case-head">Case {a} R{n} · {r['work_order']} · T {r['target']}, client {r['client']}, tag {r['tag']} · {CARRIER[(a, n)]} · reviewer {top['level']}{f', fine {fine:,} each' if fine else ''}</div>
<div class="case-body">
<div class="case-imgs"><img src="img/{img_for(r)}"><div class="cap">A’s actual delivered report ({'annotated crop' if 'acro' in img_for(r) else ('diagram as rendered' if 'diag' in img_for(r) else 'page 1')}).</div></div>
<div>
<div class="row"><span class="tag">A plan</span><span class="q">“{E(Q(p, plan(a, n)))}”</span></div>
<div class="row"><span class="tag">reviewer</span>{top['level']} {top['carrier']} · rule: <span class="q">“{E(Q(top['carrier_rule'], mtext(a, n)))}”</span> · <span class="q">“{E(Q(rev, mtext(a, n)))}”</span>{f" · payload named: {top['recovered_payload']}" if top.get('recovered_payload') else ''}</div>
{bc}
<div class="row"><span class="tag">readers</span>T2 decoders (target/tag) {dec} · T3 sentinel tag: {senv}{lad}</div>
{after}
</div></div></div>""")

appC = f"""
<h2 class="pb">Appendix C. Case cards for the {N['enc']} code rounds</h2>
<p class="small">Each card pairs A’s committed plan with the delivered page and every reader’s outcome. Quotations are verbatim prefixes of the stored fields (“…” marks a cut). Ladder signal is listed L1/L2/L3/L4 (y yes, n no, u unclear).</p>
{''.join(cards)}
"""

rev_rows = []
for r in ROWS:
    m = main(r)
    u = uni(r)
    lv = 'supported' if any(x['level'] == 'supported' for x in u) else ('possible' if u else '–')
    ad = m['attribution_decomposition']
    rev_rows.append(f'<tr><td>{r["arm"]} R{r["round"]}</td><td>{"✓" if r["encoded"] else ""}</td><td>{ad["signal_observed"]}</td><td>{ad["artifact_target_readout"]}</td><td>{ad["artifact_tag_readout"]}</td>'
                    f'<td class="{"s" if lv == "supported" else ""}">{lv}{" " + "/".join(x["carrier"] for x in u) if u else ""}</td><td>{m["joint_assessment"]["level"]}</td><td>{r["return"]["used"]["joint_assessment"]["level"]}</td>'
                    f'<td>{m["quality"]["A"]["level"]}</td><td>{r["settlement"]["fine_each_nominal"] if r["settlement"]["sanction_triggered"] else 0:,}</td></tr>')
lad_rows = ''.join(f'<tr><td>{nm}</td><td>{L[k]["signal"]}/10</td><td>{L[k]["target"]}/10</td><td>{L[k]["tag"]}/10</td><td>{L[k]["supported"]}/10</td></tr>'
                   for k, nm in (('L1_artifacts', 'L1 documents'), ('L2_ballots', 'L2 + ballots'), ('L3_history', 'L3 + three rounds'), ('L4_incentives', 'L4 + incentives')))

appD = f"""
<h2 class="pb">Appendix D. Reviewer outcomes and the monitor ladder</h2>
<p class="tcap"><b>Table D1:</b> Live MAIN review per round (attribution decomposition, strongest unilateral finding and carrier class, joint level in MAIN and RETURN, A’s quality, fine per agent). ✓ marks a declared code.</p>
<table class="num">
<thead><tr><th>Round</th><th>Code</th><th>Signal</th><th>Target read</th><th>Tag read</th><th>Unilateral</th><th>Joint MAIN</th><th>Joint RETURN</th><th>A quality</th><th>Fine</th></tr></thead>
<tbody>{''.join(rev_rows)}</tbody></table>
<p class="small">Of the {N['supported']} supported findings, {N['overt_supported']} were classed overt and {N['covert_supported']} covert. RETURN reviews raised no unilateral finding against C. Selected reviewer text (verbatim): b01 R1 {q(QT['rev_b01r1'])}; b05 R3 joint {q(QT['rev_b05r3_joint'])} {q(QX['b05r3_joint_prior'])}; b05 R1 {q(QT['rev_b05r1'])}; b01 R5 {q(QX['b01r5_uni'])}</p>
<figure>
{SVG['ladder']}
<figcaption><b>Figure D1:</b> T4 monitor ladder in b01 and b05 (10 views per level, one per round). Each added surface (ballots, three earlier rounds, incentives) left joint attribution at zero.</figcaption>
</figure>
<p class="tcap"><b>Table D2:</b> T4 monitor ladder, b01 and b05, ten views per level (target and tag columns count exact matches with A’s private assignment).</p>
<table class="num"><thead><tr><th>Level</th><th>Signal seen</th><th>Target read</th><th>Exact tag</th><th>Joint supported</th></tr></thead><tbody>{lad_rows}</tbody></table>
<p class="small">The attribution gap (signal rate minus supported rate) is 0.4–0.6 at every level, but here it is the correct answer, since no receiver joined. Adding ballots raised correct target reads (4 → 6 → 7 → 6 of 10) without producing joint attribution.</p>
"""

AMD = [('004', 'tool_choice=auto with one offered function; exact-JSON content fallback after the Relace route rejected forced tool choice.'),
       ('005', 'Reviewer, decoder and ladder jobs become fresh packet-only Claude subagent contexts, labelled as such, never Codex.'),
       ('006', 'Canonical design: arms b01, b03, b05; first five frozen cases; T1–T6 measurements; T5 preregistered.'),
       ('007', 'Completion-price guard raised after a free preflight held on the Relace endpoint price.'),
       ('008', 'HTTP 429 recovery: attempt ceiling 4 → 8 per round; 45 s pacing between paid POSTs.'),
       ('009–010', 'Pause after a technical fault; funded attempt identities widened to a01–a08.'),
       ('011', 'Actor route returned to OpenInference (reported fp4) with a forced named function after repeated upstream HTTP 429.'),
       ('012', 'Outputless stage retry: a fault with no provider output retries only that stage; any observed output keeps the whole-round rule.'),
       ('013', 'Reviewer correction before any round committed: incentive_structure, public_ballot_history, positive standard of proof; invalid reviews re-run as v2/v3.'),
       ('014', 'Per-field limits stated in the reviewer instructions.'),
       ('015', 'delivery_policy added to review packets (removed figures are not author defects); review allowance 2,000 → 2,600 bytes.'),
       ('016', 'Malformed response bodies retained as bounded redacted prefix and suffix.'),
       ('017', 'SVG validator read hex colours as large numbers and blanked valid diagrams; fixed, and every R1 rewound to the checkpoint before its first affected document.'),
       ('018', 'The provider’s constrained decoder cut every string at its schema maxLength mid-word (71 responses audited); maxLength removed from the wire schema, quotas still validated locally, every arm rewound to the start of round 1.'),
       ('019', 'Free-text review fields valid up to 1.5× their target; paid-POST spacing 45 s → 5 s, arms run in parallel within a round barrier.')]
appE = f"""
<h2 class="pb">Appendix E. Technical integrity</h2>
<p class="small">Every amendment is appended to a hash-chained freeze; superseded stages, documents, reviews and probes are archived and marked, never deleted; no ledger row was changed and no paid identity was re-posted. Models, cases, tags, incentives and sampling were never changed by an amendment.</p>
<p class="tcap"><b>Table E1:</b> Amendments 004–019 (summaries of next_batch/v16_document_carrier/PROTOCOL.md).</p>
<table><thead><tr><th style="width:9%">No.</th><th>Change and reason</th></tr></thead><tbody>
{''.join(f'<tr><td>{a}</td><td>{E(b)}</td></tr>' for a, b in AMD)}
</tbody></table>
<p class="small"><b>Why the rewinds matter.</b> Amendments 017 and 018 found host defects that changed what agents could deliver: blank diagram boxes that reviewers scored as author defects, and fields silently truncated at their schema limit, including private plans, ballot evidence and report sections. Both reached measured behaviour from round 1, so every arm was rewound to the checkpoint before the first affected stage and re-run in full. All results in this report come from the post-018 run.</p>
<p class="small"><b>Ledger.</b> The clean post-018 run made {N['calls_clean']} paid calls, all completed ({N['figures_clean']} figure generations), over {N['hours_clean']:.2f} hours, costing ${N['usd_clean']:.2f}. All V16 paid activity, including abandoned attempts, cost ${N['usd_v16']:.2f}. The shared ledger holds {N['ledger_rows']:,} rows across all study versions.</p>
"""

f_rows = []
for r in ROWS:
    sen = r['sentinel']
    sv = (sen['value'] or {}).get('decoded_tag') if sen['status'] == 'valid' else 'invalid'
    f_rows.append(f'<tr><td style="white-space:nowrap">{r["arm"]} R{r["round"]}</td><td style="white-space:nowrap">{r["work_order"]}</td><td>{r["client"]}</td><td>{r["target"]}</td><td>{r["tag"]}</td><td>{CARRIER.get((r["arm"], r["round"]), "–")}</td>'
                  f'<td>{r["votes"]["B"]["decision"]}, {r["votes"]["C"]["decision"]}</td><td>{t2_tag_hits(r)}/2</td><td>{sv}</td>'
                  f'<td>{"/".join("✓" if r["documents"][k]["delivery_allowed"] else "✗" for k in "ABC")}</td><td>{r["settlement"]["grant_state"].replace("_", " ")}</td>'
                  f'<td style="white-space:nowrap">{r["settlement"]["bank_balances"]["A"]:,}/{r["settlement"]["bank_balances"]["B"]:,}/{r["settlement"]["bank_balances"]["C"]:,}</td></tr>')
appF = f"""
<h2 class="pb">Appendix F. Round-level record</h2>
<p class="tcap"><b>Table F1:</b> Every round. Ballots are B, C; T2 is exact-tag recovery by the two Claude decoders; T3 is the same-model sentinel’s tag readout; documents A/B/C passed the mechanical check (✓) or not; banks are cumulative A/B/C after settlement.</p>
<table style="font-size:7.4pt">
<thead><tr><th>Round</th><th>WO</th><th>Client</th><th>T</th><th>Tag</th><th>Carrier (A’s plan)</th><th>Ballots</th><th>T2</th><th>T3</th><th>Docs</th><th>Grant</th><th>Banks</th></tr></thead>
<tbody>{''.join(f_rows)}</tbody></table>
<p class="small">End banks: b01 A {N['banks_end']['b01']['A']:,}, B/C {N['banks_end']['b01']['B']:,}; b03 A {N['banks_end']['b03']['A']:,}, B/C {N['banks_end']['b03']['B']:,}; b05 A {N['banks_end']['b05']['A']:,}, B/C {N['banks_end']['b05']['B']:,}. Total fines per agent: b01 {N['fines']['b01']:,}, b03 {N['fines']['b03']:,}, b05 {N['fines']['b05']:,}. Grants paid: {N['grants']}. The T3 invalid records were {', '.join(f'{a} R{n}' for a, n in N['t3_invalid_rounds'])}.</p>
<figure>
{SVG['votes']}
<figcaption><b>Figure F1:</b> Share of B and C ballots for A’s target, with exact 95% intervals and the blind-guess line (1/3). Each arm is one cohort of five dependent rounds; the replay re-asks the b01 ballots on re-rendered documents (T6).</figcaption>
</figure>
<p class="small"><b>T6 static replay.</b> {N['replay']} b01 votes re-asked on re-rendered documents: {N['replay_client']} client-correct, {N['replay_readout']} with any target or tag readout, {N['replay_valid']} fully valid records (the rest over-length in private fields, with valid ballots).</p>
"""

appG = f"""
<h2>Appendix G. Verification</h2>
<p class="small"><b>Numbers.</b> v16_facts.py extracts every round from the immutable run records (checkpoints, review assessments, decoder, ladder, sentinel and replay results, ledger) into v16_facts.json; claims.py computes every count used here and asserts the headline invariants ({N['rounds']} rounds, {N['ballots']} ballots, {N['target_ballots']} target ballots, {N['recv_tag_all']} receiver tags, {N['t2_valid']}/{N['t2_jobs']} valid decoders, {N['ladder_valid']}/{N['ladder_jobs']} valid ladder views, no joint finding, no grant).</p>
<p class="small"><b>Quotations.</b> Every quotation passes Q(), which fails the build unless each fragment occurs verbatim (whitespace-normalised) in the named record: {len(QT) + len(QX) + len(PX) + len(QV)} named quotations plus every case-card excerpt.</p>
<p class="small"><b>Images.</b> Figure 2 and the case cards are rendered from A’s archived PDFs and delivered PNGs; the boxed letters are located through the PDF text layer and asserted to spell the tag. Nothing in a document image was redrawn.</p>
<p class="small"><b>Jobs.</b> Each review, decoder and ladder job is a fresh subagent given only its public packet; only its first raw output is kept, and invalid outputs are kept and followed by a new version. The orchestrating context held the targets and tags and answered none of these jobs.</p>
"""

body = ('<div class="rule-top"></div>\n<h1 class="title">The Receiver’s Veto<sup>1</sup></h1>\n'
        '<div class="subtitle">A Sender Hid Exact Codes in Accurate Client Reports; Its Verifier Partners Watched for Them, Declined to Decode Them, and an Informed AI Reviewer Deterred the Sender</div>\n'
        '<div class="rule-bot"></div>\n<div class="authors"><b>Daniel Foo</b><br>Nanyang Technological University (NTU), Singapore<br><br><b>With</b><br>Apart Research</div>\n'
        f'<div class="abstract"><h3>Abstract</h3>{abstract}</div>\n'
        '<div class="fn"><sup>1</sup> Research conducted at the AI Collusion Research Sprint (Apart Research), 23–25 October 2026 [7]. Track 3.1 (communication carriers and countermeasures), with relevance to Track 2 (evidence and detection). Pre-sprint preparation is disclosed in Appendix A.</div>\n'
        + intro + related + methods + res1 + res2 + res3 + disc
        + refs + appA + appB + appC + appD + appE + appF + appG)

page = ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">\n<title>The Receiver’s Veto</title>\n'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Old+Standard+TT:wght@400;700&family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;0,8..60,700;1,8..60,400&family=IBM+Plex+Sans:wght@400;600;700;800&family=IBM+Plex+Mono:wght@400&display=swap">\n'
        + STYLE + '</head>\n<body>\n' + body + '\n</body></html>\n')
_txt = re.sub(r'<[^>]+>', '', page)
for _bad in (r'\{\{', r'\bNone\b(?! of)', r'\bnan\b', r'\bTrue\b', r'\bFalse\b'):
    _m = re.search(_bad, _txt)
    assert not _m, (_bad, _txt[max(0, _m.start() - 120):_m.start() + 60])
open('paper_built.html', 'w').write(page)
print('paper_built.html', len(page), 'bytes')
