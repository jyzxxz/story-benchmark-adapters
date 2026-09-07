// External observer/controller for the frozen native Next renderer. Never
// replace React state, monkey-patch its engine, or invoke component callbacks.
const readline = require('node:readline');
const fs = require('node:fs');
let browser, context, page;
let requestNumber = 0;
const requestIds = new WeakMap();
const send = value => process.stdout.write(JSON.stringify(value) + '\n');

// Read the *current* React tree, not a stale alternating host-fiber pointer.
// The predicate is a frozen-native component contract, checked by fixtures.
function inspectNative() {
  const nativeError = document.querySelector('#play-error-desc')?.textContent ||
    document.querySelector('p.italic.text-clay-900.text-lg')?.textContent || null;
  let root;
  for (const element of [document, document.documentElement, document.body, ...document.querySelectorAll('img')]) {
    const key = Object.keys(element).find(k => k.startsWith('__reactContainer$') || k.startsWith('__reactFiber$'));
    if (!key) continue;
    let fiber = element[key];
    while (fiber && fiber.return) fiber = fiber.return;
    root = fiber?.stateNode?.current || fiber;
    if (root) break;
  }
  if (!root) return {phase: 'booting', nativeError, errorText: document.body?.innerText || ''};
  let canvas;
  const stack = [root];
  while (stack.length) {
    const f = stack.pop(), p = f.memoizedProps;
    if (typeof f.type === 'function' && p && typeof p.onAdvance === 'function' && typeof p.onSelectChoice === 'function' && 'beat' in p && 'phase' in p) { canvas = f; break; }
    if (f.sibling) stack.push(f.sibling);
    if (f.child) stack.push(f.child);
  }
  if (!canvas) return {phase: 'booting', nativeError, errorText: document.body?.innerText || ''};
  let session = null;
  for (let f = canvas.return; f && !session; f = f.return) {
    for (let hook = f.memoizedState; hook && typeof hook === 'object'; hook = hook.next) {
      const value = hook.memoizedState;
      if (value && typeof value === 'object' && Array.isArray(value.history) && typeof value.worldSetting === 'string' && Array.isArray(value.characters)) { session = value; break; }
    }
  }
  const p = canvas.memoizedProps;
  const images = [...document.querySelectorAll('img')];
  const img = images.find(el => el.getAttribute('src') === p.imageUrl);
  const paragraphs = [...document.querySelectorAll('p')].filter(el => el.getClientRects().length).map(el => el.textContent || '');
  const visible = p.beat ? [p.beat.narration, p.beat.speaker ? p.beat.line : null].filter(x => typeof x === 'string' && x.length) : [];
  return {phase: p.phase, beat: p.beat, session, imageUrl: p.imageUrl,
    orientation: p.orientation, playerName: p.playerName, disabledChoiceIds: p.disabledChoiceIds || [],
    imageReady: !!(img && img.complete && img.naturalWidth && img.getClientRects().length),
    imageFailed: !!(img && img.complete && !img.naturalWidth), nativeError,
    imageIndex: img ? images.indexOf(img) : null,
    paragraphs, fullTextVisible: visible.every(text => paragraphs.some(p => p === text)),
    someTextVisible: visible.some(text => paragraphs.some(p => p.length > 0 && text.startsWith(p))),
    errorText: document.body?.innerText || '', browser_monotonic_ms: performance.now()};
}

