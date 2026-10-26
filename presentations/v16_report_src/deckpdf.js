// Print combined_deck.html to a 16:9 PDF (run combined_deck.py first).
const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1280, height: 720 } });
  await p.goto('file://' + process.cwd() + '/' + (process.argv[3] || 'combined_deck.html')); await p.waitForTimeout(500);
  await p.pdf({ path: process.argv[2] || 'deck.pdf', width: '13.333in', height: '7.5in', printBackground: true });
  await b.close();
})();
