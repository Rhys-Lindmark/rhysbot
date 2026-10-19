"""Explicit finite Modal jobs; no cron, endpoint, auto-retry or auto-serving.

Run via launch.sh after authentication and a clean committed training release.
Reservations and a distributed singleton lock precede every GPU invocation.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import re
import shutil

import modal

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from runtime import ROOT, read_json, write_json, release_manifest, verify_manifest, safe_run_id
from budget import initial_ledger, committed, reserve, finish, reconcile
from plan import build_plan

CONFIG = read_json(HERE / "config.json")
PINS = read_json(HERE / "upstream-pins.json")
REVISION = PINS["nanochat"]["revision"]
app = modal.App("rhysbot-training")
volume = modal.Volume.from_name("rhysbot-training-artifacts", create_if_missing=True)
singleton = modal.Dict.from_name("rhysbot-training-singleton", create_if_missing=True)
image = (modal.Image.debian_slim(python_version="3.12")
         .apt_install("git", "build-essential")
         .pip_install("torch==2.9.1", "transformers==" + PINS["package_versions_observed"]["transformers"],
                      "peft==" + PINS["package_versions_observed"]["peft"],
                      "accelerate==" + PINS["package_versions_observed"]["accelerate"],
                      "pyarrow==21.0.0", "rustbpe==0.1.0", "tiktoken==0.11.0",
                      "numpy==2.2.6", "psutil==7.1.0", "filelock==3.19.1", "wandb==0.21.3",
                      "kernels==0.11.7", "requests==2.32.5", "pyyaml==6.0.2")
         .run_commands("git init /opt/nanochat",
                       "git -C /opt/nanochat remote add origin https://github.com/karpathy/nanochat.git",
                       "git -C /opt/nanochat fetch --depth=1 origin " + REVISION,
                       "git -C /opt/nanochat checkout --detach FETCH_HEAD")
         .add_local_dir(str(HERE), remote_path="/rhysbot/training", copy=True)
         .add_local_dir(str(ROOT / "data/training"), remote_path="/rhysbot/data/training", copy=True))


@app.function(image=image, gpu="A100-80GB", cpu=(2, 4), memory=(16384, 32768),
              timeout=900, retries=0, max_containers=1, min_containers=0,
              scaledown_window=2, volumes={"/artifacts": volume})
def gpu_job(family: str, phase: str, run_id: str, frozen: dict,
            timeout_seconds: int, parent_run: str | None, release_commit: str):
    """Called only by the budget coordinator; direct calls fail the lock check."""
    if singleton.get("active") != run_id:
        raise ValueError("No active budget reservation owns this job")
    sys.path.insert(0, "/rhysbot/training")
    from runtime import verify_manifest, read_json, write_json
    from plan import build_plan
    verify_manifest(Path("/rhysbot/data/training"), frozen)
    run_root = Path("/artifacts/runs") / run_id
    if run_root.exists():
        raise ValueError("Run output already exists; never overwrite a checkpoint run")
    run_root.mkdir(parents=True)
    os.environ["HF_HOME"] = "/artifacts/rhysbot-hf-cache"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    os.environ["OMP_NUM_THREADS"] = "2"
    os.environ["WANDB_MODE"] = "disabled"
    started = time.monotonic()
    metadata = {"family": family, "phase": phase, "run_id": run_id,
                "release_commit": release_commit, "frozen_data": frozen, "pins": PINS,
                "status": "running_no_trained_claim", "commands": []}
    write_json(run_root / "launch.json", metadata)
    write_json(run_root / "installed-packages.json", subprocess.check_output(
        [sys.executable, "-m", "pip", "freeze"], text=True).splitlines())
    try:
        for index, command in enumerate(build_plan(family, phase, run_id, parent_run=parent_run)):
            # Leave a minute to flush checkpoints/metadata before provider timeout.
            remaining = timeout_seconds - (time.monotonic() - started) - 60
            if remaining < 5:
                raise TimeoutError("No time remains within reserved GPU budget")
            with (run_root / f"stage-{index:02d}.log").open("w") as log:
                subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                               check=True, timeout=remaining, cwd="/opt/nanochat")
            metadata["commands"].append({"command": command, "status": "completed"})
            log_text = (run_root / f"stage-{index:02d}.log").read_text()
            speeds = re.findall(r"tok/sec:\s*([\d,]+)", log_text)
            peaks = re.findall(r"Peak memory usage:\s*([\d.]+)MiB", log_text)
            metadata["commands"][-1]["observed_nanochat_metrics"] = {
                "tokens_per_second_samples": [int(value.replace(",", "")) for value in speeds],
                "peak_cuda_allocated_mib": float(peaks[-1]) if peaks else None}
            write_json(run_root / "launch.json", metadata)
            volume.commit()
        if phase == "eval":
            evidence = {"evaluation_completed": True}
        elif family == "qwen":
            evidence = read_json(run_root / "qwen/sft/run.json")
            if not (run_root / "qwen/sft/adapter_model.safetensors").exists():
                raise ValueError("No trained Qwen adapter artifact")
        else:
            checkpoint = run_root / "nanochat/chatsft_checkpoints/rhysbot"
            metas = sorted(checkpoint.glob("meta_*.json"))
            weights = sorted(checkpoint.glob("model_*.pt"))
            if not metas or not weights:
                raise ValueError("No trained nanochat SFT checkpoint")
            meta = read_json(metas[-1])
            evidence = {"optimizer_steps": meta["step"], "trained_checkpoint": str(weights[-1])}
            if meta["step"] < 1:
                raise ValueError("No completed nanochat optimizer update")
        metadata.update(status="completed_actual_artifacts", elapsed_seconds=time.monotonic() - started)
        write_json(run_root / "launch.json", metadata)
        return evidence
    except BaseException as error:
        metadata.update(status="failed_no_success_claim", error=str(error), elapsed_seconds=time.monotonic() - started)
        write_json(run_root / "launch.json", metadata)
        raise
    finally:
        # Downloaded base weights are reproducible cache, not our checkpoints.
        # Reuse throughout this job, then retain only trained outputs/logs.
        cache = Path("/artifacts/rhysbot-hf-cache")
        if cache.is_dir() and not cache.is_symlink():
            shutil.rmtree(cache)
        volume.commit()


@app.function(image=image, cpu=0.25, memory=512, timeout=7800, retries=0,
              max_containers=1, min_containers=0, scaledown_window=2,
              volumes={"/artifacts": volume})
def coordinate(family: str, phase: str, run_id: str, frozen: dict,
               timeout_seconds: int, parent_run: str | None, release_commit: str):
    if not singleton.put("active", run_id, skip_if_exists=True):
        raise ValueError("Another Rhysbot job owns the singleton; no concurrent GPU launch")
    reserved = False
    try:
        volume.reload()
        ledger_path = Path("/artifacts/spend-ledger.json")
        ledger = read_json(ledger_path) if ledger_path.exists() else initial_ledger()
        if parent_run:
            prior = read_json(Path("/artifacts/runs") / safe_run_id(parent_run) / "launch.json")
            if prior["family"] != family or prior["status"] != "completed_actual_artifacts" or prior["frozen_data"] != frozen:
                raise ValueError("Parent checkpoint family, completion or frozen data mismatch")
        amount = reserve(ledger, run_id, family, phase, timeout_seconds, CONFIG["budget"])
        write_json(ledger_path, ledger)
        volume.commit()
        reserved = True
        try:
            evidence = gpu_job.with_options(timeout=timeout_seconds).remote(
                family, phase, run_id, frozen, timeout_seconds, parent_run, release_commit)
            volume.reload()
            ledger = read_json(ledger_path)
            finish(ledger, run_id, True, evidence)
        except BaseException as error:
            volume.reload()
            ledger = read_json(ledger_path)
            finish(ledger, run_id, False, {"error": str(error)})
            write_json(ledger_path, ledger)
            volume.commit()
            raise
        write_json(ledger_path, ledger)
        volume.commit()
        return {"run_id": run_id, "reserved_usd": amount, "committed_usd": committed(ledger),
                "status": ledger["jobs"][run_id]["status"], "evidence": evidence}
    finally:
        # Hard coordinator death leaves a persistent active ledger/lock, failing
        # closed. Clear only this run's lock after an observed terminal call.
        if singleton.get("active") == run_id:
            singleton.pop("active")


@app.function(image=image, cpu=0.25, memory=512, timeout=60, retries=0,
              volumes={"/artifacts": volume})
def billing_receipt(run_id: str, actual_usd: float, receipt: str):
    token = "billing-" + run_id
    if not singleton.put("active", token, skip_if_exists=True):
        raise ValueError("A training/billing operation is active")
    try:
        volume.reload()
        path = Path("/artifacts/spend-ledger.json")
        ledger = read_json(path)
        reconcile(ledger, run_id, actual_usd, receipt)
        write_json(path, ledger)
        volume.commit()
        return {"committed_usd": committed(ledger), "remaining_usd": 200 - committed(ledger)}
    finally:
        singleton.pop("active")


@app.local_entrypoint()
def main(family: str = "nanochat", phase: str = "pilot", run_id: str = "",
         timeout_seconds: int = 900, parent_run: str = "", reconcile_usd: float = -1,
         receipt: str = ""):
    safe_run_id(run_id)
    if reconcile_usd >= 0:
        print(json.dumps(billing_receipt.remote(run_id, reconcile_usd, receipt), indent=2))
        return
    frozen = release_manifest(ROOT / "data/training")
    release = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    # Never describe uncommitted bytes as a released Git checkpoint.
    status = subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain", "--", "training", "data/training"], text=True)
    if status.strip():
        raise ValueError("Commit the reviewed training code/data release before remote execution")
    build_plan(family, phase, run_id, parent_run=parent_run or None)
    print(json.dumps(coordinate.remote(family, phase, run_id, frozen, timeout_seconds,
                                      parent_run or None, release), indent=2))
