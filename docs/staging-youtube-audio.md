# Private YouTube audio staging

`scripts/stage_youtube_audio.py` stages only the explicitly inventoried public
video-only recordings. It does not run speech models, GPU functions or corpus
integration. It uses existing yt-dlp 2026.08.19, yt-dlp-ejs 0.8.0 and Node 24;
no cookies, account login, browser, cache or new dependency installation is used.
The existing Modal account credentials stay in the private environment.

Input is an immutable JSON list of at most 76 episodes. Each needs a unique
`podcast-<20 lowercase hex>` ID, explicit 11-character `youtube_id`, exact
`https://www.youtube.com/watch?v=ID` `youtube_url` and matching `source_url`,
`audio_source_kind=youtube_audio` and `enclosure_url=null`. Original title,
duration and publication fields are retained. Signed streaming URLs are neither
input identities nor output manifest fields.

One recording is downloaded at a time, as best available m4a audio with a
best-audio fallback. There is no local conversion or fixup. Config/cache use,
playlists and all yt-dlp retry classes are disabled. The total attempt budget is
150 seconds across download, streamed hashing and upload; all subprocesses have
finite deadlines and owned process-group teardown. A 500 MiB child file-size
limit, live output-size monitor, streaming hash ceiling and 64 KiB log guards
bound the operation. Concurrent invocations on the same staging directory are
blocked by a file lock. There is no background monitor.

Only existing **v1** volume `rhysbot-training-artifacts` is used. It is never
created, converted or overwritten. The public SDK context is
`Volume.from_name(..., create_if_missing=False, version=1).batch_upload(force=False)`.
The destination remains:

```text
audio-inputs/<speech-youtube-run-id>/<podcast-20hex>.(m4a|webm|mp3|ogg|opus)
```

The uploader uses a source-reviewed bounded **file object**, rather than passing
a path or complete bytes. Modal 1.6.2's v1 implementation retains the provided
seekable object for blob hashing and `BytesIOSegmentPayload` streaming. Its
default 16 MiB reads create large transport buffers; the wrapper returns at most
1 MiB per requested read. The payload advances by actual bytes read, preserving
hashes and all media bytes. SDK inline content below 4 MiB may be read completely,
but an unexpected unbounded large-file read fails closed. If the provider selects
multipart, the SDK tries to reopen `.name` and would bypass this reader; that
access also fails closed. No internal SDK constructor, global monkeypatch,
provider RPC forgery or unbounded fallback remains in the accepted source.

Installed Modal must be exactly **1.6.2**, and the public `batch_upload` signature
must match its reviewed `force=False` interface. Version/signature drift fails
before upload. The object stays readable throughout the upload context. Public
SDK checksum, error and no-overwrite behavior is retained. SDK transport retries
can occur internally within the overall deadline; the stager never retries an
uncertain upload as a new attempt.

The private progress JSON contains `schema_version`, `run_id`, `volume_name`,
`input_sha256` and `episodes`. Each episode entry contains the original `episode`,
`status`, optional `staged_audio`, and bounded private `attempt` evidence:
commands, child PIDs, start times, elapsed time, observed child peak RSS and
failure type. Statuses are pending, downloading, downloaded, uploading, uploaded,
failed or recovery_required. No credential values are recorded. Child peak RSS
is the OS-reported maximum of reaped children, not a measurement of simultaneous
whole-machine memory or proof that swap pressure recovered.

The separate output manifest is a JSON **list of uploaded episodes only**. It
retains original episode fields and adds exactly:

```json
{
  "staged_audio": {
    "relative_path": "audio-inputs/speech-youtube-20261019a/podcast-00000000000000000001.m4a",
    "sha256": "<64 lowercase hex characters>",
    "bytes": 12345
  }
}
```

State and output JSON use 0600 permissions, fsync and atomic replacement. Their
input/run/episode identities are checked on resume. Successfully uploaded
entries are skipped without redownloading; a downloaded checkpoint can resume
upload after verifying its local bytes/hash. Local audio is deleted only after
cloud upload success and durable uploaded progress. A crash after that durable
confirmation permits deletion only of an exact hash-matching local copy.

A failed download records metadata and removes only its own regenerable audio
basename. It is not automatically retried. An interrupted download can be cleaned
and resumed from its durable downloading state. An upload exception or interrupted
upload is uncertain: preserve the local audio, mark recovery_required and stop
all further staging. Do not clear that state or overwrite the cloud path based
on age. The operator must reconcile the exact remote object/hash and any live
worker before authorizing a correction. The script never auto-retries uncertain
uploads or uses force=True. Downloader and upload logs remain private and may
contain provider diagnostics; do not publish them.

## Invocation

Run with the existing tools Python, which already has the Modal SDK, and explicit
existing executables. All manifest/progress/work paths must be under the private
workspace; symlinks are refused. The initial measured pilot is `--limit 1` only:

```sh
MODAL_CONFIG_PATH="$PRIVATE_MODAL_CONFIG" \
PYTHONDONTWRITEBYTECODE=1 "$TOOLS_PYTHON" scripts/stage_youtube_audio.py \
  --manifest "$PRIVATE_VIDEO_ONLY_MANIFEST" \
  --state "$PRIVATE_STAGING_PROGRESS" \
  --output-manifest "$PRIVATE_UPLOADED_MANIFEST" \
  --work-dir "$PRIVATE_AUDIO_SCRATCH" \
  --run-id speech-youtube-20261019a \
  --yt-dlp "$EXISTING_YT_DLP" --node "$BUNDLED_NODE_24" --limit 1
```

