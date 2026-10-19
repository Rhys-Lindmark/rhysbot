"""Extract Anki notes and public X posts only. Outputs are PRIVATE candidates.

Never point --out inside the publication repository. Any ancestor workspace
repository must explicitly ignore it. No Anki media or X DMs are read.
"""
import argparse
import ctypes
import ctypes.util
import json
import re
import sqlite3
import subprocess
import tempfile
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path

from corpus_lib import clean_text, digest, privacy_flags, write_jsonl


def decompress_zstd(data, limit=256 * 1024 * 1024):
    try:
        import zstandard
        return zstandard.ZstdDecompressor().decompress(data, max_output_size=limit)
    except ImportError:
        library = ctypes.util.find_library('zstd')
        if not library:
            raise RuntimeError('Install zstandard or system libzstd; do not extract media.')
        z = ctypes.CDLL(library)
        z.ZSTD_decompressBound.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        z.ZSTD_decompressBound.restype = ctypes.c_ulonglong
        capacity = z.ZSTD_decompressBound(data, len(data))
        if not 0 < capacity <= limit:
            raise ValueError('Anki database exceeds bounded extraction limit')
        output = ctypes.create_string_buffer(capacity)
        z.ZSTD_decompress.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
        z.ZSTD_decompress.restype = ctypes.c_size_t
        n = z.ZSTD_decompress(output, capacity, data, len(data))
        if n > capacity:
            raise ValueError('Invalid zstd database')
        return output.raw[:n]


