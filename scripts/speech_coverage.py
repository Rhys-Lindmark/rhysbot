"""Publish recording coverage without private ASR arrays or voice measurements."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit


def coverage(rss, videos, public_dir, reports=()):
    rows = []
    for item in rss['episodes']:
        rows.append({'id': item['id'], 'source_url': item['source_url'],
                     'youtube_url': item.get('youtube_url'), 'audio_source_kind': 'rss_audio',
                     'inventoried_duration_seconds': item.get('rss_duration_seconds')})
    for item in videos:
        video = item['id']
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video):
            raise ValueError('Explicit inventoried YouTube ID required')
        rows.append({'id': 'podcast-' + hashlib.sha256(('youtube:' + video).encode()).hexdigest()[:20],
                     'source_url': 'https://www.youtube.com/watch?v=' + video,
                     'youtube_url': 'https://www.youtube.com/watch?v=' + video,
                     'audio_source_kind': 'youtube_audio', 'inventoried_duration_seconds': item.get('duration')})
    if any(not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', row['id']) for row in rows):
        raise ValueError('Safe recording IDs required')
    for row in rows:
        source = urlsplit(row['source_url'])
        allowed = {'soundcloud.com', 'www.soundcloud.com'} if row['audio_source_kind'] == 'rss_audio' else {'www.youtube.com'}
        if source.scheme != 'https' or source.netloc not in allowed or source.fragment:
            raise ValueError('Inventoried public source URL required')
        if row['audio_source_kind'] == 'rss_audio' and source.query:
            raise ValueError('Private URL parameters forbidden')
        youtube = row['youtube_url']
        if youtube is not None and not re.fullmatch(r'https://www\.youtube\.com/watch\?v=[A-Za-z0-9_-]{11}', youtube):
            raise ValueError('Canonical public YouTube alias required')
        duration = row['inventoried_duration_seconds']
        if duration is not None and (type(duration) not in (float, int) or not math.isfinite(duration) or duration < 0):
            raise ValueError('Finite inventoried duration required')
    if len({r['id'] for r in rows}) != len(rows):
        raise ValueError('Duplicate recording inventory')
    attempts = {}
    for report in reports:
        for item in report['episodes']:
            attempts.setdefault(item['id'], []).append(item['status'])
    for row in rows:
        path = Path(public_dir) / 'speaker-transcripts' / (row['id'] + '.json')
        if path.exists():
            sidecar = json.loads(path.read_text())
            if sidecar['source_url'] != row['source_url'] or sidecar['audio_source_kind'] != row['audio_source_kind']:
                raise ValueError('Published sidecar/inventory mismatch')
            row.update(status='released_speaker_transcript',
                       transcript_url='/rhysbot/transcript/?episode=' + row['id'],
                       rhys_voice_match=sidecar['rhys_speaker_id'] is not None,
                       observed_duration_seconds=sidecar['audio_duration_seconds'],
                       turn_counts=sidecar['turn_counts'])
        elif 'completed' in attempts.get(row['id'], []):
            row['status'] = 'processed_but_not_released'
        elif attempts.get(row['id']):
            row['status'] = 'processing_failed'
        else:
            row['status'] = 'not_completed'
    counts = Counter(row['status'] for row in rows)
    return {'schema_version': 1, 'recordings': rows,
            'summary': {'inventoried_recordings': len(rows), 'statuses': dict(sorted(counts.items())),
                        'rss_audio_recordings': len(rss['episodes']), 'youtube_only_recordings': len(videos),
                        'released_rhys_matches': sum(row.get('rhys_voice_match', False) for row in rows)},
            'limitations': ['Automatic speech recognition, diarization and voice matching can be wrong.',
                            'Unknown and overlapping speech stays uncertain. Guest labels are local to each recording.',
                            'RSS audio is canonical where available; linked YouTube editions are aliases, not byte-identical recordings.',
                            'Unreleased recordings can retain earlier captions in the corpus; captions do not establish speaker identity.',
                            'The separate 89 HEE transcripts have owner-confirmed Rhys narration; quotations are not automatically his views.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rss-inventory', type=Path, required=True)
    parser.add_argument('--youtube-only-inventory', type=Path, required=True)
    parser.add_argument('--public-dir', type=Path, default=Path(__file__).resolve().parents[1] / 'data/public')
    parser.add_argument('--report', type=Path, action='append', default=[])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = coverage(json.loads(args.rss_inventory.read_text()), json.loads(args.youtube_only_inventory.read_text()),
                      args.public_dir, [json.loads(path.read_text()) for path in args.report])
    # Paths, exception messages, raw words, speaker embeddings and private
    # inventory metadata never enter this explicit public schema.
    result['input_sha256'] = {'rss_inventory': hashlib.sha256(args.rss_inventory.read_bytes()).hexdigest(),
                              'youtube_only_inventory': hashlib.sha256(args.youtube_only_inventory.read_bytes()).hexdigest()}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result['summary'], indent=2))


if __name__ == '__main__':
    main()
