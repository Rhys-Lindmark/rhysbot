#!/usr/bin/env python3
"""Run pinned nanochat on released Rhysbot data, with no upstream datasets.

Run only in the remote training image. The wrapper redirects upstream dataset
and SFT task hooks without modifying the pinned checkout.
"""
import argparse
import importlib
import json
import os
from pathlib import Path
import runpy
import sys
import time
from runtime import ROOT, read_json, rows, write_json, release_manifest, verify_manifest, git_revision, metrics


def eager_compile(model=None, *unused, **kwargs):
    """Support both torch.compile(model) and @torch.compile(options) forms."""
    return (lambda function: function) if model is None else model


def split_text(text, limit):
    words = text.split()
    chunk, size = [], 0
    for word in words:
        if chunk and size + len(word) + 1 > limit:
            yield " ".join(chunk)
            chunk, size = [], 0
        chunk.append(word)
        size += len(word) + 1
    if chunk:
        yield " ".join(chunk)


def prepare(data, base, config):
    import pyarrow as pa
    import pyarrow.parquet as pq
    target = base / "base_data_climbmix"
    target.mkdir(parents=True, exist_ok=True)
    names = {"train": "shard_00000.parquet", "validation": "shard_99999.parquet"}
    if any(path.name not in names.values() for path in target.glob("*.parquet")):
        raise ValueError("Refusing a data directory containing other upstream shards")
    for split, name in names.items():
        texts = [chunk for row in rows(data / f"pretrain-{split}.jsonl")
                 for chunk in split_text(row["text"], config["parquet_document_max_chars"])]
        if not texts:
            raise ValueError("Empty training/validation shard")
        pq.write_table(pa.table({"text": texts}), target / name, row_group_size=128)
    # Test is never in upstream's train/val directory or tokenizer iterator.
    if sorted(path.name for path in target.glob("*.parquet")) != sorted(names.values()):
        raise ValueError("Unexpected parquet shard list")
    return names


def install_local_tasks(data):
    from tasks.common import Task

    class LocalConversations(Task):
        def __init__(self, split, **kwargs):
            super().__init__()
            mapped = {"train": "train", "test": "validation"}[split]
            from nanochat.tokenizer import get_tokenizer
            tokenizer = get_tokenizer()
            self.examples = []
            for row in rows(data / f"sft-{mapped}.jsonl"):
                _, mask = tokenizer.render_conversation({"messages": row["messages"]}, max_tokens=513)
                if any(mask[1:]):
                    self.examples.append(row)
            if not self.examples:
                raise ValueError("No assistant targets in local SFT split")

        def num_examples(self):
            return len(self.examples)

        def get_example(self, index):
            return {"messages": self.examples[index]["messages"]}

    class EmptyTask:
        def __init__(self, *args, **kwargs):
            pass

        def __len__(self):
            return 0

    importlib.import_module("tasks.smoltalk").SmolTalk = LocalConversations
    importlib.import_module("tasks.mmlu").MMLU = EmptyTask
    importlib.import_module("tasks.gsm8k").GSM8K = EmptyTask


def upstream(module, arguments):
    old = sys.argv
    try:
        sys.argv = [module] + arguments
        runpy.run_module(module, run_name="__main__")
    finally:
        sys.argv = old


def common_args(config):
    return ["--device-type=cuda", "--run=dummy",
            f"--max-seq-len={config['max_seq_len']}",
            f"--device-batch-size={config['device_batch_size']}",
            f"--total-batch-size={config['total_batch_size']}",
            f"--eval-tokens={config['eval_tokens']}"]