def extract_anki(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        member = next((name for name in ('collection.anki21b', 'collection.anki21', 'collection.anki2') if name in names), None)
        if not member:
            raise ValueError('No supported Anki database found')
        if archive.getinfo(member).file_size > 256 * 1024 * 1024:
            raise ValueError('Database member too large')
        data = archive.read(member)
        if member.endswith('b'):
            data = decompress_zstd(data)
    with tempfile.TemporaryDirectory(prefix='rhysbot-anki-') as directory:
        db = Path(directory) / 'collection.sqlite'
        db.write_bytes(data)
        db.chmod(0o600)
        con = sqlite3.connect('file:' + str(db) + '?mode=ro', uri=True)
        try:
            for nid, flds in con.execute('SELECT id, flds FROM notes ORDER BY id'):
                fields = flds.split('\x1f')
                if len(fields) != 3:
                    raise ValueError('Unexpected Anki fields; inspect schema before continuing')
                front, back, source = [clean_text(f, markup=True) for f in fields]
                text = 'Question: ' + front + '\nAnswer: ' + back
                if source:
                    text += '\nSource: ' + source
                flags = privacy_flags(text, local=True)
                if not front or not back:
                    flags.append('incomplete_card')
                if '<img' in flds.lower() and (len(back) < 30 or 'image' in front.lower() or 'this' in front.lower()):
                    flags.append('image_dependent')
                yield {
                    'id': 'anki-' + digest(str(nid))[:20],
                    'source_type': 'anki', 'source_url': None,
                    'title': front, 'text': text, 'front': front, 'back': back,
                    'citation': source, 'published_at': None,
                    'authorship': 'rhys_curated_notes_not_necessarily_original',
                    'acquisition': 'local_anki_export',
                    'source_group_id': 'anki-' + digest(str(nid))[:20],
                    'content_sha256': digest(text),
                    'privacy_flags': sorted(set(flags)),
                    'privacy_status': 'quarantine' if flags else 'pending_semantic_review',
                    'private_note_id': str(nid),
                }
        finally:
            con.close()


def load_archive_js(path):
    text = Path(path).read_text(encoding='utf-8-sig')
    # Only accept X's assignment wrapper or a plain JSON array; never execute JS.
    match = re.match(r'\s*(?:window\.YTD\.[\w.]+\s*=\s*)?(\[.*\])\s*;?\s*$', text, re.S)
    if not match:
        raise ValueError('Unrecognized X archive wrapper')
    return json.loads(match.group(1))


def extract_x(directory, username='RhysLindmark'):
    directory = Path(directory)
    if directory.name != 'data':
        directory = directory / 'data'
    path = next((directory / name for name in ('tweets.js', 'tweet.js') if (directory / name).exists()), None)
    if path is None:
        raise ValueError('No public tweets.js found')
    notes = {}
    prefix_notes = {}
    if (directory / 'note-tweet.js').exists():
        for item in load_archive_js(directory / 'note-tweet.js'):
            note = item.get('noteTweet', {})
            if note.get('lifecycle', {}).get('name') == 'Active':
                notes[str(note.get('noteTweetId'))] = note.get('core', {})
                prefix = re.sub(r'\s+', ' ', clean_text(note.get('core', {}).get('text', ''))).strip()[:70]
                if len(prefix) >= 40:
                    prefix_notes.setdefault(prefix, []).append(note)
    rows = load_archive_js(path)
    stats = Counter(total_archive_posts=len(rows))
    result = []
    matched_note_ids = set()
    for item in rows:
        tweet = item.get('tweet', {})
        tid = str(tweet.get('id_str', ''))
        original_text = tweet.get('full_text', '')
        if not tid.isdigit():
            stats['invalid_id'] += 1
            continue
        if original_text.startswith('RT @'):
            stats['reposts_excluded'] += 1
            continue
        is_reply = bool(tweet.get('in_reply_to_status_id') or original_text.lstrip().startswith('@'))
        # Long post IDs may appear in note_tweet URLs rather than the tweet ID.
        note_id = tid
        note_info = tweet.get('note_tweet') or {}
        note_id = str(note_info.get('note_tweet_id', note_id))
        core = notes.get(note_id)
        if core is None:
            # X note IDs differ from the enclosing public tweet's ID. Match a
            # unique long prefix and nearby creation time, never ingest orphan drafts.
            prefix = re.sub(r'\s+', ' ', clean_text(original_text)).strip()[:70]
            matches = prefix_notes.get(prefix, [])
            nearby = [n for n in matches if abs((int(n['noteTweetId']) >> 22) - (int(tid) >> 22)) < 86400000]
            if len(nearby) == 1:
                note_id = str(nearby[0]['noteTweetId'])
                core = nearby[0]['core']
        text = core.get('text', '') if core else original_text
        if core:
            stats['long_posts_matched'] += 1
            matched_note_ids.add(note_id)
        for entity in tweet.get('entities', {}).get('urls', []):
            text = text.replace(entity.get('url', '\x00'), entity.get('expanded_url') or entity.get('url', ''))
        text = clean_text(text)
        if len(re.sub(r'https?://\S+', '', text).strip()) < 25:
            stats['low_text_excluded'] += 1
            continue
        if is_reply:
            stats['public_reply_candidates'] += 1
        flags = privacy_flags(text)
        if tweet.get('truncated') and not core:
            flags.append('truncated_post')
        created = datetime.strptime(tweet['created_at'], '%a %b %d %H:%M:%S %z %Y').isoformat()
        day = created[:10]
        result.append({
            'id': 'x-' + tid, 'source_type': 'x',
            'source_url': 'https://x.com/' + username + '/status/' + tid,
            'title': 'Original public post ' + day,
            'published_at': created, 'text': text,
            'authorship': ('rhys_public_reply_parent_context_not_included' if is_reply else 'rhys_original_public_post_may_contain_quotations'),
            'acquisition': 'local_x_archive_public_posts_only',
            # Group neighboring posts by calendar month, reducing thread leakage.
            'source_group_id': 'x-month-' + day[:7],
            'content_sha256': digest(text),
            'privacy_flags': sorted(set(flags)),
            'privacy_status': 'quarantine' if flags else 'public_source_screened',
        })
    stats['candidate_posts'] = len(result)
    stats['unmatched_active_long_posts'] = len(set(notes) - matched_note_ids)
    return result, stats


def assert_private_output(out):
    out = Path(out).resolve()
    publication = Path(__file__).resolve().parents[1]
    if out == publication or publication in out.parents:
        raise ValueError('Raw output must be outside the publication repository')
    for ancestor in [out] + list(out.parents):
        if (ancestor / '.git').exists():
            ignored = subprocess.run(['git', '-C', str(ancestor), 'check-ignore', '--quiet', str(out) + '/'], check=False)
            if ignored.returncode:
                raise ValueError('Ancestor workspace Git must explicitly ignore private output')
    out.mkdir(parents=True, exist_ok=True)
    out.chmod(0o700)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--anki', type=Path)
    p.add_argument('--x-archive', type=Path)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    out = assert_private_output(args.out)
    summary = {}
    if args.anki:
        rows = list(extract_anki(args.anki))
        write_jsonl(out / 'anki-candidates.jsonl', rows)
        summary['anki'] = {'notes': len(rows), 'flags': dict(Counter(f for r in rows for f in r['privacy_flags'])), 'statuses': dict(Counter(r['privacy_status'] for r in rows))}
    if args.x_archive:
        rows, stats = extract_x(args.x_archive)
        write_jsonl(out / 'x-candidates.jsonl', rows)
        summary['x'] = dict(stats)
    (out / 'local-ingestion-summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
