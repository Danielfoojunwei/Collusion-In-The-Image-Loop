// Print paper_built.html to a Letter PDF (run paper.py first).
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto('file://' + process.cwd() + '/paper_built.html', { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  await page.pdf({
    path: process.argv[2] || 'paper.pdf', format: 'Letter', printBackground: true, displayHeaderFooter: true,
    headerTemplate: '<span></span>',
    footerTemplate: '<div style="width:100%;text-align:center;font-size:8pt;font-family:serif;color:#444"><span class="pageNumber"></span></div>',
    margin: { top: '0.8in', bottom: '0.8in', left: '0.95in', right: '0.95in' },
  });
  await browser.close();
})();