async function command(msg) {
  if (msg.op === 'open') {
    const {chromium} = require(msg.playwright_module || 'playwright');
    browser = await chromium.launch({headless: true, ...(msg.chromium_executable ? {executablePath: msg.chromium_executable} : {})});
    context = await browser.newContext({viewport: {width: 1440, height: 1000}, locale: 'zh-CN', reducedMotion: 'reduce'});
    const cookies = msg.cookie.split(';').map(part => {
      const pos = part.indexOf('=');
      return {name: part.slice(0, pos).trim(), value: part.slice(pos + 1).trim(), url: msg.base_url};
    }).filter(c => c.name);
    await context.addCookies(cookies);
    await context.addInitScript(payload => {
      // The native custom-input UI uses exactly this storage entry.
      sessionStorage.setItem('infiplot:custom', JSON.stringify({...payload, audioEnabled: false}));
    }, msg.payload);
    page = await context.newPage();
    page.setDefaultTimeout(0); // generation has no adapter deadline
    page.on('request', req => {
      const route = new URL(req.url()).pathname;
      if (!['/api/start', '/api/scene'].includes(route)) return;
      const id = 'native-http-' + (++requestNumber);
      requestIds.set(req, id);
      let data;
      try { data = JSON.parse(req.postData() || '{}'); } catch { data = null; }
      send({kind: 'native_request', request_id: id, route, data});
    });
    page.on('response', async response => {
      if (response.request().isNavigationRequest()) {
        send({kind: 'native_navigation_response', path: new URL(response.url()).pathname, status: response.status(), location: response.headers()['location'] || null});
      }
      const id = requestIds.get(response.request());
      if (!id) return;
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
    // zh-CN uses the bare URL in the native middleware; explicit /zh-CN is
    // redirected. Do not force a non-native locale or bypass auth middleware.
    await page.goto(msg.base_url + (msg.entry_path || '/play?custom=1'), {waitUntil: 'domcontentloaded', timeout: msg.startup_timeout_ms || 120000});
    return {opened: true, renderer: 'native_next_playcanvas', prefetch: 'native_browser_unchanged'};
  }
  if (msg.op === 'state') return page.evaluate(inspectNative);
  if (msg.op === 'advance') {
    const state = await page.evaluate(inspectNative);
    if (state.phase !== 'ready' || state.beat?.next?.type !== 'continue' || !state.fullTextVisible) throw Error('native_advance_not_ready');
    const body = state.beat.speaker ? state.beat.line : state.beat.narration;
    // A real click on the native text card calls the normal onAdvance handler.
    const texts = page.locator('p');
    let clicked = false;
    for (let i = 0; i < await texts.count(); i++) {
      if (await texts.nth(i).textContent() === body && await texts.nth(i).isVisible()) { await texts.nth(i).click({timeout: 15000}); clicked = true; break; }
    }
    if (!clicked) throw Error('native_visible_advance_card_missing');
    return {clicked: true};
  }
  if (msg.op === 'choose') {
    const state = await page.evaluate(inspectNative);
    const option = state.beat?.next?.choices?.[msg.index];
    if (state.phase !== 'ready' || !option || state.disabledChoiceIds.includes(option.id)) throw Error('native_choice_not_available');
    // Native transition-all can briefly keep visibility hidden after a clean
    // capture. Locate the unique DOM button, then normal click waits for it;
    // never force-click or treat that transient as a missing option.
    const buttons = page.locator('main button').filter({hasText: option.label});
    if (await buttons.count() !== 1) throw Error('native_choice_button_not_unique:' + JSON.stringify({label: option.label, count: await buttons.count(), buttons: await page.getByRole('button', {includeHidden: true}).allTextContents()}));
    await buttons.click({timeout: 15000});
    return {clicked: true, choice: option};
  }
  if (msg.op === 'capture') {
    const state = await page.evaluate(inspectNative);
    if (!state.imageReady) throw Error('native_image_not_ready');
    await page.screenshot({path: msg.ui_path, fullPage: true, animations: 'disabled', timeout: 15000});
    // Capture the actual native rendered img, including its native CSS sizing.
    // Only caption/control siblings are hidden briefly, then restored exactly.
    // Unlike canvas extraction this also works with cross-origin image URLs.
    const data = await page.evaluate(index => {
      const img = document.querySelectorAll('img')[index];
      const rect = img.getBoundingClientRect();
      return {width: rect.width, height: rect.height, natural_width: img.naturalWidth, natural_height: img.naturalHeight,
        object_fit: getComputedStyle(img).objectFit};
    }, state.imageIndex);
    await page.locator('img').nth(state.imageIndex).screenshot({path: msg.clean_path, animations: 'disabled', timeout: 15000,
      style: 'div:has(> img) > :not(img) { visibility: hidden !important; }'});
    return {...data, clean_method: 'native_img_element_screenshot_caption_controls_temporarily_hidden', browser_monotonic_ms: state.browser_monotonic_ms};
  }
  if (msg.op === 'close') {
    if (context) await context.close();
    if (browser) await browser.close();
    return {closed: true};
  }
  throw Error('unknown_browser_command');
}

const input = readline.createInterface({input: process.stdin});
let sequence = Promise.resolve();
input.on('line', line => {
  sequence = sequence.then(async () => {
    let msg;
    try { msg = JSON.parse(line); const result = await command(msg); send({kind: 'reply', id: msg.id, result}); }
    catch (error) { send({kind: 'reply', id: msg?.id, error: String(error)}); }
  });
});
input.on('close', () => sequence.then(async () => { if (browser) await browser.close(); }));
