const { chromium } = require('playwright');
const fs = require('fs');

(async () => {
  const svgs = [1, 2, 3, 4, 5, 6].map(n => fs.readFileSync(`r${n}.svg`, 'utf8')).join('<div style="height:12px"></div>');
  fs.writeFileSync('r_test.html', `<html><head><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600;700;800&display=swap"></head><body style="margin:0;width:1000px">${svgs}</body></html>`);
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1000, height: 800 } });
  await page.goto('file://' + process.cwd() + '/r_test.html', { waitUntil: 'networkidle' });
  await page.screenshot({ path: 'r_test.png', fullPage: true });
  await browser.close();
})();
