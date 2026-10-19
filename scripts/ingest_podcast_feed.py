"""Build private podcast coverage from saved public metadata; stdlib, no network.

Example (run from rhysbot):
  python3 scripts/ingest_podcast_feed.py --rss ../work/rhysbot-private/asr/soundcloud-feed.xml \
    --apple ../work/rhysbot-private/asr/apple-podcast.json \
    --youtube ../work/rhysbot-private/public-source-candidates/youtube-inventory.json \
    --caption-coverage ../work/rhysbot-private/public-source-candidates/youtube-coverage.jsonl \
    --reviewed-aliases ../work/rhysbot-private/podcast-feed/reviewed-aliases.json \
    --output ../work/rhysbot-private/podcast-feed/coverage.json

Aliases are explicit human identity judgments, bound to both titles/durations.
They are never generated from similarity. Descriptions can link other episodes
or external talks; an explicit video ID alone is not proof of episode identity.
RSS metadata and YouTube captions do not establish identical audio editions.
"""
import argparse
from collections import Counter, defaultdict
from datetime import timezone
from email.utils import parsedate_to_datetime
import hashlib
from html import unescape
import json
import math
from pathlib import Path
import re
import unicodedata
from urllib.parse import parse_qs, urlsplit
import xml.etree.ElementTree as ET

MAX_BYTES = 8 * 1024 * 1024
ITUNES = '{http://www.itunes.com/dtds/podcast-1.0.dtd}'
FEED_URL = 'https://feeds.soundcloud.com/users/soundcloud:users:46689666/sounds.rss'
VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')
EDITION_LIMITATION = ('Episode identity only: YouTube captions describe the YouTube edition. '
                      'RSS audio has not been transcribed or byte-compared by this inventory; '
                      'equal durations also do not establish identical recordings.')


def bounded_read(path):
    with Path(path).open('rb') as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Metadata input exceeds 8 MiB')
    return raw


def normalized_title(title):
    # Deliberately exact after accent/case/punctuation normalization, not fuzzy.
    ascii_title = unicodedata.normalize('NFKD', title).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]', '', ascii_title.lower())


def public_url(value):
    value = (value or '').strip()
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Expected a public HTTPS URL without credentials')
    return value


def duration_seconds(value):
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        raise ValueError('Invalid duration')
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or value < 0:
            raise ValueError('Invalid duration')
        return value
    if not re.fullmatch(r'\d+(?::\d{1,2}){0,2}', value.strip()):
        raise ValueError('Invalid duration')
    parts = [int(p) for p in value.strip().split(':')]
    if len(parts) > 1 and any(n >= 60 for n in parts[1:]):
        raise ValueError('Invalid duration component')
    total = 0
    for part in parts:
        total = total * 60 + part
    return total


def explicit_youtube_ids(text):
    found = set()
    for raw in re.findall(r'https?://[^\s<>"\']+', unescape(text or '')):
        parsed = urlsplit(raw.rstrip(').,;'))
        host = (parsed.hostname or '').lower()
        identifier = None
        if host in {'youtube.com', 'www.youtube.com', 'm.youtube.com'}:
            if parsed.path == '/watch':
                identifier = parse_qs(parsed.query).get('v', [None])[0]
            elif parsed.path.startswith(('/shorts/', '/embed/', '/live/')):
                identifier = parsed.path.split('/')[2]
        elif host in {'youtu.be', 'www.youtu.be'}:
            identifier = parsed.path.strip('/')
        if identifier and VIDEO_ID.fullmatch(identifier):
            found.add(identifier)
    return sorted(found)


