"""Conservative cumulative compute reservations; no automatic refunds/retries.

Use under one remote coordinator lock. Unreconciled and failed jobs continue to
consume their full reservation; provider invoices, not elapsed-time estimates,
are required to release the difference. Ledger never stores credentials.
"""
import argparse
import json
import math
from pathlib import Path
import time
from runtime import read_json, write_json, safe_run_id

CAP = 200.0


def initial_ledger():
    return {"authorization_usd": CAP, "authorization_date": "2026-10-18",
            "initial_spent_usd": 0, "jobs": {}, "pilot_passed": {}}


def committed(ledger):
    amounts = [float(ledger["initial_spent_usd"])] + [
        float(job.get("actual_usd", job["reserved_usd"])) for job in ledger["jobs"].values()]
    if any(not math.isfinite(amount) or amount < 0 for amount in amounts):
        raise ValueError("Invalid cumulative spend ledger")
    return sum(amounts)


def reserve(ledger, run_id, family, phase, timeout, config):
    safe_run_id(run_id)
    if ledger.get("blocked_reason"):
        raise ValueError(ledger["blocked_reason"])
    if ledger.get("authorization_usd") != CAP or config["authorization_usd"] != CAP:
        raise ValueError("Authorization mismatch; cannot raise the $200 cap")
    if run_id in ledger["jobs"]:
        raise ValueError("Run ID already exists; retries require a new reservation")
    if any(job["status"] == "active" for job in ledger["jobs"].values()):
        raise ValueError("Another GPU job is active or not yet reconciled after interruption")
    if family not in {"nanochat", "qwen"} or phase not in {"pilot", "train", "eval"}:
        raise ValueError("Unsupported family or phase")
    if timeout < 1 or timeout > config["max_job_timeout_seconds"]:
        raise ValueError("Invalid job timeout")
    if config["conservative_rate_usd_per_hour"] < 12 or config["billing_overhead_seconds"] < 180:
        raise ValueError("Do not reduce the conservative billing safety margin")
    ceiling = (timeout + config["billing_overhead_seconds"]) * config["conservative_rate_usd_per_hour"] / 3600
    if phase == "pilot":
        if timeout > config["pilot_timeout_seconds"]:
            raise ValueError("Pilot may not exceed 15 minutes")
        amount = float(config["pilot_reservation_usd"])
        if not 0 < amount <= 5 or amount < ceiling:
            raise ValueError("Pilot reservation must cover its ceiling and may not exceed $5")
    else:
        if not ledger["pilot_passed"].get(family):
            raise ValueError("A successful bounded pilot is required for this model family")
        amount = math.ceil(ceiling * 100) / 100
    if committed(ledger) + amount > CAP:
        raise ValueError("Cumulative $200 compute cap would be exceeded")
    ledger["jobs"][run_id] = {"family": family, "phase": phase,
                              "reserved_usd": amount, "timeout_seconds": timeout,
                              "status": "active", "created_at": time.time()}
    return amount


def finish(ledger, run_id, success, evidence=None):
    job = ledger["jobs"][run_id]
    job["status"] = "completed_pending_billing" if success else "failed_pending_billing"
    job["evidence"] = evidence
    if success and job["phase"] == "pilot":
        if not evidence or not evidence.get("trained_checkpoint") or evidence.get("optimizer_steps", 0) < 1:
            raise ValueError("Pilot needs a trained checkpoint and real optimizer steps")
        ledger["pilot_passed"][job["family"]] = run_id


def reconcile(ledger, run_id, actual_usd, receipt):
    if actual_usd < 0 or not math.isfinite(actual_usd) or not receipt.strip():
        raise ValueError("A nonnegative provider bill and receipt reference are required")
    job = ledger["jobs"][run_id]
    if job["status"] == "active":
        raise ValueError("Confirm the remote job stopped before reconciling")
    job.update(actual_usd=actual_usd, receipt=receipt)
    if committed(ledger) > CAP:
        ledger["blocked_reason"] = "Actual billing exceeded authorization; all new jobs are blocked"


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("ledger", type=Path)
    args = p.parse_args()
    ledger = read_json(args.ledger) if args.ledger.exists() else initial_ledger()
    print(json.dumps({"authorization_usd": CAP, "committed_usd": committed(ledger), "ledger": ledger}, indent=2))
