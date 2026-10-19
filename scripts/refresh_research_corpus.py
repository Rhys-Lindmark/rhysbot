"""Small-memory collection/linked-writing refresh of an already screened release.

Reads public corpus/card-registry text and public-article candidates only: never
raw Anki/X inputs. Existing unrelated screened text is preserved. No tokenization,
model loading, network, training-data regeneration, or publication occurs.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re

from build_corpus import (Groups, chunks, collection, public_writing_reviews,
                          public_excerpt_review, source_group, enrich_collection)
from corpus_lib import clean_text, digest, group_split, privacy_flags, read_jsonl


def normalized(text):
    return digest(' '.join(text.lower().split()))


def shingles(text):
    words = text.lower().split()
    return {digest(' '.join(words[i:i+5]))[:12] for i in range(len(words)-4)} if len(words) >= 60 else set()


def title_key(title):
    return re.sub(r'[^a-z0-9]+', '', title.lower())


def linked_rows(private_root, reviews):
    latest = {}
    for name in ('linked-articles.jsonl', 'linked-writing.jsonl'):
        path = Path(private_root) / 'public-source-candidates' / name
        if path.exists():
            latest.update((row['id'], row) for row in read_jsonl(path))
    if sum(len(row['text'].encode('utf-8')) for row in latest.values()) > 512 * 1024:
        raise ValueError('Bounded refresh limited to 512 KiB public article text; split a larger phase explicitly')
    urls = [row['source_url'].rstrip('/') for row in latest.values()]
    if len(urls) != len(set(urls)):
        raise ValueError('Multiple linked-writing IDs share one canonical URL')
    counts, excluded, output = Counter(), Counter(), []
    for raw in latest.values():
        if raw['source_type'] != 'linked_writing':
            raise ValueError('Only public linked-writing candidates permitted')
        counts['input_documents'] += 1
        if privacy_flags(raw.get('title', '')):
            counts['withheld_title_flagged'] += 1
            continue
        if 'truncated_post' in raw.get('privacy_flags', []):
            counts['withheld_truncated'] += 1
            continue
        clean = clean_text(raw['text'])
        parts, emitted = list(chunks(clean)), 0
        for index, text in enumerate(parts):
            flags = privacy_flags(text)
            reviewed = public_excerpt_review(dict(raw, text=text), reviews, digest(clean)) if flags else None
            if flags and not reviewed:
                counts['withheld_flagged_excerpts'] += 1
                excluded.update(flags)
                continue
            if len(text.split()) < 5:
                counts['withheld_low_text_excerpts'] += 1
                continue
            row = {key: raw.get(key) for key in ('source_type', 'source_url', 'title', 'published_at', 'authorship', 'acquisition')}
            row.update(id=raw['id'] + ':chunk-' + str(index), document_id=raw['id'],
                       source_group_id=source_group(raw), collection='writing', text=text,
                       content_sha256=digest(text), excerpt_index=index, excerpt_count_before_filtering=len(parts),
                       privacy_status='public_source_exact_contextual_review' if reviewed else 'public_source_automated_screen')
            if reviewed:
                row['public_writing_review'] = {key: reviewed[key] for key in ('document_content_sha256', 'reviewer', 'reviewed_at')}
                row['public_writing_review']['resolved_flags'] = flags
            output.append(row)
            emitted += 1
        counts['documents_with_public_excerpts'] += bool(emitted)
        counts['public_excerpts_before_dedup'] += emitted
    return output, counts, excluded


class Metrics:
    def __init__(self):
        self.values, self.documents, self.splits = defaultdict(lambda: defaultdict(Counter)), defaultdict(set), Counter()
        self.characters = 0

    def add(self, row, encoded):
        self.splits[row['split']] += 1
        self.characters += len(row['text'])
        for level, key in (('collection', row['collection']), ('source', row['source_type'])):
            counts = self.values[level][key]
            counts.update(excerpts=1, words=len(row['text'].split()), text_utf8_bytes=len(row['text'].encode('utf-8')),
                          corpus_jsonl_bytes=len(encoded))
            self.documents[(level, key)].add(row['document_id'])

    def finish(self):
        for level, groups in self.values.items():
            for key, counts in groups.items():
                counts['documents'] = len(self.documents[(level, key)])
        return self.values


def encode(row):
    return (json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n').encode('utf-8')


def refresh(public, private):
    public = Path(public)
    corpus = public / 'corpus.jsonl'
    if corpus.stat().st_size >= 64 * 1024 * 1024:
        raise ValueError('Input corpus exceeds bounded publication size')
    old = json.loads((public / 'stats.json').read_text())
    incoming, linked_counts, linked_excluded = linked_rows(private, public_writing_reviews(public / 'public-writing-review-approvals.json'))
    groups, exact_new, unique_new = Groups(), {}, []
    duplicate_new, near_pairs = 0, 0
    titles, signature_index = defaultdict(list), defaultdict(set)
    for row in incoming:
        key = normalized(row['text'])
        groups.root(row['source_group_id'])
        if key in exact_new:
            prior = exact_new[key]
            groups.union(row['source_group_id'], prior['source_group_id'])
            prior.setdefault('duplicate_source_urls', []).append(row['source_url'])
            duplicate_new += 1
            continue
        exact_new[key] = row
        unique_new.append(row)
    new_shingles = [shingles(row['text']) for row in unique_new]
    for index, row in enumerate(unique_new):
        titles[title_key(row['title'])].append(index)
        for signature in sorted(new_shingles[index])[:8]:
            signature_index[signature].add(index)
        for prior in range(index):
            if new_shingles[index] and new_shingles[prior] and len(new_shingles[index] & new_shingles[prior]) / len(new_shingles[index] | new_shingles[prior]) >= .8:
                groups.union(row['source_group_id'], unique_new[prior]['source_group_id'])
                near_pairs += 1
    updated_urls = {row['source_url'].rstrip('/') for row in unique_new}
    removed, duplicate_old, skip_new = 0, defaultdict(list), set()
    for row in read_jsonl(corpus):
        if row['source_type'] == 'linked_writing':
            removed += 1
            for fresh in unique_new:
                if row['source_url'].rstrip('/') == fresh['source_url'].rstrip('/'):
                    if row['document_id'] != fresh['document_id']:
                        raise ValueError('Canonical article ID must remain stable')
                    groups.union(row['split_group_id'], fresh['source_group_id'])
            continue
        old_group = row['split_group_id']
        if row['source_type'] not in ('anki', 'x'):
            for index in titles.get(title_key(row['title']), []):
                if len(title_key(row['title'])) >= 15:
                    groups.union(old_group, unique_new[index]['source_group_id'])
        acquisition = row.get('acquisition') or {}
        if isinstance(acquisition, dict):
            for link in acquisition.get('linked_own_articles', []):
                if link.rstrip('/') in updated_urls:
                    groups.union(old_group, link.rstrip('/'))
        match = exact_new.get(normalized(row['text']))
        if match:
            groups.union(old_group, match['source_group_id'])
            duplicate_old[row['id']].append(match['source_url'])
            skip_new.add(match['id'])
        other = shingles(row['text'])
        candidates = set(index for signature in sorted(other)[:8] for index in signature_index.get(signature, ()))
        for index in candidates:
            if other and new_shingles[index] and len(other & new_shingles[index]) / len(other | new_shingles[index]) >= .8:
                groups.union(old_group, unique_new[index]['source_group_id'])
                near_pairs += 1
    new_rows = iter(sorted((row for row in unique_new if row['id'] not in skip_new), key=lambda row: row['id']))
    next_new = next(new_rows, None)
    def merged():
        nonlocal next_new
        for row in read_jsonl(corpus):
            if row['source_type'] == 'linked_writing':
                continue
            while next_new is not None and next_new['id'] < row['id']:
                yield next_new
                next_new = next(new_rows, None)
            if row['id'] in duplicate_old:
                row.setdefault('duplicate_source_urls', []).extend(duplicate_old[row['id']])
            yield row
        while next_new is not None:
            yield next_new
            next_new = next(new_rows, None)
    names = ('corpus.jsonl', 'train.jsonl', 'validation.jsonl', 'test.jsonl', 'sources.jsonl', 'anki-cards.jsonl')
    temps = {name: public / (name + '.refresh-tmp') for name in names}
    handles = {name: path.open('wb') for name, path in temps.items()}
    metrics, records, anki, documents = Metrics(), 0, 0, set()
    try:
        for row in merged():
            enrich_collection(row)
            prior_group = row.get('split_group_id', row['source_group_id'])
            group = groups.root(prior_group)
            # Unaffected groups keep their exact historical split.
            if group != prior_group or 'split' not in row:
                row['split'] = group_split(group)
            row['split_group_id'] = group
            data = encode(row)
            if handles['corpus.jsonl'].tell() + len(data) >= 64 * 1024 * 1024:
                raise ValueError('Corpus publication limit exceeded before writing oversized output')
            handles['corpus.jsonl'].write(data)
            handles[row['split'] + '.jsonl'].write(data)
            keys = ('id','document_id','source_type','collection','source_url','title','published_at','authorship','content_sha256','split','split_group_id','privacy_status')
            handles['sources.jsonl'].write(encode({key: row[key] for key in keys}))
            metrics.add(row, data)
            records += 1
            anki += row['source_type'] == 'anki'
            documents.add(row['document_id'])
        cards = 0
        for card in read_jsonl(public / 'anki-cards.jsonl'):
            card['collection'] = 'anki'
            group = groups.root(card['split_group_id'])
            if group != card['split_group_id']:
                card['split'] = group_split(group)
            card['split_group_id'] = group
            handles['anki-cards.jsonl'].write(encode(card))
            cards += 1
    finally:
        for handle in handles.values():
            handle.close()
    if cards != old['retained_anki_cards_before_text_deduplication'] or anki != old['by_source'].get('anki', 0):
        raise ValueError('Anki membership changed unexpectedly')
    if temps['corpus.jsonl'].stat().st_size >= 64 * 1024 * 1024:
        raise ValueError('Corpus publication limit exceeded')
    values = metrics.finish()
    summary = dict(old)
    prior_dedup = old.get('deduplication')
    if prior_dedup and prior_dedup.get('method') == 'streaming_linked_writing_refresh_preserves_prior_group_relations':
        prior_dedup = prior_dedup.get('prior_release')
    for stale in ('tokens_by_source', 'tokenizer', 'token_count_corpus_sha256'):
        summary.pop(stale, None)
    summary.update(schema_version=2, release_date='2026-10-19', records=records, unique_documents=len(documents),
                   words=sum(value['words'] for value in values['source'].values()), characters=metrics.characters,
                   text_utf8_bytes=sum(value['text_utf8_bytes'] for value in values['source'].values()),
                   corpus_jsonl_bytes=temps['corpus.jsonl'].stat().st_size,
                   byte_measurement_note='UTF-8 text fields only versus complete serialized corpus rows including metadata and newline delimiters; no media.',
                   collection_metrics=values['collection'], source_metrics=values['source'],
                   by_collection={key:value['excerpts'] for key,value in values['collection'].items()},
                   by_source={key:value['excerpts'] for key,value in values['source'].items()}, by_split=metrics.splits,
                   artifact_bytes={name:path.stat().st_size for name,path in temps.items()}, tokens=None,
                   token_count_note='Research corpus refreshed; no new tokenizer measurement. Historical model input remains frozen.',
                   frozen_model_training_input={'corpus_release_commit':'1e20626c7b3b2b2bc3404fdb0b1fd39a93419b3d',
                                                'source_corpus_sha256':'6da8afa85c1b9c8e4406823f643a8bdf80fecccac4bd58c28878ca950e6544d8',
                                                'note':'Unchanged historical data/training bytes; enriched research corpus was not used to train existing checkpoints.'},
                   deduplication={'method':'streaming_linked_writing_refresh_preserves_prior_group_relations',
                                  'prior_release':prior_dedup, 'old_linked_excerpts_replaced':removed,
                                  'exact_duplicate_new_excerpts_removed':duplicate_new + len(skip_new),
                                  'near_duplicate_pairs_grouped_in_refresh':near_pairs,'near_duplicate_threshold':.8})
    summary['source_processing']['linked_writing'] = linked_counts
    summary['exclusion_flags_aggregate']['linked_writing'] = linked_excluded
    stats_temp = public / 'stats.json.refresh-tmp'
    stats_temp.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for name, path in temps.items():
        path.replace(public / name)
    stats_temp.replace(public / 'stats.json')
    print(json.dumps({key:summary[key] for key in ('records','unique_documents','words','text_utf8_bytes','corpus_jsonl_bytes','by_collection')}, indent=2))
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--public-dir', type=Path, default=Path(__file__).resolve().parents[1] / 'data/public')
    parser.add_argument('--private-root', required=True, type=Path)
    args = parser.parse_args()
    refresh(args.public_dir, args.private_root)
