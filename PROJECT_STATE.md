# Rhysbot project state

Goal: a privacy-reviewed personal corpus published to GitHub, then a nanochat model trained from scratch and Qwen adapted/post-trained on that corpus. Intended website: https://ai.rhyslindmark.com/rhysbot .

## Operating constraints
- Raw local inputs, excluded material, review queues, local paths, credentials and private reviews stay outside this repository in ../work/rhysbot-private (mode 700).
- No private source text may enter any Git commit, including a temporary commit. Use explicit staged file lists and inspect the initial commit before push.
- Dating, family and other sensitive personal details must be excluded from Anki. Automated keyword checks alone do not constitute semantic privacy approval. Unreviewed records default to quarantine.
- X ingestion reads only public posts and account identity needed for attribution, never DMs or other account archives.
- Preserve third-party quotations, guest speech and uncertain authorship as such; never label them as Rhys's beliefs.
- User authorized a $200 TOTAL paid-compute cap on 2026-10-18. Modal profile rhysbot credentials are now connected in ignored private config. Cumulative compute spend starts at $0; budget must cover both models and evals. Use bounded pilot first and record actual provider billing. No always-on serving under this cap.
- Resource baseline: 16 GiB RAM, 29 GiB disk free, workspace 672 MiB; swap 1.68 GiB, compressor 4.90 GiB, no process above 1.1 GiB RSS in initial top-process sample. Sequential workloads; no local chat-model weights or GPU training. Small pinned CPU ASR models are permitted within the existing resource limits. Stall interval: 25 minutes.
- Hourly heartbeat: rhysbot-hourly-progress (active).

## Phases and acceptance criteria
### 1. Inventory and ingestion
- [x] I1 Locate latest Anki Information export, HEE transcript folder and X archive (local paths in private inventory only).
- [x] I2 Parse Anki text/database without media extraction; validate note count and field structure. Evidence: 21,301 three-field notes; no media extracted.
- [x] I3 Ingest local HEE transcripts and enumerate channel videos/podcast sources; preserve acquisition failures and coverage gaps. Evidence: all 304 inventoried videos attempted; 89 local HEE transcripts and 212 public caption tracks acquired; the two spoken caption gaps have original-RSS ASR follow-up and one title-labeled solo-piano gap remains.
- [x] I4 Collect public Substack archive with title, URL, publication date and full accessible body. Evidence: all 223 archive entries attempted; 223 full public bodies; a public essay mentioning a paywall was manually checked and the structural-marker classifier corrected.
- [x] I5 Extract public original X posts and matched long posts, excluding reposts, DMs and account metadata. Evidence: 18,726 archive entries; 12,746 candidates including 8,254 labeled public replies, 79 matched long posts. Reposts and low-text posts excluded; contextless replies excluded from default adaptation.
Exit: reproducible scripts and private normalized JSONL, source counts, provenance and gap manifest.

### 2. Privacy and corpus construction
- [x] P1 Conservative sensitive-data detection and quarantines for local sources, with meaningful tests. Evidence: 6,480 Anki notes quarantined; tests cover contacts, family, relationships, credentials and release gate.
- [x] P2 Semantic review of candidate Anki records; only approved records enter public corpus; private cases remain quarantined. Evidence: all 14,821 initially clear candidates individually reviewed, zero uncovered; 6,480 initially flagged notes automatically excluded; current release rules applied again; exact-hash-bound approval manifest and private complete disposition audit.
- [x] P3 Normalize text, exact/near duplicate checks and document-level train/validation/test grouping; no guest speech as Rhys instruction targets. Evidence: build/validate scripts and training-data tests.
- [x] P4 Public dataset card, source manifest, privacy methodology, limitations and coverage report. Evidence: docs/DATASET_CARD.md, data/public/sources.jsonl, docs/acquisition-coverage.json.
- [x] P5 Reconsider automatically withheld Anki cards for false positives; recover complete educational text, distinguish image-dependent/incomplete cards from privacy exclusions, and contextually review generic personal/family words. User requested this after seeing the exclusion count.
Exit: public artifacts pass privacy gate; all included local records have review evidence, with exclusions counted but not exposed.

### 3. GitHub and website
- [x] G1 Create Rhys-Lindmark/rhysbot repository and push only reviewed corpus/code/docs. Evidence: public root commit eb6f74feb1c693a1949c15acfff6b0a085b6c251; GitHub Actions run 39201668425 passed release checks for expanded commit 74b96a38c1eb9a692a895e118dab23d5b21d75d3 (25 tests); current local source suite has 79 passing tests before final rebuilt-release validation.
- [x] G2 Publish a useful project/corpus page at ai.rhyslindmark.com/rhysbot with truthful status and coverage. Evidence: website commit e3291be, live HTTP 200; browser verified corpus loading, entropy search and Anki filter (34 matching excerpts in initial snapshot).
Exit: verified remote repository and website route; no private data in Git history.

