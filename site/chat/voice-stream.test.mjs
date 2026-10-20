import test from 'node:test';
import assert from 'node:assert/strict';
import { createVoiceStream, VOICE_STREAM_LIMITS } from './voice-stream.js';
const encoder = new TextEncoder();
const frame = value => encoder.encode(`data: ${JSON.stringify(value)}\r\n\r\n`);
const pcm = samples => {
  const bytes = new Uint8Array(samples.length * 2), view = new DataView(bytes.buffer);
  samples.forEach((sample, index) => view.setInt16(index * 2, sample, true));
  return Buffer.from(bytes).toString('base64');
};
const audio = samples => ({ type: 'audio', data: pcm(samples) });
const done = { type: 'done' };
class FakeAudioContext {
  constructor() { this.state = 'running'; this.currentTime = 10; this.destination = {}; this.sources = []; this.closed = false; }
  createBuffer(channels, length, sampleRate) {
    assert.equal(channels, 1); assert.equal(sampleRate, 24000);
    const samples = new Float32Array(length); return { samples, getChannelData: channel => { assert.equal(channel, 0); return samples; } };
  }
  createBufferSource() {
    const source = { starts: [], stopped: false, disconnected: false, connect() {}, start(time) { this.starts.push(time); }, stop() { this.stopped = true; }, disconnect() { this.disconnected = true; }, end() { this.onended?.(); } };
    this.sources.push(source); return source;
  }
  close() { this.closed = true; }
}
function controlled() {
  let controller, cancelled = false;
  const body = new ReadableStream({ start(c) { controller = c; }, cancel() { cancelled = true; } });
  return { body, push: value => controller.enqueue(value), close: () => controller.close(), cancelled: () => cancelled };
}
function responseFor(rows) {
  return new Response(new ReadableStream({ start(c) { rows.forEach(row => c.enqueue(row instanceof Uint8Array ? row : frame(row))); c.close(); } }), { headers: { 'Content-Type': 'text/event-stream; charset=utf-8' } });
}

test('first PCM is scheduled before the final network chunk; samples, ordering and playback timestamps are distinct', async () => {
  const network = controlled(), context = new FakeAudioContext(), calls = [], metadata = [];
  let firstStarted, elapsed = 100, completed = false;
  const firstPlayback = new Promise(resolve => { firstStarted = resolve; });
  const voice = createVoiceStream({ audioContext: context, text: 'A full reply.', requestId: 'one', verification: 'proof', now: () => elapsed += 10,
    fetcher: async (url, init) => { calls.push({ url, init }); return new Response(network.body, { headers: { 'Content-Type': 'text/event-stream' } }); },
    onFirstPlayback: firstStarted, onProgress: event => metadata.push(event),
  });
  voice.finished.then(() => { completed = true; });
  network.push(frame({ type: 'progress', message: 'Starting café 🪴' })); network.push(frame(audio([-32768, 0, 32767])));
  const first = await firstPlayback;
  assert.equal(completed, false); assert.equal(context.sources.length, 1); assert.deepEqual([...context.sources[0].buffer.samples], [-1, 0, 32767 / 32768]);
  assert.equal(first.firstScheduledContextTime, 10.06); assert(first.firstScheduledAtMs > first.firstAudioReceivedAtMs); assert(first.firstAudioReceivedAtMs > first.firstByteAtMs); assert.equal(metadata.length, 1);
  network.push(frame(audio([16384, -16384]))); network.push(frame(done)); network.close();
  const result = await voice.finished;
  assert.equal(calls.length, 1); assert.equal(calls[0].url, '/api/rhysbot-voice-stream'); assert.equal(calls[0].init.method, 'POST'); assert.equal(calls[0].init.credentials, 'same-origin');
  assert.deepEqual(JSON.parse(calls[0].init.body), { text: 'A full reply.', request_id: 'one', verification_token: 'proof' });
  assert.equal(context.sources.length, 2); assert.deepEqual([...context.sources[1].buffer.samples], [0.5, -0.5]); assert.equal(context.sources[1].starts[0], context.sources[0].starts[0] + 3 / 24000);
  assert.equal(result.pcmBytes, 10); assert.equal(result.durationSeconds, 5 / 24000); assert.equal(result.firstScheduledAtMs, first.firstScheduledAtMs); assert.equal(result.wav.type, 'audio/wav');
  const bytes = await result.wav.arrayBuffer(), view = new DataView(bytes); assert.equal(bytes.byteLength, 54); assert.equal(view.getUint32(24, true), 24000); assert.equal(view.getUint32(40, true), 10);
  assert.deepEqual(Array.from({ length: 5 }, (_, i) => view.getInt16(44 + i * 2, true)), [-32768, 0, 32767, 16384, -16384]);
  let playbackEnded = false; voice.playbackFinished.then(() => { playbackEnded = true; }); await Promise.resolve(); assert.equal(playbackEnded, false);
  context.sources.forEach(source => source.end()); await voice.playbackFinished; assert(context.sources.every(source => source.disconnected));
  // The successful Blob is already local: handing it to a replay player twice
  // has no relationship to this session's sole fetch.
  const replay = { plays: 0, async play(blob) { assert.equal(blob, result.wav); this.plays++; } };
  await replay.play(result.wav); await replay.play(result.wav); assert.equal(calls.length, 1); assert.equal(replay.plays, 2); assert.equal(context.closed, false);
});

test('arbitrary split UTF8 and CRLF boundaries preserve audio and unknown metadata', async () => {
  const context = new FakeAudioContext();
  const bytes = Buffer.concat([Buffer.from(frame({ type: 'future-metadata', value: 'café 🪴' })), Buffer.from(frame(audio([7, -7]))), Buffer.from(frame(done))]);
  const rows = [...bytes].map(byte => new Uint8Array([byte]));
  const voice = createVoiceStream({ audioContext: context, text: 'Reply', requestId: 'split', fetcher: async () => responseFor(rows) });
  const result = await voice.finished; assert.equal(result.pcmBytes, 4); assert.equal(context.sources.length, 1); voice.stop(); await voice.playbackFinished;
});

