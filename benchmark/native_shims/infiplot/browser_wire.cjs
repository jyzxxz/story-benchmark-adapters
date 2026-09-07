// Read-only Playwright wire metadata. Local HTTP disk capture supplies bodies
// when Chromium's inspector cache evicts them.
const crypto = require('node:crypto');
function installWireObserver(page, send) {
  let requestNumber = 0;
  const requestIds = new WeakMap();
    page.on('request', req => {
      const route = new URL(req.url()).pathname;
      if (!['/api/start', '/api/scene'].includes(route)) return;
      const id = 'native-http-' + (++requestNumber);
      requestIds.set(req, id);
      let data;
      const posted = req.postData();
      try { data = JSON.parse(posted || '{}'); } catch { data = null; }
      send({kind: 'native_request', request_id: id, route, data,
        observed_postdata_utf8_bytes: posted === null ? null : Buffer.byteLength(posted, 'utf8'),
        observed_postdata_sha256: posted === null ? null : crypto.createHash('sha256').update(posted).digest('hex')});
    });
    page.on('response', async response => {
      if (response.request().isNavigationRequest()) {
        send({kind: 'native_navigation_response', path: new URL(response.url()).pathname, status: response.status(), location: response.headers()['location'] || null});
      }
      const id = requestIds.get(response.request());
      if (!id) return;
      // Preserve operation/status even if Chromium has evicted the body.
      send({kind: 'native_response_headers', request_id: id,
        native_operation_id: response.headers()['x-benchmark-native-operation-id'] || null,
        status: response.status(), content_type: response.headers()['content-type'] || null});
      try {
        const raw = await response.body();
        let data = null;
        try { data = JSON.parse(raw.toString('utf8')); } catch {}
        send({kind: 'native_response', request_id: id, native_operation_id: response.headers()['x-benchmark-native-operation-id'] || null,
          status: response.status(), content_type: response.headers()['content-type'], data, raw_utf8: data === null ? raw.toString('utf8') : null});
      } catch (error) { send({kind: 'native_response_error', request_id: id, message: String(error)}); }
    });
    page.on('requestfailed', req => {
      const id = requestIds.get(req);
      if (id) send({kind: 'native_request_failed', request_id: id, message: req.failure()?.errorText || 'unknown'});
    });
    page.on('pageerror', err => send({kind: 'page_error', message: String(err)}));
}
module.exports = {installWireObserver};
