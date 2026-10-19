const $ = id => document.getElementById(id);
const API = '/api/rhysbot/';
let config, job, verification = '', widget;
const states = {
  uploaded: 'Uploaded privately. Ready for test checkout.', checkout_creating: 'Creating test checkout.', awaiting_payment: 'Waiting for a verified Stripe test event.',
  dispatching: 'Authorization recorded. Dispatch is being coordinated; do not submit again.', queued: 'The real bounded training job is queued.', running: 'The real training job is running.',
  completed: 'The training backend reports completion. You can download the actual saved adapter and metrics below.', failed: 'The backend reports failure. The pilot quota is retained.',
  budget_blocked: 'Training was blocked by the shared quota or budget. No success is claimed.', recovery_required: 'Operator reconciliation required. Dispatch will not be retried automatically.',
  deleted: 'The private uploaded corpus was deleted. This does not revoke a training artifact or restore the lifetime quota.', deleting: 'Corpus deletion is pending.',
};
function save() { try { sessionStorage.setItem('rhysbot-training-job', JSON.stringify(job)); } catch { /* Recovery text remains available. */ } }
function display() {
  $('identity').textContent = job ? `Job ${job.job_id}` : '';
  $('recovery').value = job ? `${job.job_id}\n${job.access_token}` : '';
  $('refresh').disabled = !job; $('delete').disabled = !job;
  $('download').disabled = !job || job.state !== 'completed';
  $('checkout').disabled = !job || !config?.available || (job.state && !['uploaded','awaiting_payment'].includes(job.state));
}
async function call(action, options = {}) {
  const headers = new Headers(options.headers || {});
  if (job) headers.set('Authorization', `Bearer ${job.access_token}`);
  const r = await fetch(`${API}${action}`, { ...options, headers, credentials: 'omit', cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(25000) });
  const data = await r.json(); if (!r.ok) throw new Error(data.error || 'Request unavailable'); return data;
}
function error(e) { $('status').textContent = `Request stopped: ${e.message}. No automatic retry.`; }
async function status() {
  if (!job) return;
  const data = await call(`train-job?job=${encodeURIComponent(job.job_id)}`);
  job.state = data.state; save(); display(); $('status').textContent = states[data.state] || `State: ${data.state}`;
}
function uploadReady() { $('upload').disabled = !config?.available || !verification || !$('consent').checked || !$('file').files.length; }
$('consent').addEventListener('change', uploadReady); $('file').addEventListener('change', uploadReady);
$('upload').addEventListener('click', async () => {
  $('upload').disabled = true;
  try {
    const file = $('file').files[0]; const ext = file?.name.split('.').at(-1)?.toLowerCase();
    if (!['txt','jsonl'].includes(ext) || !file.size || file.size > 5 * 1024 * 1024) throw new Error('Choose a nonempty TXT or JSONL file up to 5 MiB');
    const result = await call(`train-upload?format=${ext}`, { method: 'POST', headers: { 'Content-Type': ext === 'txt' ? 'text/plain;charset=utf-8' : 'application/x-ndjson', 'X-Turnstile-Token': verification }, body: file });
    job = result; save(); display(); $('status').textContent = states.uploaded;
  } catch (e) { error(e); }
  finally { verification = ''; if (window.turnstile && widget !== undefined) window.turnstile.reset(widget); uploadReady(); }
});
$('checkout').addEventListener('click', async () => {
  $('checkout').disabled = true;
  try {
    const result = await call('train-checkout', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ job_id: job.job_id, mode: config.payment_mode }) });
    if (result.checkout_url) { save(); location.assign(result.checkout_url); return; }
    job.state = result.state; save(); display(); $('status').textContent = states[result.state] || result.state;
  } catch (e) { error(e); } finally { display(); }
});
$('refresh').addEventListener('click', () => status().catch(error));
$('download').addEventListener('click', async () => {
  $('download').disabled = true;
  try {
    const r = await fetch(`${API}train-artifact?job=${encodeURIComponent(job.job_id)}`, { headers: { Authorization: `Bearer ${job.access_token}` }, credentials: 'omit', cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(250000) });
    if (!r.ok || r.headers.get('Content-Type')?.split(';')[0] !== 'application/zip') throw new Error('Trained artifact is unavailable');
    const size = r.headers.get('Content-Length'), limit = 100 * 1024 * 1024;
    if (size && (!/^\d+$/.test(size) || Number(size) > limit)) throw new Error('Artifact exceeds download limit');
    const reader = r.body.getReader(); const chunks = []; let total = 0;
    try { for (;;) { const { value, done } = await reader.read(); if (done) break; total += value.byteLength; if (total > limit) { await reader.cancel(); throw new Error('Artifact exceeds download limit'); } chunks.push(value); } } finally { reader.releaseLock(); }
    if (!total || (size && total !== Number(size))) throw new Error('Incomplete artifact');
    const href = URL.createObjectURL(new Blob(chunks, { type: 'application/zip' })); const a = document.createElement('a'); a.href = href; a.download = `${job.job_id}-adapter.zip`; a.click(); setTimeout(() => URL.revokeObjectURL(href), 1000);
  } catch (e) { error(e); } finally { display(); }
});
$('delete').addEventListener('click', async () => {
  if (!confirm('Delete this private uploaded corpus? The lifetime test quota will not be restored.')) return;
  try { const result = await call(`train-job?job=${encodeURIComponent(job.job_id)}`, { method: 'DELETE' }); job.state = result.state; save(); display(); $('status').textContent = states.deleted; } catch (e) { error(e); }
});
$('restore').addEventListener('click', () => {
  const id = $('restoreId').value.trim(), token = $('restoreToken').value.trim();
  if (!/^job_[a-f0-9]{32}$/.test(id) || !/^[a-f0-9]{64}$/.test(token)) return error(new Error('Invalid recovery details'));
  job = { job_id: id, access_token: token }; save(); display(); status().catch(error);
});
try { const saved = JSON.parse(sessionStorage.getItem('rhysbot-training-job') || 'null'); if (/^job_[a-f0-9]{32}$/.test(saved?.job_id) && /^[a-f0-9]{64}$/.test(saved?.access_token)) job = saved; } catch { /* No recovery state. */ }
display();
try {
  config = await call('train');
  $('availability').textContent = config.available ? (config.payment_mode === 'sandbox' ? 'Sandbox is configured. No card data or Stripe payment; one global training pilot is available.' : 'Stripe TEST mode is configured. Use test payment details only; one global training pilot is available.') : 'Training checkout is unavailable: required configuration or the one-job pilot quota is missing. Existing jobs can still be checked below.';
  $('recipe').textContent = config.available ? `${config.recipe}: at most ${config.limits.timeout_seconds} seconds, with a $${config.limits.max_cost_usd.toFixed(2)} conservative shared-budget hold. ${config.payment_mode === 'stripe_test' ? `Test amount: $${(config.price_cents / 100).toFixed(2)} USD; no real charge.` : 'Sandbox confirmation; $0 charged.'}` : 'No payment or training can start from this page until configured.';
  $('checkout').textContent = config.payment_mode === 'sandbox' ? 'Confirm Sandbox training pilot · no charge' : 'Continue to Stripe TEST checkout';
  if (config.available && config.turnstile_site_key) {
    const script = document.createElement('script'); script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'; script.async = true;
    script.onload = () => { widget = window.turnstile.render('#human', { sitekey: config.turnstile_site_key, action: 'rhysbot_train', callback: value => { verification = value; uploadReady(); }, 'expired-callback': () => { verification = ''; uploadReady(); }, 'error-callback': () => { verification = ''; uploadReady(); } }); };
    document.head.appendChild(script);
  }
  display(); uploadReady(); if (job) await status();
} catch (e) { $('availability').textContent = 'Training checkout is unavailable.'; error(e); }
