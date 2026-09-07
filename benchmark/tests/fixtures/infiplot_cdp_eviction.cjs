const fs=require('node:fs');
const {installWireObserver}=require('../../native_shims/infiplot/browser_wire.cjs');
(async()=>{
  const {chromium}=require(process.argv[2]);
  const browser=await chromium.launch({headless:true});
  const page=await browser.newPage();
  const cdp=await page.context().newCDPSession(page);
  await cdp.send('Network.enable',{maxTotalBufferSize:1024,maxResourceBufferSize:1024,maxPostDataSize:1024});
  // Network buffer settings belong to a CDP session, not every Playwright
  // session. Route only the test Response.body read through this real 1 KiB
  // inspector session. The error comes from Chromium, never a canned throw.
  const requests=new Map(), completed=new Set(), waiting=new Map();
  cdp.on('Network.requestWillBeSent',event=>requests.set(event.request.url,event.requestId));
  cdp.on('Network.loadingFinished',event=>{completed.add(event.requestId);waiting.get(event.requestId)?.();});
  page.on('response',response=>{
    if(!response.url().includes('/api/scene'))return;
    response.body=async()=>{
      const requestId=requests.get(response.url());
      if(!completed.has(requestId))await new Promise(resolve=>waiting.set(requestId,resolve));
      const result=await cdp.send('Network.getResponseBody',{requestId});
      return Buffer.from(result.body,result.base64Encoded?'base64':'utf8');
    };
  });
  const rows=[];
  installWireObserver(page,row=>rows.push(row));
  await page.goto(process.argv[3]);
  const bytes=await page.evaluate(async()=>{const r=await fetch('/api/scene?kind=cache',{method:'POST',headers:{'Content-Type':'application/json'},body:'{"original":true}'});return (await r.arrayBuffer()).byteLength;});
  for(let i=0;i<100&&!rows.some(r=>r.kind==='native_response_error'||r.kind==='native_response');i++)await new Promise(r=>setTimeout(r,10));
  await browser.close();
  fs.writeFileSync(process.argv[4],JSON.stringify({bytes,body_transport:'real independent CDP session with 1 KiB resource limit for fault injection',rows},null,2));
})().catch(e=>{process.stderr.write(String(e));process.exit(1);});
