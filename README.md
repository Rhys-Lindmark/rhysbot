# Rhysbot

Building a small language model from scratch and adapting Qwen to Rhys Lindmark's public writing and curated knowledge.

Project: **https://ai.rhyslindmark.com/rhysbot**

[Browse the research corpus](https://ai.rhyslindmark.com/rhysbot/#corpus).
Its additive collection labels unify **Writing** (Substack and linked essays) and
**Podcasts/YouTube** (captions and original-audio transcripts), with **HEE**,
**Anki** and **X** separate. Original source types/URLs/acquisition remain intact.
All 89 HEE narrations are explicitly labeled as owner-confirmed Rhys. Screened
speaker transcripts cover 214 recordings: 140 RSS recordings and 74 YouTube-only
recordings. Rhys matches are machine attribution; guest identities are not inferred
from titles or text. All transcripts were excluded from the already trained
models' frozen input. See the [dataset card](docs/DATASET_CARD.md) and
[speaker transcript methods](docs/speaker-transcripts.md).

`stats.json` distinguishes exact UTF-8 text bytes from JSONL file bytes, and
reports documents/excerpts/words per source and collection. The applied refresh
contains 39,692 excerpts from 33,508 documents: 22,609,936 text bytes versus
65,167,948 corpus JSONL bytes (below the 64 MiB publication limit). Its new token count is not yet measured.
AI2026 Parts 1/2/3 are refreshed by canonical URL; new research-corpus text and
labels do not alter the historical model training data.

Source: **https://github.com/Rhys-Lindmark/rhysbot**

## Chat and private training pilot

[Open conversational Rhysbot](https://ai.rhyslindmark.com/rhysbot/chat/): streaming source-grounded chat, side-by-side Qwen comparisons, a separate actual trained-4B comparison, and opt-in authorized synthetic Rhys voice. Chat and voice share a $10/month guard. [Train yours](https://ai.rhyslindmark.com/rhysbot/train/) is a private, bounded Sandbox pilot; no live payments. See [service methods and limits](docs/CHAT_SERVICE.md).

Chat also offers **Pure nanochat Rhys**, the actual tiny scratch checkpoint with
conversation history and no Qwen or retrieval fallback. Its current replies are
often incoherent. The [model explainer](https://ai.rhyslindmark.com/rhysbot/learn/)
shows how the corpus, training and inference paths differ. The
[lab](https://ai.rhyslindmark.com/rhysbot/lab/) runs finite backroom conversations,
an uploaded guest's writing as prompted context, and separate image tools. The
[Anki evaluations](https://ai.rhyslindmark.com/rhysbot/evals/) expose paired saved
adapter/base measurements, actual answer samples and local reviewer exports.
See [playground methods and next experiments](docs/PLAYGROUND.md).

## Status

**The reviewed corpus and measured model results are published. Both trained
checkpoints are served in the [live experimental demo](https://ai.rhyslindmark.com/rhysbot/).** See the [model card](docs/MODEL_CARD.md)
for measured results and current limitations. Nanochat outputs are incoherent; Qwen answers remain unreliable. Saved artifacts live in the authenticated Modal volume, with hashes and access instructions in the model card.

- Located a 21,301-note Anki export, local HEE transcripts, and the original X archive.
- Published corpus contains only individually approved Anki cards and conservatively screened public excerpts. Raw exports, private review material, personal family/dating details, DMs and account metadata are excluded.
- Collected all 223 listed public Substack bodies and refreshed AI2026 Parts 1/2/3. The audio inventory has 216 recordings (140 RSS plus 76 YouTube-only); 214 have released speaker transcripts. MetaBlockchain audio acquisition failed, so its screened captions remain; the title-labeled Solo Piano recording is explicitly excluded from speech processing without claiming an audio audit. See [recording coverage](docs/speaker-coverage.json) and exact [release stats](data/public/stats.json).
- The 214 speaker transcripts contain 113,830 turns: 113,008 published texts and 822 privacy-withheld texts. There are 142 episodes with a machine Rhys match and 72 anonymous/ambiguous episodes. These labels and automatic transcripts are not human-verified identity or accuracy claims.
- Training data and launch recipes preserve source splits. Mixed/unverified guest speech is not silently trained as Rhys's voice.
- Total authorized paid-compute budget: **$200** across training, evaluation, inference and audio processing. Full nanochat and Qwen CPT/SFT runs, four-way Qwen evaluation and hosted inference are complete. Paid-compute reservations are conservative holds, not final provider charges; billing remains pending.

Read the [dataset card](docs/DATASET_CARD.md), [acquisition notes](docs/public-ingestion.md), and [training guide](docs/training.md) before use.

## Files

| Artifact | Purpose |
|---|---|
| `data/public/anki-cards.jsonl` | Every approved card, including duplicates/incomplete text, with completeness labels |
| `data/public/corpus.jsonl` | Source-linked released excerpts |
| `data/public/{train,validation,test}.jsonl` | Grouped research splits |
| `data/public/sources.jsonl` | Provenance and split metadata |
| `data/public/speaker-transcripts/` | Screened timestamped speaker turns for 214 recordings; no voiceprints or raw media |
| `docs/speaker-coverage.json` | Recording-level release coverage and explicit gaps |
| `data/anki-review-approvals.jsonl` | Exact-content approval evidence for included local notes |
| `data/training/` | Derived pretraining text and sourced instruction examples |
| `scripts/ingest_local.py` | Anki database-only and original-public-X extraction |
| `scripts/ingest_public.py` | HEE, public Substack bodies, YouTube captions |
| `scripts/ingest_podcast_feed.py` | RSS episode inventory and reviewed YouTube identity mapping |
| `scripts/transcribe_podcast.py` | Bounded, pinned local CPU transcription of public RSS audio |
| `scripts/ingest_linked_writing.py` | Public articles linked from Substack, with canonical/extraction checks |
| `scripts/ingest_ai2026.py` | Offline canonical upsert of three saved public AI2026 HTML articles |
| `scripts/refresh_research_corpus.py` | Bounded-memory refresh from an already screened corpus and public essays |
| `scripts/integrate_speaker_transcripts.py` | Screened canonical RSS/YouTube audio editions and speaker sidecars |
| `data/public/public-writing-review-approvals.json` | Exact document/excerpt contextual reviews for two public-writing false positives |
| `scripts/build_corpus.py` | Privacy gate, excerpt construction, deduplication and splits |
| `scripts/validate_corpus.py` | Fail-closed release checks |
| `training/` | Pinned upstreams and bounded training jobs |

## Rebuild a reviewed release

Python 3.9+ is sufficient for ingestion and corpus checks; modern Python/CUDA dependencies are separate for remote training. `ingest_local.py` needs system libzstd or the `zstandard` package for compressed Anki exports. YouTube acquisition uses yt-dlp. No code executes X archive JavaScript.

```sh
# PRIVATE_WORK must be outside this repository and ignored by any parent Git repo.
python3 scripts/ingest_local.py --anki "$ANKI_EXPORT" --x-archive "$X_ARCHIVE" --out "$PRIVATE_WORK/local"
# See docs/public-ingestion.md for source acquisition commands.
# Individually review local Anki candidates and add approvals only after reading.
# Full regeneration from private inputs, only when resource limits permit:
python3 scripts/build_corpus.py --private-root "$PRIVATE_WORK"
# Or bounded-memory enrichment of the already screened release from public essays:
python3 scripts/refresh_research_corpus.py --private-root "$PRIVATE_WORK"
python3 scripts/validate_corpus.py
# data/training is a frozen experiment input: do not regenerate it during a research-corpus refresh.
python3 -m unittest discover -s tests -v
```

Never commit candidates or use automatic privacy flags as semantic approval. Training on your own copy of a private corpus is a different scope from publishing it.
