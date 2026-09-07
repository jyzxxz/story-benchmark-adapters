// Disk observation of original HTTP response chunks. Never read the request
// stream or replace, buffer-before-send, retry or re-render native responses.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

function createResponseCapture(directory) {
  fs.mkdirSync(directory, {recursive: true});
  const pending = new Set();
  function observe(req, res, operation) {
    const metadataPath = path.join(directory, `${operation}.json`);
    const rawPath = path.join(directory, `${operation}.response.raw`);
    const metadata = {version: 1, native_operation_id: operation, route: new URL(req.url, 'http://localhost').pathname,
      request_method: req.method, request_content_length: req.headers['content-length'] || null,
      request_body_consumed_by_observer: false, response_file: path.basename(rawPath),
      response_state: 'recording', response_status: null, content_encoding: null, content_type: null,
      captured_bytes: 0, response_sha256: null, errors: [], request_or_response_changed: false};
    let sink, settled = false, endObserved = false, finished = false, sinkFinished = false, nested = 0;
    const hash = crypto.createHash('sha256');
    let resolve;
    const done = new Promise(r => { resolve = r; });
    pending.add(done);
    let metadataQueue = Promise.resolve();
    const persist = () => {
      const snapshot = JSON.stringify(metadata, null, 2);
      metadataQueue = metadataQueue.then(async () => {
        const temporary = `${metadataPath}.tmp`;
        await fs.promises.writeFile(temporary, snapshot);
        await fs.promises.rename(temporary, metadataPath);
      }).catch(error => {
        metadata.errors.push('metadata_write_failed:' + String(error));
        process.stderr.write(`[benchmark-response-capture] ${operation} metadata failure: ${String(error)}\n`);
      });
    };
    function headers() {
      metadata.response_status = res.statusCode;
      metadata.content_type = String(res.getHeader('content-type') || '');
      metadata.content_encoding = String(res.getHeader('content-encoding') || 'identity');
    }
    function fail(error) {
      metadata.errors.push(String(error));
      metadata.response_state = 'capture_failed';
      persist();
    }
    function settle() {
      if (settled) return;
      settled = true;
      headers();
      metadata.response_state = metadata.errors.length ? 'capture_failed' : finished && sinkFinished ? 'complete' : 'interrupted';
      metadata.response_sha256 = hash.digest('hex');
      persist();
      metadataQueue.finally(() => { pending.delete(done); resolve(); });
    }
    try {
      sink = fs.createWriteStream(rawPath, {flags: 'wx'});
      sink.on('error', error => { fail(error); });
      sink.on('finish', () => { sinkFinished = true; });
      sink.on('close', settle);
      persist();
    } catch (error) { fail(error); settle(); }
    function capture(chunk, encoding) {
      if (!sink || settled || chunk === undefined || chunk === null || typeof chunk === 'function') return;
      try {
        // Own these bytes: user/native code may reuse its buffer after write.
        const copy = typeof chunk === 'string' ? Buffer.from(chunk, typeof encoding === 'string' ? encoding : undefined) : Buffer.from(chunk);
        hash.update(copy);
        metadata.captured_bytes += copy.length;
        sink.write(copy); // Observation queue only; native backpressure is untouched.
      } catch (error) { fail(error); sink.destroy(); }
    }
    const write = res.write, end = res.end;
    res.write = function (...args) {
      if (this === res && (res.writableEnded || res.destroyed)) fail('native_write_after_end_or_close');
      if (this === res && !nested) capture(args[0], args[1]);
      nested++;
      try { return Reflect.apply(write, this, args); }
      catch (error) { fail(error); throw error; }
      finally { nested--; }
    };
    res.end = function (...args) {
      if (this === res && !nested) { capture(args[0], args[1]); endObserved = true; }
      nested++;
      try { return Reflect.apply(end, this, args); }
      catch (error) { fail(error); throw error; }
      finally { nested--; }
    };
    function stop(complete) {
      if (settled) return;
      finished = complete && endObserved;
      if (!finished) metadata.errors.push('native_response_closed_before_finish');
      headers();
      if (sink && !sink.writableEnded && !sink.destroyed) sink.end();
      else if (!sink || sink.destroyed) settle();
    }
    res.once('finish', () => stop(true));
    res.once('close', () => { if (!finished) stop(false); });
    return {done};
  }
  return {observe, drain: async () => { await Promise.allSettled([...pending]); }};
}
module.exports = {createResponseCapture};
