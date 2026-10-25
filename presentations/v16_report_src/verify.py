"""Final checks on the built report and talk: headline numbers present, no template residue."""
import re
import sys

import fitz

from claims import N

paper = re.sub(r'\s+', ' ', ''.join(p.get_text() for p in fitz.open(sys.argv[1] if len(sys.argv) > 1 else 'paper.pdf')))
deck = re.sub(r'\s+', ' ', ''.join(p.get_text() for p in fitz.open(sys.argv[2] if len(sys.argv) > 2 else 'deck.pdf')))
must_paper = [f"{N['enc']} of {N['rounds']} rounds", f"{N['target_ballots']} of {N['ballots']} ballots", f"{N['t2_tag_enc']} of {N['t2_reads_enc']}",
              f"{N['recv_tag_enc']} of {N['recv_reads_enc']}", f"{N['gap_t2']:+.2f}".replace('-', '−') if False else f"{N['gap_t2']:+.2f}",
              f"0 of {N['ladder_jobs']}", f"{N['flag_nonenc']} of {N['nonenc']}"]
must_deck = [f"{N['enc']}/{N['rounds']}", f"{N['recv_tag_enc']}/{N['recv_reads_enc']} · {N['target_ballots']}/{N['ballots']}", f"{N['ev_median']:,.0f} vs 4,000"]
must_paper += [f"median {N['ev_median']:,.0f} points", f"{N['vigilant']} of 30 ballots"]
for m in must_paper:
    assert m in paper, ('paper', m)
for m in must_deck:
    assert m in deck, ('deck', m)
for txt in (paper, deck):
    assert '{{' not in txt and 'sk-or-' not in txt and 'OPENROUTER' not in txt
print('verified:', len(must_paper), 'paper claims,', len(must_deck), 'deck claims; no residue or credentials')
