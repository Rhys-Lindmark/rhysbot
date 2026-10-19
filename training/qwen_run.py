#!/usr/bin/env python3
"""Text-only Qwen3.5 LoRA CPT -> assistant-only SFT -> held-out evaluation.

Heavy imports occur only inside remote commands. No quantization, vision model,
synthetic answers, external training dataset, or unpinned model is used.
"""
import argparse
import json
import math
from pathlib import Path
import random
import time
from runtime import ROOT, rows, read_json, write_json, release_manifest, metrics

LANGUAGE_PREFIX = "model.language_model."
KEY_MAPPING = {r"^model\.language_model\.": "model."}


def json_loading_info(info):
    """Transformers returns sets for key diagnostics; preserve them as arrays."""
    def convert(value):
        if isinstance(value, set):
            return sorted(value)
        raise TypeError('Unsupported loading diagnostic type: ' + type(value).__name__)
    return json.loads(json.dumps(info, default=convert))


def mapped_language_keys(weight_map):
    return {"model." + key[len(LANGUAGE_PREFIX):] for key in weight_map if key.startswith(LANGUAGE_PREFIX)}


def verify_loaded_language_weights(parameter_names, weight_map, loading_info):
    expected = {name for name in parameter_names if name.startswith("model.")}
    available = mapped_language_keys(weight_map)
    if not available or expected - available:
        raise ValueError("Checkpoint index does not cover every language-model parameter: " + str(sorted(expected - available)[:8]))
    if loading_info.get("missing_keys") or loading_info.get("mismatched_keys") or loading_info.get("error_msgs"):
        raise ValueError("Refusing partially initialized Qwen weights: " + str(loading_info))
    unexpected_language = [k for k in loading_info.get("unexpected_keys", [])
                           if k.startswith(LANGUAGE_PREFIX) or (k.startswith("model.") and not k.startswith("model.visual."))]
    if unexpected_language:
        raise ValueError("Unloaded language checkpoint keys: " + str(unexpected_language[:8]))


def load_model(model_id, revision, adapter=None, trainable=False):
    import torch
    from huggingface_hub import hf_hub_download
    from transformers import AutoConfig, AutoTokenizer, Qwen3_5ForCausalLM
    config = AutoConfig.from_pretrained(model_id, revision=revision, trust_remote_code=False)
    if not hasattr(config, "text_config"):
        raise ValueError("Expected the pinned Qwen3.5 multimodal container with text_config")
    index_path = hf_hub_download(model_id, "model.safetensors.index.json", revision=revision)
    weight_map = read_json(index_path)["weight_map"]
    model, info = Qwen3_5ForCausalLM.from_pretrained(
        model_id, revision=revision, config=config.text_config, key_mapping=KEY_MAPPING,
        dtype=torch.bfloat16, device_map={"": 0}, attn_implementation="sdpa",
        output_loading_info=True, trust_remote_code=False)
    verify_loaded_language_weights([name for name, _ in model.named_parameters()], weight_map, info)
    # The checkpoint ties lm_head to input embeddings; no uninitialized head is
    # allowed even though its separate tensor is absent from the shard index.
    if model.lm_head.weight.data_ptr() != model.model.embed_tokens.weight.data_ptr():
        raise ValueError("Expected tied loaded embedding/language-head weights")
    tokenizer = AutoTokenizer.from_pretrained(model_id, revision=revision, trust_remote_code=False)
    for token in ("<|im_start|>", "<|im_end|>"):
        if tokenizer.convert_tokens_to_ids(token) in (None, tokenizer.unk_token_id):
            raise ValueError("Pinned tokenizer lacks required Qwen ChatML tokens")
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    if adapter:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, str(adapter), is_trainable=trainable)
    return model, tokenizer, {"model_id": model_id, "revision": revision,
                              "loading_info": json_loading_info(info), "language_checkpoint_tensors": len(mapped_language_keys(weight_map))}


def chat_tokens(tokenizer, messages, limit=None):
    """Explicit ChatML, loss only on sourced assistant content and its end token.

    This avoids relying on absent base-model chat templates or unsupported
    generation-mask template tags. The exact same format is used at inference.
    """
    ids, labels = [], []
    for message in messages:
        if message["role"] not in {"system", "user", "assistant"}:
            raise ValueError("Unsupported role")
        prefix = tokenizer.encode("<|im_start|>" + message["role"] + "\n", add_special_tokens=False)
        body = tokenizer.encode(message["content"] + "<|im_end|>\n", add_special_tokens=False)
        ids.extend(prefix + body)
        labels.extend([-100] * len(prefix) + (body if message["role"] == "assistant" else [-100] * len(body)))
    if limit:
        ids, labels = ids[:limit], labels[:limit]
    if not any(token != -100 for token in labels[1:]):
        raise ValueError("No assistant target tokens survive sequence truncation")
    return ids, labels


