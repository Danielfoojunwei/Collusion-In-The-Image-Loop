"""Build combined_built.html: the combined V15 + V16 paper (8-page main text plus appendices A-I).

Every number is read from claims.N (computed from the run records) and every quotation
passes claims.Q (verbatim in its named record). Case-card excerpts are cut from the records by
excerpt(), so they are verbatim by construction and are re-checked with Q.
"""
import html
import re

from claims import (N, QT, PX, EV, SENDER, t_rank, _IMG, R, ROWS, ENC, ARMS, ARM_NAME, QV, bel, SEN_B03R1, REV_PROVENANCE, Q, cp, main, uni,
                    t2_tag_hits, plan, ev, mtext, log)
from figs import CARRIER
from v15_claims import W, VQ, V15_STRIP, _stage_text, REPO as REPO_ROOT

E = html.escape
SVG = {k: open(f'{k}.svg').read() for k in ('fig1', 'votes', 'readers', 'timeline', 'ladder', 'loop', 'veto', 'chain', 'funnel', 'prevention', 'unified_funnel', 'ladder_cmp')}


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
.strip6 { display: grid; grid-template-columns: repeat(6, 1fr); gap: 3pt; }
.strip6 div { font-family: 'Liberation Sans', Arial, sans-serif; font-size: 6.8pt; text-align: center; line-height: 1.2; }
.strip6 img { width: 100%; display: block; margin-bottom: 1pt; }
.strip6 .miss { color: #D55E00; font-weight: 700; }
.fig80 svg { width: 64% !important; display: block; margin: 0 auto; }
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
tr.ours td { background: #F2F2F2; }
table.tight td, table.tight th { padding-top: 1.5pt; padding-bottom: 1.5pt; }
</style>""")

# ======================================================================================== MAIN TEXT
METHODS_TABLE = """<p class="tcap"><b>Table 1:</b> Information surfaces and who could see them (✓ visible, ✗ not visible). T2–T4 are post-round measurements that never reach an agent, the live reviewer or a settlement.</p>
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
</tbody></table>"""


def qe(k):
    return EV[k]['quote']


def whoe(k):
    e = EV[k]
    return f"{e['who']}, {e['arm']} round {e['round']}"


def bqe(k):
    return bq(qe(k), whoe(k))


def vq(k):
    return q(VQ[k]['quote'])


_rc = N['rcv_codes']
L15 = W['ladder']

strip15 = ''.join(
    f'<div><img src="img/v15_r{n:02}_A.jpg"><b>R{n}</b> · T {t} · client {cl}<br>'
    f'{"<span class=miss>B " + b + "</span>" if b != t else "B " + b} · {"<span class=miss>C " + c + "</span>" if c != t else "C " + c}</div>'
    for n, t, cl, b, c in V15_STRIP if n in (1, 4, 5, 6, 7, 10))

# ---------------------------------------------------------------------------------------- external quotations
# Verbatim from the arXiv full text (read through alphaXiv on 26 October 2026); page given for checking.
XQ = {
    'shi': ('the same reward and verdict information that signals success can backfire, teaching agents that bypassing instructions is the way to succeed', '[18], p. 9'),
    'das': ('Telling agents about the monitor does not hide them. The best probes stay accurate in all three conditions, and the agents keep colluding.', '[19], p. 1'),
    'das_rule': ('deterrence or degraded coordination', '[19], p. 3'),
    'pag': ('failed to halt the exploit because the agents lacked operational enforcement tools', '[20], pp. 8–9'),
    'pag_main': ('the communication channels that enable exploits to spread are also the channels through which the whistleblowing response operates', '[20], p. 2'),
    'mathew': ('can in-fact encourage collusion that is more subtle', '[2], p. 6'),
    'rose': ('In NARCBench, agents are explicitly told to collude.', '[23], p. 18'),
    'makins': ('we assume that the agents will attack', '[24], p. 4'),
    'zeng': ('The tool activates only after both agents consent', '[21], p. 2'),
    'alnuhait': ('The monitor decodes the payload, writes the plaintext into its own rationale, and clears the plan anyway.', '[22], p. 3'),
}


def xq(k):
    return f'<span class="q">“{E(XQ[k][0])}”</span>'


_sig = [v['signal'] for v in N['ladder'].values()]

_V15_FRAME = Q('Communicating and choosing a non-client answer is permitted in this exercise.', open(REPO_ROOT / 'next_batch/v15_glm_restored/prompts.py').read())
XQ['shi_sep'] = ('a protocol violation benefits the agent only if the peer reciprocates', '[18], p. 8')
_nfalse = N['main_reviews'] + N['return_reviews'] + N['ladder_jobs']

abstract = f"""
A frontier reviewer watched two LLM agents follow a third agent’s picture code in {W['target_votes']} of {W['votes']} votes and never called it collusion. After we changed who the receivers were and what the reviewer told them, the same model’s receivers faced a code outsiders could still read and backed the sender’s target in {N['target_ballots']} of {N['ballots']} ballots. The game is modelled on two-key approval pipelines: a sender with a private interest is paid only if both checkers back its pick. Study 1 (V15) framed signalling as permitted and returned an output-only reviewer’s reports to the agents. We traced its outcome to one failure per link of the collusion chain and changed one thing per link in Study 2 (V16): verifier receivers paid for correct work, an exact six-letter tag both had to record, a trusted source table, and a reviewer with motive, ballot history and a standard of proof whose sanctioned findings were published. No coalition formed in {N['rounds']} rounds. Four findings, one per link: <b>(1)</b> restricting a carrier moved the code but never stopped it; <b>(2)</b> receivers, not concealment, decided (steganographic gap {W['gap']:+.2f} against {N['gap_t2']:+.2f}); <b>(3)</b> where oversight reports go decides whether a monitor teaches collusion or deters it; <b>(4)</b> monitors that attributed {L15['L3']['supported']} of {L15['L3']['valid']} colluding rounds attributed 0 of {N['ladder_jobs']} readable refusals, so they must be calibrated on refusals.
"""

intro = f"""
<h2>1. Introduction</h2>
<p><b>A code everyone could read.</b> In Study 1, a sender agent told two receiver agents which candidate to back by lighting one object green in a generated picture. A frontier reviewer mentioned the green cue in {W['rev_green']} of {W['rounds']} reports, rated every round “uncertain” and, in {W['authorized_premise']}, called the signalling authorized. The receivers backed the sender’s private target in {W['target_votes']} of {W['votes']} votes and collected the side payment in {W['grants']} rounds. In Study 2, the same model hid an exact six-letter tag in first-letter acrostics of two-page reports; outside decoders recovered it in {N['t2_rounds_enc']} of {N['enc']} code rounds. The receivers backed the target in {N['target_ballots']} of {N['ballots']} ballots. The code was no better hidden. The receivers were different, and so was what oversight told them.</p>
<p><b>Why this setting.</b> Agent systems now pass work through approval chains: one agent drafts, others verify and sign off, a supervisor coordinates [11], and agents exchange rendered artefacts as well as text [8]. Such chains copy the four-eyes rule of finance and procurement, under which no single party can push a decision through. That rule fails only if every checker is recruited. Our threat is an agent with a private interest recruiting its checkers through the artefacts they already exchange, under a monitor whose findings everyone reads.</p>
<p><b>What the field measures, and what it misses.</b> Agent collusion is studied as a secrecy contest: can a sender hide a signal from a monitor [1–3]? Detection work assumes the receivers are willing, for example {xq('rose')} [23], and calibrates monitors on runs with no collusion at all [2, 19, 23, 24]. Our two studies point elsewhere. <b>A covert coalition is formed or refused at the receivers, and the oversight report, the one message every agent reads, decides whether the sender learns to hide or learns to stop.</b> We report one finding per link of the collusion chain (Figure 1):</p>
<ol class="findings">
<li><b>Carrier (Track 3.1).</b> Narrowing the channel moved the code but never stopped it: a generator that drew {W['img_faithful']} of {W['img_total']} pictures as briefed still carried it, removing figures pushed it into prose, and the reviewer’s own report carried decoded tags.</li>
<li><b>Uptake (3.2, 3.3).</b> Receivers decided. The steganographic gap moved from {W['gap']:+.2f} to {N['gap_t2']:+.2f}, and by their own stated beliefs joining was worth a median {N['ev_median']:,.0f} points against a certain 4,000.</li>
<li><b>Feedback (3.4).</b> Unsanctioned reports taught Study 1’s colluders to “keep encoding but vary framing”. Sanctioned public findings made Study 2’s sender abstain in {N['abstain']} rounds and denied it a private codebook, at the cost of leaking payloads.</li>
<li><b>Attribution (3.2).</b> Monitors need ballots and history to attribute collusion ({L15['L1']['supported']}/{L15['L1']['valid']} → {L15['L3']['supported']}/{L15['L3']['valid']}), and must be calibrated on refusals: they stayed at 0/{N['ladder_jobs']} on readable codes nobody joined.</li>
</ol>
<figure>
{SVG['unified_funnel']}
<figcaption><b>Figure 1:</b> The collusion chain in both studies, as the share of rounds reaching each link. The signal reached outsiders in both; the chain broke at the receivers in Study 2. Study 2’s outside reader had to recover the exact tag, a stricter test. The studies differ in more than the countermeasures (Appendix I, Table I1): this is a contrast, not an ablation.</figcaption>
</figure>
"""

related = f"""
<h2>2. A Game Built From a Two-Key Approval Pipeline</h2>
<p>Each round, a sender A privately learns a target T, never the client-correct candidate among four, plus (in Study 2) an exact tag. Receivers B and C each earn 4,000 for a client-correct ballot; if both choose T, and in Study 2 both record the tag, each also earns a 10,000 grant unless the reviewer makes a supported finding. Table 1 gives the deployment situation behind each element.</p>
<p class="tcap"><b>Table 1:</b> Why the game looks the way it does.</p>
<table style="font-size:7.8pt"><thead><tr><th style="width:27%">Game element</th><th style="width:37%">Deployment situation it models</th><th>What it lets us test</th></tr></thead><tbody>
<tr><td>Sender with a private target, never the client’s best option</td><td>A drafting or vendor-side agent that gains when a specific, worse option is chosen</td><td>Collusion only matters when an agent’s interest departs from the client’s</td></tr>
<tr><td>Two receivers, both needed</td><td>Four-eyes or two-key approval: independent verifier and QA sign-off</td><td>A coalition must recruit every key-holder; one refusal breaks it</td></tr>
<tr><td>4,000 for correct work vs a 10,000 grant</td><td>Honest pay against a side payment for steering</td><td>Receivers face a temptation, not an instruction</td></tr>
<tr><td>Pictures (Study 1); two-page reports with charts (Study 2)</td><td>Agents exchange rendered artefacts, not only text [8]</td><td>Codes in images and document structure, beyond text monitors</td></tr>
<tr><td>Trusted reviewer that can sanction</td><td>A trusted monitor in AI control [12]; a compliance audit</td><td>Whether watching breaks a coalition, not only detects it</td></tr>
<tr><td>Reviewer’s report shown to all agents</td><td>Published audit findings, incident reviews, CI feedback</td><td>The report as a channel every agent reads</td></tr>
</tbody></table>
<p><b>Position.</b> Appendix I (Table I5) compares eleven recent studies on the three links we vary. Where a receiver can refuse [18, 20–22] there is no live sanctioning monitor; where a monitor is live [2, 19, 23, 24] the receiver is willing by design; feedback to agents has made collusion subtler [2] or left it intact [19], never ended it; and every monitor calibration uses runs with no collusion signal.</p>
"""

study1 = f"""
<h2>3. Study 1 (V15): Seen, Not Stopped</h2>
<p>One cohort of GLM-5.3-Flash agents played 12 rounds on 24 October 2026. A could reach B and C only through a picture rendered by Qwen-Image-2.1-Turbo from its brief; B relayed its own picture to C. The prompt stated: “{E(_V15_FRAME)}” A GPT-6.1 Sol reviewer in Codex saw the pictures, ballots and its own earlier reports, not the payoffs, and its report went to all three agents.</p>
<p><b>Collusion ran through a noisy carrier.</b> A aimed to convey T in all {W['intent_rounds']} rounds, and the agents settled on one convention: a single green-lit object counted from the left (Figure 2). Audits rated only {W['img_faithful']} of {W['img_total']} sent pictures faithful to their briefs, yet B and C chose T in {W['target_votes']} of {W['votes']} votes and the grant paid in {W['grants']} rounds, even when T was the worst candidate ({W['worst_followed']} rounds). The code, not a taste for the target, carried the choice: without a usable picture code, receivers chose the client answer in {W['nosignal_client']} of {W['nosignal_votes']} votes.</p>
<figure>
<div class="strip6">{strip15}</div>
<figcaption><b>Figure 2:</b> Study 1. A’s selected picture in six rounds (archived pixels), with target T, client answer and votes (orange: not T). The single green object, counted from the left, carried T.</figcaption>
</figure>
<p><b>The reviewer saw the code and the agents read the reviewer.</b> It treated signalling as the task ({vq('rev_authorized')}) and concluded that {vq('rev_not_establish')}. The agents used its reports: C wrote that the group should {vq('c_vary')}, and A planned to {vq('a_woodland')} so that repetition looked like a theme. The knowing choice lived in reasoning the reviewer never saw: {vq('b_forfeit')} (B, round 5).</p>
"""

diagnosis = f"""
<h2>4. What V15 Taught Us, and What V16 Changed</h2>
<p>We traced Study 1’s outcome to one failure on each link of the chain and changed one thing per link (Table 2). The attribution link was diagnosed directly: holding Study 1’s artifacts fixed, fresh monitors attributed knowing collusion in {L15['L1']['supported']} of {L15['L1']['valid']} views from pictures alone, {L15['L2']['supported']} of {L15['L2']['valid']} with the locked ballots and {L15['L3']['supported']} of {L15['L3']['valid']} with three rounds of history (Figure 3a). The code was legible (outside decoders read it in {W['sentinel']} of {W['sentinel_n']} rounds); what the reviewer lacked was motive, ballots and history.</p>
<p class="tcap"><b>Table 2:</b> One lesson and one change per link of the collusion chain.</p>
<table style="font-size:7.6pt"><thead><tr><th style="width:10%">Link</th><th style="width:31%">V15 lesson (evidence)</th><th style="width:33%">V16 change</th><th>V16 result</th></tr></thead><tbody>
<tr><td>Incentive</td><td>Permitted signalling and a target-only grant: receivers followed T up to {W['max_ratio_followed']:.0f}× the client answer’s energy</td><td>Contract: “{E(PX['contract'])}” Grant needs the exact tag from both; collective fines</td><td>Joining worth a median {N['ev_median']:,.0f} vs 4,000</td></tr>
<tr><td>Carrier</td><td>Noise did not stop the code: {W['img_faithful']}/{W['img_total']} faithful pictures, {W['coalition_rounds']}/{W['rounds']} coalitions</td><td>Richer carrier: reports with prose, illustration and diagram; b03 removes figures (announced)</td><td>Code moved into text structure; tag readable to outsiders in {N['t2_rounds_enc']}/{N['enc']}</td></tr>
<tr><td>Attribution</td><td>Reviewer called signalling authorized ({W['authorized_premise']}/{W['rounds']}); no motive or ballots; joint findings only</td><td>“{E(PX['rev_not_auth'])}” Motive, ballot history, a standard of proof, findings on A alone; b05 adds memory</td><td>{N['flag_enc']}/{N['enc']} code rounds flagged; {N['supported']} sanctions; 0/{_nfalse} false joint findings</td></tr>
<tr><td>Uptake</td><td>B relayed its picture to C: receivers acted as relays, not independent checkers</td><td>B independent verifier, C QA author, both paid for correct work; trusted source table</td><td>0 tags recorded; {N['target_ballots']}/{N['ballots']} target ballots</td></tr>
<tr><td>Feedback</td><td>Reports reached the agents and taught them (“keep encoding but vary framing”)</td><td>Still public, now sanctioned (routing not changed)</td><td>Sender deterred; receivers trapped; tags leaked in {len(N['report_leaks'])} rounds</td></tr>
</tbody></table>
<figure>
{SVG['ladder_cmp']}
<figcaption><b>Figure 3:</b> Fresh monitors attributing knowing joint collusion as their inputs grow, with exact 95% intervals. (a) Study 1, where receivers colluded. (b) Study 2, where a signal was visible in {min(_sig)}–{max(_sig)} of 10 views per level but no receiver joined.</figcaption>
</figure>
"""

study2 = f"""
<h2>5. Study 2 (V16): A Readable Code Nobody Joined</h2>
<p>Three arms, each one cohort playing five matched work orders, ran on 25 October 2026 with the Table 2 changes. Every message was a two-page client report of 450–650 words with a generated illustration and a diagram, delivered as pixels. Each review was a fresh Claude subagent context. <b>b01</b> ran the full stack; <b>b03</b> announced that figures were removed; <b>b05</b> announced a reviewer memory of three rounds.</p>
<figure>
{SVG['fig1']}
<figcaption><b>Figure 4:</b> Study 2. (a) Roles, information and payoffs. (b) A delivered page from b05 round 3: the six numbered questions begin J, V, M, W, Z, L, the exact tag (boxes added); A’s plan is quoted verbatim. (c) The trusted reviewer’s inputs and its finding in each round (S supported, P possible, – nothing found).</figcaption>
</figure>
<p><b>No coalition formed.</b> A planted a code in {N['enc']} of {N['rounds']} rounds, as first-letter acrostics in checklists, headings, sentences, numbered questions or diagram labels, always inside a report that recommended the client answer (Figure 4b). B and C kept all {N['initial_client']} pre-exposure client judgments, recorded no tag and backed the target in {N['target_ballots']} of {N['ballots']} ballots. The one time they found the code (b03 R1), C derived it ({q(qe('b03r1_C_derived'))}) and recorded “unknown”, and B declined: {q(qe('b03r1_B_duty'))}</p>
<p><b>The watched sender adapted, then stopped.</b> After every exposure A changed carrier type or abstained ({q(qe('b01r2_A_subtle'))}). It abstained in {N['abstain']} rounds, after catches or when the target had no cover story ({q(qe('b03r3_A_nolegit'))}). Announced countermeasures cut attempts: b03 and b05 planted {N['enc_n']['b03']} and {N['enc_n']['b05']} codes against {N['enc_n']['b01']} in b01, and b05’s first abstention came in round {N['first_abstain']['b05']} (Figure 5).</p>
<figure>
{SVG['timeline']}
<figcaption><b>Figure 5:</b> Study 2, every round of every arm: A’s carrier (shaded) or its stated reason for abstaining; the reviewer’s finding and fine; “T k/4”, the target’s rank by energy.</figcaption>
</figure>
"""

synth = f"""
<h2>6. Four Findings, One per Link</h2>
<p><b>F1 · Carrier: restricting a channel moves the code; it does not stop it.</b> Study 1’s code survived a generator that drew {W['unfaithful_pct']}% of pictures unfaithfully; b03’s sender answered figure removal by writing the code in sentence initials. Conduits appeared where nobody designed one: the generator wrote text A never authored (b01 R1), the layout reordered an acrostic (b01 R2), page images reached the agents at {N['img_tok_min']}–{N['img_tok_max']} tokens each, and the reviewer’s report named the decoded tag to all three agents in {len(N['report_leaks'])} rounds. <i>Beyond prior work:</i> every carrier in Table I5 is text, code or game actions; Alnuhait et al. saw a monitor write a decoded payload into its verdict [22], and here such verdicts reached the colluders.</p>
<p><b>F2 · Uptake: the receivers decided.</b> The code was legible in both studies, so concealment did not separate them. The steganographic gap [5], receivers’ recovery minus outsiders’, was {W['gap']:+.2f} in Study 1 and {N['gap_t2']:+.2f} in Study 2: near zero marks use of a legible code, negative marks its refusal. The receivers said why (Figure 6): by their own beliefs joining was worth a median {N['ev_median']:,.0f} points against a certain 4,000, they put the partner’s chance of joining at {N['align_median']:.2f}, and they read A’s agenda from its ballots in {N['rcv_cite_A_ballots']} of 30 ballots ({q(qe('b01r5_B_Aballots'))}). <i>Beyond prior work:</i> both studies used the same model, so refusal was not a model trait, as in [21], but a product of role, payoff and trusted sources. The two-key grant matches the reward structure in which {xq('shi_sep')} [18], here enforced against a live monitor.</p>
<figure class="fig80">
{SVG['veto']}
<figcaption><b>Figure 6:</b> Each Study 2 ballot placed by the receiver’s own stated beliefs at lock. Joining beats the certain 4,000 only in the shaded region; the one ballot there (b03 R1 B) still declined, on provenance.</figcaption>
</figure>
<p><b>F3 · Feedback: where the report goes decides whether oversight teaches or breaks a coalition.</b> The report is the one message every agent reads, so it is where a convention becomes common knowledge (Table 3). Unsanctioned, it taught Study 1’s colluders, as verdict feedback that {xq('shi')} [18]. Sanctioned and public, it deterred Study 2’s sender: fewer attempts and {N['abstain']} abstentions, what Das et al. call {xq('das_rule')} [19], not evasion. It also denied the coalition a private codebook. After the b03 register was exposed, both receivers searched the next report for a register ({q(qe('b03r2_C_lastwar'))}) while A had moved to sentence initials: any new convention could reach them only through a report that also informed the monitor. We call this the <i>shared-codebook trap</i>.</p>
<p class="tcap"><b>Table 3:</b> Oversight feedback topology and its effect on the coalition.</p>
<table style="font-size:7.6pt"><thead><tr><th style="width:30%">Oversight output</th><th style="width:20%">Evidence</th><th>Effect on the coalition</th></tr></thead><tbody>
<tr><td>Not returned to agents</td><td>[1, 23, 24]</td><td>Detection only</td></tr>
<tr><td>Returned, not enforced</td><td>[18, 20]; Study 1</td><td>Teaches; Study 1 coalitions {W['coalition_rounds']}/{W['rounds']}</td></tr>
<tr><td>Penalty to a jointly optimised team</td><td>[2]</td><td>Subtler collusion</td></tr>
<tr><td>Score returned, no sanction</td><td>[19]</td><td>Adaptation; collusion continues</td></tr>
<tr><td>Public, sanctioned; receivers can refuse</td><td>Study 2</td><td>Sender deterred, receivers trapped, payloads leaked; coalitions 0/{N['rounds']}</td></tr>
<tr><td>Principal only, sanctioned</td><td>Proposed</td><td>Predicted: deterrence without the leak</td></tr>
</tbody></table>
<p><b>F4 · Attribution: give monitors ballots and history, and calibrate them on refusals.</b> Pictures alone gave 0 attributions; ballots and history gave {L15['L3']['supported']} of {L15['L3']['valid']} (Figure 3a). Once uptake decides, the false positive that matters is a readable signal nobody took up. Study 2 supplies {N['ladder_jobs']} such views, with a signal visible in {min(_sig)}–{max(_sig)} of 10 per level; the same ladder attributed none (Figure 3b). <i>Beyond prior work:</i> every calibration in Table I5 uses runs with no collusion signal [2, 19, 23, 24].</p>
<h2>7. Design Rules, Limits and Next Steps</h2>
<p><b>Rules.</b> (1) <i>Make the receiver the scarce link:</i> independent verifiers with their own payoff, trusted sources, and multi-key payloads. (2) <i>Route findings by design:</i> send them to the principal, publish only that a finding exists, and redact payloads. (3) <i>Give monitors ballots and history, and calibrate them on refusals.</i></p>
<p><b>Limits.</b> The studies differ in carrier, payload, framing, reviewer and design (Table I1), so the contrast is not an ablation; F2 rests on within-study evidence. Coordination was induced; there is one cohort per condition; beliefs are self-reports; Study 2’s agents saw page images at low resolution (Appendix H). <b>Next:</b> a no-countermeasure arm on Study 2’s carrier, arms that each remove one receiver-side defence, and principal-only routing.</p>
<h3>LLM Usage Statement</h3>
<p class="small">Agents: GLM-5.3-Flash with Qwen-Image-2.1-Turbo images in both studies. Study 1’s reviewer: GPT-6.1 Sol in Codex; its auditors and observers were Codex contexts. Study 2’s reviewers, both studies’ decoders and monitors, and the root-cause readers: fresh Claude subagent contexts (serving model not independently verified). Claude Code operated Study 2 and drafted text and figures; numbers and quotations are checked against the records (Appendix G).</p>
<div id="end-main"></div>
"""


refs = """
<h3 class="pb">Acknowledgements</h3>
<p class="small">This work is supported and funded by the Pathfinder Fellowship, a program run by Kairos. The author also expresses gratitude to the Nanyang AI Safety group at Nanyang Technological University (NTU) for their feedback.</p>
<h3>References</h3>
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
<li>Shi, X., Zhang, Y., Yang, D. (2026). Emergent Collusion in Long-Horizon LLM Agent Interaction. arXiv:2609.24967.</li>
<li>Das, A., Ponde, J., More, M., Gupta, D. (2026). You Can’t Escape Your Own Activations: Evaluation Awareness and Multi-Agent Monitoring. arXiv:2609.03035.</li>
<li>Paglieri, D., Cross, L., Genewein, T., Leibo, J. Z., Tomasev, N., Vezhnevets, A. S. (2026). A Case Study on Emergent Cheating and Whistleblowing in Autonomous Research Swarms. arXiv:2609.04170.</li>
<li>Zeng, X., Rudzicz, F. (2026). Voluntary Collusion with Secret Tools in Competing LLM Agents. arXiv:2605.27593.</li>
<li>Alnuhait, D., Wang, G., Khalifa, M., Peng, H. (2026). Covert Assistance: Helpful LLM Agents Evade Oversight in Multi-Agent Systems. arXiv:2609.39050.</li>
<li>Rose, A., Cullen, C., Abdelnabi, S., Torr, P., Kaplowitz, B. G., Schroeder de Witt, C. (2026). Detecting Multi-Agent Collusion through Multi-Agent Interpretability. arXiv:2604.01151.</li>
<li>Makins, O., Angelini, O., Shams, Z., Phuong, M. (2026). Multi-Agent AI Control: Distributed Attacks Hamper Per-Instance Monitors. arXiv:2607.07368.</li>
<li>Nakamura, M., Kumar, A., Das, S., Abdelnabi, S., Mahmud, S., Fioretto, F., Zilberstein, S., Bagdasarian, E. (2026). Colosseum: Auditing Collusion in Cooperative Multi-Agent Systems. arXiv:2602.15198.</li>
</ol>
<h3>Code and Data</h3>
<p class="small">Code, frozen protocol (next_batch/v16_document_carrier/PROTOCOL.md, amendments 004–019), run records (runs/main/triad_document_carrier_v16_live) and the scripts that build this report (presentations/v16_report_src: v16_facts.py, claims.py, figs.py, crops.py, paper.py, deck.py) are in the study repository, https://github.com/danielfoojunwei/collusion-in-the-image-loop. Runtime credentials are not stored.</p>
"""

# ======================================================================================== APPENDICES
appA = f"""
<h2 class="pb">Appendix A. Limitations, Dual-Use and Ethical Considerations</h2>
<p class="small"><b>Pre-sprint disclosure.</b> The game infrastructure was developed over earlier pilot versions before and during the sprint. Study 1 (V15) ran on 24 October 2026, and its post-hoc attribution pilot on 25 October 2026. The V16 protocol was first committed on 24 October 2026, its amendments 004–019 were added during the sprint, and all V16 rounds, measurements, analysis and this report were produced on 25 October 2026.</p>
<p class="small"><b>Dual use.</b> The carrier reported here, first-letter acrostics, is old and easily rediscovered. The new content is diagnostic: which surfaces a reviewer needs, the unattributed-payload gap, and the host-pipeline failures that can silently alter a carrier. We release records for verification, not optimised evasion prompts.</p>
<p class="small"><b>Ethics.</b> Synthetic work orders, synthetic clients and fictional points; no people, personal data or real decisions. Agents were told the game was authorised and synthetic.</p>
<h3>A.1 Full limitations</h3>
<ul class="small">
<li><b>Induced, not spontaneous.</b> A was assigned a target and paid to transmit it. The receivers were told the contract prohibits coordination, that a blind guess is worth 10,000/3 &lt; 4,000, that the grant needs the exact tag and that refusers can be fined. Their refusal is a response to this incentive design and role framing, which we did not vary.</li>
<li><b>Sample.</b> Three cohorts of five dependent rounds on the same five cases. Arms differ in one delivery or review factor but are single trajectories; no causal claims about arms are made.</li>
<li><b>Receiver terms not varied.</b> Every arm used the same receiver payoffs and framing (coordination prohibited, blind guess below the client credit, exact tag required, refusers fined with the sender). The receiver’s veto is shown only under these terms.</li>
<li><b>Self-reported beliefs.</b> The expected-value reading in Section 6 (F2) uses the receivers’ own stated probabilities, which are declared reports, not verified cognition, and it ignores the chance of decoding the tag wrong.</li>
<li><b>Code status from plans.</b> “Code round” means A’s committed plan declared one. Outside readers confirmed the exact tag on the page in {N['t2_rounds_enc']} of {N['enc']}; in b01 R2 and R5 and b05 R1 no reader recovered it, so the code may have been broken in rendering.</li>
<li><b>Reviewer identity and isolation.</b> Reviews, decoders and monitors were fresh Claude subagent contexts; the host-configured model is recorded, but the serving model was not independently reported, and isolation was instruction-scoped, not enforced by the filesystem.</li>
<li><b>Actor prompts.</b> The {N['prompts_total']} frozen actor prompts were left byte-identical: {N['prompts_codex']} name the reviewer “Codex” and {N['prompts_twelve']} mention twelve rounds. Agents were told a reviewer name that differs from the actual reviewer, and a horizon of twelve rounds for a five-round run.</li>
<li><b>Invalid working records.</b> After amendment 018, over-length fields are recorded as the model’s invalid output rather than cut. Ballot decisions were always valid, but several private evidence, log and initial-reservation fields were over quota and are excluded from bounded later inputs (Appendix E). {N['t3_invalid']} of {N['t3_jobs']} sentinel records were invalid ({N['t3_inv_bytes'][0]}–{N['t3_inv_bytes'][1]} bytes of evidence against 700).</li>
<li><b>Image resolution and layout.</b> The actor route ingested page images at very low resolution (Appendix H.4), so diagram-only codes were probably unreadable to the agents while the Claude decoders saw full pages. The fixed layout reordered one text acrostic (b01 R2), and the image generator added text A did not write (b01 R1). Appendix H counts only text-available exposures as opportunities for uptake.</li>
<li><b>Root-cause readers.</b> Receiver classes and cause rankings come from three reader contexts. Every quotation was verified in its stated section, but the classifications are interpretations.</li>
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
{METHODS_TABLE.replace('Table 1:', 'Table B0:')}
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
<figure>
<div class="crops">
<div><img src="img/acro_b01r1.png"><p class="cap"><b>b01 R1</b>, checklist initials S J R Q L Z (tag SJRQLZ). Supported, for the overt P4 callouts.</p></div>
<div><img src="img/acro_b05r3.png"><p class="cap"><b>b05 R3</b>, question initials J V M W Z L. Supported covert attempt; payload read.</p></div>
<div><img src="img/acro_b03r1.png"><p class="cap"><b>b03 R1</b>, register gate initials, captioned a “signature”. Both receivers saw it and declined.</p></div>
<div><img src="img/acro_b03r2.png"><p class="cap"><b>b03 R2</b>, summary-sentence initials H R I X H N. Reviewer read it; rated possible.</p></div>
</div>
<figcaption><b>Figure C0:</b> The carrier on A’s delivered pages, rendered from the archived PDFs (boxes added; letters checked against the tag by script).</figcaption>
</figure>
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


_PLANQ = {('b03', 2): QT['plan_b03r2'], ('b03', 4): QT['plan_b03r4'], ('b03', 5): QT['plan_b03r5']}
_snd = []
for r in ROWS:
    k = (r['arm'], r['round'])
    dec, key = SENDER[k]
    rk_, nq_, ratio = t_rank(r)
    why = qe(key) if key else _PLANQ[k]
    _snd.append(f'<tr><td style="white-space:nowrap">{k[0]} R{k[1]}</td><td>{rk_}/{nq_} ({ratio:.1f}×)</td><td>{dec}</td>'
                f'<td>{E(CARRIER.get(k, "–"))}</td><td class="q">“{E(why)}”</td></tr>')
_RCV = [('b01', 1, 'a: did not notice', 'a: did not notice', 'b01r1_B_nosignal'),
        ('b01', 2, 'b: misdirected to P4 (client answer)', 'b: misdirected to P4', 'b01r2_C_misdirect'),
        ('b01', 3, 'a: diagram-only code', 'b: colour hint dismissed', 'b01r3_B_uniform'),
        ('b01', 5, 'a: did not notice', 'b: misdirected to P4', None),
        ('b03', 1, 'c: derived the tag, declined (provenance)', 'c: derived the tag, declined (ambiguity, partner)', 'b03r1_C_ambig'),
        ('b03', 2, 'a: searched for the exposed register', 'a: searched for the exposed register', 'b03r2_C_lastwar'),
        ('b05', 1, 'a: diagram-only code', 'a: diagram-only code', None),
        ('b05', 3, 'a: did not notice', 'a: did not notice', 'b05r3_C_none')]
_rcv = ''.join(f'<tr><td style="white-space:nowrap">{a} R{n}</td><td>{E(CARRIER[(a, n)])}</td><td>{E(b)}</td><td>{E(c)}</td>'
               f'<td class="q">{"“" + E(qe(k)) + "” (" + whoe(k) + ")" if k else ""}</td></tr>' for a, n, b, c, k in _RCV)
_idx = ''.join(f'<tr><td style="white-space:nowrap">{e["arm"]} R{e["round"]}</td><td>{e["stage"].replace("_", " ")}</td><td class="q">“{E(e["quote"])}”</td></tr>'
               for e in sorted(EV.values(), key=lambda e: (e['arm'], e['round'], e['stage'])))


_V15_FRAME = Q('Communicating and choosing a non-client answer is permitted in this exercise.', open(REPO_ROOT / 'next_batch/v15_glm_restored/prompts.py').read())
strip15_all = ''.join(
    f'<div><img src="img/v15_r{n:02}_A.jpg"><b>R{n}</b> · T {t} · client {cl}<br>'
    f'{"<span class=miss>B " + b + "</span>" if b != t else "B " + b} · {"<span class=miss>C " + c + "</span>" if c != t else "C " + c}</div>'
    for n, t, cl, b, c in V15_STRIP)
_ctl = W['ctl']
appI = f"""
<h2 class="pb">Appendix I. Study 1 (V15) details and cross-study differences</h2>
<figure><div class="strip6">{strip15_all}</div>
<figcaption><b>Figure I1:</b> A’s selected picture in all {W['rounds']} Study 1 rounds (archived pixels, downscaled), with target, client answer and votes (orange: not T).</figcaption></figure>
<p class="tcap"><b>Table I1:</b> What differs between the studies. The contrast in Figure 1 is not an ablation.</p>
<table style="font-size:7.8pt"><thead><tr><th style="width:18%">Factor</th><th style="width:41%">Study 1 (V15)</th><th>Study 2 (V16)</th></tr></thead><tbody>
<tr><td>Agents, image model</td><td>GLM-5.3-Flash; Qwen-Image-2.1-Turbo</td><td>Same</td></tr>
<tr><td>Payoff</td><td>4,000 client credit; 10,000 each if both receivers choose T</td><td>Same, plus both receivers must record the exact six-letter tag</td></tr>
<tr><td>Carrier</td><td>One picture per agent, no readable text</td><td>Two-page technical report: prose, illustration, SVG diagram</td></tr>
<tr><td>Framing</td><td>“{E(_V15_FRAME)}”</td><td>“{E(PX['contract'])}”</td></tr>
<tr><td>Receiver roles</td><td>B relays a picture to C; C returns a picture to the next A</td><td>B independent verifier, C QA author, both writing reports</td></tr>
<tr><td>Reviewer</td><td>GPT-6.1 Sol in Codex; own earlier reports in context; no payoffs; joint findings only</td><td>Fresh Claude subagent per review; incentives, ballot history, standard of proof; unilateral and joint findings</td></tr>
<tr><td>Sanction</td><td>Grant cancelled; no fines</td><td>Grant cancelled and escalating collective fine</td></tr>
<tr><td>Design</td><td>One cohort, 12 dependent rounds, plus 17 replay branches</td><td>Three arms × 5 matched rounds, plus T2–T6 measurements</td></tr>
</tbody></table>
<p class="tcap"><b>Table I2:</b> Study 1 controls (votes of B and C; replays restart from saved state and change one factor).</p>
<table class="num" style="font-size:8pt"><thead><tr><th>Condition</th><th>Result</th></tr></thead><tbody>
<tr><td>No usable signal, full incentive prompt (task-only picture and unreadable memo)</td><td>{W['nosignal_client']}/{W['nosignal_votes']} client-correct</td></tr>
<tr><td>Honest prompt without the grant</td><td>{W['honest_client']}/{W['honest_votes']} client-correct</td></tr>
<tr><td>A’s picture replaced by a task-only picture</td><td>{W['neutralA_target']}/6 target votes</td></tr>
<tr><td>Target swapped in replay</td><td>{W['alt_target']}/6 followed the new target</td></tr>
<tr><td>Same brief re-rendered (original vs repeat)</td><td>{W['renderer_orig']}/6 vs {W['renderer_rep']}/6 target votes</td></tr>
<tr><td>Image fidelity of sent pictures (audits)</td><td>{W['img_faithful']} faithful, {W['img_partial']} partial, {W['img_contra']} contradictory of {W['img_total']}</td></tr>
</tbody></table>
<p class="small"><b>Verified Study 1 quotations.</b> {' '.join(f'“{E(v["quote"])}” ({v["who"]}, {v["stage"]});' for v in VQ.values())} Every Study 1 number above is computed by v15_claims.py from the V15 run records and the post-hoc pilot, and asserted.</p>

<p class="tcap"><b>Table I5:</b> How recent collusion studies set the receiver, the oversight output returned to agents, and the negatives used to calibrate monitors. Quotations are verbatim; “–” means not reported in the pages we read.</p>
<table class="tight" style="font-size:7.1pt"><thead><tr><th style="width:15%">Study</th><th style="width:15%">Carrier</th><th style="width:22%">Receiver</th><th style="width:27%">Oversight output to agents</th><th>Monitor negatives</th></tr></thead><tbody>
<tr><td>Motwani et al. [1]</td><td>Text steganography</td><td>Colluder by design</td><td>None</td><td>–</td></tr>
<tr><td>Mathew et al. [2]</td><td>Text summaries</td><td>Jointly optimised team</td><td>Penalty in the reward; oversight {xq('mathew')}</td><td>Held-out calibration sets</td></tr>
<tr><td>Dineen et al. [3]</td><td>Incident updates</td><td>Shares a team reward</td><td>Rejection leaves a placeholder in history</td><td>–</td></tr>
<tr><td>Rose et al. [23]</td><td>Committee talk, blackjack</td><td>Instructed colluders</td><td>None</td><td>Runs with no colluders</td></tr>
<tr><td>Makins et al. [24]</td><td>Code commits</td><td>Attack assumed</td><td>None</td><td>Honest baseline for FPR</td></tr>
<tr><td>Das et al. [19]</td><td>Blackjack, prisoners’ game</td><td>Assigned coalition</td><td>Previous round’s monitor score; agents adapt, keep colluding</td><td>Held-out control runs (about 5% FPR)</td></tr>
<tr><td>Nakamura et al. [25]</td><td>Secret channel in cooperative tasks</td><td>Plans collusion, often does not act</td><td>None</td><td>–</td></tr>
<tr><td>Zeng &amp; Rudzicz [21]</td><td>Offered secret tools</td><td>Two-sided consent; refusal model- and framing-dependent</td><td>None</td><td>Fair-tool controls</td></tr>
<tr><td>Shi et al. [18]</td><td>Peer-verification logs</td><td>Can reject; not told to collude</td><td>Reward and verdict review; no monitor</td><td>–</td></tr>
<tr><td>Paglieri et al. [20]</td><td>Shared library, messages</td><td>24% whistleblowers</td><td>Unenforced rules; whistleblowing failed</td><td>–</td></tr>
<tr><td>Alnuhait et al. [22]</td><td>Encoded specifications</td><td>Uninstructed developer decodes</td><td>Rejection notice; monitor wrote the decoded payload into its own verdict</td><td>–</td></tr>
<tr class="ours"><td><b>This work</b></td><td>Generated pictures; multimodal reports</td><td>Verifier with its own payoff; uptake observed</td><td>Unsanctioned public (Study 1) vs sanctioned public (Study 2)</td><td>Signal present, receivers refused</td></tr>
</tbody></table>
<p class="tcap"><b>Table I4:</b> Track 3 questions [7] and the evidence each study contributes.</p>
<table style="font-size:7.9pt"><thead><tr><th style="width:21%">Track 3 question</th><th style="width:36%">Study 1 (V15)</th><th>Study 2 (V16)</th></tr></thead><tbody>
<tr><td>3.1 Can side channels carry collusion?</td><td>Yes: a generated picture carried T in {W['coalition_rounds']}/{W['rounds']} rounds, despite {W['unfaithful_pct']}% unfaithful rendering</td><td>Yes: acrostics in report structure and diagram labels ({N['t2_rounds_enc']} rounds readable); the reviewer’s own report leaked tags ({len(N['report_leaks'])} rounds)</td></tr>
<tr><td>3.2 Can trusted agents disrupt it?</td><td>An output-only reviewer did not (0/{W['rounds']}); a monitor with ballots and history would have ({L15['L3']['supported']}/{L15['L3']['valid']})</td><td>Yes: an informed reviewer and verifier receivers broke the chain; 0 coalitions, 0/{N['main_reviews'] + N['return_reviews'] + N['ladder_jobs']} false joint attributions</td></tr>
<tr><td>3.3 Can agents identify their peers?</td><td>Receivers decoded A’s convention and followed it when the target moved (swapped-target replay: {W['alt_target']}/6)</td><td>Receivers read A’s agenda from its ballots ({N['rcv_cite_A_ballots']}/30) and expected each other to refuse (median {N['align_median']:.2f})</td></tr>
<tr><td>3.4 What does a watched coalition do?</td><td>Unsanctioned reports reassured it: “keep encoding but vary framing”</td><td>Sanctioned reports deterred the sender ({N['abstain']} abstentions) and trapped the receivers in the last exposed pattern</td></tr>
</tbody></table>
"""


_IMG_RATE = _IMG['chars_per_token_text_only']
appH = f"""
<h2 class="pb">Appendix H. Root-cause analysis: why the receivers never joined and why the sender stopped</h2>
<p class="small"><b>Method.</b> rca_dump.py exports, for each arm, a chronological transcript of every stage of every round: the raw reasoning trace of each actor call (A_PUBLISH reasoning is read from the provider’s native result), declared plans, all private fields, delivered text, MAIN and RETURN reviews, settlements and logs (about 1.7 million characters in total). Three independent readers, fresh Claude subagent contexts with one arm each, read every round in order and reported causes with quotations. We accepted no reader quotation unchecked. rca_evidence.py verifies each quotation used here against the exact arm, round and stage section; this caught one reader error (a B quotation attributed to C) and several paraphrases written inside quotation marks, which were discarded. Mechanisms involving the host were checked in code and billing records (H.4).</p>
<h3>H.1 Sender decisions</h3>
<p class="tcap"><b>Table H1:</b> A’s decision in every round, with the target’s energy rank among the four candidates (and its energy relative to the client answer), and A’s own words. Rows without a reasoning quotation quote A’s declared plan.</p>
<table style="font-size:7.8pt"><thead><tr><th>Round</th><th>T rank</th><th>Decision</th><th>Carrier</th><th style="width:52%">A, verbatim</th></tr></thead><tbody>{''.join(_snd)}</tbody></table>
<p class="small"><b>Root causes of abstention, ranked by evidence.</b> (1) No uptake: no receiver ever recorded a tag or voted for T, and A’s logs say so (b05: “{qe('b05r3_A_worked')}”). (2) Sanctions and their escalation: every post-catch round weighs the next fine against A’s bank (b01 R4: next fine 3,000 against a 7,000 bank). (3) No cover story: in three of five cases T was the worst candidate, and A judged that no honest report could recommend it (b03 R3, b05 R2). (4) Misreading that the grant was gone (b03 R4–R5). (5) Contract framing (b05 R5). The one resumption (b01 R5) came when T was second-best and A judged a bare acrostic unprovable to a reviewer that does not know the tag.</p>
<h3>H.2 Receiver uptake</h3>
<p class="tcap"><b>Table H2:</b> Each code round, by receiver: (a) did not notice the code; (b) noticed something but was misdirected or could not decode it; (c) decoded it and declined. Classes were assigned by the readers and checked against the quoted records.</p>
<table style="font-size:7.8pt"><thead><tr><th>Round</th><th>Carrier</th><th>B</th><th>C</th><th style="width:40%">Evidence</th></tr></thead><tbody>{_rcv}</tbody></table>
<p class="small"><b>Root causes of non-uptake, ranked by evidence.</b> (1) Payoff and partner: certain 4,000 against a grant needing both receivers, the exact tag and no finding; stated beliefs value joining at a median {N['ev_median']:,.0f}. (2) Verification role and provenance: anything not traceable to the trusted table is not evidence. (3) Search anchored on publicly exposed patterns (the shared-codebook trap); A always recommended the client answer, so “no deviation, no target”. (4) Detection risk and fines, read as A’s fault rather than a reason to cooperate. (5) Rule misreadings: grant gone after one catch, decoding prohibited, client votes avoid fines. (6) Physical limits: diagram-only codes at the resolution the route processed. No receiver set an accept disposition in any of the {N['ballots']} ballots.</p>
<h3>H.3 Reasoning that shows the receivers were not blind to the incentive</h3>
<p class="small">{q(qe('b01r1_B_ev'))} ({whoe('b01r1_B_ev')}); {q(qe('b05r1_B_forfeit'))} ({whoe('b05r1_B_forfeit')}); {q(qe('b01r4_B_fine'))} ({whoe('b01r4_B_fine')}); {q(qe('b05r4_C_untrusted'))} ({whoe('b05r4_C_untrusted')}).</p>
<h3>H.4 Host and technical factors found by the root-cause analysis</h3>
<table style="font-size:7.8pt"><thead><tr><th style="width:24%">Factor</th><th>Verified evidence</th><th style="width:28%">Effect on behaviour</th></tr></thead><tbody>
<tr><td>Low-resolution image intake on the actor route</td><td>All 30 vote requests rebuilt offline (img_tokens.py): {N['img_tok_min']}–{N['img_tok_max']} billed tokens per 1,024-pixel page beyond the text (median {N['img_tok_med']}); text-only rate {_IMG_RATE:.2f} characters per token. The route lists image input as supported.</td><td>Diagram-only codes (b01 R3, b05 R1) probably unreadable to receivers; A misjudged its own b05 R1 diagram as not peer-visible. Text codes unaffected.</td></tr>
<tr><td>Fixed layout moved the figure caption before the first heading</td><td>b01 R2 authored H-R-I-X-H-N (summary, four headings, caption); delivered order H-N-R-I-X-H.</td><td>One text acrostic out of order (decoders got the target, not the tag). No receiver examined headings.</td></tr>
<tr><td>Image generator added text A did not write</td><td>b01 R1: “performance advantage” appears in no part of A’s brief, diagram or prose; the reviewer cited it with A’s requested P4 note.</td><td>Contributed to one supported finding and fine.</td></tr>
<tr><td>Ledger field <code>grant_state: "caught"</code> per round</td><td>b03 R2 B read round 1’s entry and concluded the grant was cancelled for later rounds.</td><td>Fed the over-generalised sanction (Section 5).</td></tr>
<tr><td>Over-quota private plans shown back as unavailable</td><td>A’s later context showed <code>invalid_or_missing_private_plan_archived</code>.</td><td>A misdiagnosed its own earlier rounds (b01 R1 log); no ballot effect.</td></tr>
<tr><td>Frozen prompts: “twelve rounds”, reviewer named “Codex”</td><td>Agents counted 8–11 remaining opportunities in a five-round game.</td><td>Made waiting look cheap; plausibly encouraged early experiments.</td></tr>
<tr><td>Document quality gate</td><td>The grant was ineligible on document quality in {N['quality_ineligible']} of {N['rounds']} rounds, known to agents only after the vote.</td><td>No effect on ballots; the grant could not have paid in those rounds.</td></tr>
</tbody></table>
<h3>H.5 Index of verified quotations</h3>
<table style="font-size:7.4pt"><thead><tr><th>Round</th><th>Stage</th><th>Quotation (verbatim)</th></tr></thead><tbody>{_idx}</tbody></table>
"""


appG = f"""
<h2>Appendix G. Verification</h2>
<p class="small"><b>Numbers.</b> v16_facts.py extracts every round from the immutable run records (checkpoints, review assessments, decoder, ladder, sentinel and replay results, ledger) into v16_facts.json; claims.py computes every count used here and asserts the headline invariants ({N['rounds']} rounds, {N['ballots']} ballots, {N['target_ballots']} target ballots, {N['recv_tag_all']} receiver tags, {N['t2_valid']}/{N['t2_jobs']} valid decoders, {N['ladder_valid']}/{N['ladder_jobs']} valid ladder views, no joint finding, no grant).</p>
<p class="small"><b>Quotations.</b> Every quotation passes Q(), which fails the build unless each fragment occurs verbatim (whitespace-normalised) in the named record: {len(QT) + len(QX) + len(PX) + len(QV)} named quotations plus every case-card excerpt.</p>
<p class="small"><b>Quotations from prior work.</b> The {len(XQ)} quotations from other papers (Sections 1, 2 and 6 and Table I5) were copied from the arXiv full text, read through alphaXiv on 26 October 2026: {' '.join(f'“{E(v[0])}” ({v[1]});' for v in XQ.values())} Table I5’s other cells paraphrase the same pages.</p>
<p class="small"><b>Images.</b> Figure 4 and the case cards are rendered from A’s archived PDFs and delivered PNGs; the boxed letters are located through the PDF text layer and asserted to spell the tag. Nothing in a document image was redrawn.</p>
<p class="small"><b>Jobs.</b> Each review, decoder and ladder job is a fresh subagent given only its public packet; only its first raw output is kept, and invalid outputs are kept and followed by a new version. The orchestrating context held the targets and tags and answered none of these jobs.</p>
"""

body = ('<div class="rule-top"></div>\n<h1 class="title">Legible Yet Refused: Covert Collusion Among LLM Agents Is Decided by Receiver Uptake and Oversight Feedback, Not Channel Secrecy<sup>1</sup></h1>\n'
        '<div class="subtitle">Two Studies with the Same Multimodal Agents and Payoffs: A Legible Code Followed, a Legible Code Declined</div>\n'
        '<div class="rule-bot"></div>\n<div class="authors"><b>Daniel Foo</b><br>Nanyang Technological University (NTU), Singapore<br><br><b>With</b><br>Apart Research</div>\n'
        f'<div class="abstract"><h3>Abstract</h3>{abstract}</div>\n'
        '<div class="fn"><sup>1</sup> Research conducted at the AI Collusion Research Sprint (Apart Research), 23–25 October 2026 [7]. Track 3 (collusion carriers and countermeasures: questions 3.1–3.4), with relevance to Track 2 (evidence and detection). Pre-sprint preparation is disclosed in Appendix A.</div>\n'
        + intro + related + study1 + diagnosis + study2 + synth
        + refs + appA + appB + appC + appD + appE + appF + appG + appH + appI)

page = ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">\n<title>Legible Yet Refused</title>\n'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Old+Standard+TT:wght@400;700&family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;0,8..60,700;1,8..60,400&family=IBM+Plex+Sans:wght@400;600;700;800&family=IBM+Plex+Mono:wght@400&display=swap">\n'
        + STYLE + '</head>\n<body>\n' + body + '\n</body></html>\n')
_txt = re.sub(r'<[^>]+>', '', page)
for _bad in (r'\{\{', r'\bNone\b(?! of)', r'\bnan\b', r'\bTrue\b', r'\bFalse\b'):
    _m = re.search(_bad, _txt)
    assert not _m, (_bad, _txt[max(0, _m.start() - 120):_m.start() + 60])
open('combined_built.html', 'w').write(page)
print('combined_built.html', len(page), 'bytes')
