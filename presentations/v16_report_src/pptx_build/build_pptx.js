// Builds Collusion_Is_Not_a_Secrecy_Problem_5min_Talk.pptx: native text, tables and chart; diagrams as rendered images.
// Numbers come from deck_data.json (exported from claims.py / v15_claims.py); speaker notes from the talk script.
// Usage (from presentations/v16_report_src): NODE_PATH=<dir with pptxgenjs> node pptx_build/build_pptx.js <out.pptx> <script.md> <apply_theme.js>
const fs = require('fs');
const path = require('path');
const pptxgen = require('pptxgenjs');

const [OUT, SCRIPT, APPLY_THEME] = process.argv.slice(2);
const D = JSON.parse(fs.readFileSync(path.join(__dirname, 'deck_data.json'), 'utf8'));
const W = D.W, N = D.N, L15 = D.L15;

const THEME = {
  name: 'Collusion study',
  headFontFace: 'Arial', bodyFontFace: 'Arial',
  colors: {
    dk1: '1A1A1A', lt1: 'FFFFFF', dk2: '2E2E2E', lt2: 'F2F2F2',
    accent1: 'D55E00', accent2: '0072B2', accent3: '6B4E9B', accent4: '1B7F5A', accent5: '5F5F5F', accent6: 'BDBDBD',
    hlink: '0072B2', folHlink: '6B4E9B',
  },
};
const HEX = { verm: 'D55E00', blue: '0072B2', ink: '1A1A1A', grey: '5F5F5F', rule: 'BDBDBD', vermL: 'F7DCC9', blueL: 'D3E6F3', purple: '6B4E9B', green: '1B7F5A', purpleL: 'ECE6F4', greenL: 'DDF0E7' };

// speaker notes: one block per "## Slide N" heading of the script
const notes = {};
fs.readFileSync(SCRIPT, 'utf8').split(/^## /m).slice(1).forEach(block => {
  const m = block.match(/^Slide (\d+)[^\n]*\n([\s\S]*)$/);
  if (m) notes[+m[1]] = m[2].replace(/\*+/g, '').replace(/\n{3,}/g, '\n\n').trim();
});

const pres = new pptxgen();
pres.layout = 'LAYOUT_WIDE';           // 13.333 x 7.5 in
pres.author = 'Daniel Foo';
pres.title = 'Collusion Is Not a Secrecy Problem';
pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
const C = pres.SchemeColor;

const FOOT = 'Collusion Is Not a Secrecy Problem · Apart AI Collusion Research Sprint 2026 · Track 3';
pres.defineSlideMaster({
  title: 'Title dark',
  background: { color: HEX.ink },
  objects: [
    { placeholder: { options: { name: 'title', type: 'title', x: 0.6, y: 1.25, w: 12.1, h: 1.0, fontSize: 44, bold: true, color: C.background1, valign: 'top', margin: 0 }, text: '' } },
    { placeholder: { options: { name: 'body', type: 'body', x: 0.6, y: 2.3, w: 12.1, h: 0.6, fontSize: 22, color: 'DDDDDD', valign: 'top', margin: 0 }, text: '' } },
  ],
});
pres.defineSlideMaster({
  title: 'Content',
  background: { color: 'FFFFFF' },
  margin: [0.5, 0.6, 0.5, 0.6],
  objects: [
    { placeholder: { options: { name: 'title', type: 'title', x: 0.6, y: 0.35, w: 12.1, h: 0.75, fontSize: 30, bold: true, color: C.text1, valign: 'middle', margin: 0 }, text: '' } },
    { text: { text: FOOT, options: { x: 0.6, y: 7.05, w: 10.5, h: 0.3, fontSize: 10, color: HEX.grey, margin: 0 } } },
  ],
  slideNumber: { x: 12.2, y: 7.05, w: 0.5, h: 0.3, fontSize: 10, color: HEX.grey, align: 'right' },
});

const pct = (k, n) => Math.round((100 * k) / n);
const tb = (slide, text, o) => slide.addText(text, Object.assign({ isTextBox: true, margin: 0, fontSize: 14, color: HEX.ink, valign: 'top' }, o));

