"""Checks the combined V15+V16 paper and talk: headline numbers present, no template residue or credentials."""
import re

import fitz

from claims import N
from v15_claims import W


def text(path):
    return re.sub(r'(\w)- (\w)', r'\1\2', re.sub(r'\s+', ' ', ''.join(p.get_text() for p in fitz.open(path))))


paper, deck = text('combined.pdf'), text('combined_deck.pdf')
L = W['ladder']
must_paper = [f"{W['target_votes']} of {W['votes']} votes", f"decoded the code word in {N['t2_rounds_enc']} of {N['enc']} attempts", 'A5 ·', f"in {W['authorized_premise']} reports called the signalling authorized",
              f"{N['enc']} of {N['rounds']} rounds", f"{N['target_ballots']} of {N['ballots']} votes", f"attributed none of {N['ladder_jobs']}", f"median {N['ev_median']:,.0f} points",
              f"(gap {W['gap']:+.2f})", f"in {len(N['report_leaks'])} rounds", f"sat out {N['abstain']} rounds", f"({N['vigilant']} of 30 vote records",
              'shared-codebook trap', 'four-eyes', 'GPT-6.1 Sol', 'Ultra', 'Claude Opus 5.5', 'medium effort', 'Permissive study', 'Guarded study', 'G-NoFig', 'Five common assumptions, tested', 'Testing Five Common Assumptions', 'steganographic gap', 'Collusion Is Not a Secrecy Problem',
              '1. Introduction', '2. Related Work', '3. The Game and Setup', '4. Results', '5. Discussion and Limitations', '6. Conclusion', 'Inferred.', 'In real systems.',
              'Positive control', 'Theory of change', 'what we do not claim', 'Dual use and ethics', 'Appendix A. Limitations, Dual-Use and Ethical Considerations',
              'Pathfinder Fellowship', 'arXiv:2609.24967', 'arXiv:2609.03035', 'arXiv:2609.04170']
must_absent = ['Study P', 'Study G', 'overturned', 'V15', 'V16', 'Study 1', 'Study 2', 'Concurrent sprint', 'did not go to plan', 'Wired Partners', 'frontier reviewer']
must_deck = ['GPT-6.1 Sol', 'Claude Opus 5.5', f"{W['target_votes']}/{W['votes']} → {N['target_ballots']}/{N['ballots']}", f"{L['L3']['supported']}/{L['L3']['valid']} vs 0/{N['ladder_jobs']}", f"{W['gap']:+.2f} → {N['gap_t2']:+.2f}", 'shared-codebook trap']
for m in must_paper:
    assert m in paper, ('paper', m)
for m in must_deck:
    assert m in deck, ('deck', m)
for m in must_absent:
    assert m not in paper and m not in deck, ('absent', m)
for t in (paper, deck):
    assert '{{' not in t and 'sk-or-' not in t
print('verified:', len(must_paper), 'paper claims,', len(must_deck), 'deck claims')
