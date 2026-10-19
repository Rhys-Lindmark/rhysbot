"""Publish only screened public excerpts and hash-bound, semantically approved Anki.

This command does not push Git. Run validate_corpus.py before committing.
"""
import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from corpus_lib import clean_text, digest, group_split, privacy_flags, read_jsonl, write_jsonl


def chunks(text, max_words=350):
    """Non-overlapping chunks; no excerpts cross original document boundaries."""
    words = text.split()
    for start in range(0, len(words), max_words):
        yield ' '.join(words[start:start + max_words])


def approve_anki(record, approvals):
    entry = approvals.get(record['id'])
    return bool(entry and entry.get('decision') == 'approve'
                and entry.get('content_sha256') == digest(record['text'])
                and not privacy_flags(record['text'], local=True)
                and not record.get('privacy_flags'))


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
    rows, counts = [], defaultdict(Counter)
    flags_by_source = defaultdict(Counter)
    inputs = [private_root/'local/anki-candidates.jsonl', private_root/'local/x-candidates.jsonl']
    inputs += sorted((private_root/'public-source-candidates').glob('*.jsonl'))
    allowed_names = {'anki-candidates.jsonl','x-candidates.jsonl','hee.jsonl','substack.jsonl','youtube.jsonl','linked-articles.jsonl','linked-writing.jsonl'}
    for path in inputs:
        if path.name not in allowed_names or not path.exists():
            continue
        for raw in read_jsonl(path):
            kind = raw['source_type']
            counts[kind]['input_documents'] += 1
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
            emitted = 0
            for n, text in enumerate(segments):
                flags = privacy_flags(text, local=(kind == 'anki'))
                if flags:
                    counts[kind]['withheld_flagged_excerpts'] += 1
                    flags_by_source[kind].update(flags)
                    continue
                if len(text.split()) < 5:
                    counts[kind]['withheld_low_text_excerpts'] += 1
                    continue
                row = {k: raw.get(k) for k in ('source_type','source_url','title','published_at','authorship','source_group_id')}
                row['source_group_id'] = str(row['source_group_id']).rstrip('/')
                row['id'] = raw['id'] if kind == 'anki' else raw['id'] + ':chunk-' + str(n)
                row['document_id'] = raw['id']
                row['text'] = text
                row['content_sha256'] = digest(text)
                row['privacy_status'] = status
                row['acquisition'] = raw.get('acquisition')
                row['excerpt_index'] = n
                row['excerpt_count_before_filtering'] = len(segments)
                row['linked_own_articles'] = raw.get('acquisition', {}).get('linked_own_articles', []) if isinstance(raw.get('acquisition'), dict) else []
                if kind == 'anki':
                    row.update(front=raw['front'], back=raw['back'], citation=raw['citation'])
                    row['review'] = {'reviewer': approvals[raw['id']]['reviewer'], 'reviewed_at': approvals[raw['id']]['reviewed_at'], 'scope': 'privacy_only_not_factual_validation'}
                rows.append(row)
                emitted += 1
            counts[kind]['documents_with_public_excerpts'] += bool(emitted)
            counts[kind]['public_excerpts_before_dedup'] += emitted
    rows, dedup = deduplicate_and_split(rows)
    rows.sort(key=lambda r:r['id'])
    output.mkdir(parents=True, exist_ok=True)
    write_jsonl(output/'corpus.jsonl', rows)
    for split in ('train','validation','test'):
        write_jsonl(output/(split+'.jsonl'), (r for r in rows if r['split'] == split))
    summary = {
        'release_date': '2026-10-18', 'status': 'partial_reviewed_release',
        'records': len(rows), 'unique_documents': len({r['document_id'] for r in rows}),
        'words': sum(len(r['text'].split()) for r in rows),
        'characters': sum(len(r['text']) for r in rows),
        'tokens': None, 'token_count_note': 'Not yet tokenized. Words and characters are exact; no token estimate is presented as a measurement.',
        'by_source': dict(Counter(r['source_type'] for r in rows)),
        'by_split': dict(Counter(r['split'] for r in rows)),
        'source_processing': dict(counts), 'exclusion_flags_aggregate': dict(flags_by_source),
        'deduplication': dedup,
        'privacy': {'anki_requires_hash_bound_semantic_approval': True, 'public_sources': 'automated conservative excerpt screening; not exhaustive semantic audit', 'sensitive_raw_inputs_uploaded': False},
    }
    (output/'stats.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False)+'\n')
    write_jsonl(output/'sources.jsonl', ({k:r[k] for k in ('id','document_id','source_type','source_url','title','published_at','authorship','content_sha256','split','split_group_id','privacy_status')} for r in rows))
    print(json.dumps({k:summary[k] for k in ('records','unique_documents','words','by_source','by_split')}, indent=2))
    return rows, summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-root', required=True, type=Path)
    parser.add_argument('--out', type=Path, default=Path(__file__).resolve().parents[1]/'data/public')
    args = parser.parse_args()
    build(args.private_root, args.out)
