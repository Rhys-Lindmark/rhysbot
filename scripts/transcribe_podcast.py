"""Bounded CPU ASR for a supplied public podcast recording; private output only.

Requires optional faster-whisper==1.2.1, av==16.1.0 and a separately downloaded pinned
Systran/faster-whisper base.en or small.en snapshot. This never trains a language model.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import resource
import subprocess
import sys
import time
import urllib.parse

from ingest_local import assert_private_output

MODELS = {
    'base.en': ('Systran/faster-whisper-base.en', '3d3d5dee26484f91867d81cb899cfcf72b96be6c'),
    'small.en': ('Systran/faster-whisper-small.en', 'd1d751a5f8271d482d14ca55d9e2deeebbae577f'),
}


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def peak_mib():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value / (1024 ** 2 if sys.platform == 'darwin' else 1024)


def transcribe(audio, model_dir, output, source_url, title, published_at=None, youtube_id=None,
               model_size='base.en', memory_limit_mib=1536):
    source = urllib.parse.urlsplit(source_url)
    if (source.scheme != 'https' or source.hostname != 'soundcloud.com'
            or source.username or source.password or source.port not in (None, 443)
            or not source.path.startswith('/the-rhys-show/')):
        raise ValueError('Expected the identified public podcast source URL')
    audio, model_dir = Path(audio).resolve(), Path(model_dir).resolve()
    if audio.stat().st_size > 100 * 1024 ** 2:
        raise ValueError('Audio exceeds 100 MiB bound')
    probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries',
                                              'format=duration', '-of', 'json', str(audio)], timeout=30))
    if not 0 < float(probe['format']['duration']) <= 7200:
        raise ValueError('Podcast duration exceeds two-hour bound before decoding')
    model_id, model_revision = MODELS[model_size]
    if not 512 <= memory_limit_mib <= 2560:
        raise ValueError('ASR process memory allowance must be 512–2560 MiB')
    if model_dir.name != model_revision:
        raise ValueError('Expected the pinned ASR snapshot directory')
    output = assert_private_output(output)
    from faster_whisper import WhisperModel
    started = time.monotonic()
    model = WhisperModel(str(model_dir), device='cpu', compute_type='int8', cpu_threads=2, num_workers=1)
    segments, info = model.transcribe(str(audio), language='en', beam_size=3,
                                      vad_filter=True, condition_on_previous_text=False)
    if not 0 < info.duration <= 7200:
        raise ValueError('Podcast duration exceeds two-hour bound')
    result = []
    for segment in segments:
        if peak_mib() > memory_limit_mib:
            raise MemoryError('CPU transcription exceeded its recorded process memory allowance')
        result.append({'start': segment.start, 'end': segment.end, 'text': segment.text.strip(),
                       'avg_logprob': segment.avg_logprob, 'no_speech_prob': segment.no_speech_prob})
        if len(result) % 25 == 0:
            print(json.dumps({'segments': len(result), 'audio_seconds': segment.end,
                              'elapsed_seconds': round(time.monotonic() - started, 1), 'peak_rss_mib': round(peak_mib())}), flush=True)
    text = '\n'.join(row['text'] for row in result if row['text'])
    if len(text.split()) < 30:
        raise ValueError('No adequate speech transcript; do not fabricate silent/music text')
    report = {'status': 'automatic_transcription_complete_not_audio_verified',
              'created_at': datetime.now(timezone.utc).isoformat(), 'duration_seconds': info.duration,
              'seconds': time.monotonic() - started, 'peak_rss_mib': peak_mib(),
              'audio_sha256': file_hash(audio), 'model_bin_sha256': file_hash(model_dir/'model.bin'),
              'model': model_id, 'revision': model_revision, 'memory_limit_mib': memory_limit_mib,
              'packages': {name: importlib.metadata.version(name) for name in ['faster-whisper', 'ctranslate2', 'onnxruntime', 'av']},
              'segments': result}
    acquisition = {k: v for k, v in report.items() if k != 'segments'}
    acquisition.update(method='public_rss_audio_cpu_asr', speaker_labels=False,
                       coverage='automatic_asr_not_audio_verified', visual_content_not_transcribed=True)
    if youtube_id:
        acquisition['corresponding_youtube_video_id'] = youtube_id
    identifier = 'podcast_transcript:' + hashlib.sha256(source_url.encode()).hexdigest()[:24]
    candidate = {'id': identifier, 'source_type': 'podcast_transcript', 'source_url': source_url,
                 'source_group_id': 'https://www.youtube.com/watch?v='+youtube_id if youtube_id else source_url,
                 'title': title, 'published_at': published_at, 'text': text,
                 'authorship': 'mixed_or_unverified_speakers', 'acquisition': acquisition}
    (output/'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n')
    (output/'candidate.jsonl').write_text(json.dumps(candidate, ensure_ascii=False)+'\n')
    print(json.dumps({k: report[k] for k in ['status', 'duration_seconds', 'seconds', 'peak_rss_mib']}), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--audio', type=Path, required=True)
    p.add_argument('--model-dir', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--source-url', required=True)
    p.add_argument('--title', required=True)
    p.add_argument('--published-at')
    p.add_argument('--youtube-id')
    p.add_argument('--model-size', choices=list(MODELS), default='base.en')
    p.add_argument('--memory-limit-mib', type=int, default=1536)
    a = p.parse_args()
    transcribe(a.audio, a.model_dir, a.output, a.source_url, a.title, a.published_at, a.youtube_id,
               a.model_size, a.memory_limit_mib)
