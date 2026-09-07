const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const zlib = require('node:zlib');
const {once} = require('node:events');
const {createResponseCapture} = require('../../native_shims/infiplot/response_capture.cjs');
const folder = process.argv[2];
const capture = createResponseCapture(folder);
const stats = {};
const server = http.createServer(async (req, res) => {
  const name = new URL(req.url, 'http://localhost').searchParams.get('kind') || 'small';
  if (req.url === '/stats') { res.end(JSON.stringify(stats)); return; }
  if (req.url === '/shutdown') { res.end('ok'); server.close(async () => { await capture.drain(); process.exit(0); }); return; }
  if (!req.url.startsWith('/api/')) { res.setHeader('content-type','text/html'); res.end('<html><body>fixture</body></html>'); return; }
  const op = 'op-' + name;
  stats[name] = {finished: false, callbacks: 0, backpressure_false: 0};
  res.setHeader('X-Benchmark-Native-Operation-Id', op);
  res.setHeader('Content-Type', 'application/json');
  if (name === 'capture-error') fs.mkdirSync(path.join(folder, op + '.response.raw'));
  capture.observe(req,res,op);
  res.on('finish',()=> { stats[name].finished = true; });
  if (name === 'interrupted') { res.write('{"unfinished":"'); setTimeout(()=>res.destroy(),25); return; }
  const payload = JSON.stringify({scene:{id:name,beats:[{id:'b1',narration:'Original native response.'}]}, padding:'x'.repeat(name === 'cache' ? 2*1024*1024 : 64)});
  if (['gzip','br','deflate'].includes(name)) {
    const raw = {gzip:zlib.gzipSync,br:zlib.brotliCompressSync,deflate:zlib.deflateSync}[name](Buffer.from(payload));
    res.setHeader('Content-Encoding',name); res.end(raw); return;
  }
  if (name === 'sse') {
    res.setHeader('Content-Type','text/event-stream');
    res.write('event: beat\ndata: {"type":"beat","beat":{"id":"b1"}}\n\n');
    setTimeout(()=>res.end('event: done\ndata: '+JSON.stringify({type:'done',response:JSON.parse(payload)})+'\n\n'),25); return;
  }
  if (name === 'large') {
    res.write('{"padding":"');
    await new Promise(resolve=>setTimeout(resolve,100));
    const chunk = Buffer.alloc(1024*1024,'A');
    for(let i=0;i<112;i++) {
      const returned = res.write(chunk,()=>stats[name].callbacks++);
      if (!returned) { stats[name].backpressure_false++; await once(res,'drain'); }
    }
    res.end('","scene":{"id":"large","beats":[{"id":"b1","narration":"Large original response."}]}}'); return;
  }
  if (name === 'post') {
    const chunks=[]; for await (const part of req) chunks.push(part);
    res.end(JSON.stringify({scene:{id:'post'},received_body:Buffer.concat(chunks).toString()})); return;
  }
  if (name === 'signed') {
    res.end(JSON.stringify({scene:{id:'signed'},imageUrl:'https://images.example/scene.png?X-Amz-Signature=fixture-secret-signature&token=fixture-secret-token'})); return;
  }
  res.end(payload,()=>stats[name].callbacks++);
});
server.listen(0,'127.0.0.1',()=>process.stdout.write(JSON.stringify({port:server.address().port})+'\n'));
