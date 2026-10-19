"""Small stdlib-only utilities; importing this file never loads torch/models."""
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(Path(path).read_text())


def rows(path):
    with Path(path).open() as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temp.replace(path)


def file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def release_manifest(data):
    data = Path(data)
    files = {}
    split_groups = {}
    counts = {}
    for kind in ("pretrain", "sft"):
        for split in ("train", "validation", "test"):
            path = data / f"{kind}-{split}.jsonl"
            seen = 0
            for row in rows(path):
                if row["split"] != split:
                    raise ValueError(f"Incorrect split label in {path.name}")
                group = row["split_group_id"]
                if group in split_groups and split_groups[group] != split:
                    raise ValueError("Source group crosses training/validation/test boundaries")
                split_groups[group] = split
                seen += 1
            if not seen:
                raise ValueError(f"Empty required dataset: {path.name}")
            counts[path.name] = seen
            files[path.name] = file_sha(path)
    return {"files_sha256": files, "counts": counts,
            "source_manifest": read_json(data / "manifest.json")}


def verify_manifest(data, frozen):
    actual = release_manifest(data)
    if actual != frozen:
        raise ValueError("Training data differ from the frozen launch manifest")


def git_revision(path):
    return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()


def run_metadata(family, stage, data, config, pins):
    return {"family": family, "stage": stage,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "config": config, "pins": pins, "data": release_manifest(data),
            "status": "running_no_quality_claim"}


def safe_run_id(value):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,63}", value):
        raise ValueError("run ID must be 3–64 lowercase letters, numbers, underscores or hyphens")
    return value


def metrics(nats, tokens, nbytes):
    if tokens < 1 or nbytes < 1 or not math.isfinite(nats):
        raise ValueError("No finite held-out loss observations")
    return {"nats": nats, "tokens": tokens, "utf8_bytes": nbytes,
            "nll_per_token": nats / tokens,
            "perplexity": math.exp(min(nats / tokens, 80)),
            "bits_per_byte": nats / (math.log(2) * nbytes)}
