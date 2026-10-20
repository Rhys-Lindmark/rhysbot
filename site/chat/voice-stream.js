// The caller creates and resumes AudioContext in the original user gesture.
// This module owns only its source nodes and one request; it never closes a
// shared context, retries a request, or marks interrupted PCM as a complete clip.
export const VOICE_STREAM_LIMITS = Object.freeze({ pcmBytes: 8 * 1024 * 1024, seconds: 150, wireBytes: 12 * 1024 * 1024 });
const SAMPLE_RATE = 24000;
const abortError = () => new DOMException('Voice playback stopped.', 'AbortError');

function limitsFor(overrides = {}) {
  return Object.fromEntries(Object.entries(VOICE_STREAM_LIMITS).map(([key, maximum]) => {
    const requested = overrides[key] ?? maximum;
    if (!Number.isFinite(requested) || requested <= 0) throw new Error('Invalid voice stream limit.');
    return [key, Math.min(requested, maximum)];
  }));
}

function decodePCM(value) {
  // Require canonical padded base64, including zero unused padding bits.
  if (typeof value !== 'string' || !value.length || value.length % 4 || !/^[A-Za-z0-9+/]*={0,2}$/.test(value)) throw new Error('Invalid voice audio encoding.');
  let binary;
  try { binary = atob(value); } catch { throw new Error('Invalid voice audio encoding.'); }
  if (btoa(binary) !== value || !binary.length || binary.length % 2) throw new Error('Invalid voice PCM samples.');
  const pcm = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index++) pcm[index] = binary.charCodeAt(index);
  return pcm;
}

function wavFromPCM(parts, bytes) {
  const header = new Uint8Array(44), view = new DataView(header.buffer), encode = new TextEncoder();
  header.set(encode.encode('RIFF'), 0); header.set(encode.encode('WAVEfmt '), 8); header.set(encode.encode('data'), 36);
  view.setUint32(4, bytes + 36, true); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, SAMPLE_RATE, true); view.setUint32(28, SAMPLE_RATE * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true); view.setUint32(40, bytes, true);
  return new Blob([header, ...parts], { type: 'audio/wav' });
}

async function* events(stream, signal, maxBytes, onFirstByte) {
  const reader = stream.getReader(), decoder = new TextDecoder('utf-8', { fatal: true });
  let pending = '', received = 0;
  const abort = () => { reader.cancel().catch(() => {}); };
  signal.addEventListener('abort', abort, { once: true });
  const parse = frame => {
    const lines = frame.split(/\r?\n/).filter(line => line.startsWith('data:'));
    if (!lines.length) return null;
    const data = lines.map(line => line.slice(5).replace(/^ /, '')).join('\n');
    if (!data) return null;
    const event = JSON.parse(data);
    if (!event || typeof event !== 'object' || Array.isArray(event) || typeof event.type !== 'string') throw new Error('Invalid voice stream event.');
    return event;
  };
  try {
    for (;;) {
      signal.throwIfAborted();
      const { value, done } = await reader.read();
      signal.throwIfAborted();
      if (done) {
        pending += decoder.decode();
        // A terminal event must end with a complete SSE frame, not a partial
        // transport frame that happens to contain parseable JSON.
        if (pending.trim()) throw new Error('Voice stream ended with an incomplete event.');
        return;
      }
      if (!value?.byteLength) continue;
      if (!received) onFirstByte();
      received += value.byteLength;
      if (received > maxBytes) throw new Error('Voice stream exceeded its wire limit.');
      pending += decoder.decode(value, { stream: true });
      let boundary;
      while ((boundary = /\r?\n\r?\n/.exec(pending))) {
        const frame = pending.slice(0, boundary.index); pending = pending.slice(boundary.index + boundary[0].length);
        const event = parse(frame); if (event) yield event;
      }
    }
  } finally {
    signal.removeEventListener('abort', abort);
    try { await reader.cancel(); } catch {}
    reader.releaseLock();
  }
}

async function requestError(response) {
  let code;
  if (response.body) {
    const reader = response.body.getReader(), chunks = []; let count = 0;
    try {
      for (;;) {
        const { value, done } = await reader.read(); if (done) break;
        count += value.byteLength; if (count > 4096) break; chunks.push(value);
      }
      if (count <= 4096) {
        const value = JSON.parse(await new Blob(chunks).text());
        if (typeof value?.error === 'string' && value.error.length <= 100) code = value.error;
      }
    } catch {} finally { try { await reader.cancel(); } catch {} reader.releaseLock(); }
  }
  const error = new Error('Rhys voice could not start. No automatic retry was sent.');
  error.code = code; error.status = response.status; return error;
}

/**
 * Begin one POST and play PCM chunks as they arrive.
 *
 * const context = new AudioContext(); const resumed = context.resume(); // click
 * const voice = createVoiceStream({audioContext: context, text, requestId, ...});
 * const {wav} = await voice.finished; // cache only this complete result for replay
 * await voice.playbackFinished;      // all scheduled nodes have ended
 * voice.stop();                     // cancel network + stop/disconnect every node
 *
 * onFirstPlayback reports when samples were SCHEDULED to begin on the audio
 * context clock; this is distinct from a network byte or proof of audibility.
 * The caller owns context.resume()/close() and URL creation/revocation for WAV.
 */
