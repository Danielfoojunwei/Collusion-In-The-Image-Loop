"""Display names for the two studies and Study G's groups, applied to visible text only."""
import re

_REPS = [('Study 1 (V15)', 'Study P'), ('Study 2 (V16)', 'Study G'), ('Study 1', 'Study P'), ('Study 2', 'Study G'),
         ('V15', 'Study P'), ('V16', 'Study G')]
_ARMS = (('b01', 'G-Full'), ('b03', 'G-NoFig'), ('b05', 'G-Memory'))


def _fix(t):
    for x, y in _REPS:
        t = t.replace(x, y)
    for x, y in _ARMS:
        t = re.sub(rf'\b{x}\b', y, t)
    return t


def rename(html_text):
    """Rename in text nodes; tags, attributes and file paths are left untouched."""
    parts = re.split(r'(<[^>]+>)', html_text)
    return ''.join(p if p.startswith('<') else _fix(p) for p in parts)