def parse_feed(raw):
    if len(raw) > MAX_BYTES:
        raise ValueError('RSS exceeds 8 MiB')
    # Reject DTD/entity declarations, including alternate XML encodings. No
    # external entities, expansion, network lookups, HTML execution or fetches.
    if b'\x00' in raw or re.search(br'<!\s*(?:DOCTYPE|ENTITY)\b', raw, re.I):
        raise ValueError('DTD, entities and NUL/alternate-encoding RSS are unsupported')
    root = ET.fromstring(raw)
    channel = root.find('channel')
    if root.tag != 'rss' or channel is None:
        raise ValueError('Expected RSS channel')
    episodes, seen = [], set()
    for item in channel.findall('item'):
        title = (item.findtext('title') or '').strip()
        if not title or not normalized_title(title):
            raise ValueError('Missing/unsupported episode title')
        url = public_url(item.findtext('link'))
        enclosure = item.find('enclosure')
        if enclosure is None:
            raise ValueError('Episode missing enclosure')
        enclosure_url = public_url(enclosure.get('url'))
        guid = (item.findtext('guid') or '').strip()
        warnings = []
        if not guid:
            guid = 'url-sha256:' + hashlib.sha256(url.encode()).hexdigest()
            warnings.append('RSS GUID absent; stable public URL digest used')
        if guid in seen:
            raise ValueError('Duplicate RSS GUID')
        seen.add(guid)
        raw_date = (item.findtext('pubDate') or '').strip()
        try:
            date = parsedate_to_datetime(raw_date)
            if date.tzinfo is None:
                raise ValueError('Timezone absent')
            published_at = date.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
        except (ValueError, TypeError, OverflowError):
            published_at = None
            warnings.append('Missing/invalid publication date')
        description = '\n'.join(item.findtext(key) or '' for key in
                                ('description', ITUNES + 'summary'))
        episodes.append({'id': 'podcast-' + hashlib.sha256(guid.encode()).hexdigest()[:20],
                         'rss_guid': guid, 'title': title, 'source_url': url,
                         'canonical_url': url, 'enclosure_url': enclosure_url,
                         'canonical_url_basis': 'RSS item link; HTTP canonical not independently fetched',
                         'enclosure_type': enclosure.get('type'),
                         'enclosure_length_bytes': int(enclosure.get('length'))
                         if (enclosure.get('length') or '').isdigit() else None,
                         'published_at': published_at, 'rss_pub_date': raw_date,
                         'rss_duration_seconds': duration_seconds(item.findtext(ITUNES + 'duration')),
                         'explicit_youtube_reference_ids': explicit_youtube_ids(description),
                         'metadata_warnings': warnings})
    if not episodes:
        raise ValueError('Empty RSS feed')
    return {'title': channel.findtext('title'), 'author': channel.findtext(ITUNES + 'author'),
            'home_url': public_url(channel.findtext('link')),
            'feed_url': FEED_URL, 'rss_pub_date': channel.findtext('pubDate')}, episodes


def index_youtube(videos):
    by_id, by_title = {}, defaultdict(list)
    for video in videos:
        identifier, title = video.get('id', ''), video.get('title', '')
        if not VIDEO_ID.fullmatch(identifier) or not title or identifier in by_id:
            raise ValueError('Invalid/duplicate YouTube inventory ID or missing title')
        normalized = normalized_title(title)
        if not normalized:
            raise ValueError('Unsupported YouTube title')
        by_id[identifier] = {**video, 'duration': duration_seconds(video.get('duration'))}
        by_title[normalized].append(identifier)
    return by_id, by_title


def validated_aliases(aliases, episodes, by_id):
    by_guid = {r['rss_guid']: r for r in episodes}
    result, used_videos = {}, set()
    for alias in aliases:
        guid, identifier = alias['rss_guid'], alias['youtube_id']
        if guid in result or identifier in used_videos:
            raise ValueError('Duplicate reviewed alias or reused video')
        if guid not in by_guid or identifier not in by_id:
            raise ValueError('Reviewed alias references absent metadata')
        rss, yt = by_guid[guid], by_id[identifier]
        if (alias.get('decision') != 'same_episode_identity'
                or alias.get('reviewer') != 'codex-source-identity-review'
                or not alias.get('reviewed_at') or not alias.get('evidence')
                or alias.get('rss_title') != rss['title']
                or alias.get('youtube_title') != yt['title']
                or alias.get('rss_duration_seconds') != rss['rss_duration_seconds']
                or alias.get('youtube_duration_seconds') != yt['duration']):
            raise ValueError('Reviewed alias evidence/metadata mismatch')
        result[guid] = alias
        used_videos.add(identifier)
    return result


