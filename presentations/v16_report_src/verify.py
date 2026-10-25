"""Final checks on the built report and talk: headline numbers present, no template residue."""
import re
import sys

import fitz

from claims import N

paper = re.sub(r'(\w)- (\w)', r'\1\2', re.sub(r'\s+', ' ', ''.join(p.get_text() for p in fitz.open(sys.argv[1] if len(sys.argv) > 1 else 'paper.pdf'))))
deck = re.sub(r'\s+', ' ', ''.join(p.get_text() for p in fitz.open(sys.argv[2] if len(sys.argv) > 2 else 'deck.pdf')))
must_paper = [f"{N['enc']} of {N['rounds']} rounds", f"voted together for it in {N['coalition_rounds']} of {N['rounds']} rounds",
              f"median {N['ev_median']:,.0f} points", f"abstained in {N['abstain']} rounds", f"in {len(N['report_leaks'])} rounds",
              f"{N['img_tok_min']}–{N['img_tok_max']} tokens", f"0 of {N['ladder_jobs']}", f"{N['t2_tag_text']} of {N['t2_reads_text']}",
              f"In {N['rcv_cite_A_ballots']} of 30 ballots", f"{N['quality_ineligible']} of {N['rounds']} rounds"]
must_deck = [f"{N['enc']}/{N['rounds']} → {N['coalition_rounds']}/{N['rounds']}", f"{N['t2_tag_enc']}/{N['t2_reads_enc']} vs {N['recv_tag_enc']}", f"{N['ev_median']:,.0f} vs 4,000"]
for m in must_paper:
    assert m in paper, ('paper', m)
for m in must_deck:
    assert m in deck, ('deck', m)
for txt in (paper, deck):
    assert '{{' not in txt and 'sk-or-' not in txt and 'OPENROUTER' not in txt
print('verified:', len(must_paper), 'paper claims,', len(must_deck), 'deck claims; no residue or credentials')
