"""Fail closed on unapproved Anki, PII flags, hash changes, or split leakage."""
import json
from pathlib import Path
from corpus_lib import anki_release_approved, digest, privacy_flags, read_jsonl


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
        if row['source_type'] != 'anki':
            assert not privacy_flags(row['text']), 'Privacy flag in released text'
            assert not privacy_flags(row['title']), 'Privacy flag in released title'
        assert 'private_note_id' not in row, 'Private metadata leaked'
        group = row['split_group_id']
        assert groups.setdefault(group,row['split']) == row['split'], 'Split-group leakage'
        h = digest(' '.join(row['text'].lower().split()))
        assert h not in hashes, 'Exact duplicate text'
        hashes.add(h)
        if row['source_type'] == 'anki':
            approval = approvals[row['id']]
            assert row['title'] == (row['front'] or 'Study note without a text question'), 'Anki title differs from reviewed question'
            assert anki_release_approved(row, approval), 'Anki contextual privacy approval missing or stale'
            if approval.get('contextual_privacy_review'):
                assert row.get('review', {}).get('contextual_privacy_review') is True, 'Missing contextual review label'
                assert row['review'].get('resolved_automatic_flags') == approval['approved_privacy_flags'], 'Changed contextual flag evidence'
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
    cards_file = root/'data/public/anki-cards.jsonl'
    if 'retained_anki_cards_before_text_deduplication' in stats:
        assert cards_file.is_file(), 'Required complete Anki card registry missing'
    if cards_file.exists():
        cards = list(read_jsonl(cards_file))
        assert len(cards) == stats['retained_anki_cards_before_text_deduplication'], 'Stale card registry'
        assert len({r['id'] for r in cards}) == len(cards), 'Duplicate card ID'
        assert {r['id'] for r in cards} == {key for key, value in approvals.items() if value.get('decision') == 'approve'}, 'Approved cards omitted from registry'
        corpus_by_id = {r['id']: r for r in rows}
        for card in cards:
            assert card['source_type'] == 'anki' and anki_release_approved(card, approvals.get(card['id'])), 'Unapproved card registry text'
            assert digest(card['text']) == card['content_sha256'], 'Changed card registry text'
            representative = corpus_by_id[card['corpus_record_id']]
            assert ' '.join(card['text'].lower().split()) == ' '.join(representative['text'].lower().split()), 'Wrong card deduplication link'
            assert (card['split_group_id'], card['split']) == (representative['split_group_id'], representative['split']), 'Card split leakage'
    if stats.get('tokens') is not None:
        assert stats['token_count_corpus_sha256']==digest((root/'data/public/corpus.jsonl').read_text()), 'Stale token counts'
    print(json.dumps({'validated_records':len(rows),'split_groups':len(groups),'anki_approvals':sum(r['source_type']=='anki' for r in rows),'status':'passed'}))


if __name__=='__main__':
    validate(Path(__file__).resolve().parents[1])