export function createVoiceStream({ audioContext, text, requestId, verification = '', fetcher = fetch, signal, onFirstPlayback = () => {}, onProgress = () => {}, timeoutMs = 180000, jitterSeconds = 0.06, now = () => performance.now(), limits: requestedLimits } = {}) {
  if (!audioContext || typeof audioContext.createBuffer !== 'function' || typeof audioContext.createBufferSource !== 'function' || audioContext.state === 'closed') throw new Error('Audio playback is unavailable.');
  if (typeof text !== 'string' || !text.trim() || typeof requestId !== 'string' || !requestId || !Number.isFinite(timeoutMs) || timeoutMs <= 0 || timeoutMs > 240000 || !Number.isFinite(jitterSeconds) || jitterSeconds < 0.04 || jitterSeconds > 0.08) throw new Error('Invalid voice stream request.');
  const limits = limitsFor(requestedLimits), controller = new AbortController(), sources = new Set(), pcmParts = [];
  const startedAtMs = now();
  let pcmBytes = 0, nextStart = 0, networkDone = false, firstByteAtMs = null, firstAudioReceivedAtMs = null, firstScheduledAtMs = null, firstScheduledContextTime = null, playbackResolved = false, resolvePlayback;
  const playbackFinished = new Promise(resolve => { resolvePlayback = resolve; });
  const settlePlayback = () => {
    if (!playbackResolved && (controller.signal.aborted || (networkDone && !sources.size))) { playbackResolved = true; signal?.removeEventListener('abort', externalAbort); resolvePlayback(); }
  };
  const stop = () => {
    if (!controller.signal.aborted) controller.abort(abortError());
    for (const source of sources) { source.onended = null; try { source.stop(); } catch {} try { source.disconnect(); } catch {} }
    sources.clear(); pcmParts.length = 0; settlePlayback();
  };
  const externalAbort = () => stop();
  signal?.addEventListener('abort', externalAbort, { once: true });
  if (signal?.aborted) stop();
  const timeout = setTimeout(stop, timeoutMs); timeout.unref?.();
  const finished = (async () => {
    try {
      controller.signal.throwIfAborted();
      const response = await fetcher('/api/rhysbot-voice-stream', {
        method: 'POST', credentials: 'same-origin', redirect: 'error', cache: 'no-store',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body: JSON.stringify({ text, request_id: requestId, ...(verification ? { verification_token: verification } : {}) }), signal: controller.signal,
      });
      controller.signal.throwIfAborted();
      if (!response.ok) throw await requestError(response);
      if (response.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase() !== 'text/event-stream' || !response.body) throw new Error('The voice service did not return an audio stream.');
      const declaredBytes = response.headers.get('Content-Length');
      if (declaredBytes !== null && (!/^\d+$/.test(declaredBytes) || Number(declaredBytes) > limits.wireBytes)) throw new Error('Voice stream exceeded its wire limit.');
      for await (const event of events(response.body, controller.signal, limits.wireBytes, () => { firstByteAtMs ??= now(); })) {
        controller.signal.throwIfAborted();
        if (event.type === 'error') throw new Error(typeof event.message === 'string' ? event.message.slice(0, 300) : 'Voice generation failed.');
        if (event.type === 'done') {
          if (!pcmBytes) throw new Error('The voice stream returned no audio.');
          networkDone = true;
          const wav = wavFromPCM(pcmParts, pcmBytes); pcmParts.length = 0;
          settlePlayback();
          return { wav, pcmBytes, durationSeconds: pcmBytes / (SAMPLE_RATE * 2), startedAtMs, firstByteAtMs, firstAudioReceivedAtMs, firstScheduledAtMs, firstScheduledContextTime };
        }
        if (event.type !== 'audio') { onProgress(event); continue; }
        firstAudioReceivedAtMs ??= now();
        const pcm = decodePCM(event.data), samples = pcm.byteLength / 2;
        if (pcmBytes + pcm.byteLength > limits.pcmBytes || (pcmBytes + pcm.byteLength) / (SAMPLE_RATE * 2) > limits.seconds) throw new Error('Voice audio exceeded its playback limit.');
        if (audioContext.state && audioContext.state !== 'running') throw new Error('Audio playback is paused. Resume audio before starting a new request.');
        const view = new DataView(pcm.buffer, pcm.byteOffset, pcm.byteLength), buffer = audioContext.createBuffer(1, samples, SAMPLE_RATE), channel = buffer.getChannelData(0);
        for (let index = 0; index < samples; index++) channel[index] = view.getInt16(index * 2, true) / 32768;
        const source = audioContext.createBufferSource(); source.buffer = buffer; source.connect(audioContext.destination); sources.add(source);
        source.onended = () => { sources.delete(source); try { source.disconnect(); } catch {} settlePlayback(); };
        const scheduled = Math.max(audioContext.currentTime + jitterSeconds, nextStart); nextStart = scheduled + samples / SAMPLE_RATE;
        source.start(scheduled);
        if (firstScheduledAtMs === null) {
          firstScheduledContextTime = scheduled; firstScheduledAtMs = now() + Math.max(0, scheduled - audioContext.currentTime) * 1000;
          onFirstPlayback({ startedAtMs, firstByteAtMs, firstAudioReceivedAtMs, firstScheduledAtMs, firstScheduledContextTime });
        }
        pcmBytes += pcm.byteLength; pcmParts.push(pcm);
      }
      throw new Error('Voice stream ended before completion.');
    } catch (error) {
      stop(); throw error;
    } finally {
      clearTimeout(timeout);
    }
  })();
  return { finished, playbackFinished, stop };
}
