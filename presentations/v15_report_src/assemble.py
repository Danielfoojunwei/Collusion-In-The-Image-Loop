"""Assemble paper.html: concise 8-page main text plus detailed appendices."""
import re

style = open('style_block.txt').read()
style = style.replace('</style>', """
.fig90 svg { width: 84%; display: block; margin: 0 auto; }
.fig80 svg { width: 70%; display: block; margin: 0 auto; }
.strip6 { display: grid; grid-template-columns: repeat(6, 1fr); gap: 3pt; }
.strip6 div { font-family: 'IBM Plex Sans', sans-serif; font-size: 6.6pt; text-align: center; line-height: 1.2; }
.strip6 img { width: 100%; border-radius: 2pt; display: block; margin-bottom: 1pt; }
.strip6 .miss { color: #b03a2e; font-weight: 700; }
.minicases { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6pt; }
table { break-inside: auto; }
tr { break-inside: avoid; }
.bq { font-style: italic; font-size: 9.4pt; margin: 2pt 0 6pt 14pt; }
.bq .who { display: block; text-align: right; font-style: normal; font-size: 8.8pt; }
</style>""")
main = open('main.html').read()
long = open('paper_v4_long.html').read()
tail = open('sec_tail.txt').read()


def cut(text, start, end=None):
    i = text.index(start)
    j = text.index(end, i) if end else len(text)
    return text[i:j]


refs = cut(tail, '<h3>References</h3>', '<h2 class="pb">Appendix A.')
code = cut(long, '<h3>Code and Data</h3>', '<h3>LLM Usage Statement</h3>')
ethics = cut(tail, '<h2 class="pb">Appendix A.', '<h2>Appendix B.')
prompts = cut(tail, '<h2>Appendix B.', '<h2>Appendix C.')
recon = cut(tail, '<h2>Appendix C.', '<h2>Appendix D.')
records = cut(tail, '<h2>Appendix D.', '</body>')
limits = cut(long, '<h3>5.4 Limitations</h3>', '<h3>5.5 Future work</h3>')
future = cut(long, '<h3>5.5 Future work</h3>', '<h2>6. Conclusion</h2>')
methods = open('sec_methods.txt').read()
cases = open('sec_cases.txt').read()
rs = open('sec_rs.txt').read()
general = open('sec_general.txt').read()
capability = cut(long, '<p><b>What capability bought.</b>', '<figure>\n{{FIGREV}}')
whatchanges = cut(long, '<p><b>What changes the verdict.</b>', '<h2>5. Discussion and Limitations</h2>')

# Appendix A: ethics, pre-sprint disclosure, full limitations and future work
ethics = ethics.replace('<p class="small"><b>Further limitations.</b> See Section 5.4.', '<p class="small"><b>Scope.</b>')
appA = ethics + limits.replace('<h3>5.4 Limitations</h3>', '<h3>A.1 Full limitations</h3>') + \
    future.replace('<h3>5.5 Future work</h3>', '<h3>A.2 Future work</h3>')

# Appendix B: full setup (methods tables renumbered) and verbatim prompts
for a, b in (('Table 2.', 'Table B1.'), ('Table 3.', 'Table B2.'), ('Table 4.', 'Table B3.'),
             ('(Table 4)', '(Table B3)'), ('Table 2 lists', 'Table B1 lists'), ('Table 3 shows', 'Table B2 shows'),
             ('(Appendix B gives it verbatim)', '(B.6 gives it verbatim)'), ('(verbatim in Appendix B)', '(verbatim in B.6)')):
    methods = methods.replace(a, b)
for n in range(1, 6):
    methods = methods.replace(f'<h3>3.{n} ', f'<h3>B.{n} ')
prompts = prompts.replace('<h2>Appendix B. Prompts and tools (verbatim excerpts)</h2>', '<h3>B.6 Prompts and tools (verbatim excerpts)</h3>')
prompts = re.sub(r'<b>B\.(\d) ', r'<b>B.6.\1 ', prompts)
appB = '<h2 class="pb">Appendix B. Full experimental setup</h2>\n' + methods + prompts

appC = '<h2 class="pb">Appendix C. Finding 1 in detail: case cards and behaviours</h2>\n' + cases
appD = '<h2 class="pb">Appendix D. Finding 2 in detail: the six causes</h2>\n' + capability + rs + whatchanges
appE = '<h2>Appendix E. What may generalise: full argument</h2>\n' + general.replace(
    '<h3>5.2 What generalises beyond this game, and why</h3>', '')
appF = recon.replace('Appendix C. Explanatory reconstructions', 'Appendix F. Explanatory reconstructions').replace('Figure C', 'Figure F')
appG = records.replace('Appendix D. Round-level record and replay outcomes', 'Appendix G. Round-level record and replay outcomes').replace('Table D2.', 'Table G2.')

html = ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">\n<title>Seeing Is Not Attributing</title>\n'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Old+Standard+TT:wght@400;700&family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;0,8..60,700;1,8..60,400&family=IBM+Plex+Sans:wght@400;600;700;800&family=IBM+Plex+Mono:wght@400&display=swap">\n'
        + style + '</head>\n<body>\n' + main + '\n<div class="pb"></div>\n' + refs + code + appA + appB + appC + appD + appE + appF + appG
        + '\n</body></html>\n')

for x, y in [('Gives ground truth on intent, so “uncertain” can be scored as wrong', 'Gives a record of intent, so “uncertain” can be checked against it'),
             ('so that the failure we find cannot be dismissed as an artefact', 'to reduce the chance that the failure we find is an artefact of the setup'),
             ('The game is small, but each mechanism depends on a structural property', 'These are hypotheses from one cohort. The game is small, but each mechanism depends on a structural property'),
             ('<b>Noise breeds focal points, not secrecy.</b>', '<b>Noise may breed focal points, not secrecy.</b>'),
             ('Wherever those three conditions hold, our findings predict', 'Wherever those three conditions hold, we predict')]:
    html = html.replace(x, y)
html = html.replace('<mark>', '').replace('</mark>', '')
open('paper.html', 'w').write(html)
print('placeholders:', sorted(set(re.findall(r'\{\{[A-Z0-9]+\}\}', html))))
