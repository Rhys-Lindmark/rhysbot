# Rhysbot project state

Goal: a privacy-reviewed personal corpus published to GitHub, then a nanochat model trained from scratch and Qwen adapted/post-trained on that corpus. Intended website: https://ai.rhyslindmark.com/rhysbot .

## Operating constraints
- Raw local inputs, excluded material, review queues, local paths, credentials and private reviews stay outside this repository in ../work/rhysbot-private (mode 700).
- No private source text may enter any Git commit, including a temporary commit. Use explicit staged file lists and inspect the initial commit before push.
- Dating, family and other sensitive personal details must be excluded from Anki. Automated keyword checks alone do not constitute semantic privacy approval. Unreviewed records default to quarantine.
- X ingestion reads only public posts and account identity needed for attribution, never DMs or other account archives.
- Preserve third-party quotations, guest speech and uncertain authorship as such; never label them as Rhys's beliefs.
- User authorized a $200 TOTAL paid-compute cap on 2026-10-18. No Modal/Runpod credentials detected. Cumulative compute spend starts at $0; budget must cover both models and evals. Use bounded pilot first and record actual provider billing. No always-on serving under this cap.
- Resource baseline: 16 GiB RAM, 29 GiB disk free, workspace 672 MiB; swap 1.68 GiB, compressor 4.90 GiB, no process above 1.1 GiB RSS in initial top-process sample. Sequential workloads; no model downloads or local GPU training. Stall interval: 25 minutes.
- Hourly heartbeat: rhysbot-hourly-progress (active).

## Phases and acceptance criteria
### 1. Inventory and ingestion
- [x] I1 Locate latest Anki Information export, HEE transcript folder and X archive (local paths in private inventory only).
- [x] I2 Parse Anki text/database without media extraction; validate note count and field structure. Evidence: 21,301 three-field notes; no media extracted.
- [x] I3 Ingest local HEE transcripts and enumerate channel videos/podcast sources; preserve acquisition failures and coverage gaps. Evidence: all 304 inventoried videos attempted; 89 local HEE transcripts and 212 public caption tracks acquired; three explicit gaps remain.
- [x] I4 Collect public Substack archive with title, URL, publication date and full accessible body. Evidence: all 223 archive entries attempted; 223 full public bodies; a public essay mentioning a paywall was manually checked and the structural-marker classifier corrected.
- [x] I5 Extract public original X posts and matched long posts, excluding reposts, DMs and account metadata. Evidence: 18,726 archive entries; 12,746 candidates including 8,254 labeled public replies, 79 matched long posts. Reposts and low-text posts excluded; contextless replies excluded from default adaptation.
Exit: reproducible scripts and private normalized JSONL, source counts, provenance and gap manifest.

### 2. Privacy and corpus construction
- [x] P1 Conservative sensitive-data detection and quarantines for local sources, with meaningful tests. Evidence: 6,480 Anki notes quarantined; tests cover contacts, family, relationships, credentials and release gate.
- [ ] P2 Semantic review of candidate Anki records; only approved records enter public corpus; private cases remain quarantined.
- [x] P3 Normalize text, exact/near duplicate checks and document-level train/validation/test grouping; no guest speech as Rhys instruction targets. Evidence: build/validate scripts and training-data tests.
- [x] P4 Public dataset card, source manifest, privacy methodology, limitations and coverage report. Evidence: docs/DATASET_CARD.md, data/public/sources.jsonl, docs/acquisition-coverage.json.
Exit: public artifacts pass privacy gate; all included local records have review evidence, with exclusions counted but not exposed.

### 3. GitHub and website
- [x] G1 Create Rhys-Lindmark/rhysbot repository and push only reviewed corpus/code/docs. Evidence: public root commit eb6f74feb1c693a1949c15acfff6b0a085b6c251; GitHub Actions run 39200860642 passed all 23 tests and release checks.
- [x] G2 Publish a useful project/corpus page at ai.rhyslindmark.com/rhysbot with truthful status and coverage. Evidence: website commit e3291be, live HTTP 200; browser verified corpus loading, entropy search and Anki filter (34 matching excerpts in initial snapshot).
Exit: verified remote repository and website route; no private data in Git history.