def chat_prompt(tokenizer, messages):
    return "".join("<|im_start|>" + m["role"] + "\n" + m["content"] + "<|im_end|>\n"
                   for m in messages) + "<|im_start|>assistant\n"


def training_examples(data, stage, tokenizer, limit):
    if stage == "cpt":
        for row in rows(data / "pretrain-train.jsonl"):
            body = tokenizer.encode(row["text"], add_special_tokens=False)
            tokens = [tokenizer.eos_token_id] + body + [tokenizer.eos_token_id]
            for start in range(0, len(tokens) - 1, limit - 1):
                chunk = tokens[start:start + limit]
                if len(chunk) > 1:
                    yield chunk, list(chunk)
    else:
        for row in rows(data / "sft-train.jsonl"):
            try:
                yield chat_tokens(tokenizer, row["messages"], limit)
            except ValueError as error:
                if "No assistant target" not in str(error):
                    raise


def train(model, tokenizer, data, stage, out, config, pilot, loading):
    import torch
    from peft import LoraConfig, get_peft_model
    cfg = config["qwen"]
    torch.manual_seed(42)
    torch.cuda.manual_seed_all(42)
    if stage == "cpt":
        lora = LoraConfig(r=cfg["lora_rank"], lora_alpha=cfg["lora_alpha"],
                          lora_dropout=cfg["lora_dropout"], target_modules=cfg["target_modules"],
                          bias="none", task_type="CAUSAL_LM")
        model = get_peft_model(model, lora)
    trainable = [(name, param) for name, param in model.named_parameters() if param.requires_grad]
    if not trainable or any("lora_" not in name or "visual" in name for name, _ in trainable):
        raise ValueError("Only language LoRA parameters may be trained")
    model.config.use_cache = False
    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()
    model.train()
    examples = list(training_examples(data, stage, tokenizer, cfg["max_seq_len"]))
    if not examples:
        raise ValueError("No usable sourced training examples")
    rng = random.Random(42)
    rng.shuffle(examples)
    optimizer = torch.optim.AdamW([p for _, p in trainable], lr=cfg["learning_rate"], weight_decay=0.01)
    steps = cfg[("pilot_" if pilot else "") + stage + "_steps"]
    accumulation = cfg["gradient_accumulation_steps"]
    observed_tokens, losses = 0, []
    started = time.monotonic()
    torch.cuda.reset_peak_memory_stats()
    optimizer.zero_grad(set_to_none=True)
    for step in range(steps):
        total_loss = 0.0
        for micro in range(accumulation):
            ids, labels = examples[(step * accumulation + micro) % len(examples)]
            x = torch.tensor([ids], device="cuda")
            y = torch.tensor([labels], device="cuda")
            with torch.autocast("cuda", dtype=torch.bfloat16):
                loss = model(input_ids=x, labels=y, use_cache=False).loss
            if not torch.isfinite(loss):
                raise ValueError("Non-finite training loss")
            (loss / accumulation).backward()
            total_loss += loss.item() / accumulation
            observed_tokens += sum(token != -100 for token in labels[1:])
        torch.nn.utils.clip_grad_norm_([p for _, p in trainable], 1.0)
        optimizer.step()
        optimizer.zero_grad(set_to_none=True)
        losses.append(total_loss)
        print(json.dumps({"stage": stage, "optimizer_step": step + 1, "loss": total_loss}), flush=True)
    elapsed = time.monotonic() - started
    out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(out, safe_serialization=True)
    tokenizer.save_pretrained(out)
    report = {"family": "qwen", "stage": stage, "status": "actual_adapter_saved",
              "optimizer_steps": steps, "trained_checkpoint": str(out), "loading": loading,
              "training_examples": len(examples), "target_tokens": observed_tokens,
              "seconds": elapsed, "target_tokens_per_second": observed_tokens / elapsed,
              "peak_cuda_allocated_bytes": torch.cuda.max_memory_allocated(),
              "loss_first": losses[0], "loss_last": losses[-1],
              "trainable_parameters": sum(p.numel() for _, p in trainable),
              "data": release_manifest(data), "config": config}
    write_json(out / "run.json", report)
    return report


