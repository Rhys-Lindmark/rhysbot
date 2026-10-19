import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { createHmac } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { createTrainingHandler, ORIGIN, MAX_BYTES, LIMITS, validateCorpus, verifyStripeSignature, sha256, artifactStream } from './train-core.js';
import { verifiedArtifactBlob } from './public/train/artifact.js';

const NOW = 1800000000;
const schema = readFileSync(new URL('./schema.sql', import.meta.url), 'utf8');
const bytes = text => new TextEncoder().encode(text);
const study = label => `${label} General educational source material for a bounded private adaptation test. `.repeat(5);
function database() {
  const db = new DatabaseSync(':memory:'); db.exec(schema);
  const adapter = {
    prepare(sql) { return { bind(...args) {
      const params = args.map(x => x instanceof ArrayBuffer ? new Uint8Array(x) : x);
      return {
        async first() { return db.prepare(sql).get(...params) || null; },
        async run() { return { meta: db.prepare(sql).run(...params) }; },
        execute() { if (/\bRETURNING\b|^SELECT/i.test(sql)) return { results: db.prepare(sql).all(...params) }; return { results: [], meta: db.prepare(sql).run(...params) }; },
      };
    } }; },
    async batch(queries) { db.exec('BEGIN'); try { const results = queries.map(q => q.execute()); db.exec('COMMIT'); return results; } catch (e) { db.exec('ROLLBACK'); throw e; } },
  };
  return { db, adapter };
}
function req(action, { method = 'GET', body, token, origin = ORIGIN, headers = {} } = {}) {
  const h = new Headers({ ...(origin === null ? {} : { Origin: origin }), ...headers });
  if (token) h.set('Authorization', `Bearer ${token}`);
  const init = { method, headers: h };
  if (body !== undefined) { init.body = typeof body === 'string' || body instanceof Uint8Array || body instanceof ReadableStream ? body : JSON.stringify(body); if (body instanceof ReadableStream) init.duplex = 'half'; }
  return new Request(`${ORIGIN}/api/rhysbot/${action}`, init);
}
function setup({ stripe = false, dispatchThrows = false, pay = 'paid', storage = 'd1', artifactSize = '4', artifactBytes = '4', artifactHash } = {}) {
  const { db, adapter } = database(); const calls = []; const objects = new Map(); let n = 0;
  const env = { RHYSBOT_TRAIN_DB: adapter, RHYSBOT_TRAIN_AUTH_SECRET: 'auth-secret-'.repeat(4), RHYSBOT_TRAIN_BRIDGE_SECRET: 'bridge-secret-'.repeat(4), RHYSBOT_TRAIN_BRIDGE_URL: 'https://test--train.modal.run', MODAL_PROXY_KEY: 'proxy-key', MODAL_PROXY_SECRET: 'proxy-secret', RHYSBOT_TURNSTILE_SECRET: 'human-secret', RHYSBOT_TURNSTILE_SITE_KEY: 'public-key', RHYSBOT_TRAIN_SANDBOX_QUOTA: '1' };
  if (stripe) Object.assign(env, { RHYSBOT_TRAIN_STRIPE_KEY: 'sk_test_testing', RHYSBOT_TRAIN_STRIPE_WEBHOOK_SECRET: 'whsec_testing', RHYSBOT_TRAIN_STRIPE_PRICE: 'price_testing', RHYSBOT_TRAIN_PRICE_CENTS: '100' });
  if (storage === 'r2') env.RHYSBOT_TRAIN_BUCKET = {
    async put(k, data, meta) { objects.set(k, { data: data.slice(), meta }); },
    async get(k) { const v = objects.get(k); return v ? { size: v.data.length, customMetadata: v.meta.customMetadata, body: new Response(v.data).body } : null; },
    async delete(k) { objects.delete(k); },
  };
  const fetcher = async (url, init) => {
    calls.push({ url, init });
    if (url.startsWith('https://challenges.cloudflare.com/')) return Response.json({ success: true, action: 'rhysbot_train', hostname: 'ai.rhyslindmark.com' });
    if (url === 'https://api.stripe.com/v1/checkout/sessions') return Response.json({ id: `cs_test_session${++n}`, livemode: false, url: `https://checkout.stripe.com/c/pay/cs_test_session${n}` });
    if (url.startsWith('https://api.stripe.com/')) {
      const sid = url.split('/').at(-1).split('?')[0]; const job = db.prepare('SELECT * FROM training_jobs WHERE stripe_session=?').get(sid);
      return Response.json({ id: sid, livemode: false, mode: 'payment', client_reference_id: job.id, metadata: { job_id: job.id, recipe: 'qwen-lora-v1' }, payment_status: pay, status: 'complete', amount_total: job.amount, currency: 'usd', line_items: { has_more: false, data: [{ quantity: 1, price: { id: job.stripe_price, livemode: false } }] } });
    }
    if (url.endsWith('/dispatch')) {
      if (dispatchThrows) throw new Error('uncertain/private transport detail');
      const payload = JSON.parse(init.body); return Response.json({ job_id: payload.job_id, state: 'queued', provider_id: 'must-not-leak' }, { status: 202 });
    }
    if (url.includes('/status?')) return Response.json({ unknown: true }, { status: 404 });
    if (url.includes('/artifact?')) return new Response(bytes('PKxx'), { headers: { 'Content-Type': 'application/zip', ...(artifactSize === null ? {} : { 'Content-Length': artifactSize }), ...(artifactBytes === null ? {} : { 'X-Rhysbot-Artifact-Bytes': artifactBytes }), ...(artifactHash === null ? {} : { 'X-Rhysbot-Artifact-SHA256': artifactHash || await sha256(bytes('PKxx')) }), 'Set-Cookie': 'secret', 'X-Provider': 'private' } });
    throw new Error('Unexpected network call');
  };
  const handler = createTrainingHandler({ fetcher, clock: () => NOW });
  const call = request => handler({ request, env });
  async function upload(text = study('Default'), format = 'txt') {
    const r = await call(req(`train-upload?format=${format}`, { method: 'POST', body: typeof text === 'string' ? bytes(text) : text, headers: { 'Content-Type': format === 'txt' ? 'text/plain' : 'application/x-ndjson', 'X-Turnstile-Token': 'proof' } }));
    assert.equal(r.status, 201); return r.json();
  }
  async function checkout(job) { return call(req('train-checkout', { method: 'POST', token: job.access_token, body: { job_id: job.job_id, mode: stripe ? 'stripe_test' : 'sandbox' } })); }
  function event(job, id = 'evt_testing', extra = {}) {
    const row = db.prepare('SELECT stripe_session FROM training_jobs WHERE id=?').get(job.job_id);
    const data = JSON.stringify({ id, type: 'checkout.session.completed', livemode: false, data: { object: { id: row.stripe_session } }, ...extra });
    const signature = createHmac('sha256', env.RHYSBOT_TRAIN_STRIPE_WEBHOOK_SECRET).update(`${NOW}.${data}`).digest('hex');
    return req('train-webhook', { method: 'POST', body: data, origin: null, headers: { 'Stripe-Signature': `t=${NOW},v1=${signature}` } });
  }
  return { db, env, calls, call, upload, checkout, event, objects };
}
const dispatches = x => x.calls.filter(x => x.url.endsWith('/dispatch'));

