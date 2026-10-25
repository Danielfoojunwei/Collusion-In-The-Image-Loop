# V15 report: Seeing Is Not Attributing

Daniel Foo, Nanyang Technological University (NTU). Apart Research AI Collusion Research Sprint, 23–25 October 2026, Track 3.1.

## Contents

| Path | What it is |
| --- | --- |
| `Seeing_Is_Not_Attributing_V15_Apart_Report.pdf` | The submission: 8-page main text (title to LLM Usage Statement), then references and Appendices A–G |
| `v15_report_src/` | Sources for the PDF: `main.html` (main text), `assemble.py` (adds appendices), `build.py` (figures and data), `pdf.js` (Playwright print), figure scripts and images |
| `v15_slides_src/deck_v2_project/` | The 12-slide deck (Slides artifact format: `deck.json` plus one HTML file per slide) |
| `v15_slides_src/deck_v2_images/` | Figure images used by the deck |
| `../analysis/v15_deep_dive/` | Extracts of every agent action, brief, vote and log from the V15 run (`stages.json`), the actor prompts (`prompts.txt`) and the extraction script |
| `../research_plans/v15_attribution_pilot/` | Post-hoc pilot: 24 target decoders and 48 monitor-ladder views (fresh Claude contexts) on the frozen V15 artifacts |

The raw V15 run records (`runs/main/triad_glm_restored_v15_live`, about 1.1 GB) are in the archive parts under `archives/` on `main` and are not duplicated here.

## Rebuilding the PDF

From `v15_report_src/`, with Python 3 and the pre-installed Playwright:

```sh
python3 assemble.py   # writes paper.html from main.html, the appendix sections and the style block
python3 build.py      # renders the figure SVGs and fills the placeholders into paper_built.html
node pdf.js           # prints paper.pdf (US Letter)
```

`assemble.py` reads `paper_v4_long.html` and the `sec_*.txt` files for the appendices. The images in `img/` are thumbnails of the actual V15 PNGs (Figures 3 and 4, Appendix C) and four explanatory reconstructions (Appendix F).

## Caveats recorded in the paper

- External citations were collected from web search; the hallucinator reference check could not reach the citation databases from the build environment, so the references still need a manual check.
- The reviewer's model identity (GPT-6.1 Sol in Codex, Ultra) comes from the operator's configuration; the run logs record a Codex context without a model string.
