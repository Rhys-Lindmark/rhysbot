"""Finite operator repair for a legacy inference lacking a provider call ID.

Not a web endpoint. Requires an exact token and two recorded zero-work provider
observations >=30 seconds apart; rechecks the provider inside the repair. Keeps
the entire paid-compute reservation pending billing. Never retries inference.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
import re
import modal

app = modal.App('rhysbot-legacy-repair')
volume = modal.Volume.from_name('rhysbot-training-artifacts')
image = modal.Image.debian_slim(python_version='3.12')


def verified_observations(token, observations):
    if not re.fullmatch(r'serve-[a-f0-9]{32}', token):
        raise ValueError('Exact legacy serving token required')
    if len(observations) < 2:
        raise ValueError('Two independent provider observations required')
    stamps = []
    for obs in observations:
        if obs['active_lock'] != token or any(obs[key] != 0 for key in (
                'backlog', 'num_total_runners', 'num_running_inputs')):
            raise ValueError('Provider must show zero work for this exact lock')
        stamps.append(datetime.fromisoformat(obs['observed_at']).timestamp())
    if max(stamps) - min(stamps) < 30:
        raise ValueError('Observations must be separated by at least 30 seconds')


@app.function(image=image, cpu=0.25, memory=256, timeout=60, retries=0,
              volumes={'/artifacts': volume})
def repair(token, observations):
    verified_observations(token, observations)
    singleton = modal.Dict.from_name('rhysbot-training-singleton')
    if singleton.get('active') != token:
        raise ValueError('Lock owner changed; no mutation')
    infer = modal.Function.from_name('rhysbot-serving', 'infer')
    stats = infer.get_current_stats()
    current = {key: getattr(stats, key) for key in ('backlog', 'num_total_runners', 'num_running_inputs')}
    if any(current.values()):
        raise ValueError('Provider has work; no mutation')
    volume.reload()
    path = Path('/artifacts/spend-ledger.json')
    ledger = json.loads(path.read_text())
    job = ledger['jobs'][token]
    if (job.get('status') != 'active' or job.get('phase') != 'inference'
            or job.get('provider_call_id') or singleton.get('active') != token):
        raise ValueError('Not the reviewed untracked legacy inference')
    now = datetime.now(timezone.utc).isoformat()
    if datetime.now(timezone.utc).timestamp() - job['created_at'] < job['timeout_seconds'] + 60:
        raise ValueError('Execution deadline has not passed')
    # No other inference can start while this exact distributed owner remains.
    job.update(status='failed_pending_billing', coordination_state='operator_confirmed_provider_idle',
               evidence={'operator_repair_at': now, 'provider_observations': observations,
                         'final_provider_stats': current, 'reservation_retained': True})
    temporary = path.with_suffix('.repair.tmp')
    temporary.write_text(json.dumps(ledger, indent=2, sort_keys=True) + '\n')
    temporary.replace(path)
    volume.commit()
    if singleton.get('active') != token:
        raise ValueError('Lock changed after commit; operator must inspect')
    singleton.pop('active')
    return {'repaired': token, 'status': job['status'], 'reserved_usd': job['reserved_usd'],
            'reservation_retained': True}


@app.local_entrypoint()
def main(token: str, observations_file: str):
    observations = json.loads(Path(observations_file).read_text())['observations']
    verified_observations(token, observations)
    print(json.dumps(repair.remote(token, observations), indent=2))
