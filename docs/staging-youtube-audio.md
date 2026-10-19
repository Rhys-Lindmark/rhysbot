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

Only existing volume `rhysbot-training-artifacts` is used. The SDK worker uses
`Volume.from_name(..., create_if_missing=False)` and
`batch_upload(force=False)`. The only remote destination is:

```text
audio-inputs/<speech-youtube-run-id>/<podcast-20hex>.(m4a|webm|mp3|ogg|opus)
```

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

Nine stdlib tests cover canonical identities, safe command arguments, exact
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
