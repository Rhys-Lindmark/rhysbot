"""Prepare/publish screened audio speaker turns and stream canonical corpus editions.

No ML, network, training-data regeneration or inference. Plan mode is default;
--apply is required to replace public artifacts. Full episode evidence is private:
only explicit whitelisted fields can reach staged/public speaker sidecars.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from urllib.parse import urlsplit, urlunsplit

from build_corpus import Groups, collection
from corpus_lib import clean_text, digest, group_split, privacy_flags, read_jsonl
from refresh_research_corpus import Metrics, encode, normalized, shingles

MAX_EVIDENCE_BYTES = 8 * 1024 * 1024
MAX_CORPUS_BYTES = 64 * 1024 * 1024
LIMITATIONS = [
    'Automatic ASR and diarization; no human audio audit or calibrated identity probability.',
    'Rhys labels use only the upstream rhys_speaker_id; machine identity assignment can be wrong.',
    'Other speaker IDs are local anonymous labels, not inferred guest names. Unknown/overlap is retained.',
    'A speaker can quote someone else; transcript statements are not automatically Rhys beliefs.',
    'Public privacy screening omits flagged turn text and can miss sensitive context.',
    'Canonical text transcribes the declared RSS or YouTube recording. Corresponding editions are aliases, not identical recordings.',
]


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', value):
        raise ValueError('Safe episode ID required')
    return value


def public_url(value, hosts):
    parsed = urlsplit(value or '')
    if (parsed.scheme != 'https' or parsed.hostname not in hosts or parsed.username or parsed.password
            or parsed.port not in (None, 443) or parsed.query or parsed.fragment
            or privacy_flags(value)):
        raise ValueError('Public canonical source URL required; no private paths or access arguments')
    return urlunsplit(('https', parsed.netloc, parsed.path, '', '')).rstrip('/')


def episode_metadata(evidence):
    raw = evidence['episode']
    key = identifier(raw['id'])
    title = clean_text(raw['title'])
    if not title or privacy_flags(title):
        raise ValueError('Episode title does not pass public screening')
    kind = raw.get('audio_source_kind', 'rss_audio')
    if kind not in {'rss_audio', 'youtube_audio'}:
        raise ValueError('Explicit supported audio source kind required')
    youtube = raw.get('youtube_id')
    if youtube is not None and not re.fullmatch(r'[A-Za-z0-9_-]{11}', youtube):
        raise ValueError('Explicit YouTube ID required')
    youtube_url = 'https://www.youtube.com/watch?v=' + youtube if youtube else None
    if raw.get('youtube_url') not in (None, youtube_url):
        raise ValueError('YouTube ID/URL mismatch')
    if kind == 'youtube_audio':
        if not youtube or raw.get('youtube_url') != youtube_url or raw['source_url'] != youtube_url or raw.get('enclosure_url') is not None:
            raise ValueError('YouTube audio requires an explicit canonical video URL and no RSS enclosure')
        source, enclosure, feed = youtube_url, None, None
    else:
        source = public_url(raw['source_url'], {'soundcloud.com', 'www.soundcloud.com'})
        enclosure = public_url(raw['enclosure_url'], {'feeds.soundcloud.com'})
        if not urlsplit(enclosure).path.startswith('/stream/'):
            raise ValueError('Inventoried RSS stream required')
        feed = 'https://feeds.soundcloud.com/users/soundcloud:users:46689666/sounds.rss'
    sha = evidence['audio_sha256']
    if not isinstance(sha, str) or not re.fullmatch('[a-f0-9]{64}', sha):
        raise ValueError('Audio SHA256 required')
    duration = evidence['audio_duration_seconds']
    if type(duration) not in (int, float) or not math.isfinite(duration) or not 2 <= duration <= 12000:
        raise ValueError('Finite bounded audio duration required')
    pins = {}
    for name in ('asr', 'diarization', 'voice'):
        value = evidence.get('pins', {}).get(name)
        if value is None and name == 'voice':
            continue
        if (not isinstance(value, list) or len(value) != 2 or not all(isinstance(v, str) for v in value)
                or len(value[0]) > 256 or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', value[0])
                or not re.fullmatch('[a-f0-9]{40}', value[1])):
            raise ValueError('Pinned public speech model/revision required')
        pins[name] = value
    date = raw.get('published_at')
    if date is not None and (not isinstance(date, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:T[0-9:.+-]+Z?)?', date)):
        raise ValueError('Public publication date required')
    return {'id': key, 'title': title, 'published_at': date, 'source_url': source,
            'audio_source_kind': kind, 'rss_feed_url': feed,
            'rss_enclosure_url': enclosure, 'youtube_id': youtube, 'youtube_url': youtube_url,
            'audio_sha256': sha, 'audio_duration_seconds': duration, 'pins': pins}


def speaker_label(value, rhys):
    if value == rhys:
        return 'Rhys Lindmark'
    if value in ('unknown', 'overlap'):
        return 'Unknown / overlap'
    return 'Guest Speaker ' + str(int(value.split('_')[1]) + 1)


def sanitize_episode(evidence):
    metadata = episode_metadata(evidence)
    rhys = evidence.get('rhys_speaker_id')
    if rhys is not None and (not isinstance(rhys, str) or not re.fullmatch(r'speaker_\d+', rhys)):
        raise ValueError('Invalid authoritative Rhys speaker ID')
    raw_turns = evidence['turns']
    if not isinstance(raw_turns, list) or not raw_turns or len(raw_turns) > 50000:
        raise ValueError('Nonempty bounded timestamped turns required')
    turns, flagged = [], set()
    prior_start = -1
    for index, raw in enumerate(raw_turns):
        start, end, speaker = raw['start'], raw['end'], raw['speaker_id']
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not math.isfinite(start) or not math.isfinite(end)
                or not 0 <= start <= end <= metadata['audio_duration_seconds'] + 2 or start < prior_start):
            raise ValueError('Nonmonotonic/out-of-range turn timestamps')
        if not isinstance(speaker, str) or not (re.fullmatch(r'speaker_\d+', speaker) or speaker in ('unknown', 'overlap')):
            raise ValueError('Anonymous speaker ID required; do not infer guest names')
        text = clean_text(raw['text'])
        if not isinstance(text, str) or len(text.encode()) > 65536:
            raise ValueError('Bounded turn text required')
        turns.append({'index': index, 'start': start, 'end': end, 'speaker_id': speaker,
                      'speaker': speaker_label(speaker, rhys), 'text': text})
        if privacy_flags(text):
            flagged.add(index)
        prior_start = start
    # Catch access/contact patterns split at ASR speaker boundaries without
    # copying a flagged token into another supposedly clean neighboring turn.
    for index in range(len(turns)):
        window = turns[index:index+3]
        if any(turn['index'] in flagged for turn in window):
            continue
        if privacy_flags(' '.join(turn['text'] for turn in window)):
            flagged.update(turn['index'] for turn in window)
    for turn in turns:
        if turn['index'] in flagged:
            turn['text'] = None
            turn['privacy_status'] = 'withheld_automated_screen'
        else:
            turn['privacy_status'] = 'public_source_automated_screen'
    word_count, unknown_words = evidence.get('word_count'), evidence.get('unknown_word_count')
    if word_count is not None or unknown_words is not None:
        if type(word_count) is not int or type(unknown_words) is not int or not 0 <= unknown_words <= word_count or word_count < 1:
            raise ValueError('Valid upstream word-alignment counters required')
    return {'schema_version': 1, **metadata, 'collection': 'podcasts_youtube',
            'word_alignment': {'words': word_count, 'unknown_words': unknown_words,
                               'unknown_words_fraction': unknown_words / word_count if word_count else None,
                               'basis': 'upstream_word_diarization_alignment_not_human_audit'},
            'rhys_speaker_id': rhys, 'identity_basis': 'upstream_authoritative_speaker_id_not_inferred_from_text',
            'turns': turns, 'turn_counts': {'total': len(turns), 'withheld': len(flagged),
                                          'published': len(turns)-len(flagged)},
            'limitations': list(LIMITATIONS), 'raw_audio_published': False, 'voiceprints_published': False}


def validate_sidecar(sidecar):
    allowed = {'audio_source_kind','schema_version','id','title','published_at','source_url','rss_feed_url','rss_enclosure_url',
               'youtube_id','youtube_url','audio_sha256','audio_duration_seconds','pins','collection',
               'rhys_speaker_id','identity_basis','word_alignment','turns','turn_counts','limitations',
               'raw_audio_published','voiceprints_published'}
    if set(sidecar) != allowed or sidecar['schema_version'] != 1:
        raise ValueError('Unexpected public sidecar fields; private input must not be published')
    metadata = episode_metadata({'episode':{**sidecar,'enclosure_url':sidecar['rss_enclosure_url']}, 'audio_sha256':sidecar['audio_sha256'],
                                 'audio_duration_seconds':sidecar['audio_duration_seconds'], 'pins':sidecar['pins']})
    if (sidecar['rss_feed_url'] != metadata['rss_feed_url'] or sidecar['collection'] != 'podcasts_youtube'
            or sidecar['raw_audio_published'] is not False or sidecar['voiceprints_published'] is not False
            or sidecar['limitations'] != LIMITATIONS
            or sidecar['identity_basis'] != 'upstream_authoritative_speaker_id_not_inferred_from_text'):
        raise ValueError('Unexpected public provenance/limits')
    if sidecar['pins'] != metadata['pins']:
        raise ValueError('Unexpected nested speech pin fields')
    alignment=sidecar['word_alignment']
    if (set(alignment) != {'words','unknown_words','unknown_words_fraction','basis'}
            or alignment['basis'] != 'upstream_word_diarization_alignment_not_human_audit'):
        raise ValueError('Unexpected word-alignment metadata')
    words,unknown=alignment['words'],alignment['unknown_words']
    if words is None and unknown is None:
        if alignment['unknown_words_fraction'] is not None:raise ValueError('Unavailable alignment ratio')
    elif (type(words) is not int or type(unknown) is not int or not 0 <= unknown <= words or words < 1
          or type(alignment['unknown_words_fraction']) not in (int,float)
          or not math.isclose(alignment['unknown_words_fraction'],unknown/words)):
        raise ValueError('Invalid alignment count/ratio')
    rhys = sidecar['rhys_speaker_id']
    if rhys is not None and (not isinstance(rhys,str) or not re.fullmatch(r'speaker_\d+',rhys)):
        raise ValueError('Invalid Rhys speaker ID')
    prior, withheld = -1, 0
    for index,turn in enumerate(sidecar['turns']):
        if set(turn) != {'index','start','end','speaker_id','speaker','text','privacy_status'} or turn['index'] != index:
            raise ValueError('Unexpected turn fields/index')
        speaker = turn['speaker_id']
        if not (re.fullmatch(r'speaker_\d+',speaker) or speaker in ('unknown','overlap')):
            raise ValueError('Unexpected anonymous speaker ID')
        if turn['speaker'] != speaker_label(speaker,rhys):
            raise ValueError('Display label is not derived from authoritative speaker ID')
        start,end=turn['start'],turn['end']
        if (type(start) not in (int,float) or type(end) not in (int,float) or not math.isfinite(start) or not math.isfinite(end)
                or not 0 <= start <= end <= sidecar['audio_duration_seconds']+2 or start < prior):
            raise ValueError('Invalid public turn timestamps')
        prior=start
        if turn['text'] is None:
            withheld+=1
            if turn['privacy_status'] != 'withheld_automated_screen':raise ValueError('Missing withheld label')
        elif (not isinstance(turn['text'],str) or privacy_flags(turn['text'])
              or turn['privacy_status'] != 'public_source_automated_screen'):
            raise ValueError('Unscreened public turn text')
    if sidecar['turn_counts'] != {'total':len(sidecar['turns']),'published':len(sidecar['turns'])-withheld,'withheld':withheld}:
        raise ValueError('Stale sidecar turn counts')
    return metadata


def validate_public_sidecars(public_dir):
    public_dir=Path(public_dir); known={}; chunks={}
    for path in sorted((public_dir/'speaker-transcripts').glob('*.json')):
        if path.stat().st_size > MAX_EVIDENCE_BYTES:raise ValueError('Oversized public sidecar')
        sidecar=json.loads(path.read_text());metadata=validate_sidecar(sidecar)
        if path.stem != metadata['id']:raise ValueError('Sidecar filename/episode mismatch')
        pointer='speaker-transcripts/'+path.name
        known[pointer]=(metadata['audio_sha256'],metadata['source_url'],len(sidecar['turns']),metadata['audio_source_kind'])
        expected={}
        for index,chunk in enumerate(corpus_chunks(sidecar)):
            if not privacy_flags(chunk['text']):
                expected['podcast-speaker:'+metadata['id']+':chunk-'+f'{index:05d}']=(digest(chunk['text']),
                    chunk['turn_index_start'],chunk['turn_index_end'],chunk['time_start_seconds'],chunk['time_end_seconds'])
        chunks[pointer]=expected
        del sidecar
    linked=0
    for row in read_jsonl(public_dir/'corpus.jsonl'):
        pointer=row.get('speaker_transcript')
        if not pointer:continue
        if pointer not in known:raise ValueError('Missing public speaker sidecar')
        sha,url,turns,kind=known[pointer]
        expected='https://raw.githubusercontent.com/Rhys-Lindmark/rhysbot/main/data/public/'+pointer
        if (row.get('speaker_transcript_url') != expected or row['source_url'] != url
                or row['acquisition']['audio_sha256'] != sha
                or not 0 <= row['turn_index_start'] <= row['turn_index_end'] < turns
                or row['acquisition'].get('media') != kind
                or row['source_type'] != ('youtube_transcript' if kind == 'youtube_audio' else 'podcast_transcript')
                or row['authorship'] != 'mixed_or_unverified_speakers'):
            raise ValueError('Changed corpus speaker provenance')
        expected_chunk=chunks[pointer].get(row['id'])
        actual_chunk=(digest(row['text']),row['turn_index_start'],row['turn_index_end'],
                      row['time_start_seconds'],row['time_end_seconds'])
        if expected_chunk is None or actual_chunk != expected_chunk or row['content_sha256'] != expected_chunk[0]:
            raise ValueError('Corpus speaker text/hash/time references differ from deterministic sidecar chunk')
        linked+=1
    return {'status':'passed','public_sidecars':len(known),'linked_corpus_excerpts':linked}


def corpus_chunks(sidecar):
    """Bounded mixed-turn chunks with explicit labels and sidecar/time references."""
    parts, refs, words = [], [], 0
    def row():
        return {'text': '\n'.join(parts), 'turn_index_start': min(refs), 'turn_index_end': max(refs),
                'time_start_seconds': sidecar['turns'][min(refs)]['start'],
                'time_end_seconds': max(sidecar['turns'][index]['end'] for index in set(refs))}
    for turn in sidecar['turns']:
        if turn['text'] is None:
            if parts:
                yield row()
                parts, refs, words = [], [], 0
            continue
        remaining = turn['text'].split()
        while remaining:
            count = min(350-words, len(remaining))
            selected, remaining = remaining[:count], remaining[count:]
            prefix = f"[{turn['speaker']} {turn['start']:.2f}–{turn['end']:.2f}s] "
            parts.append(prefix + ' '.join(selected))
            refs.append(turn['index'])
            words += count
            if words == 350:
                yield row()
                parts, refs, words = [], [], 0
    if parts:
        yield row()


def prepare(evidence_dir, stage):
    stage = Path(stage)
    sidecars = stage / 'speaker-transcripts'
    sidecars.mkdir()
    db = sqlite3.connect(stage / 'index.sqlite')
    db.execute('PRAGMA cache_size=-4096')
    db.execute('PRAGMA temp_store=FILE')
    db.executescript('CREATE TABLE episodes(id TEXT PRIMARY KEY, metadata TEXT, base_group TEXT);'
                     'CREATE TABLE rows(id TEXT PRIMARY KEY, episode TEXT, norm TEXT, plain_norm TEXT, payload TEXT);'
                     'CREATE INDEX row_norm ON rows(norm); CREATE INDEX plain_norm ON rows(plain_norm);'
                     'CREATE TABLE signatures(signature TEXT, row_id TEXT); CREATE INDEX signature_idx ON signatures(signature);')
    episodes, aliases, gaps = {}, {}, Counter()
    supplied = Path(evidence_dir)
    files = [supplied] if supplied.is_file() else sorted(supplied.glob('*.json'))
    for path in files:
        if path.name in {'report.json', 'manifest.json', 'reference.json'}:
            continue
        if path.stat().st_size > MAX_EVIDENCE_BYTES:
            gaps['evidence_exceeds_8MiB'] += 1
            continue
        evidence = json.loads(path.read_text())
        if not isinstance(evidence, dict) or not {'episode', 'turns', 'audio_sha256', 'pins'} <= set(evidence):
            gaps['non_episode_file_skipped'] += 1
            continue
        try:
            public = sanitize_episode(evidence)
            validate_sidecar(public)
        except (KeyError, TypeError, ValueError):
            gaps['invalid_or_title_flagged_episode'] += 1
            continue
        del evidence
        key = public['id']
        if key in episodes:
            raise ValueError('Duplicate episode evidence; choose an explicit completed batch')
        base_group = public['youtube_url'] or public['source_url']
        metadata = {k: public[k] for k in ('id','title','published_at','source_url','youtube_url','youtube_id','audio_sha256','pins','audio_source_kind')}
        included = 0
        for index, chunk in enumerate(corpus_chunks(public)):
            # Per-turn screening is necessary but not a waiver for a risky
            # rendered chunk or labels. Never mix model roles with attribution.
            if privacy_flags(chunk['text']):
                gaps['chunk_withheld_after_turn_screen'] += 1
                continue
            row_id = 'podcast-speaker:' + key + ':chunk-' + f'{index:05d}'
            pointer = 'speaker-transcripts/' + key + '.json'
            plain = re.sub(r'\[[^\]\n]+\] ', '', chunk['text'])
            row = {'id': row_id, 'document_id': 'podcast-speaker:' + key,
                   'source_type': 'youtube_transcript' if public['audio_source_kind'] == 'youtube_audio' else 'podcast_transcript', 'collection': 'podcasts_youtube',
                   'source_url': public['source_url'], 'source_aliases': sorted({value for value in (public['source_url'], public['youtube_url']) if value}),
                   'source_group_id': base_group, 'title': public['title'], 'published_at': public['published_at'],
                   'authorship': 'mixed_or_unverified_speakers', 'text': chunk['text'], 'content_sha256': digest(chunk['text']),
                   'privacy_status': 'public_source_automated_screen', 'speaker_transcript': pointer,
                   'speaker_transcript_url': 'https://raw.githubusercontent.com/Rhys-Lindmark/rhysbot/main/data/public/' + pointer,
                   'acquisition': {'method': 'public_' + public['audio_source_kind'] + '_gpu_asr_diarization',
                                   'media': public['audio_source_kind'], 'coverage': 'screened_machine_speaker_turns',
                                   'audio_sha256': public['audio_sha256'], 'corresponding_youtube_video_id': public['youtube_id'],
                                   'public_sidecar': pointer}, **{k:v for k,v in chunk.items() if k != 'text'}}
            db.execute('INSERT INTO rows VALUES(?,?,?,?,?)', (row_id,key,normalized(row['text']),normalized(plain),json.dumps(row,ensure_ascii=False)))
            for signature in sorted(shingles(plain))[:8]:
                db.execute('INSERT INTO signatures VALUES(?,?)', (signature,row_id))
            included += 1
        if not included:
            db.execute('DELETE FROM rows WHERE episode=?', (key,))
            gaps['no_publishable_corpus_text'] += 1
            continue
        db.execute('INSERT INTO episodes VALUES(?,?,?)', (key,json.dumps(metadata),base_group))
        for alias in {public['source_url'], public['youtube_url']}:
            if alias:
                if alias in aliases:
                    raise ValueError('Ambiguous RSS/YouTube alias across episode evidence')
                aliases[alias] = key
        episodes[key] = {'group': base_group, 'metadata': metadata, 'turn_counts': public['turn_counts'],
                         'rhys_match': public['rhys_speaker_id'] is not None, 'word_alignment': public['word_alignment']}
        (sidecars / (key + '.json')).write_text(json.dumps(public,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        db.commit()
    if not episodes:
        db.close()
        raise ValueError('No screened eligible episodes; public corpus remains unchanged')
    return db, episodes, aliases, gaps


def integrate(evidence_dir, public_dir, work_dir, apply=False):
    public_dir, work_dir = Path(public_dir), Path(work_dir)
    corpus = public_dir / 'corpus.jsonl'
    if corpus.stat().st_size >= MAX_CORPUS_BYTES:
        raise ValueError('Input corpus exceeds publication limit')
    if public_dir.resolve() == work_dir.resolve() or public_dir.resolve() in work_dir.resolve().parents:
        raise ValueError('Staging must remain outside public corpus')
    work_dir.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='speaker-integration-',dir=work_dir) as directory:
        stage = Path(directory)
        db, episodes, aliases, gaps = prepare(evidence_dir, stage)
        groups, existing_groups, replaced = Groups(), defaultdict(set), Counter()
        matched_episodes = set()
        dropped, duplicate_old = set(), defaultdict(list)
        def replacement(row):
            if row['source_type'] not in {'youtube_transcript','podcast_transcript'}:
                return None
            for url in ((row.get('source_url') or '').rstrip('/'), (row.get('source_group_id') or '').rstrip('/')):
                key = aliases.get(url)
                if not key:
                    continue
                metadata = episodes[key]['metadata']
                if metadata['audio_source_kind'] == 'youtube_audio':
                    # A video recording cannot erase the RSS edition, even when
                    # its leakage group uses the same YouTube alias.
                    if row['source_type'] != 'youtube_transcript' or row.get('source_url') != metadata['youtube_url']:
                        continue
                return key
            return None
        for row in read_jsonl(corpus):
            episode = replacement(row)
            if episode:
                existing_groups[episode].add(row['split_group_id'])
                matched_episodes.add(episode)
                replaced[row['source_type']] += 1
                continue
            for candidate_id, candidate_episode, payload in db.execute('SELECT id,episode,payload FROM rows WHERE norm=?',(normalized(row['text']),)):
                dropped.add(candidate_id)
                existing_groups[candidate_episode].add(row['split_group_id'])
                duplicate_old[row['id']].append(episodes[candidate_episode]['metadata']['source_url'])
            other = shingles(row['text'])
            signatures = sorted(other)[:8]
            if signatures:
                marks = ','.join('?' for _ in signatures)
                query = 'SELECT DISTINCT r.episode,r.payload FROM signatures s JOIN rows r ON r.id=s.row_id WHERE s.signature IN ('+marks+')'
                for candidate_episode,payload in db.execute(query,signatures):
                    candidate = json.loads(payload)
                    plain = re.sub(r'\[[^\]\n]+\] ', '', candidate['text'])
                    new = shingles(plain)
                    if new and len(other & new)/len(other | new) >= .8:
                        existing_groups[candidate_episode].add(row['split_group_id'])
        for key,item in episodes.items():
            old_groups = existing_groups[key]
            if old_groups:
                chosen = min(old_groups)
                for group in old_groups:
                    groups.union(chosen,group)
                item['group'] = chosen
            else:
                groups.root(item['group'])
        # New episodes can share exact/near text too; candidate filtering uses
        # bounded signature probes on disk rather than all-pairs RAM sets.
        for row_id,key,payload in db.execute('SELECT id,episode,payload FROM rows ORDER BY id'):
            row = json.loads(payload)
            plain = re.sub(r'\[[^\]\n]+\] ', '', row['text'])
            for prior_id,prior_key,prior_payload in db.execute('SELECT id,episode,payload FROM rows WHERE norm=? AND id<?',(normalized(row['text']),row_id)):
                groups.union(episodes[key]['group'],episodes[prior_key]['group'])
                dropped.add(row_id)
                prior = json.loads(prior_payload)
                prior.setdefault('duplicate_source_urls', []).append(row['source_url'])
                db.execute('UPDATE rows SET payload=? WHERE id=?',(json.dumps(prior,ensure_ascii=False),prior_id))
            sig = sorted(shingles(plain))[:8]
            if sig:
                query = 'SELECT DISTINCT r.id,r.episode,r.payload FROM signatures s JOIN rows r ON r.id=s.row_id WHERE s.signature IN ('+','.join('?' for _ in sig)+') AND r.id<?'
                first = shingles(plain)
                for prior_id,prior_key,prior_payload in db.execute(query,[*sig,row_id]):
                    prior = json.loads(prior_payload)
                    second = shingles(re.sub(r'\[[^\]\n]+\] ', '', prior['text']))
                    if second and len(first & second)/len(first | second) >= .8:
                        groups.union(episodes[key]['group'],episodes[prior_key]['group'])
        def incoming():
            for row_id,key,payload in db.execute('SELECT id,episode,payload FROM rows ORDER BY id'):
                if row_id in dropped:
                    continue
                row = json.loads(payload)
                row['split_group_id'] = groups.root(episodes[key]['group'])
                row['split'] = group_split(row['split_group_id'])
                yield row
        new = iter(incoming()); pending = next(new,None)
        def merged():
            nonlocal pending
            for row in read_jsonl(corpus):
                if replacement(row):
                    continue
                while pending is not None and pending['id'] < row['id']:
                    yield pending
                    pending = next(new,None)
                if row['id'] in duplicate_old:
                    row.setdefault('duplicate_source_urls',[]).extend(duplicate_old[row['id']])
                yield row
            while pending is not None:
                yield pending
                pending = next(new,None)
        names = ('corpus.jsonl','train.jsonl','validation.jsonl','test.jsonl','sources.jsonl','anki-cards.jsonl')
        output = stage / 'public'; output.mkdir()
        handles = {name:(output/name).open('wb') for name in names}
        metrics,documents,records,anki,cards = Metrics(),set(),0,0,0
        try:
            for row in merged():
                row['collection'] = collection(row['source_type'])
                group = groups.root(row['split_group_id'])
                if group != row['split_group_id']:
                    row['split'] = group_split(group)
                row['split_group_id'] = group
                data = encode(row)
                if handles['corpus.jsonl'].tell()+len(data) >= MAX_CORPUS_BYTES:
                    raise ValueError('Canonical speaker corpus exceeds 64 MiB; no public files applied')
                handles['corpus.jsonl'].write(data); handles[row['split']+'.jsonl'].write(data)
                keys=('id','document_id','source_type','collection','source_url','title','published_at','authorship','content_sha256','split','split_group_id','privacy_status')
                handles['sources.jsonl'].write(encode({key:row[key] for key in keys}))
                metrics.add(row,data); documents.add(row['document_id']); records+=1
                anki+=row['source_type']=='anki'
            for card in read_jsonl(public_dir/'anki-cards.jsonl'):
                group=groups.root(card['split_group_id'])
                if group!=card['split_group_id']:card['split']=group_split(group)
                card['split_group_id']=group
                handles['anki-cards.jsonl'].write(encode(card));cards+=1
        finally:
            for handle in handles.values():handle.close()
            db.close()
        old=json.loads((public_dir/'stats.json').read_text())
        if cards!=old['retained_anki_cards_before_text_deduplication'] or anki!=old['by_source'].get('anki',0):
            raise ValueError('Anki membership changed; no public files applied')
        values=metrics.finish();summary=dict(old)
        for stale in ('tokens_by_source','tokenizer','token_count_corpus_sha256'):summary.pop(stale,None)
        counts={key:sum(item['turn_counts'][key] for item in episodes.values()) for key in ('total','published','withheld')}
        known_words=sum(item['word_alignment']['words'] or 0 for item in episodes.values())
        unknown_words=sum(item['word_alignment']['unknown_words'] or 0 for item in episodes.values())
        report={'episodes_processed':len(episodes),
                'rss_audio_episodes':sum(item['metadata']['audio_source_kind'] == 'rss_audio' for item in episodes.values()),
                'youtube_audio_episodes':sum(item['metadata']['audio_source_kind'] == 'youtube_audio' for item in episodes.values()),
                'episodes_with_rhys_match':sum(item['rhys_match'] for item in episodes.values()),
                'episodes_anonymous_or_ambiguous':sum(not item['rhys_match'] for item in episodes.values()),
                'screened_turn_count':counts['total'],
                'unknown_words_fraction':unknown_words / known_words if known_words else None,
                'unknown_words_fraction_episodes_measured':sum(item['word_alignment']['words'] is not None for item in episodes.values()),
                'provenance_notes':list(LIMITATIONS),
                'processed_episodes':len(episodes),'matched_old_corpus_episodes':len(matched_episodes),
                'episodes_with_youtube_alias':sum(bool(item['metadata']['youtube_id']) for item in episodes.values()),
                'public_sidecar_bytes':sum(path.stat().st_size for path in (stage/'speaker-transcripts').glob('*.json')),
                'replaced_excerpts_by_source':dict(replaced),'turns':counts,
                'gaps_aggregate':dict(gaps),'exact_new_excerpt_duplicates_removed':len(dropped),
                'canonical_scope':'Only screened processed episodes replace explicit RSS/YouTube aliases; other videos/audio remain.',
                'recordings_assumed_identical':False,'frozen_training_changed':False,'applied':apply,
                'output_excerpts':records,'output_corpus_jsonl_bytes':(output/'corpus.jsonl').stat().st_size,
                'output_text_utf8_bytes':sum(x['text_utf8_bytes'] for x in values['source'].values())}
        summary.update(records=records,unique_documents=len(documents),words=sum(x['words'] for x in values['source'].values()),
                       characters=metrics.characters,text_utf8_bytes=sum(x['text_utf8_bytes'] for x in values['source'].values()),
                       corpus_jsonl_bytes=(output/'corpus.jsonl').stat().st_size,collection_metrics=values['collection'],source_metrics=values['source'],
                       by_collection={k:v['excerpts'] for k,v in values['collection'].items()},by_source={k:v['excerpts'] for k,v in values['source'].items()},
                       by_split=metrics.splits,artifact_bytes={name:(output/name).stat().st_size for name in names},tokens=None,
                       token_count_note='Research speaker corpus refreshed; no new token measurement. Historical model training input is unchanged.',
                       speaker_transcript_integration=report,
                       source_processing_note='Historical acquisition/screening inventories are retained. Current canonical excerpts/documents are source_metrics; replacement is speaker_transcript_integration.')
        (output/'stats.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        if apply:
            # Every artifact is completely staged first. Each rename is atomic;
            # publication must wait for the existing cross-file validator.
            destination=public_dir/'speaker-transcripts';destination.mkdir(exist_ok=True)
            for path in (stage/'speaker-transcripts').glob('*.json'):
                temporary=destination/(path.name+'.tmp');shutil.copyfile(path,temporary);temporary.replace(destination/path.name)
            for name in (*names,'stats.json'):(output/name).replace(public_dir/name)
        return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    inputs=parser.add_mutually_exclusive_group()
    inputs.add_argument('--evidence-dir',type=Path)
    inputs.add_argument('--episode-file',type=Path)
    parser.add_argument('--public-dir',type=Path,default=Path(__file__).resolve().parents[1]/'data/public')
    parser.add_argument('--work-dir',type=Path)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--apply',action='store_true',help='Apply completely staged screened outputs; default is a read-only public-corpus plan')
    mode.add_argument('--plan',action='store_true',help='Explicit read-only public-corpus plan (default)')
    parser.add_argument('--validate-only',action='store_true',help='Read-only strict public-sidecar/provenance validation')
    args=parser.parse_args()
    if args.validate_only:
        print(json.dumps(validate_public_sidecars(args.public_dir),indent=2))
    else:
        if not (args.evidence_dir or args.episode_file) or args.work_dir is None:
            parser.error('Integration requires explicit evidence and private --work-dir')
        print(json.dumps(integrate(args.evidence_dir or args.episode_file,args.public_dir,args.work_dir,args.apply),indent=2))
