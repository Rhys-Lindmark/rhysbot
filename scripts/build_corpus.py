"""Publish only screened public excerpts and hash-bound, semantically approved Anki.

This command does not push Git. Run validate_corpus.py before committing.
"""
import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from corpus_lib import anki_release_approved, clean_text, digest, group_split, privacy_flags, read_jsonl, write_jsonl


COLLECTIONS = {'substack': 'writing', 'linked_writing': 'writing',
               'youtube_transcript': 'podcasts_youtube', 'podcast_transcript': 'podcasts_youtube',
               'hee_transcript': 'hee', 'anki': 'anki', 'x': 'x'}


def collection(source_type):
    if source_type not in COLLECTIONS:
        raise ValueError('Unknown source type requires an explicit collection')
    return COLLECTIONS[source_type]


HEE_SPEAKER_ATTRIBUTION = {
    'speaker': 'Rhys Lindmark',
    'basis': 'owner_confirmed_all_HEE_narration_2026-10-19',
    'method': 'owner_confirmation_not_acoustic_diarization',
    'quoted_material_may_be_other_authors': True,
}


def enrich_collection(row):
    row['collection'] = collection(row['source_type'])
    if row['source_type'] == 'hee_transcript':
        if row.get('authorship') != 'rhys_lindmark_narration_owner_confirmed':
            row['original_authorship'] = row.get('authorship')
        row['authorship'] = 'rhys_lindmark_narration_owner_confirmed'
        row['speaker_attribution'] = dict(HEE_SPEAKER_ATTRIBUTION)
    return row


def public_writing_reviews(path=None):
    path = Path(path) if path else Path(__file__).resolve().parents[1] / 'data/public/public-writing-review-approvals.json'
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if data.get('schema_version') != 1:
        raise ValueError('Unsupported public-writing review schema')
    reviews = {}
    for item in data['documents']:
        key = (item['source_url'], item['document_id'])
        if key in reviews:
            raise ValueError('Duplicate public-writing review')
        reviews[key] = item
    return reviews


def public_excerpt_review(row, reviews, document_sha=None):
    """Validate exact persisted judgments; never approve from topic or source alone."""
    flags = privacy_flags(row['text'])
    entry = reviews.get((row.get('source_url'), row.get('document_id', row.get('id'))), {})
    metadata = row.get('public_writing_review') or {}
    expected_sha = document_sha or metadata.get('document_content_sha256')
    if (row.get('source_type') != 'linked_writing' or not flags
            or not set(flags) <= {'family', 'street_address'}
            or entry.get('source_type') != 'linked_writing'
            or entry.get('document_content_sha256') != expected_sha
            or entry.get('reviewer') != 'codex-public-writing-contextual-review'
            or entry.get('scope') != 'privacy_only_not_factual_validation'):
        return None
    for approved in entry.get('excerpt_approvals', []):
        if approved.get('content_sha256') == digest(row['text']) and approved.get('flags') == flags:
            return entry
    return None


