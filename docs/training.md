# Rhysbot training pipeline

**Status: corrected GPU pilots completed for both families; full training,
baseline comparisons and live serving remain pending.** Measured pilot results
are in `results/pilots.json`; provider billing remains pending. No downloaded base
model or configuration is presented as a trained Rhysbot model.

The user authorized **$200 total compute across both models and evaluations** on
2026-10-18. The initial spend is $0. Every model family starts with a finite
15-minute pilot that reserves at most $5. Authentication and a successful pilot
are required before longer runs. Nothing starts from a schedule or web request.

## Released input and frozen provenance

`scripts/prepare_training.py` derives pretraining and sourced instruction examples
from the approved public release. Only `data/training/*-train.jsonl` enters
optimization. Whole source groups remain in one split across pretraining/SFT;
validation is for progress checks and test is evaluated after training. Every
launch rechecks six input-file hashes, split-group disjointness, nonempty splits
and the source manifest. Remote bytes must match this frozen launch manifest.
Remote execution requires a clean committed training/data release and records
that Git commit, all model/code revisions and installed package versions.

This is a **small personal corpus**, with 23,465 pretraining documents and 17,181
sourced SFT examples in the frozen training splits. Mixed podcast
or HEE captions are excluded by the default dataset builder. Anki answers are
curated notes rather than verified facts; public-writing continuations are
verbatim passages rather than invented conversational answers. Inspect the
latest `data/training/manifest.json`; counts are intentionally not fixed here.

## Model 1: small nanochat from random weights

The upstream revision is pinned in `training/upstream-pins.json`.
`training/nanochat_run.py` uses upstream tokenizer/base/SFT implementations and
redirects their data hooks without editing the pinned checkout:

1. Write a `text`-column Parquet training shard and a separately sourced
   validation shard. The validation filename sorts last because nanochat treats
   **all shards except the final one as training**. No test shard enters that
   directory. Long documents are split into bounded text chunks within their
   original split.
2. Train an 8,192-token BPE tokenizer from the training shard only.
3. Initialize a **depth-4, width-256, 512-token-context** model from random
   weights. The pilot uses 20 pretraining steps; the initial full recipe uses
   2,000 steps with 8,192 tokens per optimizer step after measuring pilot throughput. Actual parameter counts,
   throughput, peak allocation, validation BPB and checkpoints are logged.
4. SFT the resulting checkpoint using only our sourced conversations. The wrapper
   replaces SmolTalk with local train/validation tasks and substitutes empty
   MMLU/GSM8K tasks. Upstream Hub dataset downloads and default benchmark suites
   are disabled. Conversations with no surviving assistant target are rejected.
5. Evaluate both base and SFT checkpoints on the frozen held-out inputs and emit
   bounded sample completions for manual review.

Both bounded recipes use eager execution; measured tiny-model throughput exceeds 100k tokens/s, so compilation cold starts are unnecessary. Both recipes use
PyTorch SDPA and disable upstream optional unpinned Hub-kernel downloads. The
wrapper translates upstream's prefetched microbatch counter into exactly the
requested optimizer updates and verifies the saved step. The full SFT recipe
uses 80 updates and a 0.05 initial learning-rate fraction after the pilot's
validation BPB worsened at 0.8. This
scratch model is a limited learning experiment, with a small personal corpus
and no claim to general chat capability. Extending training without checking
validation curves risks memorization.

## Model 2: Qwen continued pretraining, then LoRA SFT

`training/qwen_run.py` uses the pinned `Qwen/Qwen3.5-4B-Base` checkpoint and keeps
its tokenizer. Qwen3.5 is a multimodal/hybrid checkpoint, so loading it as an
arbitrary ordinary causal model is unsafe. The implementation uses the official
`Qwen3_5ForCausalLM` with the checkpoint's **text_config**, explicitly remaps
`model.language_model.*` to `model.*`, checks the checkpoint index covers every
language parameter, fails on missing/mismatched weights, and verifies the tied
embedding/language-head tensor. It never silently accepts random language
weights. Vision and auxiliary MTP tensors are not trained.

The recipe uses BF16 LoRA, without quantization, on one A100 80GB. Rank-8 adapters
target full-attention projections, hybrid linear-attention projections and MLP
projections. Only `lora_` parameters may require gradients. The first stage
trains next-token prediction on our training text; the second reloads **that
completed adapter** and trains only on assistant content/end tokens. System,
user and role-prefix tokens are masked. Explicit ChatML works with the base
checkpoint without relying on a missing chat template. The run records skipped
or unusable example counts through its usable-example total and fixed limits.

Pilot: four CPT optimizer steps and two SFT steps. Initial full recipe: 1,000 CPT
steps and 400 SFT steps, four microbatches per update, maximum sequence length
512. These are bounded starting experiments, not validated optimal settings.
The adapter saves separate CPT and SFT directories and their provenance. A
separate evaluation job compares the untouched base, pinned instruction-model
baseline, CPT adapter and SFT adapter.

## Evaluation and inference artifacts

Both branches measure next-token loss on the same held-out source documents,
with token-normalized NLL/perplexity and byte-normalized BPB. Tokenization,
document-boundary tokens and truncation differ between architectures; compare
BPB with those qualifications, and do not directly compare per-token perplexity
across different tokenizers. Assistant-only held-out NLL and bounded generations
use the sourced SFT test set. Every output includes reference text/source IDs
for manual review, generation latency and the checkpoint identity.

These metrics do not establish truthful beliefs, privacy behavior, personality
fidelity or usefulness. Blind human comparisons, attribution tests and app
workflow evaluation remain necessary. Test answers are public; do not train or
select hyperparameters on them. Actual inference paths already use nanochat's
Engine and Transformers `generate` to emit batch samples. A persistent API and
the website's model-backed chat are later deployment work; there is no automatic
serving endpoint or base-model substitute for trained weights.