test('unconfigured/live Stripe/missing quota fail closed, public config reveals no secret', async () => {
  const x = setup();
  const good = await (await x.call(req('train'))).json(); assert.equal(good.available, true); assert.equal(good.payment_mode, 'sandbox');
  assert(!JSON.stringify(good).includes('secret')); assert(!JSON.stringify(good).includes('.modal.run'));
  for (const name of ['RHYSBOT_TRAIN_DB','RHYSBOT_TRAIN_AUTH_SECRET','RHYSBOT_TRAIN_BRIDGE_SECRET','MODAL_PROXY_KEY','RHYSBOT_TRAIN_SANDBOX_QUOTA']) {
    const old = x.env[name]; delete x.env[name]; assert.equal((await (await x.call(req('train'))).json()).available, false); x.env[name] = old;
  }
  x.env.RHYSBOT_TRAIN_STRIPE_KEY = 'sk_live_not_allowed';
  assert.equal((await (await x.call(req('train'))).json()).available, false);
  assert.equal((await x.call(req('train-upload?format=txt', { method: 'POST', body: 'text' }))).status, 503);
});
test('private D1 upload uses immutable hashes, scoped capabilities and no unpaid dispatch', async () => {
  const x = setup(); const a = await x.upload(); const b = await x.upload(study('Another example text.'));
  assert.equal(dispatches(x).length, 0);
  const row = x.db.prepare('SELECT * FROM training_jobs WHERE id=?').get(a.job_id);
  assert.notEqual(row.auth_mac, a.access_token); assert.equal(row.storage, 'd1');
  assert.equal(x.db.prepare('SELECT COUNT(*) AS n FROM training_corpus_chunks WHERE job_id=?').get(a.job_id).n, 1);
  for (const action of ['GET','DELETE']) {
    const r = await x.call(req(`train-job?job=${a.job_id}`, { method: action, token: b.access_token })); assert.equal(r.status, 404);
  }
  assert.equal((await x.call(req(`train-job?job=${a.job_id}`, { token: a.access_token, origin: null }))).status, 200);
  assert.equal((await x.call(req(`train-data?job=${a.job_id}`, { token: a.access_token }))).status, 401);
  assert.equal((await x.call(req(`train-data?job=${a.job_id}`, { token: x.env.RHYSBOT_TRAIN_BRIDGE_SECRET }))).status, 404);
});
test('strict streaming bound, UTF8/binary/JSONL text-only validation and origin guard', async () => {
  const x = setup();
  for (const [body, format] of [[new Uint8Array([0xff]),'txt'],[bytes('a\0b'),'txt'],[bytes('{"text":"ok","url":"https://external"}'),'jsonl'],[bytes('{"text":"\\u0000"}'),'jsonl'],[bytes('{"text":""}'),'jsonl'],[bytes('not json'),'jsonl']]) assert.throws(() => validateCorpus(body, format));
  validateCorpus(bytes(JSON.stringify({text:study('Safe study')})+'\n'), 'jsonl');
  for (const [body,format] of [['hi','txt'],[JSON.stringify({text:'hi'}),'jsonl']]) {
    const short=await x.call(req(`train-upload?format=${format}`,{method:'POST',body,headers:{'Content-Type':format==='txt'?'text/plain':'application/x-ndjson','X-Turnstile-Token':'proof'}}));
    assert.equal(short.status,400);assert.equal((await short.json()).error,'corpus_too_short_minimum_200_characters');
  }
  assert.equal(x.db.prepare('SELECT COUNT(*) AS n FROM training_jobs').get().n,0);assert.equal(x.db.prepare('SELECT COUNT(*) AS n FROM training_sandbox_claim').get().n,0);assert.equal(dispatches(x).length,0);
  assert.throws(()=>validateCorpus(bytes('🌱'.repeat(100)),'txt'),/200/);
  assert.throws(()=>validateCorpus(bytes('\u0085'.repeat(199)+'a'),'txt'),/200/);
  validateCorpus(bytes('🌱'.repeat(200)),'txt');
  validateCorpus(bytes([JSON.stringify({text:'a'.repeat(99)}),JSON.stringify({text:'b'.repeat(99)})].join('\n')),'jsonl');
  const stream = new ReadableStream({ start(c) { c.enqueue(new Uint8Array(MAX_BYTES)); c.enqueue(bytes('overflow')); c.close(); } });
  const r = await x.call(req('train-upload?format=txt', { method: 'POST', body: stream, headers: { 'Content-Type': 'text/plain', 'X-Turnstile-Token': 'proof' } })); assert.equal(r.status, 413);
  assert.equal(x.db.prepare('SELECT COUNT(*) AS n FROM training_jobs').get().n, 0);
  assert.equal((await x.call(req('train-upload?format=txt', { method: 'POST', origin: 'https://evil.invalid', body: 'text' }))).status, 403);
  assert.equal((await x.call(req('train-upload?format=txt', { method: 'POST', body: 'text', headers: { 'Content-Type': 'text/plain' } }))).status, 403);
});
test('sandbox confirmation claims one global lifetime job; replay/status cannot relaunch', async () => {
  const x = setup(); const a = await x.upload(); const b = await x.upload(study('Second educational corpus.'));
  const [r1,r2] = await Promise.all([x.checkout(a),x.checkout(a)]);
  assert.deepEqual([r1.status,r2.status].sort(), [202,409]); assert.equal(dispatches(x).length, 1);
  const call = dispatches(x)[0]; const payload = JSON.parse(call.init.body);
  assert.equal(payload.corpus.url, `${ORIGIN}/api/rhysbot/train-data?job=${a.job_id}`); assert.equal(payload.idempotency_key, a.job_id); assert.deepEqual(payload.limits, LIMITS);
  assert.equal(call.init.headers.Authorization, `Bearer ${x.env.RHYSBOT_TRAIN_BRIDGE_SECRET}`); assert.equal(call.init.redirect, 'manual');
  assert.equal((await x.checkout(b)).status, 409);
  assert.equal((await (await x.call(req('train'))).json()).pilot_quota_available, false);
  const status = await (await x.call(req(`train-job?job=${a.job_id}`, { token: a.access_token }))).json();
  assert.equal(status.state, 'queued'); assert(!JSON.stringify(status).includes('provider')); assert.equal(dispatches(x).length, 1);
  const corpus = await x.call(req(`train-data?job=${a.job_id}`, { token: x.env.RHYSBOT_TRAIN_BRIDGE_SECRET, origin: null }));
  assert.equal(corpus.status, 200); assert.equal(await sha256(await corpus.arrayBuffer()), a.sha256);
  assert.equal((await x.call(req(`train-job?job=${a.job_id}`, { method: 'DELETE', token: a.access_token }))).status, 409);
});
test('uncertain dispatch retains claim and never retries even on checkout/status repetition', async () => {
  const x = setup({ dispatchThrows: true }); const a = await x.upload();
  assert.equal((await x.checkout(a)).status, 202);
  assert.equal(x.db.prepare('SELECT state FROM training_jobs WHERE id=?').get(a.job_id).state, 'recovery_required');
  assert.equal((await x.checkout(a)).status, 409);
  for (let i=0;i<2;i++) await x.call(req(`train-job?job=${a.job_id}`, { token: a.access_token }));
  assert.equal(dispatches(x).length, 1); assert.equal(x.db.prepare('SELECT COUNT(*) AS n FROM training_sandbox_claim').get().n, 1);
});
test('raw Stripe HMAC rejects body alteration, old/future timestamps and absent signature', async () => {
  const raw=bytes('{ "exact": "body" }'); const secret='whsec_test'; const sig=createHmac('sha256',secret).update(`${NOW}.${new TextDecoder().decode(raw)}`).digest('hex');
  assert.equal(await verifyStripeSignature(raw,`t=${NOW},v1=${sig}`,secret,NOW),true);
  assert.equal(await verifyStripeSignature(bytes('{"exact":"body"}'),`t=${NOW},v1=${sig}`,secret,NOW),false);
  for (const time of [NOW-301,NOW+301]) assert.equal(await verifyStripeSignature(raw,`t=${NOW},v1=${sig}`,secret,time),false);
  assert.equal(await verifyStripeSignature(raw,null,secret,NOW),false);
  const bom=new Uint8Array([239,187,191,...raw]);const bomSig=createHmac('sha256',secret).update(`${NOW}.`).update(bom).digest('hex');assert.equal(await verifyStripeSignature(bom,`t=${NOW},v1=${bomSig}`,secret,NOW),true);assert.equal(await verifyStripeSignature(raw,`t=${NOW},v1=${bomSig}`,secret,NOW),false);
});
test('test checkout never dispatches; only verified paid webhook claims quota once', async () => {
  const x=setup({stripe:true}); const a=await x.upload(); assert.equal((await x.checkout(a)).status,200); assert.equal(dispatches(x).length,0);
  const checkout=x.calls.find(x=>x.url==='https://api.stripe.com/v1/checkout/sessions'); assert.equal(new URLSearchParams(checkout.init.body).get('line_items[0][price]'),'price_testing'); assert.match(checkout.init.headers['Idempotency-Key'],new RegExp(a.job_id));
  const forged=x.event(a); forged.headers.set('Stripe-Signature','t=0,v1=bad'); assert.equal((await x.call(forged)).status,400); assert.equal(dispatches(x).length,0);
  const [r1,r2]=await Promise.all([x.call(x.event(a)),x.call(x.event(a))]); assert.equal(r1.status,200); assert.equal(r2.status,200); assert.equal(dispatches(x).length,1);
  assert.equal((await x.call(x.event(a,'evt_another',{type:'checkout.session.async_payment_succeeded'}))).status,200); assert.equal(dispatches(x).length,1);
});
test('unpaid/live event cannot claim job, and multiple Stripe TEST payments share global one-slot quota', async () => {
  const unpaid=setup({stripe:true,pay:'unpaid'});const u=await unpaid.upload();await unpaid.checkout(u);assert.equal((await unpaid.call(unpaid.event(u))).status,400);assert.equal(dispatches(unpaid).length,0);assert.equal(unpaid.db.prepare('SELECT COUNT(*) AS n FROM training_sandbox_claim').get().n,0);
  const x=setup({stripe:true});const a=await x.upload();const b=await x.upload(study('Other text'));await x.checkout(a);await x.checkout(b);
  assert.equal((await x.call(x.event(a,'evt_live',{livemode:true}))).status,400);assert.equal(dispatches(x).length,0);
  await x.call(x.event(a));await x.call(x.event(b,'evt_second'));assert.equal(dispatches(x).length,1);assert.equal(x.db.prepare('SELECT state FROM training_jobs WHERE id=?').get(b.job_id).state,'budget_blocked');
});
test('D1 multi-chunk bytes roundtrip, tampered chunk fails; authorized delete actually removes bytes', async () => {
  const x=setup();const data=bytes('x'.repeat(262144+17));const a=await x.upload(data);assert.equal(x.db.prepare('SELECT COUNT(*) AS n FROM training_corpus_chunks').get().n,2);
  await x.checkout(a);const r=await x.call(req(`train-data?job=${a.job_id}`,{token:x.env.RHYSBOT_TRAIN_BRIDGE_SECRET}));assert.deepEqual(new Uint8Array(await r.arrayBuffer()),data);
  x.db.prepare('UPDATE training_corpus_chunks SET sha256=? WHERE job_id=? AND ordinal=1').run('f'.repeat(64),a.job_id);
  const bad=await x.call(req(`train-data?job=${a.job_id}`,{token:x.env.RHYSBOT_TRAIN_BRIDGE_SECRET}));await assert.rejects(()=>bad.arrayBuffer());
  x.db.prepare("UPDATE training_jobs SET state='completed' WHERE id=?").run(a.job_id);
  assert.equal((await x.call(req(`train-job?job=${a.job_id}`,{method:'DELETE',token:a.access_token}))).status,200);assert.equal(x.db.prepare('SELECT COUNT(*) AS n FROM training_corpus_chunks').get().n,0);assert.equal(x.db.prepare('SELECT COUNT(*) AS n FROM training_sandbox_claim').get().n,1);
});
test('optional private R2 uses the same auth and deletion boundaries', async () => {
  const x=setup({storage:'r2'});const a=await x.upload();assert.equal(x.objects.size,1);assert.equal(x.db.prepare('SELECT COUNT(*) AS n FROM training_corpus_chunks').get().n,0);
  await x.checkout(a);const r=await x.call(req(`train-data?job=${a.job_id}`,{token:x.env.RHYSBOT_TRAIN_BRIDGE_SECRET}));assert.equal(await sha256(await r.arrayBuffer()),a.sha256);
  x.db.prepare("UPDATE training_jobs SET state='failed' WHERE id=?").run(a.job_id);assert.equal((await x.call(req(`train-job?job=${a.job_id}`,{method:'DELETE',token:a.access_token}))).status,200);assert.equal(x.objects.size,0);
});
test('global lifetime upload admission stops at ten even after deletions', async () => {
  const x=setup();for(let i=0;i<10;i++) await x.upload(study(`Educational study ${i}.`));
  const r=await x.call(req('train-upload?format=txt',{method:'POST',body:study('Extra'),headers:{'Content-Type':'text/plain','X-Turnstile-Token':'proof'}}));assert.equal(r.status,409);assert.equal(x.db.prepare('SELECT COUNT(*) AS n FROM training_jobs').get().n,10);
});
test('completed artifact needs same-job capability, strips headers, streams only fixed bridge path', async () => {
  const x=setup();const a=await x.upload();const b=await x.upload(study('Other safe text.'));
  assert.equal((await x.call(req(`train-artifact?job=${a.job_id}`,{token:a.access_token}))).status,409);
  x.db.prepare("UPDATE training_jobs SET state='completed' WHERE id=?").run(a.job_id);
  assert.equal((await x.call(req(`train-artifact?job=${a.job_id}`,{token:b.access_token}))).status,404);
  const r=await x.call(req(`train-artifact?job=${a.job_id}`,{token:a.access_token,origin:null}));assert.equal(r.status,200);assert.equal(r.headers.get('X-Rhysbot-Artifact-Bytes'),'4');assert.equal(r.headers.get('X-Rhysbot-Artifact-SHA256'),await sha256(bytes('PKxx')));assert.equal(await r.text(),'PKxx');assert.equal(r.headers.get('Set-Cookie'),null);assert.equal(r.headers.get('X-Provider'),null);
  const call=x.calls.find(x=>x.url.includes('/artifact?'));assert.equal(call.url,`${x.env.RHYSBOT_TRAIN_BRIDGE_URL}/artifact?job=${a.job_id}`);assert.equal(call.init.headers.Authorization,`Bearer ${x.env.RHYSBOT_TRAIN_BRIDGE_SECRET}`);
  const y=setup({artifactSize:String(100*1024*1024+1)});const c=await y.upload();y.db.prepare("UPDATE training_jobs SET state='completed' WHERE id=?").run(c.job_id);assert.equal((await y.call(req(`train-artifact?job=${c.job_id}`,{token:c.access_token}))).status,502);
  const z=setup({artifactSize:null,artifactBytes:'5'});const d=await z.upload();z.db.prepare("UPDATE training_jobs SET state='completed' WHERE id=?").run(d.job_id);const short=await z.call(req(`train-artifact?job=${d.job_id}`,{token:d.access_token}));await assert.rejects(()=>short.arrayBuffer());
});
test('artifact proxy requires verified metadata even without Content-Length, native stream selected when available',async()=>{
  for(const options of [{artifactBytes:null},{artifactHash:null},{artifactHash:'not-a-digest'},{artifactSize:'5'},{artifactBytes:String(100*1024*1024+1),artifactSize:null}]){
    const x=setup(options),job=await x.upload();x.db.prepare("UPDATE training_jobs SET state='completed' WHERE id=?").run(job.job_id);assert.equal((await x.call(req(`train-artifact?job=${job.job_id}`,{token:job.access_token}))).status,502);
  }
  const x=setup({artifactSize:null}),job=await x.upload();x.db.prepare("UPDATE training_jobs SET state='completed' WHERE id=?").run(job.job_id);const result=await x.call(req(`train-artifact?job=${job.job_id}`,{token:job.access_token}));assert.equal(result.status,200);assert.equal(await (await verifiedArtifactBlob(result)).text(),'PKxx');
  let declared;
  class NativeFixed extends TransformStream{constructor(n){super();declared=n;}}
  const body=artifactStream(new Response('PKxx').body,4,NativeFixed);assert.equal(await new Response(body).text(),'PKxx');assert.equal(declared,4);
});
test('browser verifies exact metadata size and SHA256 before offering Blob, including lost Content-Length',async()=>{
  const digest=await sha256(bytes('PKxx'));
  const response=(data='PKxx',extra={})=>new Response(data,{headers:{'Content-Type':'application/zip','X-Rhysbot-Artifact-Bytes':'4','X-Rhysbot-Artifact-SHA256':digest,...extra}});
  assert.equal(await (await verifiedArtifactBlob(response())).text(),'PKxx');
  await assert.rejects(()=>verifiedArtifactBlob(response('PKx')),/Incomplete/);
  await assert.rejects(()=>verifiedArtifactBlob(response('PKxxx')),/exceeds/);
  await assert.rejects(()=>verifiedArtifactBlob(response('PKxy')),/hash mismatch/);
  await assert.rejects(()=>verifiedArtifactBlob(response('PKxx',{'Content-Length':'5'})),/metadata/);
  await assert.rejects(()=>verifiedArtifactBlob(new Response('PKxx',{headers:{'Content-Type':'application/zip'}})),/metadata/);
});
test('D1 batch rollback leaves no partial corpus/job when a chunk statement fails', async () => {
  const x=setup(); x.db.exec("CREATE TRIGGER reject_chunk BEFORE INSERT ON training_corpus_chunks BEGIN SELECT RAISE(ABORT,'test failure'); END;");
  const r=await x.call(req('train-upload?format=txt',{method:'POST',body:study('Rollback'),headers:{'Content-Type':'text/plain','X-Turnstile-Token':'proof'}}));assert.equal(r.status,503);assert.equal(x.db.prepare('SELECT COUNT(*) AS n FROM training_jobs').get().n,0);assert.equal(x.db.prepare('SELECT COUNT(*) AS n FROM training_corpus_chunks').get().n,0);
});
test('exact 5MiB D1 corpus is twenty bounded chunks; same verified bytes stream back', async () => {
  const x=setup();const data=new Uint8Array(MAX_BYTES).fill(97);const a=await x.upload(data);const rows=x.db.prepare('SELECT COUNT(*) AS n,MAX(length(data)) AS largest,SUM(bytes) AS bytes FROM training_corpus_chunks').get();assert.equal(rows.n,20);assert.equal(rows.largest,262144);assert.equal(rows.bytes,MAX_BYTES);
  await x.checkout(a);const r=await x.call(req(`train-data?job=${a.job_id}`,{token:x.env.RHYSBOT_TRAIN_BRIDGE_SECRET}));assert.equal(await sha256(await r.arrayBuffer()),a.sha256);
});
