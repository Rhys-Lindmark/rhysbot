# Rhysbot model experiments

## Current state

The nanochat scratch experiment has completed actual training and evaluation.
The longer Qwen adaptation is running; its completed pilot is pipeline evidence,
not the final model. No model-backed website demo is live yet.

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
backbone and trains 16,232,448 rank-8 LoRA parameters in BF16. The longer run
requests 1,000 continued-pretraining updates followed by 400 assistant-only SFT
updates. Its adapter continues from CPT into SFT; it is not a replacement base
checkpoint or an instruction-model download presented as fine-tuning.

The completed corrected pilot saved separate CPT/SFT adapters and actual held-out
evaluations. Its four CPT and two SFT updates do not establish improvement.
The full run and a separate comparison against the untouched base, pinned Qwen
instruction baseline, CPT adapter and SFT adapter remain pending.

## Access and deployment

The corpus and training code are public. Trained artifacts currently remain in
the authenticated `rhysbot-training-artifacts` Modal Volume under `runs/<run_id>/`.
Operators can inspect individual files with `modal volume ls` and download them
with `modal volume get`; credentials and model weights stay out of Git.

The prepared inference code permits only explicitly hashed completed checkpoints,
uses the same shared $200 budget ledger, and scales to zero. Actual cold-start
inference and the website connection still need verification. No synthetic
answer, random checkpoint, or unadapted base-model fallback substitutes for a
trained checkpoint. See [serving.md](serving.md).

## Compute accounting

Provider billing is pending. Pilot/setup reservations total $25; the completed
nanochat run reserves $6.60; the running Qwen job reserves $24.60, for a current
conservative commitment of $56.20. These are budget holds, not confirmed charges.
Failed jobs retain their reservations until provider billing is reconciled.
Evaluation and inference must fit within the remaining shared authorization.
