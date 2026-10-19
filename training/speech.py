"""Finite, budgeted podcast ASR + diarization, executed on remote GPU only.

No deployment, schedule, retry or paid work on import. A separate reserved
corpus lane keeps the short-answer demo usable while audio is processed.
Raw audio and speaker embeddings stay private; raw audio is deleted per item.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import modal

HERE = Path('/rhysbot/training') if Path('/rhysbot/training/runtime.py').exists() else Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from runtime import read_json, write_json, safe_run_id
from budget import committed, reserve, finish
from modal_app import image as base_image, volume, CONFIG

PINS = {
    'diarization': ['nvidia/Nemotron-3-Diarization', 'f667ed73aee57d40cc39428eb768b4fd87a0a29e'],
    'asr': ['Systran/faster-distil-whisper-large-v3', 'c3058b475261292e64a0412df1d2681c06260fab'],
    'voice': ['microsoft/wavlm-base-plus-sv', 'feb593a6c23c1cc3d9510425c29b0a14d2b07b1e'],
}
app = modal.App('rhysbot-speakers')
image = (base_image.apt_install('ffmpeg', 'libsndfile1')
         .pip_install('faster-whisper==1.2.1', 'ctranslate2==4.8.3', 'av==16.1.0',
                      'librosa==0.11.0', 'soundfile==0.13.1'))
cpu_image = modal.Image.debian_slim(python_version='3.12').add_local_dir(
    str(HERE), remote_path='/rhysbot/training', copy=True)


def align_words(words, segments):
    """Assign only clear single-speaker overlaps; preserve ambiguity explicitly."""
    turns, unknown = [], 0
    segments = sorted(segments, key=lambda x: x['Start'])
    cursor = 0
    for word in words:
        start, end = word['start'], word['end']
        while cursor < len(segments) and segments[cursor]['End'] <= start:
            cursor += 1
        overlap = {}
        for segment in segments[cursor:]:
            if segment['Start'] >= end:
                break
            amount = max(0, min(end, segment['End']) - max(start, segment['Start']))
            overlap[segment['Speaker']] = overlap.get(segment['Speaker'], 0) + amount
        ranked = sorted(overlap.items(), key=lambda x: x[1], reverse=True)
        duration = max(end - start, .01)
        speaker = 'unknown'
        if ranked and ranked[0][1] / duration >= .6 and (len(ranked) == 1 or ranked[1][1] / duration < .2):
            speaker = 'speaker_%d' % ranked[0][0]
        if speaker == 'unknown':
            unknown += 1
        if turns and turns[-1]['speaker_id'] == speaker and start - turns[-1]['end'] < 1.5 and len(turns[-1]['text']) < 1800:
            turns[-1]['end'] = end
            turns[-1]['text'] += word['word']
        else:
            turns.append({'start': round(start, 2), 'end': round(end, 2),
                          'speaker_id': speaker, 'text': word['word']})
    for turn in turns:
        turn['text'] = turn['text'].strip()
    return turns, unknown


def validate_items(items):
    from urllib.parse import urlparse
    if not items or len(items) > 150:
        raise ValueError('Explicit finite episode list required')
    ids = set()
    for item in items:
        safe_run_id(item['id'])
        if item['id'] in ids:
            raise ValueError('Duplicate episode')
        ids.add(item['id'])
        url = urlparse(item['enclosure_url'])
        if url.scheme != 'https' or url.hostname != 'feeds.soundcloud.com' or not url.path.startswith('/stream/'):
            raise ValueError('Only inventoried public SoundCloud RSS enclosures allowed')


@app.function(image=image, gpu='A100-80GB', cpu=(2, 4), memory=(16384, 32768),
              timeout=900, retries=0, min_containers=0, max_containers=1,
              scaledown_window=2, volumes={'/artifacts': volume})
def process(run_id, items, timeout_seconds, reference=None):
    import numpy as np
    import torch
    from huggingface_hub import snapshot_download
    from transformers import AutoProcessor, AutoModelForAudioFrameClassification
    from faster_whisper import WhisperModel, BatchedInferencePipeline
    singleton = modal.Dict.from_name('rhysbot-training-singleton')
    if singleton.get('active-corpus') != run_id:
        raise ValueError('No corpus lane owns this GPU call')
    validate_items(items)
    volume.reload()
    job = read_json('/artifacts/spend-ledger.json')['jobs'][run_id]
    if job['status'] != 'running_reserved' or job['phase'] != 'corpus_processing' or job['manifest_sha256'] != manifest_sha(items):
        raise ValueError('No matching persisted paid-compute reservation')
    started = time.monotonic()
    os.environ.update(HF_HOME='/artifacts/rhysbot-speech-cache', TOKENIZERS_PARALLELISM='false', OMP_NUM_THREADS='2')
    torch.set_num_threads(2)
    out = Path('/artifacts/speech') / run_id
    out.mkdir(parents=True, exist_ok=False)
    report = {'run_id': run_id, 'pins': PINS, 'episodes': [], 'manifest_sha256': manifest_sha(items),
              'identity_reference': reference, 'status': 'processing', 'raw_audio_published': False}
    try:
        processor = AutoProcessor.from_pretrained(PINS['diarization'][0], revision=PINS['diarization'][1])
        diarizer = AutoModelForAudioFrameClassification.from_pretrained(
            PINS['diarization'][0], revision=PINS['diarization'][1], device_map='cuda')
        diarizer.eval()
        asr_path = snapshot_download(PINS['asr'][0], revision=PINS['asr'][1])
        asr = BatchedInferencePipeline(model=WhisperModel(asr_path, device='cuda', compute_type='float16', cpu_threads=2))
        for item in items:
            if time.monotonic() - started > timeout_seconds - 120:
                report['status'] = 'partial_deadline'
                break
            destination = out / (item['id'] + '.json')
            status = {'id': item['id'], 'title': item['title']}
            scratch = Path('/tmp/rhysbot-audio')
            scratch.mkdir(exist_ok=True)
            try:
                # One recording at a time, no cookies or local archive upload.
                import requests
                audio_path = scratch / 'episode.mp3'
                digest, size = hashlib.sha256(), 0
                with requests.get(item['enclosure_url'], stream=True, timeout=(15, 30)) as response:
                    response.raise_for_status()
                    with audio_path.open('wb') as stream:
                        for chunk in response.iter_content(1024 * 1024):
                            size += len(chunk)
                            if size > 500 * 1024 * 1024 or time.monotonic() - started > timeout_seconds - 90:
                                raise ValueError('Audio size/time ceiling reached')
                            digest.update(chunk)
                            stream.write(chunk)
                decoded = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(audio_path),
                    '-f', 'f32le', '-ac', '1', '-ar', '16000', '-'], timeout=120)
                audio = np.frombuffer(decoded, dtype=np.float32).copy()
                del decoded
                duration = len(audio) / 16000
                if duration > 12000 or duration < 2:
                    raise ValueError('Unexpected audio duration')
                inputs = processor(audio, sampling_rate=16000).to(diarizer.device, dtype=diarizer.dtype)
                with torch.inference_mode():
                    logits = diarizer(**inputs).logits
                segments = processor.extract_speaker_dict(logits, inputs.attention_mask)[0]
                del logits, inputs
                transcription, info = asr.transcribe(audio, batch_size=8, language='en',
                    word_timestamps=True, vad_filter=True, condition_on_previous_text=False,
                    beam_size=1)
                words = [{'start': w.start, 'end': w.end, 'word': w.word, 'probability': w.probability}
                         for segment in transcription for w in (segment.words or [])]
                turns, unknown = align_words(words, segments)
                evidence = {'schema_version': 1, 'episode': item, 'audio_sha256': digest.hexdigest(),
                    'audio_bytes': size, 'audio_duration_seconds': duration, 'pins': PINS,
                    'speaker_segments': segments, 'words': words, 'turns': turns,
                    'word_count': len(words), 'unknown_word_count': unknown,
                    'speaker_identity_status': 'anonymous_pending_reference_alignment',
                    'limitations': ['Automatic ASR and diarization; no human audio audit.',
                                    'Speaker IDs are local to this episode, not guest names.']}
                if not words or any(t['start'] < 0 or t['end'] > duration + 2 or t['end'] < t['start'] for t in turns):
                    raise ValueError('Empty or out-of-range ASR output')
                write_json(destination, evidence)
                status.update(status='completed', words=len(words), speakers=len({s['Speaker'] for s in segments}),
                              unknown_word_fraction=unknown / len(words), audio_duration_seconds=duration)
                del audio, words, turns
                torch.cuda.empty_cache()
            except Exception as error:
                # No inference retries, no credential/URL/prompt exception text.
                status.update(status='failed_no_retry', error_type=type(error).__name__)
            finally:
                shutil.rmtree(scratch, ignore_errors=True)
            report['episodes'].append(status)
            report['elapsed_seconds'] = time.monotonic() - started
            write_json(out / 'report.json', report)
            volume.commit()
        if report['status'] == 'processing':
            report['status'] = 'completed' if all(x['status'] == 'completed' for x in report['episodes']) else 'completed_with_gaps'
        return report
    finally:
        report['elapsed_seconds'] = time.monotonic() - started
        write_json(out / 'report.json', report)
        shutil.rmtree('/artifacts/rhysbot-speech-cache', ignore_errors=True)
        volume.commit()


def manifest_sha(items):
    return hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()


def reserve_corpus(ledger, run_id, items, timeout_seconds, budget):
    validate_items(items)
    # Same conservative $12/hour + 180-second overhead and cumulative $200 cap.
    amount = reserve(ledger, run_id, 'qwen', 'eval', timeout_seconds, budget)
    ledger['jobs'][run_id].update(family='speech', phase='corpus_processing',
        status='running_reserved', manifest_sha256=manifest_sha(items), episode_count=len(items))
    return amount


@app.function(image=cpu_image, cpu=.25, memory=256, timeout=7800, retries=0,
              min_containers=0, max_containers=1, scaledown_window=2, volumes={'/artifacts': volume})
def coordinate(run_id, items, timeout_seconds):
    singleton = modal.Dict.from_name('rhysbot-training-singleton')
    if not singleton.put('active-corpus', run_id, skip_if_exists=True):
        raise ValueError('Another corpus batch is active; no duplicate dispatch')
    reserved, accounting_committed = False, False
    path = Path('/artifacts/spend-ledger.json')
    try:
        if not singleton.put('active', run_id, skip_if_exists=True):
            raise ValueError('Budget coordinator busy; no GPU dispatched')
        try:
            volume.reload()
            ledger = read_json(path)
            amount = reserve_corpus(ledger, run_id, items, timeout_seconds, CONFIG['budget'])
            reserved = True
            write_json(path, ledger)
            volume.commit()
            accounting_committed = True
        finally:
            if (not reserved or accounting_committed) and singleton.get('active') == run_id:
                singleton.pop('active')
        # Only own output files are mutated during this remote GPU call.
        call = process.with_options(timeout=timeout_seconds).spawn(run_id, items, timeout_seconds)
        singleton.put('corpus-call-' + run_id, call.object_id)
        report = call.get(timeout=timeout_seconds + 60)
        for _ in range(120):
            if singleton.put('active', run_id, skip_if_exists=True):
                break
            time.sleep(1)
        else:
            return {'status': 'terminal_pending_accounting', 'run_id': run_id, 'report': report}
        try:
            volume.reload()
            ledger = read_json(path)
            finish(ledger, run_id, report['status'] == 'completed', {'report': '/artifacts/speech/' + run_id + '/report.json',
                'episodes_completed': sum(x['status'] == 'completed' for x in report['episodes'])})
            write_json(path, ledger)
            volume.commit()
            result = {'run_id': run_id, 'reserved_usd': amount, 'committed_usd': committed(ledger), 'report': report}
        finally:
            if singleton.get('active') == run_id:
                singleton.pop('active')
        singleton.pop('active-corpus')
        return result
    finally:
        # Unknown dispatch/result retains the corpus lane and full paid hold.
        if not reserved and singleton.get('active-corpus') == run_id:
            singleton.pop('active-corpus')


@app.local_entrypoint()
def main(run_id: str, manifest: str, timeout_seconds: int = 900):
    safe_run_id(run_id)
    items = read_json(manifest)
    validate_items(items)
    if not 1 <= timeout_seconds <= 7200:
        raise ValueError('Finite job timeout required')
    if run_id.startswith('speech-pilot') and timeout_seconds > 900:
        raise ValueError('Pilot ceiling is 900 seconds ($3.60 reservation)')
    print(json.dumps(coordinate.remote(run_id, items, timeout_seconds), indent=2))
