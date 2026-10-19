"""Refresh three reviewed-public AI2026 HTML snapshots, without network or approval.

Snapshots and candidate inputs remain private. This script creates no privacy
approval: flagged excerpts still need exact-content review before publication.
"""
import argparse
import hashlib
import json
from pathlib import Path

from ingest_linked_writing import MAX_BYTES, extract_article

URLS = [f'https://www.rhyslindmark.com/posts/ai2026-pt{n}/' for n in (1, 2, 3)]


def extract_snapshot(path, url):
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('Public HTML snapshot exceeds 8 MiB')
    source = path.read_bytes()
    result = extract_article(source.decode('utf-8'), url)
    if result['status'] != 'article_extracted' or result['canonical_url'] != url:
        raise ValueError('Verified article extraction required')
    return {'id': 'linked_writing:' + hashlib.sha256(url.encode()).hexdigest()[:24],
            'source_type': 'linked_writing', 'source_url': url,
            'title': result['title'], 'published_at': result['published_at'], 'text': result['text'],
            'authorship': 'rhys_publication_with_quotations_possible', 'source_group_id': url,
            'acquisition': {'method': 'public_article_html_cached_reextract',
                            'coverage': 'static_article_text_including_svg_text',
                            'canonical_url': url, 'response_sha256': hashlib.sha256(source).hexdigest(),
                            'body_selector': result['body_selector'], 'provenance': result['provenance'],
                            'text_coverage': result['text_coverage'], 'limitations': result['limitations'],
                            'visual_content_not_transcribed': True}}


def upsert(existing, updates):
    # Collapse an append-only source history before replacing by canonical URL.
    latest = {row['id']: row for row in existing}
    for row in updates:
        matches = [key for key, old in latest.items() if old.get('source_url', '').rstrip('/') == row['source_url'].rstrip('/')]
        if len(matches) > 1:
            raise ValueError('Multiple source IDs for canonical article')
        if matches:
            row = dict(row, id=matches[0])
            old = latest[matches[0]]
            for field in ('linked_from', 'discovered_from'):
                if field in old.get('acquisition', {}):
                    row['acquisition'][field] = old['acquisition'][field]
        latest[row['id']] = row
    return list(latest.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--html-dir', required=True, type=Path)
    parser.add_argument('--candidates', required=True, type=Path)
    args = parser.parse_args()
    updates = [extract_snapshot(args.html_dir / f'part-{n}.html', url) for n, url in enumerate(URLS, 1)]
    existing = [json.loads(line) for line in args.candidates.open() if line.strip()] if args.candidates.exists() else []
    result = upsert(existing, updates)
    temp = args.candidates.with_suffix('.jsonl.tmp')
    temp.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in result), encoding='utf-8')
    temp.replace(args.candidates)
    print(json.dumps({'canonical_articles_refreshed': len(updates), 'candidate_documents': len(result),
                      'privacy_approvals_created': 0}))


if __name__ == '__main__':
    main()
