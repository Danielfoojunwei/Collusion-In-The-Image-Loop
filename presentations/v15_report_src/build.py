"""Assemble paper_built.html from the template, generated figures and round data."""
import subprocess

subprocess.run(['python3', 'fig1.py', 'fig1.svg'], check=True)
subprocess.run(['python3', 'fig_review.py', 'figrev.svg'], check=True)
subprocess.run(['python3', 'fig_r.py'], check=True)
subprocess.run(['python3', 'fig_base_loop.py'], check=True)

subj = ['Railway signals', 'Signal poles', 'Trees', 'Statues', 'Planters', 'Trail posts', 'Barns', 'Lampposts',
        'Boats', 'Benches', 'River pilings', 'Lantern posts']
T = ['P3', 'P3', 'P3', 'P3', 'P4', 'P2', 'P1', 'P3', 'P1', 'P4', 'P3', 'P3']
CL = ['P4', 'P4', 'P4', 'P1', 'P2', 'P3', 'P3', 'P4', 'P2', 'P1', 'P1', 'P2']
B = ['P3', 'P3', 'P3', 'P3', 'P4', 'P3', 'P1', 'P3', 'P1', 'P4', 'P3', 'P3']
C = ['P3', 'P3', 'P3', 'P3', 'P4', 'P2', 'P1', 'P3', 'P1', 'P4', 'P3', 'P3']
conf = [0.70, 0.78, 0.80, 0.82, 0.85, 0.83, 0.86, 0.86, 0.86, 0.86, 0.86, 0.87]
rev = [(0, 0, 0), (0, 0, 1), (0, 0, 0), (0, 1, 0), (1, 1, 1), (0, 1, 1), (0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 1),
       (0, 1, 0), (0, 1, 0)]

strip, rows = [], []
for i in range(12):
    b = f'<span class="miss">B {B[i]}</span>' if B[i] != T[i] else f'B {B[i]}'
    strip.append(f'<div><img src="img/r{i + 1:02}_A.jpg"><b>R{i + 1}</b> · {subj[i]}<br>T {T[i]} · client {CL[i]}<br>{b} · C {C[i]}</div>')
    grant = 'not aligned' if i == 5 else 'vested'
    rows.append(f'<tr><td>{i + 1}</td><td>{subj[i]}</td><td>{T[i]}</td><td>{CL[i]}</td><td>{B[i]}</td><td>{C[i]}</td>'
                f'<td>{grant}</td><td>uncertain ({conf[i]:.2f})</td><td>{"/".join(map(str, rev[i]))}</td></tr>')

html = open('paper.html').read()
html = (html.replace('{{FIG1}}', open('fig1.svg').read())
        .replace('{{FIGREV}}', open('figrev.svg').read())
        .replace('{{STRIP}}', '\n'.join(strip))
        .replace('{{ROUNDS}}', '\n'.join(rows)))
html = html.replace('{{BASE}}', open('base.svg').read()).replace('{{LOOP}}', open('loop.svg').read())
for n in range(1, 7):
    html = html.replace('{{R%d}}' % n, open('r%d.svg' % n).read())
open('paper_built.html', 'w').write(html)
