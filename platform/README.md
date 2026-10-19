# Private corpus training pilot

Deployed private pilot; credentials and paid compute remain bounded by the
owner-controlled authorization and ledgers. No Stripe account or live
payment is claimed. This prototype accepts **Stripe TEST mode only**, or a
clearly labeled **Sandbox confirmation** with no card form/payment. Test money
does not buy GPU budget: both modes share **one global lifetime GPU claim**.

## Finite phase and evidence

- [x] Private UTF-8 TXT / text-only JSONL upload, strict streamed 5 MiB ceiling.
- [x] D1 atomic chunked storage fallback; optional private R2 binding.
- [x] Cryptographic per-job status/delete/download capability; no account needed.
- [x] Server-fixed recipe/price, hosted Stripe test Checkout, exact-byte signature
  verification, retrieved paid Session and expanded line-item validation.
- [x] Atomic payment/quota dispatch claim; replay and uncertainty cannot relaunch.
- [x] Fixed authenticated bridge contract, real-job states, completed-only adapter
  download; no fabricated training results.
- [x] Standalone HTML upload/checkout/status/delete/download UI, configuration
  unavailable state, explicit sandbox/test labels and recovery token.
- [x] Fourteen focused Node built-in tests with real in-memory SQLite and mocked
  Turnstile/Stripe/Modal transports. No network/Matplotlib/ML imports.
- [x] Root provisions schema/bindings/secrets and configures the deployed Modal bridge.
- [x] Actual hosted private corpus → bounded real training → verified saved
  adapter+metrics download → authorized corpus deletion. Owner integration probes
  seeded D1 rows directly; browser upload/checkout is covered by focused API tests.

These checks validate source behavior, not a live payment or GPU integration.
The parent manager owns project progress; this checklist records this phase's
finite acceptance. No new worker, build, dependency or polling process runs.

## Files and local check

`train-core.js` is the canonical service, mirrored verbatim into the website's
`functions/api/rhysbot/train-core.js`. `pages/` contains endpoint wrappers;
the website wrapper imports its adjacent `./train-core.js` instead of the source
wrapper's `../train-core.js`. `public/train/` mirrors the standalone website
`public/rhysbot/train/` UI. Do not overwrite the existing inference proxy or index.

```sh
/Users/rhyslindmark/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node \
  --test platform/train.test.mjs
```

No `npm install`, local server or framework migration is required. Use the
existing Pages compatibility settings. Root should retain request/error
observability with no raw request bodies, secrets, tokens or uploaded content in
logs. The current implementation returns generic errors and logs no corpus.

## Bindings, secrets and activation

Apply `schema.sql` explicitly to the private D1 database; bind it as
`RHYSBOT_TRAIN_DB`. Other services may share the database but use other table
names. This service owns only `training_jobs`, `training_corpus_chunks` and
`training_sandbox_claim`. **Never reset/delete the singleton quota row to retry
an uncertain job.** Preserve rows as recovery evidence.

Required server configuration:

| Name | Purpose |
| --- | --- |
| `RHYSBOT_TRAIN_DB` | Private D1 binding with this schema |
| `RHYSBOT_TRAIN_AUTH_SECRET` | Random ≥32-character key for job-token HMACs |
| `RHYSBOT_TRAIN_BRIDGE_SECRET` | Separate random ≥32-character service Bearer credential shared with Modal |
| `RHYSBOT_TRAIN_BRIDGE_URL` | HTTPS `*.modal.run` origin only; no path/query/user info |
| `MODAL_PROXY_KEY`, `MODAL_PROXY_SECRET` | Modal endpoint proxy authentication, kept server-side |
| `RHYSBOT_TURNSTILE_SECRET`, `RHYSBOT_TURNSTILE_SITE_KEY` | Existing Turnstile configuration; require hostname `ai.rhyslindmark.com`, action `rhysbot_train` |
| `RHYSBOT_TRAIN_SANDBOX_QUOTA` | Explicit value `1` enables ONE lifetime test/sandbox dispatch globally |
| `RHYSBOT_TRAIN_BUCKET` | Optional private R2 binding; never public access |

