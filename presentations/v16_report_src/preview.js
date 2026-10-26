// Render every figure SVG on one page (6.6in text width) and screenshot each for review.
const { chromium } = require('playwright');
const fs = require('fs');
(async () => {
  const names = process.argv.slice(2);
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 900, height: 800 }, deviceScaleFactor: 2 });
  for (const n of names) {
    const html = `<html><head><style>body{margin:12px;font-family:'Liberation Sans'} .w{width:634px}</style></head><body><div class="w">${fs.readFileSync(n + '.svg', 'utf8')}</div></body></html>`;
    fs.writeFileSync('_prev.html', html);
    await p.goto('file://' + process.cwd() + '/_prev.html'); await p.waitForTimeout(300);
    const el = await p.$('.w'); await el.screenshot({ path: `_prev_${n}.png` });
  }
  await b.close();
})();
