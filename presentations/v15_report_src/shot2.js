const { chromium } = require('playwright'); const fs = require('fs');
(async () => {
  const svgs = process.argv.slice(2).map(f => fs.readFileSync(f, 'utf8')).join('<div style="height:10px"></div>');
  fs.writeFileSync('t2.html', `<html><head><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600;700;800&display=swap"></head><body style="margin:0;width:1000px">${svgs}</body></html>`);
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1000, height: 600 } });
  await p.goto('file://' + process.cwd() + '/t2.html', { waitUntil: 'networkidle' }); await p.screenshot({ path: 't2.png', fullPage: true }); await b.close();
})();
