# Serving actual checkpoints

`training/serve.py` is deployed with the actual full nanochat step-80 checkpoint
and Qwen CPT/SFT adapter. Authenticated operator requests verified both saved
checkpoint hashes and returned real GPU-generated text: nanochat in 11.33 seconds
(incoherent output), Qwen in 30.81 seconds. Both models are enabled on the site;
the public Qwen browser path also passed automatic widget verification,
Siteverify, proxying and generation. Wrong-origin and invalid-token requests
returned HTTP 403. See [serving.json](../results/serving.json).
An inert import needs neither Modal nor torch; no checkpoint is enabled merely
by importing the source.

The service reuses the training image, `rhysbot-training-artifacts` volume at
`/artifacts`, `rhysbot-training-singleton` Dict, and `/artifacts/spend-ledger.json`.
It never creates a fresh ledger. Training, inference, and billing serialize on
the same `active` lock. GPU and HTTP functions have zero minimum containers,
one maximum container, and a two-second scaledown window. GPU execution has a
180-second timeout, no automatic retries, and at most 64 generated tokens.

## Select immutable artifacts

Set the deployment environment variable `RHYSBOT_SERVING_TARGETS` to a JSON object
with one or both keys `nanochat` and `qwen`. Their values must contain exactly:

| Target | Fields |
| --- | --- |
| nanochat | `run_id`, `step`, `weights_sha256`, `metadata_sha256`, `tokenizer_sha256` |
| qwen | `run_id`, `weights_sha256`, `metadata_sha256`, `adapter_config_sha256` |

Use actual SHA256 values from the reviewed volume artifacts, never sample
strings. `run_id` selects `/artifacts/runs/<run_id>`. Requests cannot change it.
The corresponding `launch.json` must say `completed_actual_artifacts`, family
must match, phase must be `pilot` or `train`, and upstream pins must match the
deployment code. Evaluation-only and failed runs cannot be served.

Nanochat pins these files:

- `nanochat/chatsft_checkpoints/rhysbot/model_<step:06d>.pt`
- `nanochat/chatsft_checkpoints/rhysbot/meta_<step:06d>.json`
- `nanochat/tokenizer/tokenizer.pkl`

Its metadata step must match the explicit positive step. The pinned loader
restores the saved model state strictly and checks tokenizer vocabulary size.
Generation uses a bounded greedy forward loop and does not invoke upstream
calculator/tool execution. It is labeled a tiny scratch experiment, with limited
training and no claim of capable general chat.

Qwen pins `qwen/sft/adapter_model.safetensors`, `qwen/sft/run.json`, and
`qwen/sft/adapter_config.json`. Its SFT report must show actual optimizer updates,
trainable parameters, matching frozen data, and the pinned Qwen3.5-4B-Base
revision. The existing text-backbone loader rejects missing language weights.
Serving additionally compares every restored adapter tensor against the saved
adapter. Missing or mismatched adapters fail; there is no base-model fallback.
Base weights download only into disposable remote `/tmp` cache, removed after
the call. They do not become another persistent volume copy.

After those checks and the pilot are accepted, the operator can deploy with the
existing tools virtual environment (from the repository directory):

```sh
# Set RHYSBOT_SERVING_TARGETS from the reviewed private deployment JSON first.
../work/rhysbot-tools/bin/modal deploy training/serve.py
```

This document does not authorize deployment or enable nonexistent checkpoints.
The deployment environment fixes the targets; an update requires another
reviewed deployment. Record its returned endpoint URL for the site proxy.

Serialized handlers resolve the shared named budget Dict during remote execution;
they must not capture an unhydrated local handle. Their copied helper package is
explicitly on `PYTHONPATH`. Deployment and real requests still verify packaging.

Use a dedicated Modal web proxy token (`wk-`/`ws-`), never the account API token.
The current workspace issues workspace-wide proxy tokens; environment scoping
is unavailable for this token. Keep it only in encrypted server bindings. Public
site variables belong in the website's `wrangler.toml`, because Git deployments
replace dashboard plaintext variables. Encrypted secrets remain server-side.

## Site proxy and browser contract

The Modal ASGI endpoint uses `requires_proxy_auth=True`. Configure a server-side
route `https://ai.rhyslindmark.com/api/rhysbot`; the browser must
never receive Modal proxy tokens. The route must:

1. Accept only JSON POST from exact origin `https://ai.rhyslindmark.com` and cap
   request bodies at 8 KiB before forwarding. Handle OPTIONS at the edge.
2. Enforce abuse limits before Modal invocation: at most three requests per
   client per hour, six per day, and 25 total per day. Use persistent rate-limit
   state; do not rely on browser state or CORS as authentication. A verified
   human challenge or existing signed-in session must precede paid requests.
3. Derive `X-Rhysbot-Client` using HMAC-SHA256 and the secret salt over the edge's trusted client
   identity. Do not trust an incoming client-supplied identity header or retain
   raw client addresses in the spend ledger. Forward the exact allowed Origin.