Optional **all-or-nothing** Stripe test configuration:
`RHYSBOT_TRAIN_STRIPE_KEY=sk_test_…`,
`RHYSBOT_TRAIN_STRIPE_WEBHOOK_SECRET=whsec_…`,
`RHYSBOT_TRAIN_STRIPE_PRICE=price_…`,
`RHYSBOT_TRAIN_PRICE_CENTS=<positive integer USD cents>`.
Any partial/live-key configuration fails closed rather than silently falling
back to Sandbox. Stripe events, Sessions and Price objects must all have
`livemode=false`. Only card test Checkout is enabled. Nothing here activates
real charges, Connect accounts, recurring subscriptions or unlimited jobs.

Until required configuration exists, the UI says unavailable and mutation routes
return 503. After the one pilot claim, config advertises no further pilot quota;
authorized reads/downloads/deletes for existing jobs continue to work.

## HTTP contract

All browser requests use the fixed origin `https://ai.rhyslindmark.com`. Mutation
requests require that Origin. Same-origin GET without an Origin header is allowed
only with the job capability where required. No incoming cookies/private headers
are forwarded. Responses are `no-store`; tokens never enter query strings.

| Endpoint under `/api/rhysbot/` | Contract |
| --- | --- |
| `GET train` | Public availability/test mode/fixed limits/site key; no secrets or bridge URL |
| `POST train-upload?format=txt\|jsonl` | Raw body, matching text MIME, `X-Turnstile-Token`; returns job ID and random 256-bit access token once |
| `POST train-checkout` | Job Bearer token; JSON `{job_id,mode}` where mode is configured `sandbox` or `stripe_test` |
| `GET train-job?job=ID` | Job Bearer token; status only, may make one authenticated read of bridge status, never dispatch |
| `DELETE train-job?job=ID` | Same job token; removes eligible uploaded bytes, retains minimal audit/quota metadata |
| `POST train-webhook` | Stripe raw-body signed event; no browser authorization or redirect fulfillment |
| `GET train-data?job=ID` | **Service Bearer only**, authorized active job; immutable private corpus stream for bridge |
| `GET train-artifact?job=ID` | Same job token and completed state; bounded authenticated ZIP proxy |

JSONL accepts only one nonempty string `text` per row, with ≤32 KiB encoded lines.
Invalid UTF-8, binary controls, archives, arbitrary object schemas and remote
fetch inputs are rejected. TXT remains inert text, even when it contains code or
instructions. The bridge must never execute uploaded instructions/tools.

## Private storage and capability boundary

The 256-bit recovery token is generated with Web Crypto; only its job-bound HMAC
is stored. HMAC verification uses Web Crypto, and cross-job status/delete/artifact
attempts return the same 404 as an absent job. The browser stores its token in
session storage, displays a copyable recovery value, and sends it only as Bearer.
Losing the token loses user access; no email/account recovery is implied.

Without R2, validated bytes are split into ≤20 256-KiB D1 BLOBs with individual
hashes. Admission plus all chunks commit in one `D1.batch()` transaction. A
global ten-upload lifetime admission bound caps corpus payload storage at 50 MiB;
deleting a corpus does not reset this bound. Corpus streaming reads one chunk at
a time, verifies its digest and uses at most 22 D1 queries, below the documented
Free invocation limit of 50. The bridge must additionally validate the declared
whole-file SHA256 and byte count before training. Optional R2 uses a private,
immutable job key and the same SHA/byte contract.

Corpus service access expires after seven days. This is an **access deadline**,
not a claim that a scheduled erasure task exists. Authorized deletion physically
deletes D1 chunks or the R2 object; minimal job/quota audit metadata remains.
Root must configure storage cleanup/lifecycle before broader retention promises.
An active/uncertain job or an open Checkout cannot be deleted until safely
reconciled; expiry/cancellation/refund automation is not implemented. Deleting
uploaded text does not delete a checkpoint already produced on Modal, remove
provider backups or restore GPU quota. No uploaded corpus is published or added
to the existing Rhysbot corpus/frozen model inputs.

## Payment, dispatch and recovery