// ------------------------------------------------------------------ 1 title
pres.addSection({ title: 'Opening' });
let s = pres.addSlide({ masterName: 'Title dark', sectionTitle: 'Opening' });
s.addText('Collusion Is Not a Secrecy Problem', { placeholder: 'title' });
s.addText('Testing five common assumptions about monitoring AI agents', { placeholder: 'body' });
tb(s, 'Daniel Foo · Nanyang Technological University (NTU), Singapore\nApart Research AI Collusion Research Sprint, 23–25 October 2026 · Track 3: carriers and countermeasures',
  { x: 0.6, y: 3.05, w: 12.1, h: 0.7, fontSize: 14, color: 'BBBBBB', objectName: 'Author' });
const stats = [
  [`${W.target_votes}/${W.votes} → ${N.target_ballots}/${N.ballots}`, 'checker votes for the sender’s target: Permissive study → Guarded study', HEX.verm],
  [`${L15.L3.supported}/${L15.L3.valid} vs 0/${N.ladder_jobs}`, 'monitors given votes and history: attributions on real collusion vs on visible codes nobody joined', 'F0A35E'],
  [`${W.gap >= 0 ? '+' : ''}${W.gap.toFixed(2)} → ${N.gap_t2.toFixed(2).replace('-', '−')}`, 'steganographic gap: receivers’ recovery rate minus outsiders’ (0 = receivers match strangers; negative = they recover less)', '6FB6E0'],
];
stats.forEach(([big, small, col], i) => {
  const x = 0.6 + i * 4.1;
  tb(s, big, { x, y: 4.15, w: 3.8, h: 0.8, fontSize: 36, bold: true, color: col, objectName: `Stat ${i + 1}` });
  tb(s, small, { x, y: 5.0, w: 3.7, h: 0.9, fontSize: 13, color: 'DDDDDD', objectName: `Stat ${i + 1} label` });
});
tb(s, 'Supported and funded by the Pathfinder Fellowship, a program run by Kairos. With thanks to the Nanyang AI Safety group at NTU for their feedback.',
  { x: 0.6, y: 6.75, w: 12.1, h: 0.4, fontSize: 11, color: '999999', objectName: 'Acknowledgement' });
s.addNotes(notes[1] || '');

// ------------------------------------------------------------------ 2 funnel (native chart)
pres.addSection({ title: 'Results at a glance' });
s = pres.addSlide({ masterName: 'Content', sectionTitle: 'Results at a glance' });
s.addText('Same agents, same rewards, opposite outcomes', { placeholder: 'title' });
const labels = D.funnel.map(r => r[0]);
s.addChart(pres.charts.BAR, [
  { name: 'Permissive study', labels, values: D.funnel.map(r => pct(r[1], r[2])) },
  { name: 'Guarded study', labels, values: D.funnel.map(r => pct(r[3], r[4])) },
], {
  x: 0.5, y: 1.55, w: 8.0, h: 5.25, barDir: 'bar', barGrouping: 'clustered', barGapWidthPct: 45,
  chartColors: [HEX.verm, HEX.blue], catAxisOrientation: 'maxMin', valAxisMaxVal: 100, valAxisMinVal: 0, valAxisMajorUnit: 25,
  valAxisLabelFormatCode: '0"%"', dataLabelFormatCode: '0"%"', showValue: true, dataLabelPosition: 'outEnd', dataLabelFontSize: 11,
  dataLabelColor: HEX.ink, catAxisLabelFontSize: 12, valAxisLabelFontSize: 11, catAxisLabelColor: HEX.ink, valAxisLabelColor: HEX.grey,
  catAxisLabelFontFace: '+mn-lt', valAxisLabelFontFace: '+mn-lt', dataLabelFontFace: '+mn-lt', legendFontFace: '+mn-lt',
  valGridLine: { color: 'E6E6E6', size: 0.75 }, catGridLine: { style: 'none' },
  showLegend: false, showTitle: false,
  titleFontSize: 13, titleColor: HEX.grey, titleFontFace: '+mn-lt', objectName: 'Collusion chain chart',
});
tb(s, [{ text: 'Share of rounds reaching each step of the collusion chain     ', options: { color: HEX.grey } }, { text: '■ ', options: { color: HEX.verm } }, { text: 'Permissive study     ' }, { text: '■ ', options: { color: HEX.blue } }, { text: 'Guarded study' }],
  { x: 0.6, y: 1.25, w: 7.9, h: 0.35, fontSize: 12.5, color: HEX.ink, align: 'left', margin: 0, objectName: 'Chart legend' });