4. Attach `Modal-Key` and `Modal-Secret` from server-side secrets, forwarding to
   the returned Modal endpoint's `/generate` path. Do not retry automatically.
5. Preserve error statuses and return `Cache-Control: no-store`. Use a bounded
   upstream timeout; if it expires, show the uncertain-operation error and do
   not send a duplicate request.

The browser sends:

```json
{"model":"nanochat","prompt":"Explain entropy.","max_new_tokens":32,"verification_token":"fresh-widget-token"}
```

The edge verifies and removes `verification_token` before forwarding. Only
`model`, `prompt`, and optional `max_new_tokens` reach Modal. Model is an
enabled deployment key, prompt is at most 1,600 UTF-8 bytes, generated tokens are
1–64. Tokenized input is capped at 384 tokens; total context at 512. Reserved
special-token delimiters, caller-defined roles, paths, tools, model IDs and run
IDs are rejected. The 384-token input limit includes the system message; a prompt
within the byte limit can still exceed this further tokenized limit. Single-turn
generation is supported; the UI keeps any history
locally and must not silently concatenate unbounded history.

A successful public response contains `ok`, `text`, `model`, `checkpoint_run_id`,
`weights_sha256`, `identity`, token counts, measured latency, and `tools_executed:false`.
The backend additionally records request/reservation identifiers and accounting;
the public proxy does not expose those accounting fields. Render text
as text, and show the returned identity label. Failures return `error` and a
4xx/503 status; 409 means a job is in progress, 429 is the quota, and 503
includes closed budget, missing checkpoint, or uncertain execution requiring
operator inspection. Busy/uncertain responses also carry a bounded status
summary described below. No errors
produce synthetic or base-model answers.

## Spend and operation limits

Each admitted request uses existing `budget.reserve` with the eval tariff, then
labels the ledger job `inference`. This preserves the successful-pilot gate,
active-job guard, immutable $200 authorization, and cumulative committed-spend
checks without changing training's budget implementation. With today's
configuration, `(180 + 180) seconds × $12/hour` reserves **$1.20 per request**.
This is a deliberately conservative reservation, not a measured request price.
Successful and failed calls retain the entire reservation until a provider bill
is reconciled through the existing training billing-receipt function. Elapsed
time never creates a refund. Training and serving together cannot admit a new
reservation exceeding the remaining ledger authorization.

The margin is intended to cover cold starts, short idle tails and coordinator
CPU alongside GPU inference. It is not a provider-enforced invoice cap: edge
requests, rejected HTTP traffic, network transfer, image builds, and storage can
have costs outside measured GPU execution. The mandatory authenticated proxy,
edge quotas and short idle window bound exposure; monitor actual bills and
reconcile conservatively. Persistent training/artifact volume storage continues
to accrue even after serving scales to zero. Stop the deployment when the demo
window ends, and separately review retained checkpoints/volume lifecycle with
the operator. Never delete unique trained checkpoints automatically.

## Coordination, busy responses and recovery

The source now dispatches exactly once with Modal `Function.spawn()`, records the
returned `FunctionCall.object_id` in the existing reserved ledger job, commits
that metadata, then waits with `FunctionCall.get(timeout=200)`. The GPU function
still has its 180-second provider timeout; API/proxy/browser deadlines remain
240/230/250 seconds. The wait deadline also bounds queue/cold-start waiting; it
does not prove that a timed-out provider call stopped. This coordination update
has local regression evidence; deployment and platform recovery checks must be
recorded separately from the earlier successful inference evidence.

The API permits two simultaneous HTTP inputs in one container: one can await the
GPU while a second returns status or a fast busy error. A per-process mutex
protects Volume reload/write/commit operations across those threads. The GPU's
one-container limit, shared training Dict singleton, pilot gate, quotas and spend
ledger still permit only one admitted GPU call. There is no automatic generation
queue, retry, cancellation, lock expiration or reservation refund.

After dispatch or persistence uncertainty, the coordinator retains the `active`
lock and full reservation and best-effort marks `coordination_state=uncertain`.
The private job also records `provider_call_id`, the request digest, checkpoint
identity, trusted client HMAC and generic exception type. It stores no prompt or
completion text. Even dispatch failure can occur after the provider accepted the
call; absence of a call ID is not proof that nothing started.

### Read-only status contract

Authenticated `GET /status` on the same returned Modal endpoint requires the
exact Origin and a server-derived `X-Rhysbot-Client`, just like `/generate`.
It never spawns a function, reserves budget, changes the ledger or clears a lock.
It reads the lock and ledger consistently, then, when a call ID exists, uses
`FunctionCall.from_id(id).get(timeout=0)` to inspect that existing call. A plain
Modal polling timeout means pending; expired output, provider function timeout,
network failure or other exceptions are unknown and require operator evidence.
No age threshold changes this decision.

The response exposes only `ok`, `state`, `reason`, `operator_action_required`,
`retry_after_seconds` and, for the same trusted client only, its opaque
`request_id`. It never exposes provider call IDs, other clients' request IDs,
raw/HMAC client identities, request digests, prompts, outputs or spend figures.
A successful status read returns HTTP 200 even when its state requires recovery.
It is a snapshot, not a reservation or a promise that the next POST is admitted.

