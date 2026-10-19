# Rhysbot corpus — reviewed text release

Personal model research based on Rhys Lindmark's public writing, original public X posts, curated Anki study notes, and transcripts of HEE / The Rhys Show. The corpus is incomplete and does not establish Rhys's current beliefs or a faithful simulation of a person.

## What is released

`data/public/corpus.jsonl` contains screened excerpts with original source URLs, dates where available, authorship labels, acquisition method, content hashes, privacy status, and split groups. `stats.json` is the authoritative count for the built release; source inventories are broader than acquired or approved content.

### Collections and exact sizes

The additive `collection` field groups `substack` and `linked_writing` as
`writing`, and `youtube_transcript` plus `podcast_transcript` as
`podcasts_youtube`. HEE remains `hee`; Anki and X remain `anki` and `x`.
`source_type`, original URLs and acquisition metadata retain the historical
source distinctions. A collection is a browsing group, not an authorship claim.

The updated builder reports documents, excerpts, words, `text_utf8_bytes` and
`corpus_jsonl_bytes` per collection and original source. Text bytes count only
the released `text` fields in UTF-8; JSONL bytes count complete serialized rows,
including provenance/keys/escaping and newline delimiters. `artifact_bytes`
reports actual JSONL file sizes. Characters, words, bytes and tokens are distinct
units; none is relabeled as another. The final corpus must stay below 64 MiB.
The 2026-10-19 applied speaker-transcript release contains 39,692 excerpts from
33,508 documents, 3,519,740 words, 22,609,936 UTF-8 text bytes and 65,167,948
corpus JSONL bytes (below 67,108,864 bytes / 64 MiB). These totals exclude original media. A fresh token count has
not been measured; the old model-input token count is not reused for this corpus.

| Collection | Documents | Excerpts | Words | UTF-8 text bytes | Corpus JSONL bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| Writing | 217 | 811 | 247,566 | 1,540,009 | 2,399,536 |
| Podcasts/YouTube | 215 | 5,117 | 2,028,493 | 12,967,208 | 21,315,283 |
| HEE | 89 | 771 | 254,951 | 1,383,170 | 2,422,025 |
| Anki (unique research text) | 20,923 | 20,923 | 710,053 | 4,657,692 | 28,572,914 |
| X | 12,064 | 12,070 | 278,677 | 2,061,857 | 10,458,190 |

The three public AI2026 articles are refreshed by canonical URL, replacing older
Part 1/2 candidates rather than adding aliases. Extraction retains static article
and SVG text (including source-provided accessible titles/descriptions), while
omitting navigation, scripts, subscription chrome and media fallback messages.
Runtime-only chart labels/data, images and videos are not executed, OCRed or
invented. Claims and chart estimates are source material, not fact checked data.
Two exact flagged excerpts have contextual approvals in
`data/public/public-writing-review-approvals.json`: a driving-duration phrase
mistaken for an address and a generic comparison with human children. Approvals
bind the URL, document ID/full-text hash, excerpt hash and current flags. Any
changed excerpt/document must be reviewed again; contacts/secrets remain blocked.

Every one of the 21,301 Anki text records has semantic reading evidence. The owner requested exclusion of actual personal-life material, with general knowledge retained even when it contains generic “we,” “our,” family vocabulary, quotations, images or missing answers. All included cards have exact-content approvals in `data/anki-review-approvals.jsonl`. Topic flags can be resolved by explicit, hash-bound contextual review; a clean automated scan alone never approves a card. Contacts, credentials and access-bearing URLs require separate handling. Five records have minimal redactions. Ten harmless terminology/number-pattern false positives received a second full reading and are bound to exact IDs, hashes and flags in `data/anki-pattern-false-positives.json`; these are not general exemptions for contacts or secrets.

`data/public/anki-cards.jsonl` preserves every approved card ID and its available front/back/citation text, including duplicate and incomplete cards. The research corpus deduplicates identical text and links registry cards to their shared representative/split. Cards have `text_completeness` and `source_has_images` labels. The image label reflects the original image-context heuristic, not an exhaustive inventory of all original media. The release retains 20,980 cards and withholds 321 personal/ambiguous private cards. No original images/audio are published or OCR content invented. An empty answer stays empty; cards lacking either question or answer do not become SFT pairs, and cards with neither front nor back text do not become pretraining examples. Missing image content remains a known acquisition limitation.

X ingestion reads only `tweets.js` and active `note-tweet.js`. It excludes reposts, nearly empty posts, obvious sensitive material and detected unresolved truncation. Rhys's own public replies remain labeled as replies without their parent context and are excluded from default model adaptation. Long notes are used only when linked to an archived public post by ID or an unambiguous nearby-time/prefix match. Orphan notes, DMs, deleted-note files, media, contacts and account metadata are not ingested. Public visibility today is not independently rechecked for every historical post.

## Audio editions and speaker coverage

Local HEE transcripts map to public YouTube videos. The owner identified all 89 HEE narrations as Rhys on 2026-10-19. HEE rows use `rhys_lindmark_narration_owner_confirmed` and explicit `speaker_attribution` with that basis, preserving the prior authorship label. This is owner confirmation, not acoustic diarization; quoted material may be other authors.

