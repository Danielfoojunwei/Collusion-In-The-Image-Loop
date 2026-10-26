const {chromium}=require('playwright');const fs=require('fs');
(async()=>{const b=await chromium.launch();const p=await b.newPage({viewport:{width:1000,height:520},deviceScaleFactor:1.5});
await p.setContent(`<html><head><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600;700;800&display=swap"></head><body style="margin:0">${fs.readFileSync(process.argv[2],'utf8')}</body></html>`,{waitUntil:'networkidle'});
await p.screenshot({path:process.argv[3],fullPage:true});await b.close();})();
