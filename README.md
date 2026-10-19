# Rhysbot

Building a small language model from scratch and adapting Qwen to Rhys Lindmark's public writing and curated knowledge.

Project: **https://ai.rhyslindmark.com/rhysbot**

Source: **https://github.com/Rhys-Lindmark/rhysbot**

## Status

**The reviewed corpus and measured model results are published. Both trained
checkpoints are served in the [live experimental demo](https://ai.rhyslindmark.com/rhysbot/).** See the [model card](docs/MODEL_CARD.md)
for measured results and current limitations. Nanochat outputs are incoherent; Qwen answers remain unreliable. Saved artifacts live in the authenticated Modal volume, with hashes and access instructions in the model card.

- Located a 21,301-note Anki export, local HEE transcripts, and the original X archive.
- Published corpus contains only individually approved Anki cards and conservatively screened public excerpts. Raw exports, private review material, personal family/dating details, DMs and account metadata are excluded.
- Collected all 223 listed public Substack bodies, 212 YouTube caption tracks and 89 local HEE transcripts. Three additional public RSS recordings were transcribed locally: the two spoken caption gaps and one audio-only episode. The remaining caption gap is title-labeled solo piano. Exact release counts are in [data/public/stats.json](data/public/stats.json); acquisition gaps are in [docs/acquisition-coverage.json](docs/acquisition-coverage.json) when generated.
- Training data and launch recipes preserve source splits. Mixed/unverified guest speech is not silently trained as Rhys's voice.
- Total authorized paid-compute budget: **$200** across both models and evaluation. Full nanochat and Qwen CPT/SFT runs, four-way Qwen evaluation and hosted inference are complete. Conservative holds at verification total $66.40; provider billing is pending.

Read the [dataset card](docs/DATASET_CARD.md), [acquisition notes](docs/public-ingestion.md), and [training guide](docs/training.md) before use.

## Files

| Artifact | Purpose |
|---|---|
| `data/public/anki-cards.jsonl` | Every approved card, including duplicates/incomplete text, with completeness labels |
| `data/public/corpus.jsonl` | Source-linked released excerpts |
| `data/public/{train,validation,test}.jsonl` | Grouped research splits |
| `data/public/sources.jsonl` | Provenance and split metadata |
| `data/anki-review-approvals.jsonl` | Exact-content approval evidence for included local notes |
| `data/training/` | Derived pretraining text and sourced instruction examples |
| `scripts/ingest_local.py` | Anki database-only and original-public-X extraction |
| `scripts/ingest_public.py` | HEE, public Substack bodies, YouTube captions |
| `scripts/ingest_podcast_feed.py` | RSS episode inventory and reviewed YouTube identity mapping |
| `scripts/transcribe_podcast.py` | Bounded, pinned local CPU transcription of public RSS audio |
| `scripts/ingest_linked_writing.py` | Public articles linked from Substack, with canonical/extraction checks |
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
python3 scripts/build_corpus.py --private-root "$PRIVATE_WORK"
python3 scripts/validate_corpus.py
python3 scripts/prepare_training.py
python3 -m unittest discover -s tests -v
```

Never commit candidates or use automatic privacy flags as semantic approval. Training on your own copy of a private corpus is a different scope from publishing it.
