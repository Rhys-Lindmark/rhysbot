# Rhysbot corpus — partial reviewed release

Personal model research based on Rhys Lindmark's public writing, original public X posts, curated Anki study notes, and transcripts of HEE / The Rhys Show. The corpus is incomplete and does not establish Rhys's current beliefs or a faithful simulation of a person.

## What is released

`data/public/corpus.jsonl` contains non-overlapping excerpts with original source URLs, dates where available, authorship labels, acquisition method, content hashes, privacy status, and split groups. `stats.json` is the authoritative count for this release; source inventories are broader than acquired or approved content.

The Anki export contains 21,301 notes. The initial privacy filter quarantined 6,480; all remaining 14,821 candidates received individual semantic review. Current release rules are applied again after review. Every included card has a content-hash-bound approval in `data/anki-review-approvals.jsonl`; flagged, ambiguous, incomplete and other excluded notes remain private. The 6,480 initially flagged notes were excluded automatically and were not all read individually. Missing/empty/image-dependent cards are excluded; no images, audio, schedules, deck internals or local file paths are published.

X ingestion reads only `tweets.js` and active `note-tweet.js`. It excludes reposts, nearly empty posts, obvious sensitive material and detected unresolved truncation. Rhys's own public replies remain labeled as replies without their parent context and are excluded from default model adaptation. Long notes are used only when linked to an archived public post by ID or an unambiguous nearby-time/prefix match. Orphan notes, DMs, deleted-note files, media, contacts and account metadata are not ingested. Public visibility today is not independently rechecked for every historical post.

Local HEE transcripts map to public YouTube videos. Podcast captions are public English manual/automatic captions. Three additional RSS recordings have local CPU Whisper transcripts, labeled automatic ASR and not verified against audio. These sources have transcription errors, including names and numbers, and unverified speakers. They remain marked as mixed or unverified speech. Acquired full Substack bodies are distinguished from short announcement posts. Rate-limited, paywalled, failed and unattempted bodies are gaps, never fabricated text. Some full essays live on other domains and are not equivalent to their Substack announcement.

## Privacy process

1. Original sources and candidate text remain outside the published repository, in an ignored, access-restricted local work area.
2. Conservative rules quarantine dating/intimacy, family, contacts, credentials, personal health/financial details, local file paths and other risky patterns. For public prose, affected excerpts are omitted; a risky title withholds the whole document. Entire source documents are not implied to be released when only excerpts survive.
3. Anki additionally requires individual semantic review of question, answer and source, recorded against the exact content hash. A clean keyword scan alone cannot approve a card. Approvals were performed by Codex, not by the human owner; this is a privacy review, not factual verification. Ambiguous personal-contact material, incomplete cards and obvious long standalone third-party extracts were withheld. Short cited educational answers may be included as Rhys-curated study material; they are not relabeled as original writing. A separate 250-card review audit and a second pass over quotation-only exclusions informed the final release.
4. Release validation rechecks hashes, sensitive patterns, approval membership, exact duplicates and split consistency. Git commits contain only the approved output and code; raw archives are never committed.

No filter can establish that a large corpus contains zero sensitive material. Public-source screening is automated, not an exhaustive semantic audit. Report a problematic record by its ID for removal; deleting it from the current branch does not erase prior Git history or a model already trained on it. Additional review remains required before broader distribution or training on newly added local notes.

## Splits and contamination

Documents remain in one split; X posts are grouped by calendar month to reduce thread leakage. Exact duplicates are removed after merging source groups. Matching non-X titles and detected near-duplicates are grouped before splitting. Near-duplicate detection uses word shingles and a 0.8 Jaccard threshold with candidate filtering; it is not exhaustive semantic deduplication. Topic/concept overlap and existing base-model exposure to public sources remain possible. The deterministic split is about 90/5/5 by group, not guaranteed by token count.

Freeze a release Git commit for each experiment: adding data can merge groups and change their split in a future release. Test records and answers are public for reproducibility; do not tune on them.

## Authorship, training and rights

Anki is **Rhys-curated**, not necessarily Rhys-authored: notes can include quotations, paraphrases, model-generated explanations, errors and outdated information. Podcast guests' statements are not Rhys's beliefs. The default initial adaptation/SFT recipe excludes mixed and unverified speaker transcripts. They remain in the research corpus for later speaker-aware work.

SFT seed examples use original study-card answers and verbatim passage continuations. No generated answer is represented as Rhys's own words. These examples are a small starting set, not a validated personality or opinion dataset.

Code licensing does not grant rights to every source passage. Rhys's writing/posts, third-party quotations, podcast contributions and cited books retain their respective rights. No blanket permissive data license is asserted. Preserve attribution; review source permissions for redistribution or other uses. Model weights, metrics and inference availability are separate deliverables and must not be inferred from corpus publication.