test('stop and external abort cancel the only request and disconnect all queued sources without a complete WAV', async () => {
  for (const external of [false, true]) {
    const network = controlled(), context = new FakeAudioContext(), controller = new AbortController(); let started;
    const ready = new Promise(resolve => { started = resolve; });
    let count = 0, requestSignal;
    const voice = createVoiceStream({ audioContext: context, text: 'Reply', requestId: 'stop', signal: controller.signal, onFirstPlayback: started,
      fetcher: async (_, init) => { count++; requestSignal = init.signal; return new Response(network.body, { headers: { 'Content-Type': 'text/event-stream' } }); },
    });
    network.push(frame(audio([1, 2]))); await ready;
    if (external) controller.abort(); else voice.stop();
    await assert.rejects(voice.finished, { name: 'AbortError' }); await voice.playbackFinished;
    assert.equal(requestSignal.aborted, true); assert.equal(count, 1); assert.equal(network.cancelled(), true); assert(context.sources.every(source => source.stopped && source.disconnected)); assert.equal(context.closed, false);
  }
});

test('abort after valid completion still stops remaining scheduled playback', async () => {
  const context = new FakeAudioContext(), controller = new AbortController();
  const voice = createVoiceStream({ audioContext: context, text: 'Reply', requestId: 'finished', signal: controller.signal, fetcher: async () => responseFor([audio([1]), done]) });
  const result = await voice.finished; assert.equal(result.wav.size, 46); controller.abort(); await voice.playbackFinished; assert.equal(context.sources[0].stopped, true); assert.equal(context.sources[0].disconnected, true);
});

test('invalid encodings, odd samples, malformed events, provider failure and truncated streams never return a complete clip', async () => {
  const rows = [
    [{ type: 'audio', data: '!!==' }, done],
    [{ type: 'audio', data: 'AQ==' }, done],
    [{ type: 'audio', data: 'AAJ=' }, done], // noncanonical padding bits
    [{ type: 'audio', data: 'AAI' }, done],
    [{ type: 'audio', data: '' }, done],
    [new Uint8Array([0xff])],
    [encoder.encode('data: not-json\n\n')],
    [audio([1]), { type: 'error', message: 'Provider unavailable' }],
    [audio([1])],
    [audio([1]), encoder.encode('data: {"type":"done"}')],
    [done],
  ];
  for (const values of rows) {
    const context = new FakeAudioContext(); let calls = 0;
    const voice = createVoiceStream({ audioContext: context, text: 'Reply', requestId: 'bad', fetcher: async () => { calls++; return responseFor(values); } });
    await assert.rejects(voice.finished); await voice.playbackFinished; assert.equal(calls, 1); assert(context.sources.every(source => source.stopped && source.disconnected));
  }
});

test('PCM byte, duration, declared and actual wire limits reject excess and stop prior playback', async () => {
  const cases = [
    { limits: { pcmBytes: 4 }, rows: [audio([1]), audio([2, 3]), done] },
    { limits: { seconds: 1 / 24000 }, rows: [audio([1, 2]), done] },
    { limits: { wireBytes: 30 }, rows: [audio([1]), done] },
    { limits: { wireBytes: 30 }, headers: { 'Content-Type': 'text/event-stream', 'Content-Length': '31' } },
  ];
  for (const fixture of cases) {
    const context = new FakeAudioContext();
    const voice = createVoiceStream({ audioContext: context, text: 'Reply', requestId: 'limit', limits: fixture.limits, fetcher: async () => fixture.headers ? new Response('', { headers: fixture.headers }) : responseFor(fixture.rows) });
    await assert.rejects(voice.finished, /limit/); assert(context.sources.every(source => source.stopped && source.disconnected));
  }
  assert.equal(VOICE_STREAM_LIMITS.pcmBytes, 8 * 1024 * 1024); assert.equal(VOICE_STREAM_LIMITS.seconds, 150); assert.equal(VOICE_STREAM_LIMITS.wireBytes, 12 * 1024 * 1024);
});

test('HTTP failures preserve verification codes; wrong content type, paused context and deadline fail without retry', async () => {
  const context = new FakeAudioContext(); let calls = 0;
  const denied = createVoiceStream({ audioContext: context, text: 'Reply', requestId: 'denied', fetcher: async () => { calls++; return Response.json({ error: 'verification_required' }, { status: 403 }); } });
  await assert.rejects(denied.finished, { code: 'verification_required', status: 403 }); assert.equal(calls, 1);
  const html = createVoiceStream({ audioContext: context, text: 'Reply', requestId: 'html', fetcher: async () => new Response('not audio') }); await assert.rejects(html.finished, /audio stream/);
  context.state = 'suspended';
  const paused = createVoiceStream({ audioContext: context, text: 'Reply', requestId: 'paused', fetcher: async () => responseFor([audio([1]), done]) }); await assert.rejects(paused.finished, /paused/);
  let aborted = false;
  const hanging = createVoiceStream({ audioContext: new FakeAudioContext(), text: 'Reply', requestId: 'deadline', timeoutMs: 5, fetcher: (_, init) => new Promise((_, reject) => { init.signal.addEventListener('abort', () => { aborted = true; reject(init.signal.reason); }, { once: true }); }) });
  const keepAlive = setTimeout(() => {}, 30); try { await assert.rejects(hanging.finished, { name: 'AbortError' }); assert.equal(aborted, true); } finally { clearTimeout(keepAlive); }
});