The recording inventory contains 140 RSS recordings and 76 YouTube-only recordings, totaling 216. Released speaker sidecars cover 214: all 140 RSS recordings and 74 YouTube-only recordings. Canonical RSS audio text replaces the corresponding older RSS/YouTube editions using explicit aliases; YouTube-only audio replaces only its explicit video edition. Aliases identify corresponding episodes, not identical recordings or edits. One MetaBlockchain YouTube audio acquisition failed; its screened English captions remain available without a speaker sidecar. The remaining recording is title-labeled Solo Piano and explicitly excluded from speech processing to avoid invented speech; this disposition is not an audio audit. Thus the collection has 215 text documents (214 processed recordings plus retained MetaBlockchain captions), while the audio-processing inventory has 216 recordings. See [recording-level coverage](speaker-coverage.json).

The processed recordings contain 113,830 timestamped turns: 113,008 published turn texts and 822 withheld by public privacy screening. There are 142 episodes with an upstream machine Rhys match and 72 anonymous/ambiguous episodes. The weighted upstream unknown-word fraction is 4.4318% across all 214 episodes; this measures unassigned words, not ASR accuracy, identity confidence or diarization error. Speaker labels are local to an episode. Other clusters remain anonymous; guest names are not inferred from titles or text. The 34,210,377-byte screened sidecars omit voiceprints, identity scores, raw word arrays, private paths and original media. Public screening can over-withhold ordinary speech or miss sensitive context.

These are automatic ASR/diarization results, not a human audio audit. Names, numbers, wording and speaker matches can be wrong. A speaker can quote someone else; neither Rhys labels nor guest statements establish Rhys's beliefs. Corpus chunks preserve speaker/time labels and remain `mixed_or_unverified_speakers`. One pair of adjacent overlapping turns was stably reordered in a private copy by its existing timestamps, preserving every turn field; see the [timestamp-ordering note](speaker-transcripts.md#timestamp-ordering). No recording was retranscribed for that correction. The [speaker-transcript methods](speaker-transcripts.md) describe publication, deterministic chunk validation and limitations. An empty integration-gap summary covers accepted inputs only; the two inventory dispositions above remain explicit.

Acquired full Substack bodies are distinguished from short announcement posts. Rate-limited, paywalled, failed and unattempted bodies are gaps, never fabricated text. Some full essays live on other domains and are not equivalent to their Substack announcement.

## Privacy process

1. Original sources and candidate text remain outside the published repository, in an ignored, access-restricted local work area.
2. Rules flag possible dating/intimacy, family, contacts, credentials, health/financial details, local paths and access-bearing URLs. For Anki, full reading distinguishes general educational content from actual personal-life information. For public prose, affected excerpts are omitted unless an exact, persisted public-writing contextual review covers that source/document/excerpt hash and its current flags; a risky title still withholds the whole document. Entire source documents are not implied to be released when only excerpts survive.
3. Anki additionally requires individual semantic review of question, answer and source, recorded against the exact content hash. A clean keyword scan alone cannot approve a card. Approvals were performed by Codex, not by the human owner; this is a privacy review, not factual verification. Actual private family/relationship anecdotes, private person-specific evaluations, and ambiguous private personal contexts remain withheld. General knowledge, incomplete cards and quotations are retained as Rhys-curated material. Both initially held cards and earlier semantic exclusions were reconsidered under the owner’s clarified policy. An independent 250-card sample of restored approvals found no private false approvals; this is sample evidence, not a zero-risk guarantee.
4. Release validation rechecks hashes, unresolved sensitive patterns, exact contextual flag certificates, approval membership, card-registry coverage, duplicates and split consistency. Git commits contain only the approved output and code; raw archives are never committed.

No filter can establish that a large corpus contains zero sensitive material. Public-source screening is automated, not an exhaustive semantic audit. Report a problematic record by its ID for removal; deleting it from the current branch does not erase prior Git history or a model already trained on it. New or changed local notes require another exact-content review before release.

## Splits and contamination

Documents remain in one split; X posts are grouped by calendar month to reduce thread leakage. Exact duplicates are removed after merging source groups. Matching non-X titles and detected near-duplicates are grouped before splitting. Near-duplicate detection uses word shingles and a 0.8 Jaccard threshold with candidate filtering; it is not exhaustive semantic deduplication. Topic/concept overlap and existing base-model exposure to public sources remain possible. The deterministic split is about 90/5/5 by group, not guaranteed by token count.

The actual completed models retain the historical input from corpus release
`1e20626c7b3b2b2bc3404fdb0b1fd39a93419b3d`, corpus SHA256
`6da8afa85c1b9c8e4406823f643a8bdf80fecccac4bd58c28878ca950e6544d8`.
`data/training/` and its manifest are unchanged by later collection labels or
AI2026 enrichment or the 214-recording speaker refresh. Those additions are research-corpus material, not evidence
that the existing checkpoints trained on them.

Freeze a release Git commit for each experiment: adding data can merge groups and change their split in a future release. Test records and answers are public for reproducibility; do not tune on them.

## Authorship, training and rights

Anki is **Rhys-curated**, not necessarily Rhys-authored: notes can include quotations, paraphrases, model-generated explanations, errors and outdated information. Podcast guests' statements are not Rhys's beliefs. The default initial adaptation/SFT recipe excludes mixed and unverified speaker transcripts. They remain in the research corpus for later speaker-aware work.

SFT seed examples use original study-card answers and verbatim passage continuations. No generated answer is represented as Rhys's own words. These examples are a small starting set, not a validated personality or opinion dataset.

Code licensing does not grant rights to every source passage. Rhys's writing/posts, third-party quotations, podcast contributions and cited books retain their respective rights. No blanket permissive data license is asserted. Preserve attribution; review source permissions for redistribution or other uses. Model weights, metrics and inference availability are separate deliverables and must not be inferred from corpus publication.
