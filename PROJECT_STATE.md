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
- [x] P2 Semantic review of Anki records under the owner’s personal-life-only policy. Evidence: all 21,301 text records covered by reading evidence; 20,980 retained and 321 personal/ambiguous private contexts withheld. Five minimal redactions and ten second-reviewed exact-content pattern exceptions; private originals and disposition evidence preserved.
- [x] P3 Normalize text, exact/near duplicate checks and document-level train/validation/test grouping; no guest speech as Rhys instruction targets. Evidence: build/validate scripts and training-data tests.
- [x] P4 Public dataset card, source manifest, privacy methodology, limitations and coverage report. Evidence: docs/DATASET_CARD.md, data/public/sources.jsonl, docs/acquisition-coverage.json.
- [x] P5 Reconsider automatically withheld Anki cards for false positives; recover complete educational text, distinguish image-dependent/incomplete cards from privacy exclusions, and contextually review generic personal/family words. User requested this after seeing the exclusion count.
Exit: public artifacts pass privacy gate; all included local records have review evidence, with exclusions counted but not exposed.

### 3. GitHub and website
- [x] G1 Publish reviewed corpus/code to Rhys-Lindmark/rhysbot. Evidence: frozen corpus release 1e20626 passed Actions 39207008561; measured scratch results/model card published in e9523f1 and passed Actions 39208500056. The training/serving suite has 88 passing tests; a51aa3f passed Actions39215684231, full corpus validation and publication audit.
- [x] G2 Publish a useful project/corpus page at ai.rhyslindmark.com/rhysbot with truthful status and coverage. Evidence: website commit e3291be, live HTTP 200; browser verified corpus loading and search. Final website1bdb0a1 enables both verified checkpoints and the measured evaluation table; public Qwen generation and displayed hash passed.
Exit: verified remote repository and website route; no private data in Git history.

### 4. Training and evaluation
- [x] T1 Pin nanochat and prepare tokenizer/training with source-group-safe splits. Evidence: actual corrected pilot and full nanochat-train-20261019a completed; saved pretrain/SFT checkpoints and frozen data provenance.
- [x] T2 Pin Qwen and prepare sourced CPT/SFT recipe. Evidence: corrected qwen-pilot-20261019b strictly loaded pretrained text weights, trained/saved both adapters and evaluated them; full adaptation completed1000CPT/400SFT.
- [x] T3 Prepare shared held-out measurements, baselines, checkpoint comparison and inference scripts. Evidence: real pilot/full-scratch evaluations; explicit four-way Qwen eval plan; actual authenticated serving API starts and enforces the shared training lock. Full four-way Qwen comparison and both GPU serving checks completed; public browser path verified.
- [x] T4 Run bounded GPU pilots and full training/post-training runs. Evidence: nanochat-train-20261019a completed 2000/80 updates; qwen-train-20261019a completed 1000 CPT/400 SFT updates with actual saved adapters, frozen provenance and streamed artifact hashes. Provider billing remains pending.
- [x] T5 Evaluate actual checkpoints and deploy inference. Evidence: qwen-eval-20261019a completed4actualvariants; results/qwen-comparison.json preserves40samples with questions. Both full saved checkpoints passed authenticated GPU inference; public Qwen browser verified widget/Siteverify/proxy/answer/run/hash. Results/serving.json records evidence and403denials; no fallback.
Exit: real checkpoint artifacts, run metadata, eval results and live inference. Missing credentials or authorization is a blocker, not completion.

## Current phase

All 21,301 Anki text records now have semantic reading evidence. Following the owner’s explicit personal-life-only policy, 20,980 cards are retained and 321 private/personal or ambiguous private contexts withheld. Previously held image/incomplete cards, generic pronouns/family words and quotations were reconsidered. Five records have minimal access-parameter/identifier-example redactions; ten harmless terminology/number-pattern cases received a second full reading and exact-content exception certificates. An independent sample re-read 250 restored approvals and found no private material in that sample. Private originals/evidence remain unchanged. The complete-card registry preserves duplicate and incomplete cards; research text deduplicates 57 excess card records. Empty question/answer pairs do not enter SFT. Structured front/back/citation must exactly reconstruct the approved text.

Expanded release candidate: 38,789 deduplicated excerpts, 2,929,408 words and 3,960,666 measured Qwen tokens, including 20,923 unique-text Anki entries. The separate registry retains all 20,980 approved card IDs. Source counts: HEE 771, linked writing 23, podcast ASR 32, Substack 765, X 12,070, YouTube 4,205. All 223 Substack bodies, 89 HEE transcripts, 212 captions and three original RSS ASR recordings are acquired. RSS inventory accounts for 140 episodes; video/audio editions are not assumed identical. Eighteen historical linked-writing URLs remain HTTP 404; solo-piano audio was not independently checked. No original media is published or missing image content invented.

