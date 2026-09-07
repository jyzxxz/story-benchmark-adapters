// External framework configuration only. The original page, React scheduler,
// engine, middleware/auth source and API handlers are loaded unchanged.
const path = require('node:path');
const fs = require('node:fs');
const http = require('node:http');
const crypto = require('node:crypto');
const {AsyncLocalStorage} = require('node:async_hooks');
const operationContext = new AsyncLocalStorage();
const root = path.resolve(process.argv[2]);
const port = Number(process.argv[3]);
const evidence = process.argv[4];
const mode = 'native_render_entry';
const nativeRequire = require('node:module').createRequire(path.join(root, 'package.json'));
const originalFetch = globalThis.fetch;
const observedOrigins = new Set(['TEXT_BASE_URL', 'IMAGE_BASE_URL', 'VISION_BASE_URL'].map(name => new URL(process.env[name]).origin));
globalThis.fetch = function observedNativeFetch(input, init) {
  const operation = operationContext.getStore();
  const url = typeof input === 'string' || input instanceof URL ? new URL(input) : new URL(input.url);
  if (!operation || !observedOrigins.has(url.origin)) return originalFetch(input, init);
  const headers = new Headers(init?.headers || (input instanceof Request ? input.headers : undefined));
  headers.set('X-Benchmark-Native-Operation-Id', operation);
  return originalFetch(input, {...init, headers});
};

(async () => {
  const loadConfig = nativeRequire('next/dist/server/config').default;
  const {PHASE_DEVELOPMENT_SERVER} = nativeRequire('next/constants');
  const original = await loadConfig(PHASE_DEVELOPMENT_SERVER, root);
  fs.writeFileSync(evidence, JSON.stringify({
    mode, native_config_file: 'next.config.ts',
    native_config_file_sha256: crypto.createHash('sha256').update(fs.readFileSync(path.join(root, 'next.config.ts'))).digest('hex'),
    before: {entry: 'GET/HEAD /play', dispatch: 'original Next routing wrapper'},
    after: {entry: 'GET/HEAD /play', dispatch: 'original initialized Next render server', page: '/zh-CN/play'},
    native_config_overrides: [],
    reason: 'Frozen default-locale middleware produced HTTP 307 redirect to the same /play URL in this Next environment.',
    native_source_changed: false, native_api_auth_changed: false, story_language_changed: false,
    native_react_page_and_prefetch_changed: false,
    entry_mapping: {method: ['GET', 'HEAD'], path: '/play', native_render_path: '/zh-CN/play',
      scope: 'HTML/RSC page entry only; all API requests use original getRequestHandler and requireUser',
      default_locale_middleware_redirect_bypassed_for_page_entry: true,
      page_middleware_cookie_refresh_bypassed: true, api_requireUser_preserved: true, production_identity_verified: false},
    observation_headers: {scope: 'native API operation to local model transports only', payload_changed: false}
  }, null, 2));
  const app = nativeRequire('next')({dev: true, dir: root, hostname: '127.0.0.1', port});
  await app.prepare();
  const handler = app.getRequestHandler();
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1:' + port);
    if (mode === 'native_render_entry' && ['GET', 'HEAD'].includes(req.method) && url.pathname === '/play') {
      // Next 16's public custom-server render() re-enters its routing wrapper
      // and repeats the frozen middleware redirect. Its already-initialized
      // native render server renders the same App Router page directly.
      return app.server.render(req, res, '/zh-CN/play', Object.fromEntries(url.searchParams));
    }
    if (req.method === 'POST' && ['/api/start', '/api/scene'].includes(url.pathname)) {
      const operation = crypto.randomUUID();
      res.setHeader('X-Benchmark-Native-Operation-Id', operation);
      return operationContext.run(operation, () => handler(req, res));
    }
    return handler(req, res);
  });
  server.listen(port, '127.0.0.1');
  for (const sig of ['SIGTERM', 'SIGINT']) process.on(sig, () => { server.close(); app.close().finally(() => process.exit(0)); });
})().catch(error => { process.stderr.write(String(error) + '\n'); process.exit(1); });
