# Rhysbot model experiments

## Current state

The nanochat scratch experiment and full Qwen adaptation have completed actual
training and evaluation, including the separate four-way Qwen comparison. Both
saved checkpoints passed real hosted GPU inference. The public Qwen browser
flow also passed verification, proxying and checkpoint display; the experimental
[demo is live](https://ai.rhyslindmark.com/rhysbot/). These small evaluations are diagnostic and do not validate
factuality, personality fidelity or app usefulness.

The released input is the frozen corpus from commit
`1e20626c7b3b2b2bc3404fdb0b1fd39a93419b3d`, with source corpus SHA256
`6da8afa85c1b9c8e4406823f643a8bdf80fecccac4bd58c28878ca950e6544d8`.
The training splits contain 23,465 pretraining documents and 17,181 sourced SFT
examples. Mixed/unverified podcast speakers and contextless X replies are
excluded from the default training recipe. See the [dataset card](DATASET_CARD.md)
for provenance, privacy review, source rights and coverage limitations.

## Nanochat from random weights

Run: `nanochat-train-20261019a`. Code/config release:
`c0747a0d88f4d503b9d578cf66bf36b093f98049`. Upstream nanochat revision:
`92d63d4e8bb4df75c3b71618f31ddde2378b2bcd`.

- Depth 4, width 256, vocabulary 8,192, context 512 tokens.
- Tokenizer trained on the training split; model initialized from random weights.
- 2,000 pretraining updates with 8,192 token positions per update, then 80 SFT
  updates using actual study-card answers and sourced passage continuations.
- One A100 80 GB; complete six-stage job took 307.96 seconds.
- Checkpoints, optimizer state, logs and provenance remain in the Modal artifact
  volume. Model weights are not committed to Git.

The pretraining validation BPB was best at step 800 (1.4830), then worsened to
1.6591 at step 2,000. This suggests overfitting; all saved checkpoints are
preserved. The current SFT run starts from the final step-2,000 checkpoint.
The results below describe those actual final checkpoints, not an unmeasured
claim about the best validation checkpoint.

| Frozen held-out measurement | Final pretrained checkpoint | Post-trained checkpoint |
| --- | ---: | ---: |
| Next-token bits per byte | 2.1565 | 1.8188 |
| Next-token NLL per token | 5.9459 | 5.0148 |
| Assistant-only NLL per target token | 7.2899 | 5.4419 |

The bounded evaluation covers the first 32 held-out source documents and 10
held-out SFT examples, with at most 64 generated tokens each. There are 2,610
next-token targets and 249 assistant targets in this nanochat sample. These are
small diagnostic samples, not broad benchmark scores.

Manual inspection found incoherent and repetitive completions. This model is a
small learning experiment; useful chat, factuality and personality fidelity have
not been demonstrated. Lower held-out loss does not establish those properties.
Machine-readable metrics, validation curve and verified SFT artifact hashes are
in [nanochat-full.json](../results/nanochat-full.json).

## Qwen adaptation

The recipe starts from `Qwen/Qwen3.5-4B-Base`, revision
`1001bb4d826a52d1f399e183466143f4da7b741b`. It strictly loads the pretrained text
backbone and trained 16,232,448 rank-8 LoRA parameters in BF16. Full run
`qwen-train-20261019a` completed on code/config release
`c0747a0d88f4d503b9d578cf66bf36b093f98049`: 1,000 continued-pretraining
updates followed by 400 assistant-only SFT updates. Its adapter continued from
CPT into SFT; it is not a replacement base checkpoint or an instruction-model
download presented as fine-tuning. Both stages restored 426 language checkpoint
tensors with no reported missing, unexpected or mismatched weights.

One A100 80 GB completed training and the SFT evaluation in 4,278.06 GPU-job
seconds. CPT processed 371,002 target tokens in 2,863.84 optimization seconds;
SFT processed 85,151 assistant targets in 1,280.70 seconds. These counts are
unmasked labels actually processed, not the entire corpus size. Peak allocated
CUDA memory was 10,307,323,392 bytes during CPT and 10,307,193,344 during SFT.

| Frozen SFT held-out measurement | Post-trained checkpoint |
| --- | ---: |
| Next-token bits per byte | 0.5181 |
| Next-token NLL per token | 1.5913 |
| Assistant-only NLL per target token | 2.2501 |

This small diagnostic evaluation covers the first 32 held-out source documents
(2,343 next-token targets, 10,382 UTF-8 bytes) and 10 held-out SFT examples
(220 assistant targets), with at most 64 generated tokens each. Some completions
match short study answers; others mismatch the reference, repeat text, or emit
degenerate numbers. It does not validate factuality, personality fidelity or
useful chat. Machine-readable metrics, actual completions, frozen provenance and
SFT artifact hashes are in [qwen-full.json](../results/qwen-full.json).

Separate four-way evaluation `qwen-eval-20261019a` completed in 294.81 GPU-job
seconds on release `1be5cf0738005c752474d7b59bbee9c9204aea08`. It evaluates the
same held-out inputs and the actual adapters from `qwen-train-20261019a`:

| Variant | Next-token BPB | Next-token NLL | Assistant-only NLL |
| --- | ---: | ---: | ---: |
| Untouched base | 1.3143 | 4.0368 | 2.9791 |
| Pinned instruction baseline | 1.2529 | 3.8480 | 3.7246 |
| CPT adapter | 0.5178 | 1.5905 | 2.4637 |
| CPT then SFT adapter | 0.5181 | 1.5913 | 2.2501 |

CPT lowered next-token loss on this sample; SFT lowered assistant-only loss
relative to CPT while slightly worsening next-token loss. These are small
controlled-input measurements, not accuracy or personality scores. The 64-token
cap cuts off longer answers; some samples contain unsupported assertions,
reference mismatches, repetition or degenerate numbers. No preference or accuracy
grades were assigned. All 40 exact completions, their released questions/input
messages and study-note references are in
[qwen-comparison.json](../results/qwen-comparison.json), with model revisions,
formats, frozen input hashes and parent-run provenance.

In this comparison, instruction-baseline samples use its pinned official
chat template with thinking disabled; adapted-model samples use their training
format. Assistant-only NLL uses a common explicit ChatML format across variants.
This controls the loss inputs, but does not establish best-case performance with
each model's native prompting. Generation/loss formats are recorded separately.

## Access and deployment

The corpus and training code are public. Trained artifacts currently remain in
the authenticated `rhysbot-training-artifacts` Modal Volume under `runs/<run_id>/`.
Operators can inspect individual files with `modal volume ls` and download them
with `modal volume get`; credentials and model weights stay out of Git.

The deployed inference code permits only explicitly hashed completed checkpoints,
uses the same shared $200 budget ledger, and scales to zero. Actual operator
requests restored both saved models and generated text. Public browser generation
returned the named Qwen checkpoint and matching hash; invalid verification and
wrong-origin requests returned HTTP 403. Evidence and exact output are in
[serving.json](../results/serving.json). No synthetic
answer, random checkpoint, or unadapted base-model fallback substitutes for a
trained checkpoint. See [serving.md](serving.md).

## Compute accounting

Provider billing is pending. Pilot/setup reservations total $25; the completed
nanochat run reserves $6.60; the completed Qwen job reserves $24.60; the separate
Qwen comparison reserves $6.60, for $62.80 committed before serving requests.
Three verified inference requests added $3.60, giving $66.40 committed at the
verification snapshot. Each further admitted request adds a conservative $1.20
hold; consult the shared ledger for the current total. These are budget holds, not confirmed charges.
Failed jobs retain their reservations until provider billing is reconciled.
Evaluation and inference must fit within the remaining shared authorization.