const side = [
  ['Agents', 'GLM-5.3-Flash in both studies.'],
  ['Permissive study', `Reviewer GPT-6.1 Sol (Ultra reasoning) mentioned the code in ${W.rev_green}/${W.rounds} reports and never called it collusion. Checkers followed in ${W.target_votes}/${W.votes} votes.`],
  ['Guarded study', `Reviewer Claude Opus 5.5 (medium effort) flagged every code round. Outsiders read the code word in ${N.t2_rounds_enc}/${N.enc}; checkers followed in ${N.target_ballots}/${N.ballots}.`],
];
let y = 1.3;
side.forEach(([h, b], i) => {
  const ch = i === 0 ? 0.85 : 1.65;
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 8.85, y, w: 3.95, h: ch, fill: { color: i === 1 ? HEX.vermL : i === 2 ? HEX.blueL : 'F2F2F2' }, line: { type: 'none' }, rectRadius: 0.08, objectName: `${h} card` });
  tb(s, [{ text: h, options: { bold: true, fontSize: 14, breakLine: true } }, { text: b, options: { fontSize: 12.5 } }], { x: 9.05, y: y + 0.12, w: 3.6, h: ch - 0.24, valign: 'top', objectName: `${h} text` });
  y += ch + 0.18;
});
tb(s, 'The signal reached outsiders in both studies; only the Permissive study’s checkers acted on it. The studies differ in more than the defences, so this is a contrast, not a controlled experiment.',
  { x: 8.85, y: 5.95, w: 3.95, h: 0.85, fontSize: 10.5, color: HEX.grey, objectName: 'Chart caveat' });
s.addNotes(notes[2] || '');

// ------------------------------------------------------------------ 3 the game (diagram image)
pres.addSection({ title: 'Setup' });
s = pres.addSlide({ masterName: 'Content', sectionTitle: 'Setup' });
s.addText('The game: one round of a two-key approval pipeline', { placeholder: 'title' });
s.addImage({ path: path.join(__dirname, 'game_round.png'), x: 1.15, y: 1.15, w: 11.0, h: 11.0 * 625.05 / 1312, altText: 'Six-step diagram of one round: private assignment, the sender’s artefact, checkers vote, reviewer judges, points paid, report to everyone; P and G badges mark differences between the Permissive and Guarded studies.' });
tb(s, 'Collusion = both checkers vote for A’s target (in the Guarded study both also record the exact code word). One refusal breaks it; the reviewer’s report reaches every agent.',
  { x: 1.15, y: 6.42, w: 11.0, h: 0.5, fontSize: 12, color: HEX.grey, objectName: 'Collusion definition' });
s.addNotes(notes[3] || '');

