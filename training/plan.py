"""Finite command plans; importable without Modal, torch or model downloads."""
from pathlib import Path
import sys
from runtime import ROOT, read_json, release_manifest, safe_run_id


def build_plan(family, phase, run_id, root=Path("/artifacts/runs"), parent_run=None):
    safe_run_id(run_id)
    if family not in {"nanochat", "qwen"} or phase not in {"pilot", "train", "eval"}:
        raise ValueError("Unsupported model family or phase")
    if phase == "eval" and not parent_run:
        raise ValueError("Evaluation needs a specific completed parent checkpoint run")
    if parent_run:
        safe_run_id(parent_run)
    run = Path(root) / run_id
    prior = Path(root) / (parent_run or run_id)
    pilot = ["--pilot"] if phase == "pilot" else []
    if family == "nanochat":
        base = prior / "nanochat" if phase == "eval" else run / "nanochat"
        common = ["--base", str(base), "--tag", "rhysbot"] + pilot
        if phase == "eval":
            common += ["--report-root", str(run)]
        stages = ["eval-base", "eval-sft"] if phase == "eval" else ["prepare", "tokenizer", "pretrain", "sft", "eval-base", "eval-sft"]
        return [[sys.executable, str(ROOT / "training/nanochat_run.py"), stage] + common for stage in stages]
    script = str(ROOT / "training/qwen_run.py")
    if phase == "eval":
        return [[sys.executable, script, "eval", "--out", str(run / "base-eval.json")],
                [sys.executable, script, "baseline-eval", "--out", str(run / "instruct-baseline-eval.json")],
                [sys.executable, script, "eval", "--adapter", str(prior / "qwen/cpt"), "--out", str(run / "cpt-eval.json")],
                [sys.executable, script, "eval", "--adapter", str(prior / "qwen/sft"), "--out", str(run / "sft-eval.json")]]
    return [[sys.executable, script, "cpt", "--out", str(run / "qwen/cpt")] + pilot,
            [sys.executable, script, "sft", "--adapter", str(run / "qwen/cpt"), "--out", str(run / "qwen/sft")] + pilot,
            [sys.executable, script, "eval", "--adapter", str(run / "qwen/sft"), "--out", str(run / "qwen/sft-eval.json")]]


if __name__ == "__main__":
    import argparse
    import json
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("family", choices=["nanochat", "qwen"])
    p.add_argument("phase", choices=["pilot", "train", "eval"])
    p.add_argument("run_id")
    p.add_argument("--parent-run")
    args = p.parse_args()
    print(json.dumps({"status": "dry_run_no_gpu_requested", "budget": read_json(ROOT / "training/config.json")["budget"],
                      "frozen_data": release_manifest(ROOT / "data/training"),
                      "commands": build_plan(args.family, args.phase, args.run_id, parent_run=args.parent_run)}, indent=2))