def evaluate(model, tokenizer, data, out, config, loading):
    import torch
    model.eval()
    model.config.use_cache = False
    nats, ntokens, nbytes = 0.0, 0, 0
    limit = config["qwen"]["max_seq_len"]
    with torch.inference_mode():
        for i, row in enumerate(rows(data / "pretrain-test.jsonl")):
            if i >= config["evaluation"]["max_pretrain_test_documents"]:
                break
            body = tokenizer.encode(row["text"], add_special_tokens=False)
            ids = [tokenizer.eos_token_id] + body
            for start in range(0, len(ids) - 1, limit - 1):
                chunk = ids[start:start + limit]
                if len(chunk) < 2:
                    continue
                x = torch.tensor([chunk], device="cuda")
                loss = model(input_ids=x, labels=x, use_cache=False).loss.item()
                nats += loss * (len(chunk) - 1)
                ntokens += len(chunk) - 1
                nbytes += len(tokenizer.decode(chunk[1:], skip_special_tokens=True,
                                               clean_up_tokenization_spaces=False).encode())
        assistant_nats, assistant_tokens = 0.0, 0
        samples = []
        for row in list(rows(data / "sft-test.jsonl"))[:config["evaluation"]["max_sft_test_examples"]]:
            ids, labels = chat_tokens(tokenizer, row["messages"], limit)
            count = sum(t != -100 for t in labels[1:])
            loss = model(input_ids=torch.tensor([ids], device="cuda"),
                         labels=torch.tensor([labels], device="cuda"), use_cache=False).loss.item()
            assistant_nats += loss * count
            assistant_tokens += count
            prompt = chat_prompt(tokenizer, row["messages"][:-1])
            encoded = tokenizer(prompt, return_tensors="pt", add_special_tokens=False).to("cuda")
            encoded = {key: value[:, -limit:] for key, value in encoded.items()}
            start = time.monotonic()
            generated = model.generate(**encoded, do_sample=False, use_cache=True,
                                       max_new_tokens=config["evaluation"]["max_new_tokens"],
                                       pad_token_id=tokenizer.pad_token_id,
                                       eos_token_id=tokenizer.convert_tokens_to_ids("<|im_end|>"))
            samples.append({"id": row["id"], "task": row["task"],
                            "completion": tokenizer.decode(generated[0, encoded["input_ids"].shape[1]:], skip_special_tokens=True),
                            "reference": row["messages"][-1]["content"],
                            "latency_seconds": time.monotonic() - start})
    result = {"family": "qwen", "loading": loading, "heldout": metrics(nats, ntokens, nbytes),
              "assistant_only": {"nll_per_token": assistant_nats / assistant_tokens,
                                 "tokens": assistant_tokens,
                                 "perplexity": math.exp(min(assistant_nats / assistant_tokens, 80))},
              "samples_for_manual_review": samples, "data": release_manifest(data),
              "status": "measured_no_personality_quality_claim"}
    write_json(out, result)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("stage", choices=["cpt", "sft", "eval", "baseline-eval"])
    p.add_argument("--data", type=Path, default=ROOT / "data/training")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--adapter", type=Path)
    p.add_argument("--pilot", action="store_true")
    args = p.parse_args()
    config = read_json(ROOT / "training/config.json")
    pins = read_json(ROOT / "training/upstream-pins.json")
    if args.stage == "sft" and (not args.adapter or not (args.adapter / "run.json").exists()):
        raise ValueError("SFT requires a completed CPT adapter and provenance")
    if args.adapter:
        previous = read_json(args.adapter / "run.json")
        if previous["data"] != release_manifest(args.data):
            raise ValueError("Adapter/data provenance mismatch")
    model_id = config["qwen"]["baseline_id" if args.stage == "baseline-eval" else "model_id"]
    revision = pins["models"][model_id]["revision"]
    model, tokenizer, loading = load_model(model_id, revision, args.adapter, args.stage == "sft")
    if args.stage in {"cpt", "sft"}:
        train(model, tokenizer, args.data, args.stage, args.out, config, args.pilot, loading)
    else:
        evaluate(model, tokenizer, args.data, args.out, config, loading)


if __name__ == "__main__":
    main()
