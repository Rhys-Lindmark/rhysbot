// Browser-only integrity check is independently importable without DOM side effects.
export async function verifiedArtifactBlob(response) {
  if (!response.ok || response.headers.get('Content-Type')?.split(';')[0] !== 'application/zip' || !response.body) throw new Error('Trained artifact is unavailable');
  const expected = response.headers.get('X-Rhysbot-Artifact-Bytes');
  const expectedHash = response.headers.get('X-Rhysbot-Artifact-SHA256');
  const length = response.headers.get('Content-Length');
  if (!/^[1-9]\d*$/.test(expected || '') || !Number.isSafeInteger(Number(expected)) || Number(expected) > 100 * 1024 * 1024 || !/^[a-f0-9]{64}$/.test(expectedHash || '') || (length !== null && length !== expected)) {
    await response.body.cancel(); throw new Error('Artifact integrity metadata is missing or invalid');
  }
  const size = Number(expected), data = new Uint8Array(size), reader = response.body.getReader(); let total = 0;
  try {
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      if (total + value.byteLength > size) throw new Error('Artifact exceeds its verified size');
      data.set(value, total); total += value.byteLength;
    }
  } finally { try { await reader.cancel(); } catch {} reader.releaseLock(); }
  if (total !== size) throw new Error('Incomplete artifact; download was not saved');
  const digest = [...new Uint8Array(await crypto.subtle.digest('SHA-256', data))].map(n => n.toString(16).padStart(2, '0')).join('');
  if (digest !== expectedHash) throw new Error('Artifact hash mismatch; download was not saved');
  return new Blob([data], { type: 'application/zip' });
}