Stripe Checkout creation uses a fixed server Price and a job-scoped idempotency
key. A timeout records `recovery_required`; checkout is never blindly repeated.
The webhook verifies HMAC-SHA256 over timestamp plus **exact raw bytes**, ±300s,
before parsing. It retrieves the Session with expanded line items and checks
test mode, session/job metadata, status `complete`, payment `paid`, exact USD
amount, exactly one fixed-price item/quantity, and no unexpanded additional page.
A redirect/query flag/browser claim never authorizes training.

The D1 lifetime singleton covers Stripe TEST and Sandbox alike. An atomic
compare-and-set changes the eligible job to `dispatching` before one bridge
request. Two simultaneous/replayed events cannot launch twice. A transport
exception, malformed response or uncertain bridge status records
`recovery_required`, retains the quota, and never auto-retries. Operator
reconciliation must use the existing durable bridge job/call record and provider
terminal evidence; age alone does not authorize another dispatch or refund.

Bridge requests use service Bearer plus `Modal-Key`/`Modal-Secret`, with no
redirects and a fixed configured origin:

```json
{
  "job_id": "job_<32 lowercase hex>",
  "idempotency_key": "job_<same hex>",
  "recipe": "qwen-lora-v1",
  "authorization_mode": "sandbox or stripe_test",
  "corpus": {
    "url": "https://ai.rhyslindmark.com/api/rhysbot/train-data?job=job_<same hex>",
    "sha256": "<64 lowercase hex>",
    "bytes": 5242880,
    "format": "txt or jsonl"
  },
  "limits": {"timeout_seconds": 900, "max_cost_usd": 3.60}
}
```

`POST /dispatch` returns quickly after **durably recording** the job→FunctionCall
ID and budget reservation: `{job_id,state}` with `queued`, `running`,
`completed`, `failed` or `budget_blocked`. Duplicate dispatch ID must resolve the
same durable job, never spawn a second GPU. `GET /status?job=ID` returns the same
whitelisted state shape and performs no launch. `GET /artifact?job=ID` returns
only the actual saved adapter and training metrics for a completed job as
`application/zip`, ≤100 MiB, with a trustworthy Content-Length when available.
No raw corpus, private paths or provider identifiers enter the public response.

The bridge is root-owned and must independently enforce: exact URL/job/recipe/
format/limits; service and Modal auth; SHA/bytes before parsing/training; durable
FCID before waiting; one test job; global active coordination; the original
**$200 total ledger**, including conservative ≤$3.60 holds for this ≤900s job;
no automatic retry/serving; terminal evidence before clearing locks; no mutation
of old frozen inputs/checkpoints. Stripe test payments add no real budget. The
separate $10/month chat/voice budget does not authorize more training spend.

The UI downloads a completed ZIP through the capability route, never a supplied
URL. Proxy and browser require authoritative artifact byte count and SHA256 headers.
Cloudflare uses native FixedLengthStream delivery; the browser verifies exact
size and SHA256 before offering a download, with a 100 MiB ceiling and finite
timeout. Only the browser materializes the bounded Blob. Operator reconciliation is still
needed after an interrupted request. No production payment/uptime/SLA claim is
made by this source implementation.

## Primary references reviewed

- [Stripe fulfillment](https://docs.stripe.com/checkout/fulfillment.md?payment-ui=stripe-hosted): retrieve paid Session, line items, concurrent/idempotent fulfillment.
- [Stripe webhook handling](https://docs.stripe.com/events/manage-webhook-endpoints): raw signatures, duplicate deliveries, limited event types.
- [Stripe idempotent requests](https://docs.stripe.com/api/idempotent_requests): stable server idempotency keys.
- [D1 limits](https://developers.cloudflare.com/d1/platform/limits/): 2,000,000-byte row/BLOB, 50 Free queries.
- [D1 database API](https://developers.cloudflare.com/d1/worker-api/d1-database/): prepared statements and transactional batch.
- [R2 Workers API](https://developers.cloudflare.com/r2/api/workers/workers-api-reference/): private optional binding reads/writes.

Terminal training accounting runs in the CPU status handler after an actual
provider report, retaining the shared guard until the ledger commit succeeds.
The GPU worker commits the adapter/ZIP and returns its measured report; it does
not reload the artifact volume while Hugging Face cache files may remain open.
Refresh status after the job completes. This read can reconcile finished work
and clear its retained guard; it never launches or retries GPU training.
