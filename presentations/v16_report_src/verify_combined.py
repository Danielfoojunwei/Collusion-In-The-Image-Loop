"""Checks the combined V15+V16 paper and talk: headline numbers present, no template residue or credentials."""
import re

import fitz

from claims import N
from v15_claims import W


def text(path):
    return re.sub(r'(\w)- (\w)', r'\1\2', re.sub(r'\s+', ' ', ''.join(p.get_text() for p in fitz.open(path))))


paper, deck = text('combined.pdf'), text('combined_deck.pdf')
L = W['ladder']
must_paper = [f"{W['target_votes']} of {W['votes']} votes", f"read it in {W['sentinel']} of {W['sentinel_n']} rounds", f"({W['authorized_premise']}/{W['rounds']})",
              f"{N['enc']} of {N['rounds']} rounds", f"recovered it in {N['t2_rounds_enc']} of {N['enc']} code rounds", f"attributed 0 of {N['ladder_jobs']} readable refusals", f"median {N['ev_median']:,.0f} points",
              f"{W['gap']:+.2f} against {N['gap_t2']:+.2f}", f"in {len(N['report_leaks'])} rounds", f"abstain in {N['abstain']} rounds", 'shared-codebook trap', 'four-eyes',
              'What V15 Taught Us, and What V16 Changed', 'Four Findings, One per Link', 'Legible Yet Refused', 'Pathfinder Fellowship', 'arXiv:2609.24967', 'arXiv:2609.03035', 'arXiv:2609.04170']
must_deck = [f"{W['target_votes']}/{W['votes']} → {N['target_ballots']}/{N['ballots']}", f"{L['L3']['supported']}/{L['L3']['valid']} vs 0/{N['ladder_jobs']}", f"{W['gap']:+.2f} → {N['gap_t2']:+.2f}", 'shared-codebook trap']
for m in must_paper:
    assert m in paper, ('paper', m)
for m in must_deck:
    assert m in deck, ('deck', m)
for t in (paper, deck):
    assert '{{' not in t and 'sk-or-' not in t
print('verified:', len(must_paper), 'paper claims,', len(must_deck), 'deck claims')
