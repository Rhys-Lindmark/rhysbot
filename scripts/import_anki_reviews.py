"""Validate and merge a completed semantic review shard; never publishes Git.

Call only after a reviewer has finished reading and the manager has accepted
that work. These consistency checks do not perform or prove semantic review.
Raw cards and private withholding reasons never enter the approval manifest.
"""
import argparse
import json
from pathlib import Path

from corpus_lib import digest, privacy_flags, read_jsonl
from ingest_local import assert_private_output


RATIONALE = ('Read complete question, answer, and citation; '
             'general educational content without private personal details.')
PUBLIC_KEYS = {'id', 'content_sha256', 'decision', 'reviewer', 'reviewed_at', 'scope', 'rationale'}


def unique(rows, label):
    result = {}
    for row in rows:
        if row['id'] in result:
            raise ValueError('Duplicate ID in ' + label)
        result[row['id']] = row
    return result


def validate_shard(shard):
    shard = Path(shard).resolve()
    if not shard.is_dir():
        raise ValueError('Missing private review shard')
    assert_private_output(shard)
    assignment = json.loads((shard / 'assignment.json').read_text())
    assigned_file = shard / 'assigned.jsonl'
    if digest(assigned_file.read_text()) != assignment['assigned_sha256']:
        raise ValueError('Assigned source file changed')
    assigned = unique(read_jsonl(assigned_file), 'assignment')
    decisions = unique(read_jsonl(shard / 'decisions.jsonl'), 'decisions')
    approvals = unique(read_jsonl(shard / 'approvals.jsonl'), 'approvals')
    if (len(assigned) != assignment['records'] or not assigned
            or assignment['queue_end_exclusive'] - assignment['queue_start_inclusive'] != len(assigned)
            or set(assigned) != set(decisions)):
        raise ValueError('Incomplete review or assignment coverage mismatch')
    approved = {}
    for identifier, raw in assigned.items():
        decision = decisions[identifier]
        content_hash = digest(raw['text'])
        if raw['content_sha256'] != content_hash or decision['content_sha256'] != content_hash:
            raise ValueError('Source or decision content hash mismatch')
        if decision.get('all_front_back_citation_read') is not True:
            raise ValueError('Missing full-reading review assertion')
        if decision['decision'] not in {'approve', 'withhold'}:
            raise ValueError('Unknown review decision')
        if decision['decision'] == 'withhold':
            continue
        if raw.get('privacy_flags') or privacy_flags(raw['text'], local=True):
            raise ValueError('Flagged record cannot receive a release approval')
        entry = approvals.get(identifier)
        if not entry or set(entry) != PUBLIC_KEYS:
            raise ValueError('Missing approval or unexpected public metadata')
        expected = {'id': identifier, 'content_sha256': content_hash, 'decision': 'approve',
                    'reviewer': 'codex-semantic-review', 'reviewed_at': '2026-10-18',
                    'scope': 'privacy_only_not_factual_validation', 'rationale': RATIONALE}
        if entry != expected:
            raise ValueError('Approval metadata mismatch')
        approved[identifier] = expected
    if set(approvals) != set(approved):
        raise ValueError('Approval/decision disagreement')
    return assigned, approved


def merge(shard, manifest):
    assigned, approved = validate_shard(shard)
    manifest = Path(manifest)
    previous = unique(read_jsonl(manifest), 'existing approval manifest')
    for identifier in set(assigned) & set(previous):
        if identifier not in approved or previous[identifier] != approved[identifier]:
            raise ValueError('Existing approval conflicts with reviewed decision')
    added = [entry for identifier, entry in approved.items() if identifier not in previous]
    # Validate the entire shard before the first mutation. Repeated imports
    # are idempotent; manager serializes merges after all writers have stopped.
    with manifest.open('a') as handle:
        for entry in added:
            handle.write(json.dumps(entry, sort_keys=True) + '\n')
    return {'reviewed': len(assigned), 'approved': len(approved),
            'withheld': len(assigned) - len(approved), 'new_approvals': len(added)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('shard', type=Path)
    args = parser.parse_args()
    manifest = Path(__file__).resolve().parents[1] / 'data/anki-review-approvals.jsonl'
    print(json.dumps(merge(args.shard, manifest)))