The final Qwen comparison uses the instruction baseline's pinned official chat
template with `enable_thinking=False` for sample generation, so the 64-token
output limit is not spent on its default reasoning prefill. Base/CPT/SFT samples
keep the explicit ChatML format used in our training. Assistant-only NLL retains
the same explicit ChatML inputs across all variants as a controlled-format
measurement; it is not a claim about each model's best possible native prompting.
Reports distinguish the generation format from the loss format.

## Bounded Modal execution

Use the existing lightweight tooling environment or an environment containing
the pinned Modal SDK; no local torch/model installation is required. Authenticate
with Modal's normal setup flow. Credentials stay in a private ignored config,
never in this repository or a command argument.

From the repository root:

```sh
# These only validate inputs and print finite command plans.
bash training/launch.sh --dry-run nanochat pilot rhysbot-nano-pilot-001
bash training/launch.sh --dry-run qwen pilot rhysbot-qwen-pilot-001

# After authentication and committing the reviewed release, actual paid pilots:
bash training/launch.sh --execute nanochat pilot rhysbot-nano-pilot-001 900
bash training/launch.sh --execute qwen pilot rhysbot-qwen-pilot-001 900

# Run sequentially, inspect pilot metrics first, then explicitly start a longer job.
bash training/launch.sh --execute nanochat train rhysbot-nano-train-001 3600
bash training/launch.sh --execute qwen train rhysbot-qwen-train-001 3600

# Compare real trained Qwen adapters with both untouched pinned baselines.
bash training/launch.sh --execute qwen eval rhysbot-qwen-eval-001 1800 rhysbot-qwen-train-001
```

`training/modal_app.py` builds the pinned remote Python/GPU image and uploads only
the training code and already released training files. Heavy downloads and all
optimization happen remotely. It does not upload private candidates, account
archives, review queues or the full workspace. Actual corrected pilots have
saved checkpoints and evaluations; source tests alone do not establish quality.

Before each GPU call a CPU coordinator atomically acquires a persistent named
distributed singleton and reserves budget in the artifact Volume ledger. A
second app invocation cannot start a concurrent GPU function. The job has a
provider timeout, subprocess deadlines, zero automatic retries, zero warm
containers and no network endpoint. Every retry needs a unique run ID and a new
reservation; an interrupted active ledger fails closed until provider state is
checked. Calls cannot silently overwrite an existing run/checkpoint directory.

The ledger uses the **full reservation for completed, failed and timed-out
jobs** until a provider billing receipt is supplied. It does not refund from a
wall-clock estimate or assume free credits. The conservative envelope is
$12/hour plus three minutes of billable overhead; a 15-minute pilot reserves
$5, a one-hour job reserves $12.60, and reservations cannot cumulatively exceed
$200. Each family needs evidence of a saved checkpoint and at least one actual
optimizer step before longer jobs are allowed. Receipts above the authorization
block all further jobs.

Receipt reconciliation is explicit, after confirming the job stopped:

```sh
modal run training/modal_app.py --run-id rhysbot-nano-pilot-001 --reconcile-usd ACTUAL_PROVIDER_BILL --receipt PROVIDER_USAGE_REFERENCE
```

Use actual numeric billing and an auditable reference. Download the ledger and
run directories through Modal's Volume tools. If a timeout leaves an active
lock/job, verify no corresponding provider call/container is running before
repairing its status; there is deliberately no automatic unlock/retry command.

The application reservation guard covers these planned compute jobs; it is not
a provider-wide account spending limit and cannot control unrelated workloads.
Current [Modal pricing](https://modal.com/pricing) supports the conservative
envelope, but prices/account settings must be rechecked before running. Artifact
Volume storage is separately billed; the large downloaded base-weight cache is
removed at job completion, while trained checkpoints and evidence are retained.
Delete completed disposable artifacts when no longer needed. Do not deploy
always-on inference against this experiment budget.

## Verification and remaining integration work

Static checks cover budget exhaustion/concurrency/retry behavior, pilot evidence,
provider-receipt reconciliation, split contamination, hash freezing, Qwen nested
key mapping, assistant-only loss masking, bounded plans and script syntax. The
Modal 1.6.2 function/image definitions import successfully without remote calls.
Source compatibility was inspected against the exact nanochat revision and
[Transformers 5.20.1 Qwen source](https://github.com/huggingface/transformers/blob/v5.20.1/src/transformers/models/qwen3_5/modeling_qwen3_5.py),
the pinned Hugging Face checkpoint config/index, and the installed Modal SDK.

Still unverified: remote image dependency resolution/build, actual Qwen checkpoint
remapping/loading on GPU, hybrid attention performance, backward passes,
checkpoints, measured pilot cost, model quality and live serving. The first paid
pilot is the integration test; a failure is preserved as a billed failed run,
never reported as a trained model.


## Pilot integration findings

Actual initial artifacts and measurements are recorded in `results/pilots.json`.
The first nanochat pilot completed all stages, but SFT exposed an upstream
microbatch/prefetch counting defect that produced fewer optimizer updates and a
negative learning-rate multiplier. The wrapper now translates optimizer steps
and verifies actual checkpoint updates; corrected runs must precede full-model
claims. Current full nanochat targets are 2,000 pretraining and 80 SFT updates.
The first Qwen pilot loaded real pretrained weights and completed four CPT
updates, then failed writing set-valued loading diagnostics as JSON. Those
values are now preserved as arrays. Saved partial adapters are not successful
post-training/evaluation runs. Initial pilot outputs establish execution, not
useful chat or faithful personality simulation.
