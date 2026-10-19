import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

function node() {
  return { value: '', options: [], children: [], textContent: '', append(...xs) { this.children.push(...xs); },
    addEventListener() {}, replaceChildren() { this.children = []; }, scrollIntoView() {} };
}
async function browser() {
  const html = fs.readFileSync(new URL('./index.html', import.meta.url), 'utf8');
  const stats = JSON.parse(fs.readFileSync(new URL('../data/public/stats.json', import.meta.url), 'utf8'));
  const elements = new Map();
  const context = vm.createContext({ document: { querySelector(selector) {
    if (!elements.has(selector)) elements.set(selector, node()); return elements.get(selector);
  }, createElement: node }, location: { href: 'https://ai.rhyslindmark.com/rhysbot/', search: '' },
  history: { replaceState() {} }, URL, URLSearchParams, Intl, TextEncoder,
  setTimeout, clearTimeout, fetch: async url => ({ ok: true, json: async () => url.includes('stats.json') ? stats : { enabled_models: [] } }) });
  vm.runInContext(html.match(/<script type="module">([\s\S]*?)<\/script>/)[1], context);
  await new Promise(resolve => setImmediate(resolve));
  return { context, elements, html, stats };
}

test('five collections display exact released byte counts and keep old source URLs compatible', async () => {
  const { context, elements, stats } = await browser();
  assert.equal(elements.get('#source-links').children.length, 5);
  assert.equal(elements.get('#corpus-byte-table').children.length, 5);
  assert.match(elements.get('#corpus-byte-summary').textContent, new RegExp(new Intl.NumberFormat('en-US').format(stats.text_utf8_bytes)));
  assert.equal(vm.runInContext("sourceAliases.substack === sourceAliases.linked_writing", context), true);
  assert.equal(vm.runInContext("sourceAliases.youtube_transcript === sourceAliases.podcast_transcript", context), true);
  const result = vm.runInContext("allSourcePreview([{id:'a',source_type:'anki'},{id:'b',source_type:'substack'},{id:'c',source_type:'linked_writing'},{id:'d',source_type:'youtube_transcript'},{id:'e',source_type:'podcast_transcript'}]).slice(0,3).map(rowCollection).join(',')", context);
  assert.equal(result, 'writing,podcasts_youtube,anki');
});

test('all four graph assets exist and the demo never replays generation on busy', async () => {
  const { html } = await browser();
  for (const name of ['training-loss', 'nanochat-validation', 'qwen-evaluation', 'corpus-bytes']) {
    assert.ok(html.includes(`charts/${name}.svg`));
    assert.ok(fs.statSync(new URL(`./charts/${name}.svg`, import.meta.url)).size > 1000);
  }
  assert.ok(html.includes('attempt<3'));
  assert.equal((html.match(/method:'POST'/g) || []).length, 1);
  assert.ok(html.includes('operator recovery check'));
});