def evaluate(data, base, source, tag, out, config):
    import torch
    from nanochat.checkpoint_manager import load_model
    from nanochat.common import COMPUTE_DTYPE
    from nanochat.engine import Engine
    model, tokenizer, meta = load_model(source, torch.device("cuda"), phase="eval", model_tag=tag)
    nats, tokens, nbytes = 0.0, 0, 0
    count = config["evaluation"]["max_pretrain_test_documents"]
    limit = model.config.sequence_len
    with torch.inference_mode(), torch.autocast("cuda", dtype=COMPUTE_DTYPE):
        for index, row in enumerate(rows(data / "pretrain-test.jsonl")):
            if index >= count:
                break
            # Evaluate complete non-overlapping chunks; every predicted byte is
            # counted via the tokenizer's byte-level representation.
            ids = tokenizer.encode(row["text"], prepend=tokenizer.get_bos_token_id())
            for start in range(0, len(ids) - 1, limit):
                chunk = ids[start:start + limit + 1]
                if len(chunk) < 2:
                    continue
                x = torch.tensor([chunk[:-1]], device="cuda")
                y = torch.tensor([chunk[1:]], device="cuda")
                nats += model(x, y, loss_reduction="sum").item()
                tokens += len(chunk) - 1
                nbytes += sum(len(tokenizer.decode_single_token_bytes(t)) for t in chunk[1:])
    result = {"family": "nanochat", "source": source, "checkpoint_meta": meta,
              "test_documents_limit": count, "heldout": metrics(nats, tokens, nbytes),
              "status": "measured_checkpoint_no_personality_quality_claim"}
    assistant_nats, assistant_tokens = 0.0, 0
    with torch.inference_mode(), torch.autocast("cuda", dtype=COMPUTE_DTYPE):
        for row in list(rows(data / "sft-test.jsonl"))[:config["evaluation"]["max_sft_test_examples"]]:
            ids, mask = tokenizer.render_conversation({"messages": row["messages"]}, max_tokens=limit + 1)
            count = sum(mask[1:])
            if not count:
                continue
            x = torch.tensor([ids[:-1]], device="cuda")
            y = torch.tensor([[token if keep else -1 for token, keep in zip(ids[1:], mask[1:])]], device="cuda")
            assistant_nats += model(x, y, loss_reduction="sum").item()
            assistant_tokens += count
    if not assistant_tokens:
        raise ValueError("No assistant test targets survived truncation")
    import math
    result["assistant_only"] = {"nll_per_token": assistant_nats / assistant_tokens,
                                "tokens": assistant_tokens,
                                "perplexity": math.exp(min(assistant_nats / assistant_tokens, 80))}
    engine = Engine(model, tokenizer)
    samples = []
    for row in list(rows(data / "sft-test.jsonl"))[:config["evaluation"]["max_sft_test_examples"]]:
        prompt = tokenizer.render_for_completion({"messages": row["messages"]})
        prompt = prompt[-min(len(prompt), limit - config["evaluation"]["max_new_tokens"]):]
        started = time.monotonic()
        with torch.inference_mode(), torch.autocast("cuda", dtype=COMPUTE_DTYPE):
            generated, _ = engine.generate_batch(prompt, num_samples=1,
                                                 max_tokens=config["evaluation"]["max_new_tokens"],
                                                 temperature=0.0)
        samples.append({"id": row["id"], "task": row["task"],
                        "completion": tokenizer.decode(generated[0][len(prompt):]),
                        "reference": row["messages"][-1]["content"],
                        "latency_seconds": time.monotonic() - started})
    result["samples_for_manual_review"] = samples
    write_json(out, result)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("stage", choices=["prepare", "tokenizer", "pretrain", "sft", "eval-base", "eval-sft"])
    p.add_argument("--data", type=Path, default=ROOT / "data/training")
    p.add_argument("--base", type=Path, required=True)
    p.add_argument("--upstream", type=Path, default=Path("/opt/nanochat"))
    p.add_argument("--tag", default="rhysbot")
    p.add_argument("--report-root", type=Path)
    p.add_argument("--pilot", action="store_true")
    args = p.parse_args()
    config = read_json(ROOT / "training/config.json")
    pins = read_json(ROOT / "training/upstream-pins.json")
    if git_revision(args.upstream) != pins["nanochat"]["revision"]:
        raise ValueError("nanochat upstream revision mismatch")
    args.base.mkdir(parents=True, exist_ok=True)
    frozen = args.base / "data-manifest.json"
    if frozen.exists():
        verify_manifest(args.data, read_json(frozen))
    else:
        write_json(frozen, release_manifest(args.data))
    os.environ["NANOCHAT_BASE_DIR"] = str(args.base.resolve())
    os.environ["WANDB_MODE"] = "disabled"
    sys.path.insert(0, str(args.upstream))
    if args.stage == "prepare":
        prepare(args.data, args.base, config["nanochat"])
        return
    import nanochat.dataset as dataset
    dataset.DATA_DIR = str(args.base / "base_data_climbmix")
    def forbidden(*unused, **kwargs):
        raise RuntimeError("Unrequested upstream dataset/kernel download forbidden in Rhysbot recipe")
    dataset.download_single_file = forbidden
    import nanochat.common
    nanochat.common.download_file_with_lock = forbidden
    import tasks.common
    tasks.common.load_hub_dataset = forbidden
    # Upstream otherwise probes/downloads optional unpinned Hub kernels at
    # import. Use the standard pinned PyTorch SDPA path for this bounded run.
    import kernels
    kernels.has_kernel = lambda *unused, **kwargs: False
    kernels.get_kernel = forbidden
    if args.pilot:
        import torch
        torch.compile = eager_compile
    cfg = config["nanochat"]
    if args.stage == "tokenizer":
        upstream("scripts.tok_train", [f"--vocab-size={cfg['vocab_size']}",
                                      f"--max-chars={cfg['tokenizer_max_chars']}", "--doc-cap=4096"])
    elif args.stage == "pretrain":
        steps = cfg["pilot_pretrain_steps" if args.pilot else "pretrain_steps"]
        upstream("scripts.base_train", common_args(cfg) + [f"--depth={cfg['depth']}",
                 f"--aspect-ratio={cfg['aspect_ratio']}", "--window-pattern=L", f"--num-iterations={steps}",
                 "--target-param-data-ratio=-1", "--core-metric-every=-1", "--sample-every=-1",
                 "--warmup-steps=2", "--eval-every=20", "--save-every=20", f"--model-tag={args.tag}"])
    elif args.stage == "sft":
        install_local_tasks(args.data)
        steps = cfg["pilot_sft_steps" if args.pilot else "sft_steps"]
        upstream("scripts.chat_sft", common_args(cfg) + [f"--model-tag={args.tag}",
                 f"--num-iterations={steps}", "--load-optimizer=0", "--chatcore-every=-1",
                 "--eval-every=20", "--mmlu-epochs=0", "--gsm8k-epochs=0"])
    else:
        source = "base" if args.stage == "eval-base" else "sft"
        report_root = args.report_root or args.base
        evaluate(args.data, args.base, source, args.tag, report_root / (args.stage + ".json"), config)


if __name__ == "__main__":
    main()
