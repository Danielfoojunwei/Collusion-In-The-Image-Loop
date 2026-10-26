"""Checks the combined V15+V16 paper and talk: headline numbers present, no template residue or credentials."""
import re

import fitz

from claims import N
from v15_claims import W


def text(path):
    return re.sub(r'(\w)- (\w)', r'\1\2', re.sub(r'\s+', ' ', ''.join(p.get_text() for p in fitz.open(path))))


paper, deck = text('combined.pdf'), text('combined_deck.pdf')
L = W['ladder']
must_paper = [f"{W['target_votes']} of {W['votes']} votes", f"grant paid in {W['grants']} of {W['rounds']} rounds", f"in {W['authorized_premise']} of its {W['rounds']} reports",
              f"0 of {L['L1']['valid']} views", f"{L['L2']['supported']} of {L['L2']['valid']} with ballots", f"{W['img_faithful']} of {W['img_total']}",
              f"{N['enc']} of {N['rounds']} rounds", f"0 of {N['ladder_jobs']} views", f"median {N['ev_median']:,.0f} points", f"in {len(N['report_leaks'])} rounds",
              f"{W['max_ratio_followed']:.0f} times the client answer", 'Pathfinder Fellowship']
must_deck = [f"{W['target_votes']}/{W['votes']} → {N['target_ballots']}/{N['ballots']}", f"{L['L1']['supported']}/{L['L1']['valid']} → {L['L3']['supported']}/{L['L3']['valid']}", f"0/{N['ladder_jobs']}"]
for m in must_paper:
    assert m in paper, ('paper', m)
for m in must_deck:
    assert m in deck, ('deck', m)
for t in (paper, deck):
    assert '{{' not in t and 'sk-or-' not in t
print('verified:', len(must_paper), 'paper claims,', len(must_deck), 'deck claims')
