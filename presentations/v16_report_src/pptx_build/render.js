const { chromium } = require('playwright'); const fs = require('fs');
(async () => {
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1400, height: 900 }, deviceScaleFactor: 3 });
  for (const name of ['game_round', 'timeline']) {
    let svg = fs.readFileSync('pptx_build/' + name + '.svg', 'utf8').replace(/style="width:[^"]*"/, 'style="width:1300px;display:block"');
    const html = `<html><body style="margin:0;background:#fff"><div id="f" style="display:inline-block;padding:6px">${svg}</div></body></html>`;
    fs.writeFileSync('pptx_build/_tmp.html', html);
    await p.goto('file://' + process.cwd() + '/pptx_build/_tmp.html'); await p.waitForTimeout(300);
    const el = await p.$('#f'); await el.screenshot({ path: 'pptx_build/' + name + '.png' });
    const bb = await el.boundingBox(); console.log(name, bb.width, bb.height);
  }
  await b.close();
})();
