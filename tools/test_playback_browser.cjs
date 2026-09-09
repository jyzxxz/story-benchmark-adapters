/* Offline browser regression. No provider credentials or model calls.
 * PLAYWRIGHT_MODULE=/path/to/playwright node tools/test_playback_browser.cjs
 *   --out /path/to/new-report /path/to/export/review [...]
 */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const args = process.argv.slice(2);
const outIndex = args.indexOf('--out');
if (outIndex < 0 || !args[outIndex+1]) throw new Error('--out DIRECTORY required');
const output = path.resolve(args[outIndex+1]);
args.splice(outIndex, 2);
if (!args.length) throw new Error('Provide exported review directories');
fs.mkdirSync(output, {recursive:true});
function samples(directory) {
  if (fs.existsSync(path.join(directory, 'story.json'))) return [directory];
  const catalog = JSON.parse(fs.readFileSync(path.join(directory, 'samples.json'), 'utf8'));
  return catalog.map(row => path.dirname(path.resolve(directory, row.entry_file)));
}
(async () => {
  const browser = await chromium.launch({headless:true});
  const results = [];
  try {
    for (const directory of args.map(value => path.resolve(value))) {
      if (!fs.existsSync(path.join(directory, 'story.json'))) {
        const page = await browser.newPage({viewport:{width:1365,height:1000}});
        const catalogErrors = [], catalogRemote = [];
        page.on('pageerror', error => catalogErrors.push(error.message));
        page.on('console', msg => { if (msg.type()==='error') catalogErrors.push(msg.text()); });
        page.on('request', req => { if (/^https?:/i.test(req.url())) catalogRemote.push(req.url()); });
        await page.goto(pathToFileURL(path.join(directory, '打开故事.html')).href);
        const entries = JSON.parse(fs.readFileSync(path.join(directory,'samples.json'),'utf8'));
        const links = page.locator('.card');
        assert.equal(await links.count(), samples(directory).length);
        assert.equal(await page.locator('#sample-total').textContent(),String(entries.length));
        await page.locator('#filter-body').click();
        assert.equal(await page.locator('.card:visible').count(),entries.filter(s=>s.counts.story_segments>0).length);
        await page.locator('#filter-empty').click();
        assert.equal(await page.locator('.card:visible').count(),entries.filter(s=>s.counts.story_segments===0).length);
        await page.locator('#filter-all').click();
        await page.locator('#sample-search').fill(entries[0].sample_id);
        assert.equal(await page.locator('.card:visible').count(),1);
        await page.locator('#sample-search').fill('');
        await page.screenshot({path:path.join(output,'catalog-desktop.png'),fullPage:true});
        await page.setViewportSize({width:390,height:844});
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth),false);
        await page.screenshot({path:path.join(output,'catalog-mobile.png'),fullPage:true});
        await links.first().click();
        await page.waitForFunction(() => !!window.STORY_REVIEW && document.querySelector('#page-label').textContent.includes(' / '));
        assert.equal(await page.locator('#collection-link').isVisible(),true);
        await page.locator('#collection-link').click();
        assert.equal(await page.locator('.card').count(),entries.length);
        assert.deepEqual(catalogErrors,[]);
        assert.deepEqual(catalogRemote,[]);
        await page.close();
      }
      for (const sample of samples(directory)) {
        const data = JSON.parse(fs.readFileSync(path.join(sample,'story.json'),'utf8'));
        const page = await browser.newPage({viewport:{width:1365,height:1000}});
        const errors = [], remote = [];
        page.on('pageerror', error => errors.push(error.message));
        page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()); });
        page.on('request', req => { if (/^https?:/i.test(req.url())) remote.push(req.url()); });
        await page.goto(pathToFileURL(path.join(sample,'index.html')).href);
        await page.waitForFunction(() => !!window.STORY_REVIEW && document.querySelector('#page-label').textContent.includes(' / '));
        assert.equal(await page.locator('#load-error').isVisible(), false);
        assert.equal(await page.locator('#evidence-banner').getAttribute('data-evidence'),
          ['live','fixture'].includes(data.status.evidence_kind) ? data.status.evidence_kind : 'unknown');
        let images = 0;
        for (let i = 0; i < data.pages.length; i++) {
          const expected = data.pages[i];
          if (i) await page.locator('#next').click();
          assert.equal(await page.locator('#story-text').textContent(), expected.text);
          assert.equal(await page.locator('#page-label').textContent(), `${i+1} / ${data.pages.length}`);
          assert.deepEqual(await page.locator('.choice-label').allTextContents(), expected.choices.map(c => c.label));
          assert.equal(await page.locator('.choice-item.selected').count(), expected.choices.filter(c => c.selected && expected.selection_executed).length);
          if (expected.image) {
            await page.waitForFunction(() => {
              const image=document.querySelector('#scene-image');
              return !image.hidden && image.complete && image.naturalWidth > 0;
            });
            assert.equal(await page.locator('#scene-image').getAttribute('src'), expected.image);
            images++;
          }
          if (expected.kind === 'choice') {
            await page.locator('#autoplay').click();
            assert.equal(await page.locator('#autoplay').getAttribute('aria-pressed'), 'false');
          }
        }
        assert.equal(await page.locator('#next').isDisabled(), true);
        assert.equal(await page.locator('#end-note').isVisible(), true);
        await page.locator('body').click({position:{x:2,y:2}});
        await page.keyboard.press('Home');
        assert.equal(await page.locator('#previous').isDisabled(),true);
        await page.keyboard.press('ArrowRight');
        assert.equal(await page.locator('#page-label').textContent(), `${Math.min(2,data.pages.length)} / ${data.pages.length}`);
        const pictureIndex = data.pages.findIndex(p => p.kind === 'story' && p.image);
        if (pictureIndex >= 0) {
          await page.locator('#jump-page').fill(String(pictureIndex+1));
          await page.locator('#jump-button').click();
          await page.waitForFunction(() => !document.querySelector('#scene-image').hidden);
        }
        await page.screenshot({path:path.join(output,data.sample_id+'-desktop.png'),fullPage:true});
        await page.setViewportSize({width:390,height:844});
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
        await page.screenshot({path:path.join(output,data.sample_id+'-mobile.png'),fullPage:true});
        await page.locator('#outline-toggle').click();
        assert.equal(await page.locator('#outline').isVisible(),true);
        await page.locator('.outline-item').last().click();
        assert.equal(await page.locator('#next').isDisabled(),true);
        assert.deepEqual(errors,[]);
        assert.deepEqual(remote,[]);
        assert.equal(await page.evaluate(() => window.PWNED), undefined);
        results.push({sample_id:data.sample_id,pages_checked:data.pages.length,image_pages_checked:images,
          evidence_kind:data.status.evidence_kind,console_errors:errors,remote_requests:remote,
          desktop:true,mobile:true,choice_readonly:true});
        await page.close();
      }
    }
  } finally { await browser.close(); }
  const report={status:'passed',browser:'Chromium',transport:'file://',
    catalog_checks:'recipient_entry_alias_all_samples_filters_search_return_link_desktop_mobile',runs:results,paid_calls:0};
  fs.writeFileSync(path.join(output,'browser-report.json'),JSON.stringify(report,null,2)+'\n');
  process.stdout.write(JSON.stringify(report,null,2)+'\n');
})().catch(error => { console.error(error); process.exitCode=1; });
