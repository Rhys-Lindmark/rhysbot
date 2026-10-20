# Rhysbot playground

The live entry points are [chat](https://ai.rhyslindmark.com/rhysbot/chat/),
[model explanations](https://ai.rhyslindmark.com/rhysbot/learn/),
[lab](https://ai.rhyslindmark.com/rhysbot/lab/) and
[Anki evaluations](https://ai.rhyslindmark.com/rhysbot/evals/).

## What each experiment runs

| Experiment | Actual implementation |
| --- | --- |
| Fast Rhys chat | Managed Qwen3-30B with retrieved, attributed Rhys sources and first-person conversational prompting |
| Pure nanochat | Actual pinned step-80 scratch checkpoint, CPU inference, bounded retained history; no retrieval, Qwen or tools |
| Trained checkpoint comparison | Saved Qwen3.5-4B CPT/SFT adapter versus the same base with adapter disabled |
| Live backroom | A finite panel using the same managed Qwen model in different roles; Rhys roles receive sources, Qwen receives the panel dialogue, guest receives supplied excerpts |
| Uploaded guest | Prompted simulation from up to four locally selected excerpts, not a newly trained person model |
| Image analysis | Moondream vision helper, not a vision capability trained into either Rhys checkpoint |
| Image output | FLUX.1 Schnell, four fixed diffusion steps |

The scratch model is a small research artifact and often produces incoherent or
very short answers. Showing its checkpoint identity does not imply useful chat
quality. Its 512-token context retains only whole conversation pairs that fit;
an oversized current question fails explicitly. Model-produced code never runs.

## Controls and cost

All new inference actions require an explicit click, same-origin authorization,
and the existing shared monthly ledger. Reservations: nanochat $0.02/reply,
backroom $0.005/turn (2–6 turns), image analysis $0.01 and image output $0.02.
These are conservative internal reservations, not provider invoices. The ready
voice worker still reserves its $8.90 baseline from the same $10 cap. No new
always-on GPU, subscription, automation or real payment checkout is enabled.

Guest files remain in browser memory; only matching excerpts are sent after
Run. The page does not publish or save the corpus. Image inputs are re-encoded
locally to a bounded JPEG before sending. Prompts, excerpts and prepared images
are processed by the inference provider. Stopping cancels the browser's wait;
already-started bounded provider work can continue. Retries are explicit new
requests, never an unbounded agent loop.

## Evaluation method

`scripts/prepare_anki_eval.py` inventories every released Anki record. It never
reads the original export or restores excluded material. Missing questions or
references are reported, not invented. Original training/validation/test
membership is carried into the new benchmark, separately from current corpus
splits. Membership does not prove a particular optimizer step consumed a row.

`training/anki_eval_job.py` runs a finite pilot and full evaluation using the
original research ledger and singleton. It compares actual saved adapter
weights with the same pinned base, using identical prompts and decoding.
Assistant-target likelihood covers all tokenizable QA records, with truncation
and skipped coverage reported. A deterministic cohort sample also generates
answers for normalized exact match, token overlap and human review. Image-bearing
cards may lack visual context; generated-answer samples exclude them.

Lower reference loss is not answer accuracy. Card answers are not independently
verified facts. Training-cohort improvements may reflect memorization; held-out
results are shown separately. Review labels and corrections are stored locally
with checkpoint and QA hashes, and exported only by explicit download. They are
not automatically published or used for training.

To rebuild the inventory and recompute the published aggregate locally without
starting model inference:

```sh
python scripts/prepare_anki_eval.py --out /tmp/rhysbot-qa
python scripts/summarize_anki_eval.py results/anki-evaluation --dataset data/evaluation --output /tmp/rhysbot-summary.json
```

The completed [recorded benchmark](../results/anki-evaluation/summary.json)
scored all 18,363 eligible QA pairs and generated 96 answer pairs. Held-out test
NLL was 2.352 for the Rhys adapter versus 2.744 for the same base. On 32 generated
test answers, normalized exact match was 1/32 versus 0/32 and token F1 was 0.135
versus 0.085. These small answer-overlap scores remain weak. The 34 truncated
references and 1,330 image-bearing cards are reported separately; no images were
supplied. See [artifact provenance](../results/anki-evaluation/provenance.json).

The second command validates QA hashes, score coverage, split membership and
sample provenance before aggregating. `results/anki-evaluation/` contains the
recorded measurements, not model weights. Running a new remote measurement is a
separate, finite paid job in `training/anki_eval_job.py`; unit tests and report
aggregation do not start GPUs.

## Further experiments worth doing

1. Build a first-person conversation dataset from matched Rhys podcast turns,
   HEE and an owner interview; train on reviewed answers and disagreements.
2. Collect owner preferences and corrections, then compare prompt-only,
   conversational SFT and preference-trained variants on held-out conversations.
3. Add semantic retrieval and dated worldview summaries to improve source recall
   and handle changing opinions, keeping guest views and curated notes distinct.
4. Run archived backroom experiments with actual checkpoint variants and fixed
   prompts; measure disagreement and errors without treating consensus as truth.
5. Build a separate Tyler-inspired checkpoint from an appropriate supplied
   corpus, with its own provenance, evaluation and finite compute allocation.
6. Extend Anki evaluation to released image-dependent cards with visual context,
   then compare whether vision actually improves answers.

These are proposed follow-ups, not claims that additional models were trained.

The highest-value next experiment is a small **Rhys conversation benchmark**:
questions about worldview, decisions, disagreements and uncertainty, with Rhys's
own answers kept out of training. Score factual support, agreement with his
stated view, conversational style and willingness to disagree separately. Then
compare today's retrieval prompt with a first-person SFT candidate on those
same held-out questions. Anki likelihood cannot substitute for that measurement.

For training, prioritize real Rhys podcast turns and reviewed interview answers
over synthetic paraphrases. Pair them with plausible questions and enough prior
conversation to teach follow-ups. Keep source attribution and dates in the
curation record, while the assistant answer itself is conversational. Preferences
should distinguish “sounds like me” from “is correct”; a fluent imitation can
still be wrong. A dated belief timeline would let the model explain how Rhys's
views changed without treating every old post as his current position.

Useful research starting points are the original
[LaMP personalization benchmark](https://arxiv.org/abs/2304.11406), which evaluates
personalized outputs using user profiles, and
[Direct Preference Optimization](https://arxiv.org/abs/2305.18290), which learns
from preferred/rejected response pairs. These motivate experiments; neither
establishes that a model captures a particular person's mind.