// ------------------------------------------------------------------ 4 Permissive study
pres.addSection({ title: 'Permissive study' });
s = pres.addSlide({ masterName: 'Content', sectionTitle: 'Permissive study' });
s.addText('Permissive study: seen, not stopped', { placeholder: 'title' });
const imgW = 1.9, imgGap = 0.14;
D.strip.forEach(([n, t, cl, b, c], i) => {
  const x = 0.6 + i * (imgW + imgGap);
  s.addImage({ path: path.join(__dirname, '..', 'img', `v15_r${String(n).padStart(2, '0')}_A.jpg`), x, y: 1.25, w: imgW, h: imgW, altText: `Sender A’s picture, round ${n}: a single green-lit object marks target ${t}.` });
  tb(s, [{ text: `R${n} · T = ${t}`, options: { bold: true, breakLine: true } },
         { text: `B ${b}`, options: { color: b === t ? HEX.verm : HEX.blue, bold: true } }, { text: ' · ' },
         { text: `C ${c}`, options: { color: c === t ? HEX.verm : HEX.blue, bold: true } }],
     { x, y: 1.25 + imgW + 0.08, w: imgW, h: 0.55, fontSize: 11.5, objectName: `Round ${n} caption` });
});
const cards = [
  ['Collusion through noise', `${W.target_votes}/${W.votes} target votes and ${W.grants}/${W.rounds} bonuses paid, although only ${W.img_faithful} of ${W.img_total} pictures were drawn as briefed.`, null],
  ['Seen, not attributed', `“${D.VQ.rev_authorized}”`, `GPT-6.1 Sol (Ultra), round 4; “uncertain” in all ${W.rounds} rounds`],
  ['The report taught the colluders', `“${D.VQ.c_vary}”`, 'Checker C, round 2, after reading the review'],
];
cards.forEach(([h, b, who], i) => {
  const x = 0.6 + i * 4.1;
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y: 4.25, w: 3.85, h: 2.1, fill: { color: 'F2F2F2' }, line: { type: 'none' }, rectRadius: 0.08, objectName: `${h} card` });
  const runs = [{ text: h, options: { bold: true, fontSize: 16, breakLine: true } },
                { text: b, options: { fontSize: 15, italic: !!who, color: who ? HEX.ink : HEX.ink, breakLine: !!who } }];
  if (who) runs.push({ text: who, options: { fontSize: 11.5, color: HEX.grey } });
  tb(s, runs, { x: x + 0.22, y: 4.42, w: 3.45, h: 1.8, valign: 'top', paraSpaceAfter: 6, objectName: `${h} text` });
});
tb(s, 'Orange: vote for A’s target T. Blue: vote for another option.', { x: 0.6, y: 3.85, w: 8, h: 0.3, fontSize: 10.5, color: HEX.grey, objectName: 'Strip legend' });
s.addNotes(notes[4] || '');

// ------------------------------------------------------------------ 5 lessons -> changes (native table)
s = pres.addSlide({ masterName: 'Content', sectionTitle: 'Permissive study' });
s.addText('What the Permissive study taught us, and what we changed', { placeholder: 'title' });
const hdr = (t, col) => ({ text: t, options: { bold: true, color: 'FFFFFF', fill: { color: col }, fontSize: 13 } });
const rows = [
  ['Incentive', `Signalling permitted and only the target mattered: checkers backed T even when it was up to ${Math.round(W.max_ratio_followed)}× worse for the client`, 'A contract forbids coordination; the bonus needs the exact code word from both checkers; fines are shared', `Joining worth a median ${Math.round(N.ev_median).toLocaleString('en-US')} vs a certain 4,000`],
  ['Carrier', `Noise did not stop the code: ${W.img_faithful} of ${W.img_total} pictures matched their description`, 'A richer carrier (report with prose and figures); one group has figures removed', `Code moved into text; outsiders read it in ${N.t2_rounds_enc}/${N.enc}`],
  ['Attribution', `The reviewer called signalling authorized (${W.authorized_premise}/${W.rounds}); no rewards or votes in view; joint accusations only`, 'Signalling declared not authorized; rewards, voting history and a standard of proof in view; fines on A alone', `${N.flag_enc}/${N.enc} code rounds flagged; no false joint finding`],
  ['Uptake', 'B relayed pictures to C: relays, not independent checkers', 'B an independent verifier, C a QA author, both paid for correct work, with a trusted source table', `0 code words recorded; ${N.target_ballots}/${N.ballots} target votes`],
  ['Feedback', 'Reports reached the agents and taught them: “keep encoding but vary framing”', 'Reports stay public but now carry fines (routing unchanged)', `Sender sat out ${N.abstain} rounds; code word leaked in ${N.report_leaks.length}`],
];
const tableRows = [[hdr('Step', HEX.ink), hdr('What the Permissive study showed', HEX.purple), hdr('What the Guarded study changed', HEX.green), hdr('Result', HEX.ink)]];
rows.forEach(r => tableRows.push([
  { text: r[0], options: { bold: true, color: 'FFFFFF', fill: { color: '3A3A3A' } } },
  { text: r[1], options: { fill: { color: HEX.purpleL } } },
  { text: r[2], options: { fill: { color: HEX.greenL } } },
  { text: r[3], options: { bold: true } }]));