The actual first inventory item is 927 seconds. Further staging requires the
manager's resource admission after pilot evidence. Increasing `--limit` never
creates parallel downloads. Staged input is still machine-source material: root
must verify declared size/SHA before decoding and separately reserve any GPU
speech work. Upload success does not mean transcription or publication succeeded.

Eleven stdlib tests cover canonical identities, safe command arguments, exact
upload metadata, durable resume/cleanup, uncertain upload blocking, immutable
resume provenance, symlinks/byte ceilings and actual child timeout/reaping. These
source tests use synthetic media and mocked upload; real pilot evidence is
recorded separately below after the authorized attempt.

## Actual single-video staging pilot

The authorized first 927-second video staged successfully: one uploaded input,
zero failures or uncertain uploads, and 75 inventory entries still pending. The
m4a was 14,998,141 bytes; its SHA256 is
`27a5fe27f8a5fec6d9a76f45000911249d25bd0da7e592d7d42c799b3980d081`.
It was uploaded under the exact episode basename in the configured run prefix,
then removed locally after durable progress. The output manifest contains only
that one uploaded entry. Local evidence/logs total approximately 48 KiB.

End-to-end time was 15.855 seconds. Main-process peak RSS was 32,440,320 bytes;
maximum OS-reported reaped-child peak RSS was 160,530,432 bytes (153.1 MiB).
These are separate process measurements, not simultaneous whole-machine totals.
The observed network-tool peak exceeds the earlier corpus-only 120 MiB target;
further staging requires resource admission using this actual measurement. Both
pilot child processes exited. No local ML, GPU work, full-archive staging,
transcription or corpus publication was performed by this tool acceptance.

## Corrected large-file acceptance

The first large-file loop was stopped after observed default-uploader child RSS
reached 362,184,704 bytes. Two uploads had durable confirmations; a third upload
was uncertain and its 92,845,292-byte local recording was retained. Read-only
Volume readback returned FileNotFoundError for that exact third object; no
success was inferred from elapsed time. Previous state/failure evidence remains
private. The existing shared Volume was verified as **v1**; no conversion or
alternate Volume was created.

After implementing the public bounded file-object input, an offline check using
installed Modal 1.6.2 verified an 8,704,000-byte blob's SDK hash and actual
`BytesIOSegmentPayload` output, with maximum read/write chunks of 1 MiB. The
reviewed SDK preserves short reads correctly. Twelve focused stdlib tests cover
read bounds, seek/hash integrity, blocked unbounded reads/multipart reopen,
public API version/signature drift and the existing no-overwrite/resume rules.

The explicitly authorized retained-file upload then succeeded in 23.377 seconds:
92,845,292 original media bytes, unchanged SHA256
`76b8dc6a0b1faa8a90d22cf8e141bca02c703abd5442bbcce3377c7286d58c77`.
SDK-child peak RSS was **99,958,784 bytes (95.3 MiB)**; main-process peak was
34,144,256 bytes (32.6 MiB). Their separate maxima total about 127.9 MiB, below
the admitted 250 MiB envelope; this is not a simultaneous full-machine memory
measurement. Durable uploaded state preceded local deletion. The remaining-video
run now has three confirmed uploaded entries, 72 pending and no uncertain upload.
Bulk resume remains subject to manager admission and ongoing resource checks.
No normalization, local ML, GPU job or corpus mutation was required.

## Explicit JavaScript runtime and corrected download probe

An explicit `--js-runtimes node:...` alone did not clear yt-dlp's default Deno:
an isolated diagnostic observed Deno even while Node was named. That does **not**
establish which process caused the earlier cumulative peak. The corrected
command puts `--no-js-runtimes` before `--js-runtimes node:...`, so only the
explicit bundled Node runtime is enabled. The download child receives its own
copied environment with
`NODE_OPTIONS=--max-old-space-size=64 --max-semi-space-size=4`; the parent and SDK
upload environment are not mutated. The Node heap cap is not a total-RSS limit.
Tests verify option order, actual child environment, and unchanged parent value.

One corrected finite download of the unfinished Federal Reserve clip succeeded
in 2.945 seconds: 919,912 bytes, SHA256
`9b498cb55c39eba3ee021bfd1a457c093d32c01676f064c079edc5c107e9fd1c`.
The actual observed descendant was Node PID 75194; no Deno was observed. The real
source's Popen received the exact 64/4 MiB heap options. Download-phase reaped
child peak RSS was 55,820,288 bytes; sampled owned-family peak was 93,585,408
bytes (89.3 MiB), below 250 MiB. Sampling was every 50 ms and cannot exclude a
shorter unsampled spike. Caller environment remained unchanged. The original
batch state/manifest was not changed and bulk resume still requires acceptance.

The separate diagnostic SDK upload succeeded under a unique memory-probe prefix
in 1.868 seconds, with 61,227,008-byte child peak and 88,391,680-byte sampled
family peak. It used the original 947,293-byte recording/hash. Content-addressed
storage may reuse existing global bytes, so a new remote path does not prove the
blob body was retransmitted. Its temporary remote object is recorded privately
for later cleanup; this probe did not overwrite the original batch object or
run any speech/GPU job. Historical failures and per-PID evidence remain private.
