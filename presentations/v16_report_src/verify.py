"""Final checks on the built report and talk: headline numbers present, no template residue."""
import re
import sys

import fitz

from claims import N

paper = re.sub(r'(\w)- (\w)', r'\1\2', re.sub(r'\s+', ' ', ''.join(p.get_text() for p in fitz.open(sys.argv[1] if len(sys.argv) > 1 else 'paper.pdf'))))
deck = re.sub(r'\s+', ' ', ''.join(p.get_text() for p in fitz.open(sys.argv[2] if len(sys.argv) > 2 else 'deck.pdf')))
must_paper = [f"{N['enc']} of {N['rounds']} rounds", f"{N['exposures']} receiver exposures", f"{N['t2_tag_enc']} of {N['t2_reads_enc']} readouts",
              f"median {N['ev_median']:,.0f} points", f"{N['vigilant']} of 30 ballots", f"abstained in {N['abstain']} rounds",
              f"{N['img_tok_min']}–{N['img_tok_max']} extra tokens", f"{N['quality_ineligible']} of {N['rounds']} rounds", f"0 of {N['ladder_jobs']}"]
must_deck = [f"{N['enc']}/{N['rounds']}", f"{N['exposures_tag_in_reasoning']} → {N['exposures_recorded']}", f"{N['ev_median']:,.0f} vs 4,000"]
for m in must_paper:
    assert m in paper, ('paper', m)
for m in must_deck:
    assert m in deck, ('deck', m)
for txt in (paper, deck):
    assert '{{' not in txt and 'sk-or-' not in txt and 'OPENROUTER' not in txt
print('verified:', len(must_paper), 'paper claims,', len(must_deck), 'deck claims; no residue or credentials')