def build_coverage(feed, episodes, videos, caption_rows, aliases=()):
    by_id, by_title = index_youtube(videos)
    aliases = validated_aliases(aliases, episodes, by_id)
    captions = {}
    for row in caption_rows:
        if row.get('video_id') not in by_id:
            raise ValueError('Caption status references absent YouTube inventory ID')
        captions[row['video_id']] = row  # append-only collector's latest status
    result, matched = [], set()
    for episode in episodes:
        candidates = by_title.get(normalized_title(episode['title']), [])
        references = episode['explicit_youtube_reference_ids']
        agreeing = sorted(set(candidates) & set(references))
        alias = aliases.get(episode['rss_guid'])
        identifier, method = None, None
        if len(candidates) == 1:
            identifier = candidates[0]
            method = 'unique_exact_normalized_title'
        elif len(agreeing) == 1:
            identifier = agreeing[0]
            method = 'explicit_youtube_id_disambiguates_exact_title'
        if alias:
            if identifier and identifier != alias['youtube_id']:
                raise ValueError('Reviewed alias conflicts with deterministic match')
            identifier = alias['youtube_id']
            method = 'manually_reviewed_episode_identity_alias'
        row = {**episode, 'youtube_match_method': method,
               'youtube_match_candidates': sorted(candidates),
               'rss_audio_transcription_status': 'not_established_by_this_inventory',
               'authorship': 'mixed_or_unverified_speakers',
               'edition_limitation': EDITION_LIMITATION}
        if identifier:
            if identifier in matched:
                raise ValueError('One YouTube video matched multiple RSS episodes; review required')
            matched.add(identifier)
            video = by_id[identifier]
            caption = captions.get(identifier, {})
            acquired = caption.get('status') == 'caption_acquired'
            row.update(youtube_id=identifier,
                       youtube_url='https://www.youtube.com/watch?v=' + identifier,
                       youtube_title=video['title'], youtube_duration_seconds=video['duration'],
                       youtube_upload_date=video.get('upload_date'),
                       youtube_minus_rss_duration_seconds=(video['duration'] - episode['rss_duration_seconds'])
                       if video['duration'] is not None and episode['rss_duration_seconds'] is not None else None,
                       explicit_id_agrees_with_episode_match=identifier in references,
                       youtube_caption_status=caption.get('status', 'not_recorded'),
                       youtube_caption_type=caption.get('caption_type'),
                       youtube_caption_error=caption.get('error'),
                       youtube_caption_characters=caption.get('characters'),
                       transcript_source_url=('https://www.youtube.com/watch?v=' + identifier) if acquired else None,
                       status='matched_youtube_caption_available' if acquired else 'matched_youtube_transcript_gap')
            if alias:
                row['alias_review'] = alias
        else:
            row.update(youtube_id=None, youtube_url=None, transcript_source_url=None,
                       status='ambiguous_youtube_identity_gap' if len(candidates) > 1 else 'no_youtube_match_transcript_gap')
        row['unverified_youtube_reference_ids'] = [r for r in references if r != identifier]
        result.append(row)
    return {'schema_version': 1, 'feed': feed, 'limitations': [EDITION_LIMITATION,
            'Coverage is limited to the saved RSS and channel inventory snapshots; no web discovery or ASR is performed.',
            'Descriptions and subscriber/supporter lists are not copied into this report.'],
            'summary': {'rss_episodes': len(result), 'youtube_inventory_videos': len(videos),
                        'matched_youtube_episodes': len(matched),
                        'status_counts': dict(Counter(r['status'] for r in result)),
                        'match_method_counts': dict(Counter(r['youtube_match_method'] for r in result if r['youtube_match_method'])),
                        'rss_audio_transcriptions_verified': 0}, 'episodes': result}


def private_output(path):
    path = Path(path).resolve()
    permitted = Path(__file__).resolve().parents[2] / 'work/rhysbot-private/podcast-feed'
    if permitted.resolve() not in path.parents:
        raise ValueError('Output must remain in private work/rhysbot-private/podcast-feed/')
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('rss', 'apple', 'youtube', 'caption-coverage', 'output'):
        parser.add_argument('--' + option, type=Path, required=True)
    parser.add_argument('--reviewed-aliases', type=Path)
    args = parser.parse_args()
    output = private_output(args.output)
    inputs = {'rss': args.rss, 'apple': args.apple, 'youtube': args.youtube,
              'caption_coverage': args.caption_coverage}
    if args.reviewed_aliases:
        inputs['reviewed_aliases'] = args.reviewed_aliases
    if output in {p.resolve() for p in inputs.values()}:
        raise ValueError('Output must not overwrite an input')
    raw = {name: bounded_read(path) for name, path in inputs.items()}
    feed, episodes = parse_feed(raw['rss'])
    apple = json.loads(raw['apple'])
    matches = [r for r in apple.get('results', []) if r.get('feedUrl') == FEED_URL]
    if len(matches) != 1 or matches[0].get('artistName') != feed['author']:
        raise ValueError('Apple lookup does not uniquely corroborate feed/author')
    feed['apple_lookup'] = {key: matches[0].get(key) for key in
                            ('collectionId', 'collectionName', 'collectionViewUrl', 'artistName', 'feedUrl')}
    report = build_coverage(feed, episodes, json.loads(raw['youtube']),
                            [json.loads(line) for line in raw['caption_coverage'].splitlines() if line.strip()],
                            json.loads(raw.get('reviewed_aliases', b'[]')))
    report['input_sha256'] = {name: hashlib.sha256(value).hexdigest() for name, value in raw.items()}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report['summary'], sort_keys=True))


if __name__ == '__main__':
    main()
