"""Fail closed on unapproved Anki, PII flags, hash changes, or split leakage."""
import json
from pathlib import Path
from corpus_lib import digest, privacy_flags, read_jsonl


def validate(root):
    root = Path(root)
    approvals = {r['id']:r for r in read_jsonl(root/'data/anki-review-approvals.jsonl')}
    rows = list(read_jsonl(root/'data/public/corpus.jsonl'))
    assert rows, 'Empty release'
    ids, hashes, groups = set(), set(), {}
    for row in rows:
        assert row['id'] not in ids, 'Duplicate ID'
        ids.add(row['id'])
        assert digest(row['text']) == row['content_sha256'], 'Content hash mismatch'
        assert not privacy_flags(row['text'], local=row['source_type']=='anki'), 'Privacy flag in released text'
        assert not privacy_flags(row['title']), 'Privacy flag in released title'
        assert 'private_note_id' not in row, 'Private metadata leaked'
        group = row['split_group_id']
        assert groups.setdefault(group,row['split']) == row['split'], 'Split-group leakage'
        h = digest(' '.join(row['text'].lower().split()))
        assert h not in hashes, 'Exact duplicate text'
        hashes.add(h)
        if row['source_type'] == 'anki':
            approval = approvals[row['id']]
            assert approval['content_sha256'] == row['content_sha256'] and approval['decision']=='approve', 'Anki approval missing or stale'
            assert row['privacy_status']=='semantic_review_approved', 'Anki review bypass'
        else:
            assert row['source_url'].startswith(('https://','http://')), 'Missing public provenance'
            if row['source_type'] == 'substack':
                assert row.get('acquisition', {}).get('coverage') == 'full_public_body', 'Restricted or unverified Substack body'
    split_ids = []
    for split in ('train','validation','test'):
        selected = list(read_jsonl(root/('data/public/'+split+'.jsonl')))
        assert all(r['split']==split for r in selected), 'Wrong split file'
        split_ids.extend(r['id'] for r in selected)
    assert set(split_ids)==ids and len(split_ids)==len(ids), 'Split file mismatch'
    stats = json.loads((root/'data/public/stats.json').read_text())
    assert stats['records']==len(rows), 'Stale stats'
    if stats.get('tokens') is not None:
        assert stats['token_count_corpus_sha256']==digest((root/'data/public/corpus.jsonl').read_text()), 'Stale token counts'
    print(json.dumps({'validated_records':len(rows),'split_groups':len(groups),'anki_approvals':sum(r['source_type']=='anki' for r in rows),'status':'passed'}))


if __name__=='__main__':
    validate(Path(__file__).resolve().parents[1])
