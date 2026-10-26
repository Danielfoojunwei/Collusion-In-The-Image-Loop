# V15 report: Seeing Is Not Attributing

Daniel Foo, Nanyang Technological University (NTU). Apart Research AI Collusion Research Sprint, 23–25 October 2026, Track 3.1.

## Contents

| Path | What it is |
| --- | --- |
| `Seeing_Is_Not_Attributing_V15_Apart_Report.pdf` | The submission: 8-page main text (title to LLM Usage Statement), then references and Appendices A–G |
| `Seeing_Is_Not_Attributing_V15_5min_Talk.pdf` | Six-slide, five-minute talk built from the paper’s figures; script in `V15_TALK_SCRIPT.md` |
| `v15_report_src/` | Sources for the PDF and talk: `main.html` (main text), `assemble.py` (adds appendices), `build.py` (figures and data), `figkit.py`, `fig_diag.py`, `fig_r.py` (schematics), `fig_plots.py` (matplotlib data figures with exact binomial intervals), `pdf.js` (Playwright print), `deck5.py` and `deckpdf.js` (talk), images |
| `v15_slides_src/deck_v2_project/` | The 12-slide deck (Slides artifact format: `deck.json` plus one HTML file per slide) |
| `v15_slides_src/deck_v2_images/` | Figure images used by the deck |
| `../analysis/v15_deep_dive/` | Extracts of every agent action, brief, vote and log from the V15 run (`stages.json`), the actor prompts (`prompts.txt`) and the extraction script |
| `../research_plans/v15_attribution_pilot/` | Post-hoc pilot: 24 target decoders and 48 monitor-ladder views (fresh Claude contexts) on the frozen V15 artifacts |

The raw V15 run records (`runs/main/triad_glm_restored_v15_live`, about 1.1 GB) are in the archive parts under `archives/` on `main` and are not duplicated here.

## Rebuilding the PDF

From `v15_report_src/`, with Python 3 (Pillow, matplotlib) and the pre-installed Playwright:

```sh
python3 assemble.py   # writes paper.html from main.html, the appendix sections and the style block
python3 build.py      # renders the figure SVGs and fills the placeholders into paper_built.html
node pdf.js           # prints paper.pdf (US Letter)
python3 deck5.py && node deckpdf.js   # prints the five-minute talk
```

`assemble.py` reads `paper_v4_long.html` and the `sec_*.txt` files for the appendices. The images in `img/` are 800-pixel copies of the actual V15 PNGs, downsampled from the 1024-pixel originals in the run records (Figures 1, 3 and 4, Appendices C and D), and four explanatory reconstructions (Appendix F).

## Caveats recorded in the paper

- External citations were collected from web search; the hallucinator reference check could not reach the citation databases from the build environment, so the references still need a manual check.
- The reviewer's model identity (GPT-6.1 Sol in Codex, Ultra) comes from the operator's configuration; the run logs record a Codex context without a model string.

## Prior work and sprint work

The sprint rules ask teams to mark code written before the sprint as preexisting. In this repository the game code, infrastructure, protocol (dated 22 October 2026) and earlier pilot versions are **preexisting**. The V15 run (24 October 2026), all analysis, the post-hoc attribution pilot, the figures, this report and the talk were produced during the sprint. Appendix A of the report says the same.

## Requirements checklist (sprint Guidelines)

| Requirement | Where it is met |
| --- | --- |
| At most 8 pages before references and appendices | Main text ends on page 8; References start on page 9 |
| Abstract of 150–250 words | 242 words |
| Introduction ends with "Our main contributions are:" and a numbered list | Section 1 |
| Results report variance, seeds, baselines and a positive control | Section 3 (Validity) and Section 4.1: exact 95% binomial intervals, one-trajectory caveat, positive control (original-picture replays) and negative controls |
| Appendix titled "Limitations and Dual-Use / Ethical Considerations" | Appendix A |
| Prior work marked as preexisting in the README and the report | This file and Appendix A |
| LLM Usage Statement | End of the main text |
| Optional 3–5 minute video | `Seeing_Is_Not_Attributing_V15_5min_Talk.pdf` with `V15_TALK_SCRIPT.md` |
