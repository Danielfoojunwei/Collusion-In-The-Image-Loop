// Render SVG figure files at print size (1 unit = 0.5 pt) to PNG for inspection.
const { chromium } = require('playwright');
const fs = require('fs'), path = require('path');
(async () => {
  const b = await chromium.launch(); const p = await b.newPage({ deviceScaleFactor: 2.2 });
  for (const f of process.argv.slice(2)) {
    const svg = fs.readFileSync(f, 'utf8');
    fs.writeFileSync('_prev.html', `<html><body style="margin:12px;background:#fff;width:6.6in">${svg}</body></html>`);
    await p.goto('file://' + process.cwd() + '/_prev.html');
    await p.waitForTimeout(300);
    const el = await p.$('svg'); await el.screenshot({ path: 'prev3/' + path.basename(f, '.svg') + '.png' });
  }
  await b.close();
})();
