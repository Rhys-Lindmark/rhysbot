// Canonical source mirrored verbatim into Pages functions/api/rhysbot/train-core.js.
// No third-party SDK, keys, cloud provisioning, automatic retry or GPU execution.
export const ORIGIN = 'https://ai.rhyslindmark.com';
export const MAX_BYTES = 5 * 1024 * 1024;
export const RECIPE = 'qwen-lora-v1';
export const LIMITS = Object.freeze({ timeout_seconds: 900, max_cost_usd: 3.60 });
const enc = new TextEncoder();
const decoder = new TextDecoder('utf-8', { fatal: true });
const ID = /^job_[a-f0-9]{32}$/;
const ACTIVE = new Set(['dispatching', 'queued', 'running', 'recovery_required']);
const BRIDGE_STATES = new Set(['queued', 'running', 'completed', 'failed', 'budget_blocked']);
const hex = bytes => [...new Uint8Array(bytes)].map(x => x.toString(16).padStart(2, '0')).join('');
const unhex = str => Uint8Array.from(str.match(/../g) || [], x => parseInt(x, 16));
const fail = (status, code) => { throw Object.assign(new Error(code), { status, code }); };
const response = (body, status = 200) => Response.json(body, { status, headers: {
  'Cache-Control': 'no-store', 'Access-Control-Allow-Origin': ORIGIN,
  'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
} });
// Pages treats every sibling .js as a route; this shared-module route is inert.
export function onRequest() { return response({ error: 'not_found' }, 404); }
async function key(secret) { return crypto.subtle.importKey('raw', enc.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign', 'verify']); }
async function mac(secret, text) { return hex(await crypto.subtle.sign('HMAC', await key(secret), enc.encode(text))); }
async function verified(secret, signature, text) {
  return /^[a-f0-9]{64}$/.test(signature || '') && crypto.subtle.verify('HMAC', await key(secret), unhex(signature), enc.encode(text));
}
export async function sha256(bytes) { return hex(await crypto.subtle.digest('SHA-256', bytes)); }
export async function boundedBody(request, limit) {
  const length = request.headers.get('content-length');
  if (length && (!/^\d+$/.test(length) || Number(length) > limit)) fail(413, 'body_too_large');
  if (!request.body) fail(400, 'empty_body');
  const reader = request.body.getReader(); const parts = []; let size = 0;
  try {
    for (;;) { const { value, done } = await reader.read(); if (done) break;
      size += value.byteLength; if (size > limit) { await reader.cancel(); fail(413, 'body_too_large'); } parts.push(value);
    }
  } finally { reader.releaseLock(); }
  const output = new Uint8Array(size); let offset = 0;
  for (const part of parts) { output.set(part, offset); offset += part.length; }
  return output;
}
function json(bytes) { try { return JSON.parse(decoder.decode(bytes)); } catch { fail(400, 'invalid_json'); } }
function exact(obj, keys) { return obj && !Array.isArray(obj) && Object.keys(obj).every(k => keys.includes(k)); }
export function validateCorpus(bytes, format) {
  if (!bytes.length || bytes.length > MAX_BYTES) fail(413, 'corpus_size');
  let text; try { text = decoder.decode(bytes); } catch { fail(400, 'invalid_utf8'); }
  if (!text.trim() || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(text)) fail(400, 'binary_or_empty_corpus');
  if (format === 'txt') return;
  if (format !== 'jsonl') fail(400, 'unsupported_format');
  let count = 0;
  for (const line of text.split('\n')) {
    if (!line.trim()) continue;
    if (enc.encode(line).length > 32768) fail(400, 'jsonl_line_too_large');
    let row; try { row = JSON.parse(line); } catch { fail(400, 'invalid_jsonl'); }
    if (!exact(row, ['text']) || typeof row.text !== 'string' || !row.text.trim() || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(row.text)) fail(400, 'jsonl_text_only');
    count++;
  }
  if (!count) fail(400, 'empty_corpus');
}
function bridgeURL(value) {
  try { const u = new URL(value); if (u.protocol !== 'https:' || !u.hostname.endsWith('.modal.run') || u.username || u.password || u.port || u.search || u.hash || !['','/'].includes(u.pathname)) return null;
    return u.origin;
  } catch { return null; }
}
export function configuration(env) {
  const stripe = /^sk_test_\S+$/.test(env.RHYSBOT_TRAIN_STRIPE_KEY || '') && /^whsec_\S+$/.test(env.RHYSBOT_TRAIN_STRIPE_WEBHOOK_SECRET || '') && /^price_[A-Za-z0-9]+$/.test(env.RHYSBOT_TRAIN_STRIPE_PRICE || '') && /^\d+$/.test(env.RHYSBOT_TRAIN_PRICE_CENTS || '') && Number(env.RHYSBOT_TRAIN_PRICE_CENTS) > 0;
  // Presence of any Stripe configuration disables fallback: a live/mixed key fails closed.
  const anyStripe = [env.RHYSBOT_TRAIN_STRIPE_KEY, env.RHYSBOT_TRAIN_STRIPE_WEBHOOK_SECRET, env.RHYSBOT_TRAIN_STRIPE_PRICE].some(Boolean);
  const mode = stripe ? 'stripe_test' : !anyStripe && env.RHYSBOT_TRAIN_SANDBOX_QUOTA === '1' ? 'sandbox' : 'unavailable';
  const ready = Boolean(mode !== 'unavailable' && env.RHYSBOT_TRAIN_SANDBOX_QUOTA === '1' && env.RHYSBOT_TRAIN_DB && env.RHYSBOT_TRAIN_AUTH_SECRET?.length >= 32 && env.RHYSBOT_TRAIN_BRIDGE_SECRET?.length >= 32 && bridgeURL(env.RHYSBOT_TRAIN_BRIDGE_URL) && env.MODAL_PROXY_KEY && env.MODAL_PROXY_SECRET && env.RHYSBOT_TURNSTILE_SECRET && env.RHYSBOT_TURNSTILE_SITE_KEY);
  return { ready, mode, stripe, bridge: bridgeURL(env.RHYSBOT_TRAIN_BRIDGE_URL) };
}
function statement(env, sql, args = []) { return env.RHYSBOT_TRAIN_DB.prepare(sql).bind(...args); }
async function one(env, sql, args = []) { return statement(env, sql, args).first(); }
async function run(env, sql, args = []) { return statement(env, sql, args).run(); }
async function authorized(request, env, id) {
  if (!ID.test(id || '')) fail(404, 'job_not_found');
  const token = request.headers.get('authorization')?.match(/^Bearer ([a-f0-9]{64})$/)?.[1];
  const job = await one(env, 'SELECT * FROM training_jobs WHERE id = ?', [id]);
  if (!job || !token || !await verified(env.RHYSBOT_TRAIN_AUTH_SECRET, job.auth_mac, `job-v1:${id}:${token}`)) fail(404, 'job_not_found');
  return job;
}
async function serviceAuthorized(request, env) {
  const incoming = request.headers.get('authorization') || '';
  if (!incoming.startsWith('Bearer ') || incoming.length > 512) fail(401, 'unauthorized');
  // WebCrypto verification compares a fixed HMAC of the service credential.
  const expected = await mac(env.RHYSBOT_TRAIN_AUTH_SECRET, `service:${env.RHYSBOT_TRAIN_BRIDGE_SECRET}`);
  if (!await verified(env.RHYSBOT_TRAIN_AUTH_SECRET, expected, `service:${incoming.replace(/^Bearer /, '')}`)) fail(401, 'unauthorized');
}
async function fetchJSON(fetcher, url, init, limit = 65536) {
  const r = await fetcher(url, { ...init, redirect: 'manual', signal: AbortSignal.timeout(15000) });
  if (!r.ok) fail(502, 'upstream_unavailable');
  return json(await boundedBody(r, limit));
}
function bridgeHeaders(env) { return { 'Content-Type': 'application/json', Authorization: `Bearer ${env.RHYSBOT_TRAIN_BRIDGE_SECRET}`, 'Modal-Key': env.MODAL_PROXY_KEY, 'Modal-Secret': env.MODAL_PROXY_SECRET }; }
async function stripeRequest(fetcher, env, path, form, idempotency) {
  return fetchJSON(fetcher, `https://api.stripe.com/v1/${path}`, { method: form ? 'POST' : 'GET', headers: {
    Authorization: `Bearer ${env.RHYSBOT_TRAIN_STRIPE_KEY}`, 'Stripe-Version': '2025-02-24.acacia',
    ...(form ? { 'Content-Type': 'application/x-www-form-urlencoded', 'Idempotency-Key': idempotency } : {}),
  }, ...(form ? { body: form.toString() } : {}) });
}
async function human(request, env, fetcher) {
  const token = request.headers.get('X-Turnstile-Token');
  if (!token || token.length > 2048) fail(403, 'verification_required');
  const result = await fetchJSON(fetcher, 'https://challenges.cloudflare.com/turnstile/v0/siteverify', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ secret: env.RHYSBOT_TURNSTILE_SECRET, response: token }) });
  if (result.success !== true || result.hostname !== 'ai.rhyslindmark.com' || result.action !== 'rhysbot_train') fail(403, 'verification_failed');
}
function publicJob(job) { return { job_id: job.id, state: job.state, recipe: RECIPE, authorization_mode: job.authorization_mode || null, corpus_bytes: job.bytes, created_at: job.created_at, expires_at: job.expires_at, artifact_available: job.state === 'completed', message_code: job.message_code || null }; }
async function dispatch(env, job, fetcher, now) {
  // Called only after a durable payment/sandbox claim; never from public status.
  try {
    const result = await fetchJSON(fetcher, `${configuration(env).bridge}/dispatch`, { method: 'POST', headers: bridgeHeaders(env), body: JSON.stringify({
      job_id: job.id, idempotency_key: job.id, recipe: RECIPE, authorization_mode: job.authorization_mode,
      corpus: { url: `${ORIGIN}/api/rhysbot/train-data?job=${job.id}`, sha256: job.sha256, bytes: job.bytes, format: job.format }, limits: LIMITS,
    }) });
    if (result.job_id !== job.id || !BRIDGE_STATES.has(result.state)) throw new Error('invalid_bridge_result');
    await run(env, 'UPDATE training_jobs SET state = ?, updated_at = ? WHERE id = ? AND state = ?', [result.state, now, job.id, 'dispatching']);
  } catch {
    await run(env, 'UPDATE training_jobs SET state = ?, message_code = ?, updated_at = ? WHERE id = ? AND state = ?', ['recovery_required', 'dispatch_uncertain_no_retry', now, job.id, 'dispatching']);
  }
}
export async function verifyStripeSignature(bytes, header, secret, now) {
  const fields = (header || '').split(',').map(x => x.trim().split('='));
  const timestamps = fields.filter(x => x[0] === 't').map(x => x[1]);
  const signatures = fields.filter(x => x[0] === 'v1').map(x => x[1]);
  if (timestamps.length !== 1 || !/^\d+$/.test(timestamps[0]) || Math.abs(now - Number(timestamps[0])) > 300) return false;
  // Verify exact bytes, before decoding/parsing (including any UTF8 BOM).
  const prefix = enc.encode(`${timestamps[0]}.`); const signed = new Uint8Array(prefix.length + bytes.length);
  signed.set(prefix); signed.set(bytes, prefix.length); const signingKey = await key(secret);
  for (const signature of signatures) if (/^[a-f0-9]{64}$/.test(signature || '') && await crypto.subtle.verify('HMAC', signingKey, unhex(signature), signed)) return true;
  return false;
}
export function createTrainingHandler({ fetcher = fetch, clock = () => Math.floor(Date.now() / 1000) } = {}) {
  return async ({ request, env }) => {
    try {
      const url = new URL(request.url); const action = url.pathname.split('/').at(-1); const now = clock(); const config = configuration(env);
      if (url.origin !== ORIGIN) fail(403, 'origin_denied');
      if (action === 'train' && request.method === 'GET') {
        const used = config.ready && await one(env, 'SELECT job_id FROM training_sandbox_claim WHERE singleton=1');
        return response({ available: config.ready && !used, payment_mode: config.ready ? config.mode : 'unavailable', pilot_quota_available: config.ready && !used, test_only: true, recipe: RECIPE, max_corpus_bytes: MAX_BYTES, limits: LIMITS, price_cents: config.stripe ? Number(env.RHYSBOT_TRAIN_PRICE_CENTS) : null, currency: 'usd', turnstile_site_key: env.RHYSBOT_TURNSTILE_SITE_KEY || null });
      }
      if (!config.ready) fail(503, 'training_not_configured');
      if (!['train-webhook','train-data'].includes(action) && request.headers.get('Origin') !== ORIGIN && !(request.method === 'GET' && !request.headers.has('Origin'))) fail(403, 'origin_denied');
      if (action === 'train-upload' && request.method === 'POST') {
        if (await one(env, 'SELECT job_id FROM training_sandbox_claim WHERE singleton=1')) fail(409, 'test_quota_exhausted');
        await human(request, env, fetcher);
        const format = url.searchParams.get('format');
        if (!['txt','jsonl'].includes(format)) fail(400, 'unsupported_format');
        const expected = format === 'txt' ? 'text/plain' : 'application/x-ndjson';
        if (request.headers.get('Content-Type')?.split(';')[0] !== expected) fail(415, 'content_type');
        const bytes = await boundedBody(request, MAX_BYTES); validateCorpus(bytes, format);
        const id = `job_${crypto.randomUUID().replaceAll('-', '')}`; const token = hex(crypto.getRandomValues(new Uint8Array(32)));
        const digest = await sha256(bytes); const objectKey = `private-training/${id}/corpus.${format}`;
        const storage = env.RHYSBOT_TRAIN_BUCKET ? 'r2' : 'd1';
        // At most ten lifetime uploads / 50MiB. D1 chunks + admission are atomic.
        const admission = statement(env, 'INSERT INTO training_jobs (id,auth_mac,state,format,bytes,sha256,object_key,storage,created_at,updated_at,expires_at,stripe_price,amount) SELECT ?,?,?,?,?,?,?,?,?,?,?,?,? WHERE (SELECT COUNT(*) FROM training_jobs)<10 AND NOT EXISTS (SELECT 1 FROM training_sandbox_claim) RETURNING id', [id, await mac(env.RHYSBOT_TRAIN_AUTH_SECRET, `job-v1:${id}:${token}`), storage === 'r2' ? 'uploading' : 'uploaded', format, bytes.length, digest, objectKey, storage, now, now, now + 7 * 86400, config.stripe ? env.RHYSBOT_TRAIN_STRIPE_PRICE : 'sandbox', config.stripe ? Number(env.RHYSBOT_TRAIN_PRICE_CENTS) : 0]);
        let admitted;
        if (storage === 'd1') {
          const queries = [admission];
          for (let offset = 0, ordinal = 0; offset < bytes.length; offset += 262144, ordinal++) {
            const chunk = bytes.slice(offset, offset + 262144);
            queries.push(statement(env, 'INSERT INTO training_corpus_chunks (job_id,ordinal,bytes,sha256,data) SELECT id,?,?,?,? FROM training_jobs WHERE id=?', [ordinal, chunk.length, await sha256(chunk), chunk.buffer, id]));
          }
          const results = await env.RHYSBOT_TRAIN_DB.batch(queries);
          admitted = results[0]?.results?.[0];
        } else admitted = await admission.first();
        if (!admitted) fail(409, 'upload_quota_exhausted');
        if (storage === 'r2') try {
          await env.RHYSBOT_TRAIN_BUCKET.put(objectKey, bytes, { httpMetadata: { contentType: expected }, customMetadata: { sha256: digest } });
          await run(env, "UPDATE training_jobs SET state='uploaded' WHERE id=? AND state='uploading'", [id]);
        } catch (error) {
          await run(env, "UPDATE training_jobs SET state='failed',message_code='upload_failed' WHERE id=? AND state='uploading'", [id]);
          await env.RHYSBOT_TRAIN_BUCKET.delete(objectKey); throw error;
        }
        return response({ job_id: id, access_token: token, state: 'uploaded', corpus_bytes: bytes.length, sha256: digest }, 201);
      }
      if (action === 'train-data' && request.method === 'GET') {
        await serviceAuthorized(request, env); const id = url.searchParams.get('job'); if (!ID.test(id || '')) fail(404, 'job_not_found');
        const job = await one(env, 'SELECT * FROM training_jobs WHERE id = ?', [id]);
        if (!job || !job.authorization_mode || !ACTIVE.has(job.state) || job.expires_at < now) fail(404, 'job_not_found');
        let body;
        if (job.storage === 'd1') {
          const count = await one(env, 'SELECT COUNT(*) AS count, SUM(bytes) AS bytes FROM training_corpus_chunks WHERE job_id=?', [job.id]);
          if (count.count !== Math.ceil(job.bytes / 262144) || count.bytes !== job.bytes) fail(409, 'corpus_unavailable');
          let ordinal = 0;
          // Only one 256KiB BLOB result at a time; at most 22 D1 queries total.
          body = new ReadableStream({ async pull(controller) {
            try {
              if (ordinal === count.count) { controller.close(); return; }
              const chunk = await one(env, 'SELECT bytes,sha256,data FROM training_corpus_chunks WHERE job_id=? AND ordinal=?', [job.id, ordinal++]);
              const data = chunk?.data instanceof ArrayBuffer ? new Uint8Array(chunk.data) : Uint8Array.from(chunk?.data || []);
              if (!chunk || data.length !== chunk.bytes || await sha256(data) !== chunk.sha256) throw new Error('corpus_integrity');
              controller.enqueue(data);
            } catch (error) { controller.error(error); }
          } });
        } else {
          const object = await env.RHYSBOT_TRAIN_BUCKET?.get(job.object_key);
          if (!object || object.size !== job.bytes || object.customMetadata?.sha256 !== job.sha256) fail(409, 'corpus_unavailable');
          body = object.body;
        }
        return new Response(body, { headers: { 'Content-Type': 'application/octet-stream', 'Content-Length': String(job.bytes), 'Cache-Control': 'no-store', 'X-Corpus-SHA256': job.sha256 } });
      }
      if (action === 'train-artifact' && request.method === 'GET') {
        const job = await authorized(request, env, url.searchParams.get('job'));
        if (job.state !== 'completed') fail(409, 'artifact_not_ready');
        const upstream = await fetcher(`${config.bridge}/artifact?job=${job.id}`, { method: 'GET', headers: bridgeHeaders(env), redirect: 'manual', signal: AbortSignal.timeout(230000) });
        const size = upstream.headers.get('Content-Length'); const maxArtifact = 100 * 1024 * 1024;
        if (upstream.status !== 200 || upstream.headers.get('Content-Type')?.split(';')[0] !== 'application/zip' || !upstream.body || (size && (!/^\d+$/.test(size) || Number(size) > maxArtifact || Number(size) < 1))) { await upstream.body?.cancel(); fail(502, 'artifact_unavailable'); }
        let count = 0;
        const bounded = new TransformStream({ transform(chunk, controller) { count += chunk.byteLength; if (count > maxArtifact) throw new Error('artifact_size_limit'); controller.enqueue(chunk); }, flush() { if (!count || (size && count !== Number(size))) throw new Error('artifact_size_mismatch'); } });
        return new Response(upstream.body.pipeThrough(bounded), { headers: { 'Content-Type': 'application/zip', 'Content-Disposition': `attachment; filename="${job.id}-adapter.zip"`, 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', ...(size ? { 'Content-Length': size } : {}) } });
      }
      if (action === 'train-job' && ['GET','DELETE'].includes(request.method)) {
        let job = await authorized(request, env, url.searchParams.get('job'));
        if (request.method === 'DELETE') {
          if (ACTIVE.has(job.state) || job.state === 'checkout_creating' || job.state === 'awaiting_payment') fail(409, 'job_active_or_payment_open');
          const deleting = await one(env, 'UPDATE training_jobs SET state = ?, updated_at = ? WHERE id = ? AND state = ? RETURNING id', ['deleting', now, job.id, job.state]);
          if (!deleting) fail(409, 'job_state_changed');
          if (job.storage === 'd1') await env.RHYSBOT_TRAIN_DB.batch([
            statement(env, 'DELETE FROM training_corpus_chunks WHERE job_id=?', [job.id]),
            statement(env, 'UPDATE training_jobs SET state = ?, updated_at = ? WHERE id = ?', ['deleted', now, job.id]),
          ]);
          else {
            await env.RHYSBOT_TRAIN_BUCKET.delete(job.object_key);
            await run(env, 'UPDATE training_jobs SET state = ?, updated_at = ? WHERE id = ?', ['deleted', now, job.id]);
          }
          return response({ job_id: job.id, state: 'deleted', corpus_deleted: true, sandbox_quota_restored: false });
        }
        if (job.authorization_mode && ACTIVE.has(job.state)) {
          try {
            const result = await fetchJSON(fetcher, `${config.bridge}/status?job=${job.id}`, { method: 'GET', headers: bridgeHeaders(env) });
            if (result.job_id === job.id && BRIDGE_STATES.has(result.state)) {
              await run(env, 'UPDATE training_jobs SET state = ?, updated_at = ? WHERE id = ? AND state = ?', [result.state, now, job.id, job.state]);
              job = await one(env, 'SELECT * FROM training_jobs WHERE id = ?', [job.id]);
            }
          } catch { /* Read-only uncertainty never launches or clears an active job. */ }
        }
        return response(publicJob(job));
      }
      if (action === 'train-checkout' && request.method === 'POST') {
        const body = json(await boundedBody(request, 2048));
        if (!exact(body, ['job_id','mode']) || !ID.test(body.job_id || '') || body.mode !== config.mode) fail(400, 'invalid_checkout');
        const job = await authorized(request, env, body.job_id);
        if (job.expires_at < now) fail(410, 'job_expired');
        if (job.state === 'awaiting_payment' && job.checkout_url) return response({ mode: 'stripe_test', checkout_url: job.checkout_url });
        if (job.state !== 'uploaded') fail(409, 'job_already_claimed');
        if (config.mode === 'sandbox') {
          // One lifetime GLOBAL slot, not one per user. INSERT SELECT also binds current state.
          const claim = await one(env, "INSERT OR IGNORE INTO training_sandbox_claim (singleton,job_id,claimed_at) SELECT 1,id,? FROM training_jobs WHERE id=? AND state='uploaded' RETURNING job_id", [now, job.id]);
          if (!claim) fail(409, 'sandbox_quota_exhausted');
          const claimed = await one(env, "UPDATE training_jobs SET state='dispatching',authorization_mode='sandbox',updated_at=? WHERE id=? AND state='uploaded' RETURNING *", [now, job.id]);
          if (!claimed) fail(409, 'sandbox_claim_requires_reconciliation');
          await dispatch(env, claimed, fetcher, now);
          return response(publicJob(await one(env, 'SELECT * FROM training_jobs WHERE id = ?', [job.id])), 202);
        }
        if (job.stripe_price !== env.RHYSBOT_TRAIN_STRIPE_PRICE || job.amount !== Number(env.RHYSBOT_TRAIN_PRICE_CENTS)) fail(409, 'price_configuration_changed');
        const claimed = await one(env, "UPDATE training_jobs SET state='checkout_creating',updated_at=? WHERE id=? AND state='uploaded' RETURNING id", [now, job.id]);
        if (!claimed) fail(409, 'job_already_claimed');
        try {
          const form = new URLSearchParams({ mode: 'payment', 'payment_method_types[0]': 'card', 'line_items[0][price]': job.stripe_price, 'line_items[0][quantity]': '1', client_reference_id: job.id, 'metadata[job_id]': job.id, 'metadata[recipe]': RECIPE, success_url: `${ORIGIN}/rhysbot/train/?job=${job.id}`, cancel_url: `${ORIGIN}/rhysbot/train/?job=${job.id}` });
          const result = await stripeRequest(fetcher, env, 'checkout/sessions', form, `rhysbot-checkout-v1-${job.id}`);
          if (!/^cs_test_[A-Za-z0-9]+$/.test(result.id || '') || result.livemode !== false || !/^https:\/\/checkout\.stripe\.com\//.test(result.url || '')) throw new Error('invalid_test_checkout');
          await run(env, "UPDATE training_jobs SET state='awaiting_payment',stripe_session=?,checkout_url=?,updated_at=? WHERE id=? AND state='checkout_creating'", [result.id, result.url, now, job.id]);
          return response({ mode: 'stripe_test', checkout_url: result.url });
        } catch { await run(env, "UPDATE training_jobs SET state='recovery_required',message_code='checkout_uncertain_no_retry',updated_at=? WHERE id=? AND state='checkout_creating'", [now, job.id]); fail(502, 'checkout_requires_reconciliation'); }
      }
      if (action === 'train-webhook' && request.method === 'POST') {
        if (!config.stripe) fail(503, 'stripe_test_not_configured');
        const raw = await boundedBody(request, 65536);
        if (!await verifyStripeSignature(raw, request.headers.get('Stripe-Signature'), env.RHYSBOT_TRAIN_STRIPE_WEBHOOK_SECRET, now)) fail(400, 'invalid_signature');
        const event = json(raw);
        if (event.livemode !== false || !/^evt_[A-Za-z0-9]+$/.test(event.id || '')) fail(400, 'test_events_only');
        if (!['checkout.session.completed','checkout.session.async_payment_succeeded'].includes(event.type)) return response({ received: true, ignored: true });
        const sid = event.data?.object?.id;
        if (!/^cs_test_[A-Za-z0-9]+$/.test(sid || '')) fail(400, 'test_sessions_only');
        const job = await one(env, 'SELECT * FROM training_jobs WHERE stripe_session = ?', [sid]);
        if (!job) return response({ received: true, ignored: true });
        if (job.state !== 'awaiting_payment') return response({ received: true, duplicate_or_closed: true });
        if (job.expires_at < now) {
          await run(env, "UPDATE training_jobs SET state='budget_blocked',message_code='corpus_expired',updated_at=? WHERE id=? AND state='awaiting_payment'", [now, job.id]);
          return response({ received: true, corpus_expired: true });
        }
        const session = await stripeRequest(fetcher, env, `checkout/sessions/${sid}?expand%5B%5D=line_items`);
        const lines = session.line_items?.data;
        if (session.livemode !== false || session.id !== sid || session.mode !== 'payment' || session.client_reference_id !== job.id || session.metadata?.job_id !== job.id || session.metadata?.recipe !== RECIPE || session.payment_status !== 'paid' || session.status !== 'complete' || session.amount_total !== job.amount || session.currency !== 'usd' || session.line_items?.has_more !== false || !Array.isArray(lines) || lines.length !== 1 || lines[0].quantity !== 1 || lines[0].price?.id !== job.stripe_price || lines[0].price?.livemode !== false) fail(400, 'payment_mismatch_or_unpaid');
        // Stripe TEST payment is not money: it consumes the SAME one-job lifetime slot.
        await run(env, "INSERT OR IGNORE INTO training_sandbox_claim (singleton,job_id,claimed_at) SELECT 1,id,? FROM training_jobs WHERE id=? AND state='awaiting_payment'", [now, job.id]);
        const quota = await one(env, 'SELECT job_id FROM training_sandbox_claim WHERE singleton=1');
        if (!quota || quota.job_id !== job.id) {
          await run(env, "UPDATE training_jobs SET state='budget_blocked',message_code='test_quota_exhausted',updated_at=? WHERE id=? AND state='awaiting_payment'", [now, job.id]);
          return response({ received: true, test_quota_exhausted: true });
        }
        const claimed = await one(env, "UPDATE training_jobs SET state='dispatching',authorization_mode='stripe_test',payment_event=?,updated_at=? WHERE id=? AND stripe_session=? AND state='awaiting_payment' RETURNING *", [event.id, now, job.id, sid]);
        if (claimed) await dispatch(env, claimed, fetcher, now);
        return response({ received: true });
      }
      fail(405, 'method_not_allowed');
    } catch (error) { return response({ error: error.code || 'service_unavailable' }, error.status || 503); }
  };
}
