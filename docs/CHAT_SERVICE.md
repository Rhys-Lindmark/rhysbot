# Conversational Rhysbot

The [chat](https://ai.rhyslindmark.com/rhysbot/chat/) opens an ongoing conversation
with AI Rhys using Qwen3-30B-A3B-FP8 and approved source excerpts. Side-by-side
comparison is optional: the Qwen side receives no corpus and retains its own
assistant history. This is a retrieval comparison, not a claim that this managed
model was fine-tuned.

“Trained 4B vs Qwen 4B base” uses the actual saved `qwen-train-20261019a` CPT/SFT
adapter with the pinned Qwen3.5-4B-Base. The other side disables that same adapter.
Both receive the same neutral system prompt, retained user turns and deterministic
decoding. Their previous assistant answers remain separate. Prompts over the
research checkpoint’s 384-token input allowance fail rather than silently clip
the question. Output is limited to 96 tokens per side. This comparison can still
have a GPU cold start; the default managed chat avoids loading our GPU checkpoint.

“Pure nanochat Rhys” now runs the actual pinned scratch checkpoint on CPU, with
bounded conversation history and no retrieval or Qwen fallback. Each reply shows
its run and weight hash. It is a tiny, often incoherent research model; CPU cold
requests measured around 15 seconds, and its context is only 512 tokens.

The [model explainer](https://ai.rhyslindmark.com/rhysbot/learn/),
[lab](https://ai.rhyslindmark.com/rhysbot/lab/) and
[Anki evaluation](https://ai.rhyslindmark.com/rhysbot/evals/) describe the different
paths, run bounded experiments and expose paired checkpoint evidence. See
[PLAYGROUND.md](PLAYGROUND.md) for models, controls and limitations.

The original nanochat and Qwen checkpoint demos and measured training graphs
remain available under the collapsed research demo on the corpus page. Frozen
training inputs and saved research checkpoints are unchanged.

## Sources and history

The private D1 search index contains 37,712 approved excerpts. Speaker-aware
records contribute only Rhys-matched lines, never guest or unknown turns.
Writing has no format multiplier. Each relevant medium can contribute matching
evidence, with up to six excerpts, deduplicated by document. Transcript excerpts
come from near the matching passage rather than the start of the transcript.
Explicit requests for HEE, podcasts/YouTube, X, writing or Anki select that medium.
Elliptical follow-ups recover the preceding user topic. Personal-view questions
prefer public authored/spoken material over Anki; curated notes and quotations
are not automatically treated as endorsements. Citations link to public sources.
Retrieval is
keyword-based and incomplete, and generated factual claims still need checking. First-person
conversation is requested explicitly, with sources underneath rather than a
stock third-person preamble. This prompt change does not retrain the weights or
turn a guest quote or curated card into Rhys’s own belief.
The complete downloadable research corpus includes additional guest/unknown
transcripts with attribution, so its excerpt count is larger than the chat index.

Completed histories persist in the tab's sessionStorage across refresh, with
separate conversations per mode, at most 30 turns per mode and 240 KB total.
The most recent five complete question/answer pairs, bounded to 12 KB, are sent
with a new message. No server conversation archive is implemented. Browsers may
restore sessionStorage when reopening tabs. New chat clears all local histories and
cached generated audio. Text is rendered as text, not HTML. Browser microphone
recognition is opt-in, fills a draft, and never sends automatically. Availability
checks, examples and mode switches do not launch inference.

## Authorized synthetic voice

Rhys explicitly authorized generating his own voice and confirmed the Digicene
narration and all HEE narration as his. The private reference is a 12-second
Digicene excerpt; the original recording/voice reference is not published in Git.
The legacy research model is `Qwen/Qwen3-TTS-12Hz-0.6B-Base`, revision
`5d83992436eae1d760afd27aff78a71d676296fc`, including its speech tokenizer.

### Default CPU streaming voice

The default now uses [Kyutai Pocket TTS](https://github.com/kyutai-labs/pocket-tts),
with [KevinAHM's public INT8 ONNX export](https://huggingface.co/KevinAHM/pocket-tts-onnx), pinned to
`58a6d00cf13d239b6748cb0769f35c580a8f606c`, English 2026-04. Models are CC BY 4.0;
the export's inference wrapper is Apache 2.0. Source code is pinned and hash
checked at remote image build. The original reference, derived state and model
weights stay private on Modal; no model is installed on the developer Mac or
visitor's browser. The user's consent and synthetic-voice label still apply.

`training/pocket_voice.py` serves CPU speech directly through the authenticated
`/api/rhysbot-voice-stream` bridge. It streams small PCM16/24kHz/mono SSE chunks;
the browser schedules each chunk through Web Audio with a 60ms jitter buffer.
There is no polling, WAV-part download or whole-utterance wait. AudioContext is
resumed in the Listen click gesture. Stop and New chat cancel queued playback.
Only a valid terminal event creates a complete WAV for local, unbilled replay.
An interrupted stream cannot resume; another explicit click is a new request.

One small CPU container stays ready (`min_containers=1`, `max_containers=1`).
It requests 0.125 physical cores/384 MiB and is limited to four cores/768 MiB.
ONNX CPU arenas and thread spinning are disabled; a ten-second idle probe used
no measurable process CPU time. This removes normal scale-from-zero delays.
Replacement, preemption and deployment can still require startup, so two seconds
is a measured normal-case target, not a latency guarantee.

At the published Modal rates (checked 2026-10-20), 31 days at the CPU floor and
full memory limit costs $8.845416, rounded up to an $8.90 monthly reservation.
This leaves $1.10 of the authorized shared $10 allowance for variable calls.
Each new CPU stream reserves $0.02 before dispatch; this covers two bounded
90-second inputs plus 60-second startup each at the hard resource limits.
The activation month reserves whole remaining Los Angeles calendar days at
$8.90/31 per day, rounded upward. These are conservative reservations, not
provider invoices; free credits are not assumed.

Deploy `platform/chat-standby-migration.sql` once before enabling
`RHYSBOT_CPU_STANDBY_ENABLED=1` and its ISO timestamp
`RHYSBOT_CPU_STANDBY_SINCE`. Every chat, voice and checkpoint charge atomically
includes the same fixed standby amount, once per month, preserving previous
usage. It refuses additional calls at $10. Keep the standby flag and the ready
Modal worker paired: to roll back, stop that worker and disable the flag as one
operational change. The CPU stream URL and flags belong in the website's
Wrangler configuration; Pages rebuilds replace dashboard-only plaintext values.

Client-bound IDs and atomic claims prevent repeat dispatch. Uncertain requests
are never automatically retried; the private endpoint also rejects reused
nonces. Audio is bounded to 149 seconds and 2,000 text characters. No new
subscription or model training is required. Private voice conditioning and
portable CPU graph optimization happen during the remote image build; runtime
loads four synthesis sessions and the saved state, without the voice encoder.
Loading, warm-up and every inference step run on one dedicated thread. This
keeps ONNX floating-point settings on the inference thread and avoids the
severe slowdown observed with the ASGI thread pool. Denormal values are flushed
to zero. A request retains its busy lock through queued inference and iterator
cleanup. The browser plays while the rest of the reply is being synthesized.

The loaded-model pilot reached meaningful audio in 0.176s. The older scale-zero
CPU route took 12.262s through the actual site when cold, versus 1.386s warm.
The final ready-worker website measurements and limitations are recorded in
`results/voice-fast.json`. Timing probes record received PCM and Web Audio
scheduling; they do not measure physical speaker output or human voice likeness.

### Legacy GPU research voice

The “AI Rhys voice” control reads up to 2,000 characters from a reply after an
explicit click; longer replies show that limit. The approved speaker embedding
is created once and saved privately, then reused across GPU cold starts. This
is voice cloning with a saved speaker profile, not a newly fine-tuned TTS model.
The model still has to synthesize each new text. No always-on GPU is required.

Voice submission returns a background-job acknowledgement immediately. The UI
polls short authenticated requests and shows loading/speaking progress, elapsed
time, Stop waiting and Resume. Resume retrieves the same provider result;
neither polling nor resubmission reserves another $0.50 or dispatches a second
GPU job. Client-bound request IDs and text hashes prevent cross-client replay
and changed-text reuse. Modal can recover a preempted input once, with at most
two 150-second execution attempts, with a 45-second startup deadline per attempt,
within the original voice reservation.
Failed or uncertain dispatches are not automatically re-dispatched by the app.
Request grants expire after 24 hours; provider results have Modal's own
retention for voice text and audio. Private incremental audio parts expire after
seven days of inactivity in the provider's Dict storage. Every part must match
its authoritative byte length and SHA256 before playback. Replays in the same
tab use the downloaded audio immediately.

Audio is labeled synthetic and plays after the explicit Listen click; browser
autoplay restrictions may require pressing the audio player's Play control.
This is speech synthesis plus optional browser dictation, not a live duplex
audio model. Voice GPU execution is bounded to two 150-second attempts on an
H100 GPU, with a 2-second idle reuse window and zero minimum containers. Long
text can still take time. GPU memory snapshots retain the loaded model and
approved voice profile across scale-to-zero. New infrastructure variants can
require snapshot creation again. The opening is generated first (normally up to
100 characters), then later parts (normally up to 180 characters) are played in
order while generation continues. Full replay joins verified PCM parts locally;
no extra GPU request is made. Brief gaps between parts remain possible.

## Budget and authorization

Chat, speech and saved-checkpoint comparisons share a durable $10/month allowance
in D1, resetting by calendar month in America/Los_Angeles. Conservative charges
include the $8.90 standby baseline above, $0.005 per model chat, $0.02 per CPU
stream ($0.50 per legacy GPU synthesis) and $0.20 per checkpoint pair. A
scale-to-zero CPU deployment without standby instead reserves $0.05 per stream.
These are internal cost reservations, not customer prices or provider invoices;
failed/uncertain requests are not refunded automatically. Atomic updates refuse
additional calls at the cap. Each HMAC-hashed client IP is limited to 40
generation calls/hour; voice status reads are exempt.
Turnstile grants a one-hour HttpOnly/Secure/SameSite session after verification.
A private server credential permits operator integration probes through the same
meter, without disabling browser verification.

Managed inference uses Cloudflare Workers AI. The default CPU voice keeps one
small worker ready; legacy GPU and checkpoint functions have zero minimum
containers and bounded timeouts. No always-on GPU was enabled. Voice/comparison
calls need both Modal proxy credentials and an application Bearer credential.
Single-use grants are persisted before dispatch, with provider call IDs retained.
Private corpus training uses the separate original $200 research ledger and
shared singleton, not this monthly allowance.

## Private corpus training

[Train yours](https://ai.rhyslindmark.com/rhysbot/train/) accepts private UTF-8 TXT
or text-only JSONL, up to 5 MiB. Use at least a few pages: the tokenizer must
produce at least 512 tokens. The bounded recipe adapts pinned Qwen3.5-4B-Base
with LoRA rank 8, 32 updates and 256-token sequences. It reports real held-out
losses and downloads an actual adapter, tokenizer and training metrics ZIP.
This is an experimental pilot, not an assurance of a useful personalized model.

The deployed checkout uses Sandbox confirmation, with no card form or charge.
Stripe integration only accepts test keys/events; live payments are disabled.
Both modes share one global lifetime public GPU claim, and test payments never
create new compute authorization. Wider usage requires a deliberate new quota
and cost policy. See [platform details](../platform/README.md) for schemas,
capabilities, retention, webhook validation and recovery contracts.

No uploaded corpus is published or added to Rhys’s corpus. Random per-job
capabilities protect status, deletion and completed-only downloads. Corpus bytes
are private D1 chunks because R2 is not enabled on the account. Seven days is an
access expiry, not a promise of scheduled erasure. Deleting eligible uploaded
text does not delete previously trained weights or provider backups.

## Reproduction

The website repository owns deployed Pages endpoints. Matching chat sources are
in `platform/chat-pages/`, training sources in `platform/`, and the remote bridge
in `training/platform_service.py`. Build the retrieval index from the approved
release with `platform/build_chat_index.py`; never index raw exports or private
review files. Credentials, owner reference and job capability values stay outside
Git. Deployment reads the explicit saved-checkpoint target manifest and the
approved private voice reference; it never retrains the historical models.

## Measured verification

Individual hosted probes measured 0.742 seconds to first fast-mode text and
1.580 seconds to finish both replies. Two-turn memory probes finished in
0.842/0.931 seconds per pair. Numeric-token handling was verified with exact
`2019 [1] and 42.` output. These are samples, not latency percentiles.

The actual saved 4B pair took 124.79 seconds on the first base-weight download
and 43.06 seconds on a follow-up using persistent cached weights. Its Rhys-side
follow-up answered just “Language,” illustrating that adaptation does not assure
better or more useful answers. The synthetic voice sample is 5.84 seconds at
24 kHz; first synthesis took 58.65 seconds. Voice similarity has not been assessed
by a human listener.

Two owner verification jobs each completed32 real training updates. Their small
held-out loss changed from 3.04697 to 2.85306/2.85325. The first needed verified
operator accounting after an open cache log blocked a volume reload; the second
completed through the repaired CPU finalizer. Status reads can finalize a
finished job and release its retained training guard; they never launch or
retry training. Refresh the job status after completion. Owner probes seeded
private D1 corpus rows directly; upload/checkout behavior was covered by focused
API tests rather than a new CAPTCHA/browser automation run.

Adapter downloads require authoritative byte count and SHA256 metadata. The
Cloudflare proxy uses native fixed-length streaming; the browser verifies both
the complete size and digest before offering a file. A truncated response cannot
be offered as a successful download. See [public verification data](../results/chat-upgrade.json)
for measured results and limitations.

The continuity/source/voice follow-up is recorded in
[chat-continuity.json](../results/chat-continuity.json). A real 413-character read
from an existing GPU snapshot delivered its first part at 22.948 seconds and
finished at 43.511 seconds. Two initial snapshot-creation probes took
48.043–64.514 seconds to first audio. These are individual samples, not latency
guarantees. Model startup and synthesis remain the main delay; cached individual
words would lose sentence prosody and do not remove model loading.
