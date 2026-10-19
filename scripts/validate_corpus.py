"""Fail closed on unapproved Anki, PII flags, hash changes, or split leakage."""
import json
import hashlib
from pathlib import Path
from corpus_lib import anki_release_approved, digest, privacy_flags, read_jsonl
from build_corpus import collection, corpus_metrics, public_writing_reviews, public_excerpt_review, HEE_SPEAKER_ATTRIBUTION


def validate(root):
    root = Path(root)
    approvals = {r['id']:r for r in read_jsonl(root/'data/anki-review-approvals.jsonl')}
    corpus_path = root/'data/public/corpus.jsonl'
    reviews = public_writing_reviews(root/'data/public/public-writing-review-approvals.json')
    ids, hashes, groups, corpus_by_id = set(), set(), {}, {}
    anki_count = 0
    for row in read_jsonl(corpus_path):
        assert row['id'] not in ids, 'Duplicate ID'
        ids.add(row['id'])
        assert digest(row['text']) == row['content_sha256'], 'Content hash mismatch'
        if row['source_type'] != 'anki':
            flags = privacy_flags(row['text'])
            reviewed = public_excerpt_review(row, reviews)
            assert not flags or reviewed, 'Privacy flag in released text without exact public-writing review'
            if flags:
                assert row['privacy_status'] == 'public_source_exact_contextual_review', 'Missing exact public review label'
                assert row['public_writing_review']['resolved_flags'] == flags, 'Changed public review flags'
                assert all(row['public_writing_review'].get(key) == reviewed[key] for key in ('reviewer','reviewed_at')), 'Changed public review attribution'
            assert not privacy_flags(row['title']), 'Privacy flag in released title'
        assert row.get('collection') == collection(row['source_type']), 'Wrong collection label'
        assert 'private_note_id' not in row, 'Private metadata leaked'
        group = row['split_group_id']
        assert groups.setdefault(group,row['split']) == row['split'], 'Split-group leakage'
        h = digest(' '.join(row['text'].lower().split()))
        assert h not in hashes, 'Exact duplicate text'
        hashes.add(h)
        corpus_by_id[row['id']] = (h, row['split_group_id'], row['split'])
        if row['source_type'] == 'hee_transcript':
            assert row['authorship'] == 'rhys_lindmark_narration_owner_confirmed', 'Unverified HEE owner attribution'
            assert row.get('speaker_attribution') == HEE_SPEAKER_ATTRIBUTION, 'Changed HEE attribution evidence'
        if row['source_type'] == 'anki':
            anki_count += 1
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
    assert ids, 'Empty release'
    split_ids, split_count = set(), 0
    for split in ('train','validation','test'):
        for row in read_jsonl(root/('data/public/'+split+'.jsonl')):
            assert row['split'] == split, 'Wrong split file'
            split_ids.add(row['id'])
            split_count += 1
    assert split_ids == ids and split_count == len(ids), 'Split file mismatch'
    stats = json.loads((root/'data/public/stats.json').read_text())
    assert stats['records']==len(ids), 'Stale stats'
    metrics = corpus_metrics(read_jsonl(corpus_path))
    assert stats['collection_metrics'] == metrics['collection'], 'Stale collection metrics'
    assert stats['source_metrics'] == metrics['source'], 'Stale source metrics'
    assert stats['text_utf8_bytes'] == sum(value['text_utf8_bytes'] for value in metrics['source'].values()), 'Stale UTF-8 text bytes'
    assert stats['corpus_jsonl_bytes'] == (root/'data/public/corpus.jsonl').stat().st_size, 'Stale JSONL bytes'
    assert stats['corpus_jsonl_bytes'] < 64 * 1024 * 1024, 'Corpus publication limit exceeded'
    for name, size in stats['artifact_bytes'].items():
        assert (root/'data/public'/name).stat().st_size == size, 'Stale artifact byte count'
    cards_file = root/'data/public/anki-cards.jsonl'
    if 'retained_anki_cards_before_text_deduplication' in stats:
        assert cards_file.is_file(), 'Required complete Anki card registry missing'
    if cards_file.exists():
        card_ids = set()
        for card in read_jsonl(cards_file):
            assert card['id'] not in card_ids, 'Duplicate card ID'
            card_ids.add(card['id'])
            assert card['source_type'] == 'anki' and anki_release_approved(card, approvals.get(card['id'])), 'Unapproved card registry text'
            assert digest(card['text']) == card['content_sha256'], 'Changed card registry text'
            representative_hash, representative_group, representative_split = corpus_by_id[card['corpus_record_id']]
            assert digest(' '.join(card['text'].lower().split())) == representative_hash, 'Wrong card deduplication link'
            assert (card['split_group_id'], card['split']) == (representative_group, representative_split), 'Card split leakage'
        assert len(card_ids) == stats['retained_anki_cards_before_text_deduplication'], 'Stale card registry'
        assert card_ids == {key for key,value in approvals.items() if value.get('decision') == 'approve'}, 'Approved cards omitted from registry'
    if stats.get('tokens') is not None:
        sha = hashlib.sha256()
        with corpus_path.open('rb') as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b''):
                sha.update(block)
        assert stats['token_count_corpus_sha256'] == sha.hexdigest(), 'Stale token counts'
    print(json.dumps({'validated_records':len(ids),'split_groups':len(groups),'anki_approvals':anki_count,'status':'passed'}))


if __name__=='__main__':
    validate(Path(__file__).resolve().parents[1])
