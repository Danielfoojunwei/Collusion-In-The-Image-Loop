"""Data figures (Figure 2 and Figure 5) drawn with matplotlib in a plain conference style.

Intervals are exact (Clopper-Pearson) 95% binomial intervals on the raw counts. They describe
sampling uncertainty for each count only; replays and judge views are not independent trials.
"""
import io
import re
from math import comb

import matplotlib
matplotlib.use('svg')
import matplotlib.pyplot as plt

BLUE, VERM, GREY, INK = '#0072B2', '#D55E00', '#7F7F7F', '#222222'
plt.rcParams.update({
    'font.family': 'Liberation Sans', 'font.size': 7.5, 'svg.fonttype': 'none',
    'axes.linewidth': 0.6, 'axes.edgecolor': INK, 'axes.labelcolor': INK, 'text.color': INK,
    'xtick.color': INK, 'ytick.color': INK, 'xtick.major.width': 0.6, 'ytick.major.width': 0.6,
    'xtick.major.size': 2.5, 'ytick.major.size': 2.5, 'axes.spines.top': False, 'axes.spines.right': False,
})


def cdf(k, n, p):
    return sum(comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))


def cp(k, n, a=0.05):
    """Exact two-sided Clopper-Pearson interval by bisection on the binomial CDF."""
    def solve(f):
        lo, hi = 0.0, 1.0
        for _ in range(60):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if f(mid) else (lo, mid)
        return (lo + hi) / 2
    low = 0.0 if k == 0 else solve(lambda p: 1 - cdf(k - 1, n, p) < a / 2)
    high = 1.0 if k == n else solve(lambda p: cdf(k, n, p) > a / 2)
    return low, high


def dots(ax, rows, ref=None, s=1.0):
    for i, (label, k, n, col) in enumerate(rows):
        lo, hi = cp(k, n)
        p = k / n
        ax.plot([lo, hi], [i, i], color=col, lw=1.1 * s, solid_capstyle='butt', alpha=0.85)
        ax.plot(p, i, 'o', ms=4 * s, color=col, mec='white', mew=0.5 * s, zorder=3)
        ax.text(1.03, i, f'{k}/{n}', va='center', ha='left', fontsize=7.5 * s, transform=ax.get_yaxis_transform())
    ax.set_yticks(range(len(rows)), [r[0] for r in rows])
    ax.set_ylim(len(rows) - 0.5, -0.6)
    ax.set_xlim(0, 1)
    ax.set_xticks([0, 0.25, 0.5, 0.75, 1], ['0', '.25', '.5', '.75', '1'])
    ax.tick_params(axis='y', length=0)
    ax.spines['left'].set_visible(False)
    if ref is not None:
        ax.axvline(ref, color=GREY, lw=0.6 * s, ls=(0, (3, 2)), zorder=0)


def svg(fig):
    buf = io.StringIO()
    fig.savefig(buf, format='svg', bbox_inches='tight', pad_inches=0.02, transparent=True)
    plt.close(fig)
    s = buf.getvalue()
    s = s[s.index('<svg'):]
    # let the page CSS set the width; keep the aspect ratio from the viewBox
    s = re.sub(r'<svg([^>]*?) width="([0-9.]+)pt" height="[^"]+"',
               r'<svg\1 style="width:\2pt;max-width:100%;display:block;margin:0 auto"', s, count=1)
    return s


def baseline(size=(4.4, 1.25), s=1.0):
    rows = [('Own pictures, main game (12 rounds)', 23, 24, VERM),
            ('Original pictures, replays (R4, R8, R12)', 5, 6, VERM),
            ('A’s picture replaced by task-only picture', 2, 6, BLUE),
            ('Unreadable text memo, full incentive', 0, 2, BLUE),
            ('Blank picture, honest prompt, no grant', 0, 2, BLUE)]
    fig, ax = plt.subplots(figsize=size)
    dots(ax, rows, ref=1 / 3, s=s)
    ax.axhline(1.5, color='#BBBBBB', lw=0.5)
    ax.text(1 / 3 + 0.01, 4.45, 'blind guess (1/3)', fontsize=6.5 * s, color=GREY, va='bottom')
    ax.set_xlabel('fraction of B and C votes for A’s target')
    return svg(fig)


def review():
    conf = [0.70, 0.78, 0.80, 0.82, 0.85, 0.83, 0.86, 0.86, 0.86, 0.86, 0.86, 0.87]
    fig, (a, b) = plt.subplots(1, 2, figsize=(6.5, 1.75), gridspec_kw={'width_ratios': [1, 1.1], 'wspace': 0.75})
    r = range(1, 13)
    a.plot(r, conf, color=INK, lw=0.9, marker='o', ms=3.2, mfc='white', mec=INK, mew=0.8)
    a.set_xticks([1, 3, 5, 7, 9, 11])
    a.set_xlim(0.5, 12.5)
    a.set_ylim(0.6, 0.95)
    a.set_xlabel('round')
    a.set_ylabel('stated confidence')
    a.text(6.5, 0.625, 'verdict “uncertain” in all 12 rounds', ha='center', fontsize=6.8, color=GREY)
    a.set_title('(a) live reviewer', fontsize=7.5, loc='left', pad=4)
    rows = [('Live reviewer', 0, 12, VERM),
            ('Observers (no prior reports)', 5, 6, BLUE),
            ('Monitor L1: pictures', 0, 11, INK),
            ('L2: + ballots', 10, 12, INK),
            ('L3: + 3 earlier rounds', 12, 12, INK),
            ('L4: + payoffs', 12, 12, INK)]
    dots(b, rows)
    b.axhline(1.5, color='#BBBBBB', lw=0.5)
    b.set_xlabel('fraction of views attributing coordination')
    b.set_title('(b) attribution by judge', fontsize=7.5, loc='left', pad=4)
    return svg(fig)


if __name__ == '__main__':
    open('base.svg', 'w').write(baseline())
    open('figrev.svg', 'w').write(review())
    # slide version: same data, larger type relative to the plot
    s = 1.6
    with plt.rc_context({'font.size': 7.5 * s, 'axes.linewidth': 0.6 * s, 'xtick.major.width': 0.6 * s}):
        open('base_slide.svg', 'w').write(baseline((5.6, 2.9), s))
