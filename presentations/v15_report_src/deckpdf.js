// Print deck5.html to a 16:9 PDF and PNG previews of each slide.
const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1280, height: 720 } });
  await p.goto('file://' + process.cwd() + '/deck5.html'); await p.waitForTimeout(500);
  await p.pdf({ path: 'Seeing_Is_Not_Attributing_5min.pdf', width: '13.333in', height: '7.5in', printBackground: true });
  await b.close();
})();