Frozen corpus release 1e20626c7b3b2b2bc3404fdb0b1fd39a93419b3d passed GitHub Actions 39207008561 (85 tests), full corpus validation and staged publication scan. Preserve its training-data bytes throughout the model runs.

Corrected nanochat pilot nanochat-pilot-20261019c completed all six stages in 42.2 GPU-job seconds, saved exactly 20 pretraining and 4 SFT optimizer updates, and had nonnegative learning-rate multipliers. The original SFT counter defect is fixed. Validation BPB worsened at the original 0.8 initial LR fraction, so the full recipe uses 0.05. Full nanochat: 2,000 pretraining / 80 SFT updates. Tiny pilot completions are poor; useful chat is not established.

Corrected Qwen pilot qwen-pilot-20261019b completed CPT, SFT and evaluation in 123.3 GPU-job seconds. Four CPT updates took 8.18 seconds; two SFT updates took 4.37 seconds; peak allocated CUDA memory was 10,000,668,160 bytes; 16,232,448 LoRA parameters trainable. Full Qwen recipe is 1,000 CPT / 400 SFT updates, followed by actual base/instruction/CPT/SFT comparison. Pilot loss is not evidence of improvement over baseline. Public sanitized evidence: results/pilots.json.

Full nanochat run nanochat-train-20261019a completed 2,000 pretraining and 80 SFT updates plus evaluations in 307.96 GPU-job seconds. Best pretraining validation BPB was 1.4830 at step 800; final step 2,000 was worse (1.6591). All checkpoints are preserved. Final held-out BPB: pretrained 2.1565, SFT 1.8188; small sample and incoherent completions do not establish useful chat. Public results/nanochat-full.json includes the curve and verified SFT artifact hashes; docs/MODEL_CARD.md explains limitations.

Qwen full run qwen-train-20261019a completed on immutable c0747a0 and frozen inputs: exactly 1,000 CPT and 400 SFT updates, 4,278.06 GPU-job seconds, actual saved adapters and built-in SFT evaluation. Strict loading restored 426 language tensors with empty missing/unexpected/mismatched diagnostics. Actual processed targets: CPT371,002 and SFT85,151. Public results/qwen-full.json records metrics, ten real samples and verified SFT hashes. The separate detached qwen-eval-20261019a completed all four variants from release1be5cf0 in294.81seconds. Base/instruction/CPT/SFT assistant NLL:2.9791/3.7246/2.4637/2.2501 on220targets. This common-format small diagnostic is not factual/personality validation.

User explicitly approved connecting the model demo, then requested autonomy while asleep. Existing Cloudflare project rhyslindmark-website has the Rhysbot verification widget and encrypted backend secrets; unrelated settings preserved. Website1bdb0a1 deployed successfully and enables qwen,nanochat through source-controlled vars. Forty function tests pass. Actual browser verified both model choices, stats, evaluation table, automatic verification and a public Qwen answer with checkpointqwen-train-20261019a, hash493bfdbc2279,22tokens,84.5seconds. The earlier corpus search verified38789excerpts and73entropy+Anki matches.

Scale-to-zero Modal serving now pins both actual full targets. Operator calls restored nanochat step80 (11.33seconds, incoherent “finventy”) and full Qwen SFT400 (30.81seconds, entropy explanation), verifying exact hashes and adapter restoration. A public browser call traversed Siteverify and the authenticated proxy and returned the same Qwen answer in84.54seconds. Invalid verification and wrong origin each returned403. Shared lock was empty after all three calls; no retries or fallback answers. Evidence:results/serving.json.

Conservative reservations total $66.40 of the $200 cap at verification: five pilot/setup attempts$25, completed nanochat full$6.60, completed Qwen full$24.60, completed four-way evaluation$6.60, three verified inference calls$3.60. Provider billing remains pending; reservations are not actual charges and elapsed time never refunds them. All jobs, serving and billing share the artifact-volume ledger and named Dict singleton. Exact handles and recovery notes are private.

Resources: 16 GiB Mac, 5 GiB task / 10 GiB family RSS budgets, one heavy local workload, 25-minute stall interval. Latest worker sample normal 61% free, compressor 3.8–4.2 GiB, swap 1.58 GiB, 28 GiB disk free; family RSS lower bound 4.34 GiB / 108 processes, largest 1.10 GiB. All CPU ASR jobs ended; original local exports untouched. Removed 250.3 MiB of regenerable task audio and unselected base.en weights after accepted transcript/hash evidence. Selected small.en cache, tools environment and real cloud checkpoints remain. No task preview/build server is running. Temporary browser/runtime was reset. No new GPU/always-on inference launches bypass the ledger.