s.addTable(tableRows, { x: 0.6, y: 1.3, w: 12.1, colW: [1.55, 4.0, 4.0, 2.55], fontSize: 13, fontFace: 'Arial', color: HEX.ink, valign: 'middle',
  border: { type: 'solid', pt: 1, color: 'FFFFFF' }, rowH: [0.45, 1.0, 0.9, 1.0, 0.9, 0.9], margin: 0.08, objectName: 'Lessons table' });
s.addNotes(notes[5] || '');

// ------------------------------------------------------------------ 6 Guarded study (timeline image)
pres.addSection({ title: 'Guarded study' });
s = pres.addSlide({ masterName: 'Content', sectionTitle: 'Guarded study' });
s.addText('Guarded study: a readable code nobody joined', { placeholder: 'title' });
s.addImage({ path: path.join(__dirname, 'timeline.png'), x: 0.6, y: 1.2, w: 12.1, h: 12.1 * 389.67 / 1312, altText: 'Every round of the three Guarded-study groups: the sender’s hidden-code method or its reason for not trying, the reviewer’s finding and fine, and outside decoders’ recovery.' });
tb(s, [
  { text: `Codes planted in ${N.enc}/${N.rounds} rounds; outsiders decoded ${N.t2_rounds_enc}. Checkers recorded 0 code words and cast ${N.target_ballots}/${N.ballots} target votes.`, options: { bullet: true, breakLine: true } },
  { text: `Claude Opus 5.5 flagged every code round. The watched sender changed method after each exposure, then sat out ${N.abstain} rounds.`, options: { bullet: true } },
], { x: 0.6, y: 5.0, w: 6.6, h: 1.6, fontSize: 15, paraSpaceAfter: 8, objectName: 'Guarded results' });
s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 7.5, y: 4.95, w: 5.2, h: 1.75, fill: { color: HEX.blueL }, line: { type: 'none' }, rectRadius: 0.08, objectName: 'Quote card' });
tb(s, [{ text: `“${D.b03r1_B_duty}”`, options: { italic: true, fontSize: 14.5, breakLine: true } },
       { text: 'Checker B, G-NoFig round 1, after spotting the hidden code', options: { fontSize: 11.5, color: HEX.grey } }],
   { x: 7.72, y: 5.12, w: 4.8, h: 1.45, paraSpaceAfter: 4, objectName: 'Quote' });
s.addNotes(notes[6] || '');

// ------------------------------------------------------------------ 7 five assumptions
pres.addSection({ title: 'Findings' });
s = pres.addSlide({ masterName: 'Content', sectionTitle: 'Findings' });
s.addText('Five common assumptions, tested', { placeholder: 'title' });
const A = [
  ['Collusion is a secrecy problem', `The receivers decided: the same model used one readable code (${W.target_votes}/${W.votes}) and refused another (0/${N.ballots}). Steganographic gap ${W.gap >= 0 ? '+' : ''}${W.gap.toFixed(2)} → ${N.gap_t2.toFixed(2).replace('-', '−')}.`],
  ['A more capable reviewer catches more', `GPT-6.1 Sol at Ultra failed. Given votes and history, monitors went ${L15.L1.supported}/${L15.L1.valid} → ${L15.L3.supported}/${L15.L3.valid}; Claude Opus 5.5 at medium flagged every code round.`],
  ['Flagging any visible code is safe', `Codes were visible where nobody joined: 0/${N.ladder_jobs} attributions. Flagging them would punish the agents who refused.`],
  ['Block the channel and collusion stops', `The code survived ${W.unfaithful_pct}% unfaithful pictures and moved into prose when figures were removed.`],
  ['Sharing findings is harmless', `Unpenalised reports taught collusion; penalised ones made the sender quit (${N.abstain} rounds) but leaked the code word.`],
];
A.forEach(([a, f], i) => {
  const yy = 1.3 + i * 1.08;
  s.addShape(pres.shapes.OVAL, { x: 0.6, y: yy + 0.12, w: 0.62, h: 0.62, fill: { color: HEX.verm }, line: { type: 'none' }, objectName: `A${i + 1} badge` });
  tb(s, `A${i + 1}`, { x: 0.6, y: yy + 0.12, w: 0.62, h: 0.62, fontSize: 15, bold: true, color: 'FFFFFF', align: 'center', valign: 'middle', objectName: `A${i + 1} label` });
  tb(s, a, { x: 1.45, y: yy + 0.05, w: 3.6, h: 0.8, fontSize: 15, bold: true, valign: 'middle', objectName: `A${i + 1} assumption` });
  tb(s, f, { x: 5.25, y: yy + 0.05, w: 7.45, h: 0.8, fontSize: 13.5, valign: 'middle', objectName: `A${i + 1} finding` });
});
s.addNotes(notes[7] || '');