### 4. Training and evaluation
- [x] T1 Pin nanochat upstream and prepare corpus/tokenizer/training configs with contamination-safe splits. Evidence: training/upstream-pins.json, nanochat_run.py; source/static checks only, GPU unverified.
- [x] T2 Pin Qwen model and prepare continued-pretraining/SFT configs and actual sourced training examples. Evidence: qwen_run.py strict weight loading and assistant-only SFT; data/training/manifest.json.
- [x] T3 Prepare shared eval set, baselines, checkpoint comparison and inference launch scripts. Evidence: held-out loss/sample evaluators and baseline plans; source tests, no real checkpoints yet.
- [ ] T4 Run bounded GPU pilot, record throughput/memory/cost, then authorized training and post-training runs.
- [ ] T5 Evaluate actual checkpoints and deploy inference; no stub or base model may be represented as trained.
Exit: real checkpoint artifacts, run metadata, eval results and live inference. Missing credentials or authorization is a blocker, not completion.

## Current phase

All 21,301 Anki text records now have semantic reading evidence. Following the owner’s explicit personal-life-only policy, 20,980 cards are retained and 321 private/personal or ambiguous private contexts withheld. Previously held image/incomplete cards, generic pronouns/family words and quotations were reconsidered. Five records have minimal access-parameter/identifier-example redactions; ten harmless terminology/number-pattern cases received a second full reading and exact-content exception certificates. An independent sample re-read 250 restored approvals and found no private material in that sample. Private originals/evidence remain unchanged. The complete-card registry preserves duplicate and incomplete cards; research text deduplicates 57 excess card records. Empty question/answer pairs do not enter SFT. Structured front/back/citation must exactly reconstruct the approved text.

Expanded release candidate: 38,789 deduplicated excerpts, 2,929,408 words and 3,960,666 measured Qwen tokens, including 20,923 unique-text Anki entries. The separate registry retains all 20,980 approved card IDs. Source counts: HEE 771, linked writing 23, podcast ASR 32, Substack 765, X 12,070, YouTube 4,205. All 223 Substack bodies, 89 HEE transcripts, 212 captions and three original RSS ASR recordings are acquired. RSS inventory accounts for 140 episodes; video/audio editions are not assumed identical. Eighteen historical linked-writing URLs remain HTTP 404; solo-piano audio was not independently checked. No original media is published or missing image content invented.

Frozen corpus release 1e20626c7b3b2b2bc3404fdb0b1fd39a93419b3d passed GitHub Actions 39207008561 (85 tests), full corpus validation and staged publication scan. Preserve its training-data bytes throughout the model runs.

Corrected nanochat pilot nanochat-pilot-20261019c completed all six stages in 42.2 GPU-job seconds, saved exactly 20 pretraining and 4 SFT optimizer updates, and had nonnegative learning-rate multipliers. The original SFT counter defect is fixed. Validation BPB worsened at the original 0.8 initial LR fraction, so the full recipe uses 0.05. Full nanochat: 2,000 pretraining / 80 SFT updates. Tiny pilot completions are poor; useful chat is not established.

Corrected Qwen pilot qwen-pilot-20261019b completed CPT, SFT and evaluation in 123.3 GPU-job seconds. Four CPT updates took 8.18 seconds; two SFT updates took 4.37 seconds; peak allocated CUDA memory was 10,000,668,160 bytes; 16,232,448 LoRA parameters trainable. Full Qwen recipe is 1,000 CPT / 400 SFT updates, followed by actual base/instruction/CPT/SFT comparison. Pilot loss is not evidence of improvement over baseline. Public sanitized evidence: results/pilots.json.

Full runs and live demo remain pending. A concrete local frontend/proxy is prepared. Read-only review identified serving serialization needing the copied training directory on PYTHONPATH; that packaging correction precedes real serving smoke tests. Website hosting configuration needs narrow user approval after automatic review rejected a Cloudflare account lookup. Do not bypass that block through another interface.

Modal credentials are verified and stored only in ignored private config, mode 600. Conservative reservations total $25 of the $200 cap for five pilot/setup attempts. Provider billing is pending; reservations are not actual charges and are not refunded by elapsed-time estimates. The shared Modal artifact volume ledger and distributed singleton cover training/inference/billing. Retry IDs must be new. Exact handles and recovery notes are in ignored private continuation-state.json.

Resources: 16 GiB Mac, 5 GiB task / 10 GiB family RSS budgets, one heavy local workload, 25-minute stall interval. Latest worker sample normal 61% free, compressor 3.8–4.2 GiB, swap 1.58 GiB, 28 GiB disk free; family RSS lower bound 4.34 GiB / 108 processes, largest 1.10 GiB. All CPU ASR jobs ended; original local exports untouched. Removed 250.3 MiB of regenerable task audio and unselected base.en weights after accepted transcript/hash evidence. Selected small.en cache, tools environment and real cloud checkpoints remain. No task preview/build server is running. Temporary browser/runtime was reset. No new GPU/always-on inference launches bypass the ledger.
