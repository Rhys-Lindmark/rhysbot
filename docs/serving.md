# Serving actual checkpoints

`training/serve.py` is source and mock tested. It has not been deployed or GPU
validated. No completed checkpoint is enabled by default. An inert import needs
neither Modal nor torch. Actual availability requires a completed training run,
reviewed artifact hashes, deployment, and the authenticated site proxy below.

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
3. Derive `X-Rhysbot-Client` as a salted SHA256 of the edge's trusted client
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
4xx/503 status; 409 means training/inference is busy, 429 is the quota, and 503
includes closed budget, missing checkpoint, or uncertain execution. No errors
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

If a remote RPC times out or its termination is unknown, serving preserves the
`active` lock and reservation. Both training and serving then fail closed.
Confirm the GPU call has terminated before an operator repairs the active
record/lock and attaches billing evidence. Do not clear the lock merely to make
the demo respond. A known terminal model failure is marked failed pending billing
and releases the lock. Prompts/output text are not persisted in ledger evidence;
it stores a request digest, checkpoint identity, counts and latency.

## Verification and remaining checks

Local stdlib tests cover request/deployment restrictions, shared spend/pilot
gates, rate limits, unreconciled failures, uncertain RPC locks, missing ledger,
and hash-bound checkpoint eligibility. The installed Modal 1.6.2 SDK can construct
the application locally without a cloud call. Actual GPU loading, completions,
cold-start costs, deployed proxy authentication and browser requests remain
unverified until real checkpoints and the edge proxy are available.

Source references: [Modal ASGI web functions](https://modal.com/docs/guide/webhooks),
[proxy authentication](https://modal.com/docs/guide/webhook-proxy-auth), and
[volume reload/commit semantics](https://modal.com/docs/guide/volumes).