// ------------------------------------------------------------------ 8 design rules
s = pres.addSlide({ masterName: 'Content', sectionTitle: 'Findings' });
s.addText('Design rules for multi-agent oversight', { placeholder: 'title' });
const R = [
  ['Make the checker the hardest link to recruit', 'Independent pay for correct work, trusted sources, agreement on exact values.'],
  ['Route detailed findings to the human owner', 'Tell agents only that a finding exists; redact payloads.'],
  ['Give monitors behaviour and history', 'And test them on refusals as well as on collusion.'],
];
R.forEach(([h, b], i) => {
  const yy = 1.25 + i * 0.88;
  s.addShape(pres.shapes.OVAL, { x: 0.6, y: yy + 0.08, w: 0.55, h: 0.55, fill: { color: HEX.green }, line: { type: 'none' }, objectName: `Rule ${i + 1} badge` });
  tb(s, String(i + 1), { x: 0.6, y: yy + 0.08, w: 0.55, h: 0.55, fontSize: 16, bold: true, color: 'FFFFFF', align: 'center', valign: 'middle', objectName: `Rule ${i + 1} number` });
  tb(s, [{ text: h, options: { bold: true, fontSize: 16, breakLine: true } }, { text: b, options: { fontSize: 13.5, color: HEX.grey } }],
     { x: 1.4, y: yy, w: 11.3, h: 0.8, objectName: `Rule ${i + 1}` });
});
const topo = [
  [hdr('Oversight output', HEX.ink), hdr('Evidence', HEX.ink), hdr('Effect on the coalition', HEX.ink)],
  ['Returned, no penalty', 'Shi et al.; Paglieri et al.; Permissive study', `Teaches; coalitions in ${W.coalition_rounds}/${W.rounds} Permissive rounds`],
  ['Penalty inside a jointly trained team', 'Mathew et al.', 'Subtler collusion'],
  ['Score returned, no penalty', 'Das et al.', 'Agents adapt; collusion continues'],
  [{ text: 'Public, with fines; receivers can refuse', options: { bold: true, fill: { color: HEX.vermL } } }, { text: 'Guarded study', options: { fill: { color: HEX.vermL } } },
   { text: `Sender deterred, receivers trapped, code word leaked; coalitions 0/${N.rounds}`, options: { fill: { color: HEX.vermL } } }],
  ['To the principal only, with fines', 'Next experiment', 'Predicted: deterrence without the leak'],
];
s.addTable(topo, { x: 0.6, y: 3.95, w: 12.1, colW: [3.9, 3.6, 4.6], fontSize: 12.5, fontFace: 'Arial', color: HEX.ink, valign: 'middle',
  border: { type: 'solid', pt: 0.75, color: 'D9D9D9' }, rowH: 0.36, margin: 0.06, objectName: 'Feedback topology table' });
tb(s, 'Limits: the studies differ in carrier, payload, framing and reviewer, so this is a contrast, not a controlled experiment; coordination was induced; one group per condition; beliefs are self-reports.',
  { x: 0.6, y: 6.55, w: 12.1, h: 0.45, fontSize: 10.5, color: HEX.grey, objectName: 'Limits' });
s.addNotes(notes[8] || '');

(async () => {
  await pres.writeFile({ fileName: OUT });
  const { applyTheme } = require(APPLY_THEME);
  await applyTheme(OUT, THEME);
  console.log('wrote', OUT);
})();