### 4. Training and evaluation
- [x] T1 Pin nanochat upstream and prepare corpus/tokenizer/training configs with contamination-safe splits. Evidence: training/upstream-pins.json, nanochat_run.py; source/static checks only, GPU unverified.
- [x] T2 Pin Qwen model and prepare continued-pretraining/SFT configs and actual sourced training examples. Evidence: qwen_run.py strict weight loading and assistant-only SFT; data/training/manifest.json.
- [x] T3 Prepare shared eval set, baselines, checkpoint comparison and inference launch scripts. Evidence: held-out loss/sample evaluators and baseline plans; 23 unit tests, no real checkpoints yet.
- [ ] T4 Run bounded GPU pilot, record throughput/memory/cost, then authorized training and post-training runs.
- [ ] T5 Evaluate actual checkpoints and deploy inference; no stub or base model may be represented as trained.
Exit: real checkpoint artifacts, run metadata, eval results and live inference. Missing credentials or authorization is a blocker, not completion.

## Current phase
Phases 1–3: public repository and corpus explorer live; current frozen expanded reviewed snapshot: 19,460 excerpts, 2,249,924 words, 2,873,542 measured Qwen tokens, 1,640 included Anki cards. Initial 413 semantic reviews plus a completed 1,500-card batch: 1,261 additional approvals, 239 additional exclusions. There are 1,643 hash-bound approvals; three initially approved cards are excluded by stricter/low-text release checks. All other local notes remain private; full-deck review is incomplete. Approval appends are stopped at SHA-256 9da46f10efc581b204ab53aaa3c71b80411e9c2df15301fb020dccfc687e7c39.

Implementer /root/public_corpus now owns only NEW scripts/ingest_linked_writing.py, NEW tests/test_ingest_linked_writing.py and ignored private linked-writing-remote/. It is acquiring fuller public articles linked from Substack announcements (101 own-domain links before removing navigation). These candidates must be separately inspected, privacy-screened and explicitly integrated in a later release. No public candidate text is approved by acquisition alone.

All 89 HEE transcripts match the HEE channel's public videos. Completed merged YouTube inventory: 304 videos (215 podcast-channel + 89 HEE), with 212 acquired English caption tracks and 89 existing HEE transcripts. Three gaps: two without available English captions, one HTTP 429 failure. Substack archive exhausted at 223 entries, all full public bodies. A public essay discussing Medium's paywall was checked and reacquired after correcting the classifier; audience/access checks and structural paywall-marker exclusions remain enforced. Collector sessions have finished; resumable coverage manifests preserve all gaps. A single cooled-down caption retry still returned HTTP 429. Next source work, when compute credentials exist: audio transcription for spoken videos without captions; no repeated rate-limit retries. Do not silently bypass paywall markers.

Training implementation accepted as source/static verified: 25 tests pass, Python compiles, shell parses, Modal definitions import without remote calls. $200 total paid-compute authorization; $0 spent. No GPU credentials yet. Modal login session 48214 is pending; credentials must stay in the ignored private workspace config. Never claim a trained model until real optimizer/checkpoint evidence exists.

Tools venv: ../work/rhysbot-tools (Modal/tokenizers only; no torch or weights). Pinned tokenizer: private workspace, 12.8 MB. Latest resource sample: 28 GiB disk free; task repository 79 MiB, private inputs 69 MiB, tools 84 MiB, nanochat checkout 1.7 MiB. Compressor 5.51 GiB, swap 1.66 GiB, normal pressure (52% reported free); Codex/ChatGPT family 96 processes, 3.46 GiB RSS, largest renderer 958 MiB. Temporary verification browser tab closed and REPL reset. Owned active workload: Modal login session 48214 only; no preview/build servers. Disposable Anki DB extractions (17 MB) removed after successful normalized ingestion; originals untouched. Stall interval remains 25 minutes.
