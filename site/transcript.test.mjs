import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const id = 'podcast-' + 'a'.repeat(20);
const fixture = { id, title: 'A public conversation', audio_duration_seconds: 90,
  source_url: 'https://soundcloud.com/the-rhys-show/example', youtube_url: 'https://www.youtube.com/watch?v=example',
  rss_enclosure_url: 'https://feeds.soundcloud.com/stream/123-example.mp3', limitations: ['Automatic transcript.'],
  turns: [{ start: 0, end: 2, speaker_id: 'speaker_0', speaker: 'Rhys Lindmark', text: 'A question.' },
    { start: 2, end: 4, speaker_id: 'speaker_1', speaker: 'Guest Speaker 2', text: '<img src=x onerror=evil()>' },
    { start: 4, end: 5, speaker_id: 'unknown', speaker: 'Unknown / overlap', text: null }] };

function element() { return { value: '', children: [], append(...values) { this.children.push(...values); },
  addEventListener() {}, setAttribute() {}, replaceChildren() { this.children = []; } }; }
async function load(episode) {
  const elements = new Map(); let fetches = 0;
  const document = { querySelector(key) { if (!elements.has(key)) elements.set(key, element()); return elements.get(key); }, createElement: element };
  const context = vm.createContext({ document, location: { search: '?episode=' + encodeURIComponent(episode) }, URL, URLSearchParams,
    fetch: async url => { fetches++; assert.equal(url, `https://raw.githubusercontent.com/Rhys-Lindmark/rhysbot/main/data/public/speaker-transcripts/${id}.json`); return { ok: true, json: async () => fixture }; } });
  const script = fs.readFileSync(new URL('./transcript.html', import.meta.url), 'utf8').match(/<script type="module">([\s\S]*?)<\/script>/)[1];
  await vm.runInContext('(async()=>{' + script + '})()', context);
  return { elements, fetches };
}

test('reader shows distinct voices, withheld turns and literal source text without fetching audio', async () => {
  const { elements, fetches } = await load(id);
  assert.equal(fetches, 1);
  assert.equal(elements.get('#title').textContent, fixture.title);
  assert.equal(elements.get('#speaker').children.length, 3);
  const cards = elements.get('#turns').children;
  assert.equal(cards.length, 3);
  assert.equal(cards[0].children[0].children[1].textContent, 'Rhys Lindmark');
  assert.equal(cards[1].children[1].textContent, '<img src=x onerror=evil()>');
  assert.match(cards[2].children[1].textContent, /Withheld/);
  assert.equal(elements.get('#audio'), undefined);
});
test('reader rejects arbitrary episode paths before network access', async () => {
  const { elements, fetches } = await load('../../private');
  assert.equal(fetches, 0);
  assert.equal(elements.get('#title').textContent, 'Choose an episode from the corpus.');
});