def corpus_metrics(rows):
    metrics, documents = {}, defaultdict(set)
    for row in rows:
        for level, key in [('collection', row['collection']), ('source', row['source_type'])]:
            group = metrics.setdefault(level, {}).setdefault(key, Counter())
            group['excerpts'] += 1
            group['words'] += len(row['text'].split())
            group['text_utf8_bytes'] += len(row['text'].encode('utf-8'))
            group['corpus_jsonl_bytes'] += len((json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n').encode('utf-8'))
            documents[(level, key)].add(row['document_id'])
    for level, groups in metrics.items():
        for key, group in groups.items():
            group['documents'] = len(documents[(level, key)])
    return metrics


def chunks(text, max_words=350):
    """Non-overlapping chunks; no excerpts cross original document boundaries."""
    words = text.split()
    for start in range(0, len(words), max_words):
        yield ' '.join(words[start:start + max_words])


def approve_anki(record, approvals):
    entry = approvals.get(record['id'])
    return anki_release_approved(record, entry)


def source_group(record):
    """Keep explicitly identified audio/video editions in one evaluation split.

    This relation is supplied by reviewed acquisition metadata; it does not
    assert identical audio or infer identity from similar episode titles.
    """
    if record['source_type'] == 'podcast_transcript':
        acquisition = record.get('acquisition')
        identifier = acquisition.get('corresponding_youtube_video_id') if isinstance(acquisition, dict) else None
        if identifier:
            if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}', identifier):
                raise ValueError('Invalid corresponding YouTube video ID in podcast metadata')
            return 'https://www.youtube.com/watch?v=' + identifier
        group = record.get('source_group_id') or record.get('source_url')
        if not group:
            raise ValueError('Podcast transcript requires public source grouping')
        return str(group).rstrip('/')
    return str(record.get('source_group_id')).rstrip('/')


class Groups:
    def __init__(self):
        self.parent = {}

    def root(self, key):
        self.parent.setdefault(key, key)
        while self.parent[key] != key:
            self.parent[key] = self.parent[self.parent[key]]
            key = self.parent[key]
        return key

    def union(self, a, b):
        x, y = sorted((self.root(a), self.root(b)))
        self.parent[y] = x


def deduplicate_and_split(rows):
    groups = Groups()
    exact = {}
    unique = []
    titles = {}
    indexes = defaultdict(list)
    shingle_sets = []
    near_matches = 0
    for row in rows:
        group = row['source_group_id']
        groups.root(group)
        title = re.sub(r'[^a-z0-9]+', '', row['title'].lower())
        if row['source_type'] not in ('anki', 'x') and len(title) >= 15:
            if title in titles:
                groups.union(group, titles[title])
            titles[title] = group
        for link in row.get('linked_own_articles', []):
            groups.union(group, link.rstrip('/'))
        normalized = re.sub(r'\s+', ' ', row['text'].lower()).strip()
        key = digest(normalized)
        if key in exact:
            previous = exact[key]
            groups.union(group, previous['source_group_id'])
            previous.setdefault('duplicate_source_urls', []).append(row['source_url'])
            continue
        words = normalized.split()
        shingles = {digest(' '.join(words[i:i+5]))[:12] for i in range(len(words)-4)} if len(words) >= 60 else set()
        # Shared minima cheaply find likely near-duplicates, then compare exact
        # shingle Jaccard. These are grouped together, not silently deleted.
        signature = sorted(shingles)[:8]
        candidates = set(i for s in signature for i in indexes[s])
        for i in candidates:
            other = shingle_sets[i]
            if other and len(shingles & other) / len(shingles | other) >= .8:
                groups.union(group, unique[i]['source_group_id'])
                near_matches += 1
        index = len(unique)
        for s in signature:
            indexes[s].append(index)
        shingle_sets.append(shingles)
        exact[key] = row
        unique.append(row)
    for row in unique:
        row['split_group_id'] = groups.root(row['source_group_id'])
        row['split'] = group_split(row['split_group_id'])
        row.pop('linked_own_articles', None)
    return unique, {'exact_duplicates_removed': len(rows)-len(unique), 'near_duplicate_pairs_grouped': near_matches, 'near_duplicate_threshold': .8}


def build(private_root, output):
    private_root, output = Path(private_root), Path(output)
    repo = Path(__file__).resolve().parents[1]
    approval_file = repo / 'data/anki-review-approvals.jsonl'
    approvals = {r['id']: r for r in read_jsonl(approval_file)}
    writing_reviews = public_writing_reviews()
    rows, counts = [], defaultdict(Counter)
    flags_by_source = defaultdict(Counter)
    inputs = [private_root/'local/anki-candidates.jsonl', private_root/'local/x-candidates.jsonl']
    inputs += sorted((private_root/'public-source-candidates').glob('*.jsonl'))
    allowed_names = {'anki-candidates.jsonl','x-candidates.jsonl','hee.jsonl','substack.jsonl','youtube.jsonl','podcasts.jsonl','linked-articles.jsonl','linked-writing.jsonl'}
    for path in inputs:
        if path.name not in allowed_names or not path.exists():
            continue
        # Candidate acquisition logs are append-only. Only the latest version
        # of a source document can define release eligibility.
        latest = {raw['id']: raw for raw in read_jsonl(path)}
        if path.name == 'anki-candidates.jsonl':
            overlay = private_root/'local/anki-release-overrides.jsonl'
            if overlay.exists():
                seen_overrides = set()
                for replacement in read_jsonl(overlay):
                    identifier = replacement['id']
                    if (identifier in seen_overrides or identifier not in latest
                            or replacement.get('original_content_sha256') != digest(latest[identifier]['text'])):
                        raise ValueError('Anki redaction overlay source mismatch')
                    seen_overrides.add(identifier)
                    latest[identifier] = replacement
        for raw in latest.values():
            kind = raw['source_type']
            counts[kind]['input_documents'] += 1
            if kind == 'substack' and raw.get('acquisition', {}).get('coverage') != 'full_public_body':
                counts[kind]['withheld_not_full_public_body'] += 1
                continue
            if kind == 'anki':
                if not approve_anki(raw, approvals):
                    counts[kind]['withheld_unapproved_or_flagged'] += 1
                    flags_by_source[kind].update(raw.get('privacy_flags', []))
                    continue
                segments = [raw['text']]
                status = 'semantic_review_approved'
            else:
                if privacy_flags(raw.get('title', '')):
                    counts[kind]['withheld_title_flagged'] += 1
                    continue
                if 'truncated_post' in raw.get('privacy_flags', []):
                    counts[kind]['withheld_truncated'] += 1
                    continue
                segments = list(chunks(clean_text(raw['text'])))
                status = 'public_source_automated_screen'
            document_sha = digest(clean_text(raw['text'])) if kind == 'linked_writing' else None
            emitted = 0
            for n, text in enumerate(segments):
                flags = privacy_flags(text, local=(kind == 'anki'))
                reviewed = public_excerpt_review(dict(raw, text=text), writing_reviews, document_sha) if flags and kind != 'anki' else None
                if flags and kind != 'anki' and not reviewed:
                    counts[kind]['withheld_flagged_excerpts'] += 1
                    flags_by_source[kind].update(flags)
                    continue
                if len(text.split()) < 5 and kind != 'anki':
                    counts[kind]['withheld_low_text_excerpts'] += 1
                    continue
                row = {k: raw.get(k) for k in ('source_type','source_url','title','published_at','authorship','source_group_id')}
                row['source_group_id'] = source_group(raw)
                enrich_collection(row)
                if kind == 'podcast_transcript' and not row['authorship']:
                    row['authorship'] = 'mixed_or_unverified_speakers'
                row['id'] = raw['id'] if kind == 'anki' else raw['id'] + ':chunk-' + str(n)
                row['document_id'] = raw['id']
                row['text'] = text
                row['content_sha256'] = digest(text)
                row['privacy_status'] = 'public_source_exact_contextual_review' if reviewed else status
                if reviewed:
                    row['public_writing_review'] = {key: reviewed[key] for key in ('document_content_sha256', 'reviewer', 'reviewed_at')}
                    row['public_writing_review']['resolved_flags'] = flags
                row['acquisition'] = raw.get('acquisition')
                row['excerpt_index'] = n
                row['excerpt_count_before_filtering'] = len(segments)
                row['linked_own_articles'] = raw.get('acquisition', {}).get('linked_own_articles', []) if isinstance(raw.get('acquisition'), dict) else []
                if kind == 'anki':
                    row.update(front=raw['front'], back=raw['back'], citation=raw['citation'])
                    row['title'] = raw['front'] or 'Study note without a text question'
                    row['review'] = {'reviewer': approvals[raw['id']]['reviewer'], 'reviewed_at': approvals[raw['id']]['reviewed_at'], 'scope': 'privacy_only_not_factual_validation'}
                    row['text_completeness'] = ('question_and_answer_text' if raw['front'].strip() and raw['back'].strip()
                                                else 'front_text_without_answer' if raw['front'].strip()
                                                else 'answer_text_without_question' if raw['back'].strip() else 'no_front_or_back_text')
                    row['source_has_images'] = 'image_dependent' in raw.get('privacy_flags', [])
                    if raw.get('privacy_redaction'):
                        row['privacy_redaction'] = raw['privacy_redaction']
                    row['review']['contextual_privacy_review'] = approvals[raw['id']].get('contextual_privacy_review', False)
                    if row['review']['contextual_privacy_review']:
                        row['review']['resolved_automatic_flags'] = approvals[raw['id']]['approved_privacy_flags']
                rows.append(row)
                emitted += 1
            counts[kind]['documents_with_public_excerpts'] += bool(emitted)
            counts[kind]['public_excerpts_before_dedup'] += emitted
    # Preserve every approved card, including duplicates and cards with no text
    # answer. The research corpus can still deduplicate identical text.
    anki_cards = [dict(row) for row in rows if row['source_type'] == 'anki']
    rows, dedup = deduplicate_and_split(rows)
    rows.sort(key=lambda r:r['id'])
    output.mkdir(parents=True, exist_ok=True)
    write_jsonl(output/'corpus.jsonl', rows)
    text_splits = {digest(' '.join(r['text'].lower().split())):
                   (r['split_group_id'], r['split'], r['id']) for r in rows}
    for card in anki_cards:
        card['split_group_id'], card['split'], card['corpus_record_id'] = text_splits[digest(' '.join(card['text'].lower().split()))]
        card.pop('linked_own_articles', None)
    write_jsonl(output/'anki-cards.jsonl', sorted(anki_cards, key=lambda r: r['id']))
    for split in ('train','validation','test'):
        write_jsonl(output/(split+'.jsonl'), (r for r in rows if r['split'] == split))
    metrics = corpus_metrics(rows)
    sources = ({k:r[k] for k in ('id','document_id','source_type','collection','source_url','title','published_at','authorship','content_sha256','split','split_group_id','privacy_status')} for r in rows)
    write_jsonl(output/'sources.jsonl', sources)
    jsonl_bytes = (output/'corpus.jsonl').stat().st_size
    if jsonl_bytes >= 64 * 1024 * 1024:
        raise ValueError('Corpus JSONL exceeds publication limit')
    assert sum(v['corpus_jsonl_bytes'] for v in metrics['source'].values()) == jsonl_bytes
    training_manifest = json.loads((repo/'data/training/manifest.json').read_text()) if (repo/'data/training/manifest.json').exists() else {}
    summary = {
        'release_date': '2026-10-19', 'status': 'partial_reviewed_release',
        'schema_version': 2,
        'records': len(rows),
        'text_utf8_bytes': sum(len(r['text'].encode('utf-8')) for r in rows),
        'corpus_jsonl_bytes': jsonl_bytes,
        'byte_measurement_note': 'text_utf8_bytes counts released text fields only; corpus_jsonl_bytes counts complete serialized corpus rows including keys, metadata and newline delimiters. No media bytes are included.',
        'collection_metrics': metrics['collection'], 'source_metrics': metrics['source'],
        'by_collection': dict(Counter(r['collection'] for r in rows)),
        'artifact_bytes': {name: (output/name).stat().st_size for name in ('corpus.jsonl','anki-cards.jsonl','train.jsonl','validation.jsonl','test.jsonl','sources.jsonl')},
        'frozen_model_training_input': {'source_corpus_sha256': training_manifest.get('source_corpus_sha256'),
                                        'corpus_release_commit': '1e20626c7b3b2b2bc3404fdb0b1fd39a93419b3d',
                                        'note': 'Historical model input remains frozen in data/training; later research-corpus enrichment is not a retraining claim.'}, 'unique_documents': len({r['document_id'] for r in rows}),
        'words': sum(len(r['text'].split()) for r in rows),
        'characters': sum(len(r['text']) for r in rows),
        'tokens': None, 'token_count_note': 'Not yet tokenized. Words and characters are exact; no token estimate is presented as a measurement.',
        'by_source': dict(Counter(r['source_type'] for r in rows)),
        'retained_anki_cards_before_text_deduplication': len(anki_cards),
        'by_split': dict(Counter(r['split'] for r in rows)),
        'source_processing': dict(counts), 'exclusion_flags_aggregate': dict(flags_by_source),
        'deduplication': dedup,
        'privacy': {'anki_requires_hash_bound_semantic_approval': True, 'public_sources': 'automated conservative excerpt screening; not exhaustive semantic audit', 'sensitive_raw_inputs_uploaded': False},
    }
    (output/'stats.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False)+'\n')
    print(json.dumps({k:summary[k] for k in ('records','unique_documents','words','by_source','by_split')}, indent=2))
    return rows, summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-root', required=True, type=Path)
    parser.add_argument('--out', type=Path, default=Path(__file__).resolve().parents[1]/'data/public')
    args = parser.parse_args()
    build(args.private_root, args.out)