| State | Meaning | Client behavior |
| --- | --- | --- |
| `available` | No active lock or active ledger job | Permit a new user-initiated generation; normal gates still apply |
| `busy` | Pending tracked inference, active training/billing, or an observed coordination transition | Show an in-progress message; check status after the 10-second hint |
| `recovery_required` | Untracked/unknown call, terminal call with an active ledger, orphan lock/job, or inconsistent metadata | Show an operator-recovery message; do not resubmit generation |

A terminal provider result while this container's coordinator is still recording
it remains `busy`; it needs no intervention yet. A terminal result after the
coordinator is gone remains locked until safe operator reconciliation. A status
lookup failure returns 503 and is never interpreted as availability. Legacy
requests without call IDs remain recoverable only through independent provider
inspection; installing this update cannot reconstruct their missing IDs.

A second POST returns 409 with `error=training_or_inference_busy` for a pending
job, or 503 with `error=execution_uncertain_operator_check_required` when recovery
is needed. Both include the same public status fields. Neither POST launches a
second GPU call or consumes another reservation. If a lock owner changes during
inspection, the response requests another status check; it never repairs either
owner.

Frontend/proxy integration is separate: add a fixed edge status route that
forwards only to this endpoint's `/status`, supplies the existing proxy secrets,
Origin and derived client HMAC, and projects only these public status fields.
Keep GET configuration separate. Status checks require no Turnstile generation
token and cannot initiate paid work; still rate-limit them at the edge. Poll only
a small finite number of times (for example three checks, ten seconds apart),
then stop and show the state. Reset verification after a generation attempt;
never automatically replay its POST. Do not replace `recovery_required` with a
perpetual generic “model busy” message. A new generation requires a user action
and fresh verification after availability is observed.

### Safe operator reconciliation

There is intentionally no HTTP unlock endpoint or automatic recovery mutation.
For a stuck job, follow this finite procedure in the authenticated operator
context, keeping snapshots and provider identifiers private:

1. Pause new generation admissions. Preserve fresh snapshots of the named Dict
   owner and Volume ledger. Identify the exact active token, reserved job,
   recorded call ID if present, and corresponding API coordinator execution.
2. Inspect that exact provider call. The supported existing-call lookup is
   `modal.FunctionCall.from_id(call_id).get(timeout=0)`. A plain polling timeout
   means it is still pending: wait, without restarting or spawning anything.
   A missing ID, exception or best-effort call graph is insufficient termination
   evidence. For legacy calls, correlate provider input/container records and
   logs independently; do not guess from `created_at` or the 180-second setting.
3. Confirm both that the GPU input is terminal and that its old coordinator can
   no longer write the ledger. If either fact is uncertain, leave lock and
   reservation intact. A returned provider result is useful evidence; record
   only sanitized identity/count/latency metadata, not prompts or completions.
4. While the same old token still owns `active`, reload the ledger, verify the
   exact job and call ID still match the snapshots, and record the confirmed
   terminal outcome using the existing `budget.finish` semantics. Preserve its
   full `reserved_usd`; no elapsed-time refund or billing guess is permitted.
   Commit that ledger first. Recheck the owner immediately before removing only
   that matching token. Any mismatch or persistence failure aborts repair.
5. Read back lock and ledger and check status. Re-enable admissions only after
   consistency is confirmed. A subsequent retry is an explicit new request with
   a fresh reservation. Reconcile actual money later using a provider receipt;
   job termination alone never authorizes a refund.

Finite acceptance checks for the coordinator update: reservation is durable
before exactly one spawn; call ID is committed before waiting; spawn/commit/wait
failure retains active lock and full hold; concurrent status/POST never admits a
second GPU; terminal-result recording releases only its matching lock; legacy,
orphan, changed-owner and unknown-provider states fail closed; public responses
omit private metadata. Local tests cover these cases. After reviewed deployment,
record a real read-only status response during one bounded admitted call and a
terminal status response afterwards, then verify any actual stuck legacy job
against independent provider evidence before operator repair.

## Verification and remaining checks

Local stdlib tests cover request/deployment restrictions, shared spend/pilot
gates, rate limits, unreconciled failures, uncertain RPC locks, missing ledger,
hash-bound checkpoint eligibility, durable provider call IDs, read-only status
classification and concurrent HTTP rejection without duplicate GPU admission. Actual deployment verified copied-module
paths and lazy named-Dict resolution. A real authenticated HTTP 409 verified
shared-lock enforcement while Qwen trained. Subsequent HTTP 200 operator calls
verified GPU loading, exact saved hashes and real completions for both families.
Provider-billed costs remain pending; public browser results and edge rejection
checks are recorded in the serving evidence artifact.

Source references: [Modal ASGI web functions](https://modal.com/docs/guide/webhooks),
[proxy authentication](https://modal.com/docs/guide/webhook-proxy-auth), and
[volume reload/commit semantics](https://modal.com/docs/guide/volumes).
