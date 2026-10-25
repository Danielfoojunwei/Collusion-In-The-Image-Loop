// Screenshot the paper's figures and case cards as PNGs for the slide deck.
const { chromium } = require('playwright');
const fs = require('fs');

(async () => {
  const out = 'deckimg';
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1000, height: 1200 }, deviceScaleFactor: 2.2 });
  await page.goto('file://' + process.cwd() + '/paper_built.html', { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  const fig = text => page.locator('figure').filter({ hasText: text }).first();
  await fig('Figure 1.').locator('svg').first().screenshot({ path: `${out}/fig1.png` });
  await fig('Figure 2.').locator('.strip').screenshot({ path: `${out}/strip.png` });
  await fig('Figure 3.').locator('svg').first().screenshot({ path: `${out}/review.png` });
  const cases = page.locator('.case');
  for (let i = 0; i < 3; i++) await cases.nth(i).locator('.case-imgs').screenshot({ path: `${out}/case${i + 1}.png` });
  for (let n = 1; n <= 6; n++) await fig(`Figure R${n}.`).locator('svg').first().screenshot({ path: `${out}/r${n}.png` });
  await browser.close();
})();
