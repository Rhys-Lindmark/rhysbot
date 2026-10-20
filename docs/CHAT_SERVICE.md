# Conversational Rhysbot

The [chat](https://ai.rhyslindmark.com/rhysbot/chat/) has two distinct comparison
modes. The default streams Qwen3-30B-A3B-FP8 twice: the Rhys side receives retrieved
approved source excerpts, while the Qwen side receives no corpus. This is a
retrieval comparison, not a claim that this managed model was fine-tuned.

“Trained 4B vs Qwen 4B base” uses the actual saved `qwen-train-20261019a` CPT/SFT
adapter with the pinned Qwen3.5-4B-Base. The other side disables that same adapter.
Both receive the same neutral system prompt, retained user turns and deterministic
decoding. Their previous assistant answers remain separate. Prompts over the
research checkpoint’s 384-token input allowance fail rather than silently clip
the question. Output is limited to 96 tokens per side. This comparison can still
have a GPU cold start; the default managed chat avoids loading our GPU checkpoint.

The original nanochat and Qwen checkpoint demos and measured training graphs
remain available under the collapsed research demo on the corpus page. Frozen
training inputs and saved research checkpoints are unchanged.

## Sources and history

The private D1 search index contains 37,712 approved excerpts. Speaker-aware
records contribute only Rhys-matched lines, never guest or unknown turns.
Writing receives search priority. Anki is explicitly described as curated
knowledge rather than personal belief. Four excerpts of at most 1,500 characters
are retrieved per question; citations link to public sources. Retrieval is
keyword-based and incomplete, and generated factual claims still need checking.
The complete downloadable research corpus includes additional guest/unknown
transcripts with attribution, so its excerpt count is larger than the chat index.

Histories stay in the browser tab and are sent to the provider with each request.
No server conversation archive is implemented. New chat clears local history and
cached generated audio. Text is rendered as text, not HTML. Browser microphone
recognition is opt-in, fills a draft, and never sends automatically. Availability
checks, examples and mode switches do not launch inference.

## Authorized synthetic voice

Rhys explicitly authorized generating his own voice and confirmed the Digicene
narration and all HEE narration as his. The private reference is a 12-second
Digicene excerpt; the original recording/voice reference is not published in Git.
The pinned model is `Qwen/Qwen3-TTS-12Hz-0.6B-Base`, revision
`5d83992436eae1d760afd27aff78a71d676296fc`, including its speech tokenizer.

The “AI Rhys voice” control reads up to 2,000 characters from a reply after an
explicit click; longer replies show that limit. The approved speaker embedding
is created once and saved privately, then reused across GPU cold starts. This
is voice cloning with a saved speaker profile, not a newly fine-tuned TTS model.
The model still has to synthesize each new text. No always-on GPU is required.

Voice submission returns a background-job acknowledgement immediately. The UI
polls short authenticated requests and shows loading/speaking progress, elapsed
time, Stop waiting and Resume. Resume retrieves the same provider result;
neither polling nor resubmission reserves another $0.15 or dispatches a second
GPU job. Client-bound request IDs and text hashes prevent cross-client replay
and changed-text reuse. Modal can recover a preempted input once, with at most
two 180-second execution attempts within the original voice reservation.
Failed or uncertain dispatches are not automatically re-dispatched by the app.
Request grants expire after 24 hours; provider results have Modal's
own retention for voice text and audio. Audio must match its authoritative byte length and SHA256 before
playback. Replays in the same tab use the downloaded audio immediately.

Audio is labeled synthetic and plays after the explicit Listen click; browser
autoplay restrictions may require pressing the audio player's Play control.
This is speech synthesis plus optional browser dictation, not a live duplex
audio model. Voice GPU execution is bounded to two 180-second attempts, with a 15-second
idle reuse window and zero minimum containers. Long text can still take time.

## Budget and authorization

Chat, speech and saved-checkpoint comparisons share a durable $10/month allowance
in D1, resetting by calendar month in America/Los_Angeles. Conservative charges
are $0.005 per model chat, $0.15 per synthesis and $0.20 per checkpoint pair.
These are internal cost reservations, not customer prices or provider invoices;
failed/uncertain requests are not refunded automatically. Atomic updates refuse
additional calls at the cap. Each HMAC-hashed client IP is limited to 40 generation calls/hour; voice status reads are exempt.
Turnstile grants a one-hour HttpOnly/Secure/SameSite session after verification.
A private server credential permits operator integration probes through the same
meter, without disabling browser verification.

Managed inference uses Cloudflare Workers AI. Modal functions have zero minimum
containers and bounded timeouts; no always-on GPU was enabled. Voice/comparison
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
