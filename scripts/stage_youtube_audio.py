"""Sequential, resumable private YouTube-audio staging; never launches GPU work."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import signal
import subprocess
import sys
import time

VOLUME = 'rhysbot-training-artifacts'
MAX_BYTES = 500 * 1024 * 1024
MAX_MANIFEST = 8 * 1024 * 1024
NODE_HEAP_OPTIONS = '--max-old-space-size=64 --max-semi-space-size=4'
EXTENSIONS = {'m4a', 'webm', 'mp3', 'ogg', 'opus'}
PRIVATE_ROOT = Path(__file__).resolve().parents[2] / 'work/rhysbot-private'


def validate_item(item):
    if not isinstance(item, dict) or not re.fullmatch(r'podcast-[a-f0-9]{20}', item.get('id', '')):
        raise ValueError('Expected podcast-20hex episode ID')
    video = item.get('youtube_id')
    if not isinstance(video, str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}', video):
        raise ValueError('Explicit YouTube ID required')
    url = 'https://www.youtube.com/watch?v=' + video
    if (item.get('audio_source_kind') != 'youtube_audio' or item.get('source_url') != url
            or item.get('youtube_url') != url or item.get('enclosure_url') is not None):
        raise ValueError('Canonical public YouTube recording required')
    if 'staged_audio' in item:
        raise ValueError('Use immutable original manifest, not a staged output')
    return item


def validate_run(run):
    if not re.fullmatch(r'speech-youtube-[A-Za-z0-9_-]{1,64}', run):
        raise ValueError('Safe speech-youtube run ID required')
    return run


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.chmod(temporary, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write('\n'); handle.flush(); os.fsync(handle.fileno())
    temporary.replace(path)
    directory = os.open(path.parent, os.O_RDONLY)
    try: os.fsync(directory)
    finally: os.close(directory)


def media_files(work, key):
    # Only this exact assigned episode basename; never follow symlinks.
    paths = list(Path(work).glob(key + '.*'))
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError('Unexpected symlink/directory in owned audio outputs')
    return paths


def stream_hash(path):
    if path.is_symlink() or not path.is_file(): raise ValueError('Regular audio files required; symlinks forbidden')
    digest, size = hashlib.sha256(), 0
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            size += len(block)
            if size > MAX_BYTES: raise ValueError('Audio exceeds 500 MiB')
            digest.update(block)
    if not size: raise ValueError('Empty audio')
    return digest.hexdigest(), size


def download_command(item, work, yt_dlp, node):
    return [str(yt_dlp), '--ignore-config', '--no-playlist', '--no-cache-dir',
            '--no-js-runtimes', '--js-runtimes', 'node:' + str(node), '--quiet', '--no-progress',
            '--socket-timeout', '15', '--retries', '0', '--fragment-retries', '0',
            '--extractor-retries', '0', '--file-access-retries', '0',
            '--max-filesize', str(MAX_BYTES), '--no-part', '--no-mtime', '--fixup', 'never',
            '-f', 'bestaudio[ext=m4a]/bestaudio',
            '-o', str(Path(work) / (item['id'] + '.%(ext)s')), item['youtube_url']]


def stop_owned(process):
    if process.poll() is not None: return
    try: os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError: return
    try: process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=3)


def download(item, work, command, timeout, checkpoint):
    started = time.monotonic()
    # Log filename cannot match the media basename glob.
    log = Path(work) / ('download-' + item['id'] + '.log')
    with log.open('wb') as output:
        os.chmod(log, 0o600)
        child_env=dict(os.environ)
        child_env['NODE_OPTIONS']=NODE_HEAP_OPTIONS
        process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT, env=child_env, start_new_session=True,
                                   preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_FSIZE,(MAX_BYTES,MAX_BYTES)))
        checkpoint({'command': command, 'pid': process.pid, 'started_at_unix': time.time()})
        try:
            while process.poll() is None:
                if time.monotonic() - started > timeout: raise TimeoutError('Per-video deadline exceeded')
                if log.stat().st_size > 65536: raise ValueError('Bounded downloader log exceeded')
                if sum(path.stat().st_size for path in media_files(work, item['id'])) > MAX_BYTES:
                    raise ValueError('Download exceeds 500 MiB')
                time.sleep(.25)
            if process.returncode: raise RuntimeError('Downloader exit ' + str(process.returncode))
        finally: stop_owned(process)
    candidates = media_files(work, item['id'])
    if len(candidates) != 1 or candidates[0].suffix[1:] not in EXTENSIONS:
        raise ValueError('One supported downloaded audio file required')
    return candidates[0]


class BoundedAudioReader:
    """Seekable public file-object input with bounded reads and no reopen bypass."""
    CHUNK_BYTES=1024*1024
    INLINE_BYTES=4*1024*1024

    def __init__(self,path):
        if path.is_symlink() or not path.is_file(): raise ValueError('Regular audio file required')
        self._file=path.open('rb',buffering=0)
        self.size=path.stat().st_size
        if not 0 < self.size <= MAX_BYTES:
            self._file.close();raise ValueError('Bounded nonempty input required')
        self.max_read_bytes=0

    def read(self,size=-1):
        if size is None or size < 0:
            size=self.size-self.tell()
            if size > self.INLINE_BYTES: raise RuntimeError('Unbounded blob reads prohibited')
            # The SDK inline path requires the complete <=4MiB value.
        else:
            size=min(size,self.CHUNK_BYTES)
        self.max_read_bytes=max(self.max_read_bytes,size)
        return self._file.read(size)

    def seek(self,*args):return self._file.seek(*args)
    def tell(self):return self._file.tell()
    def readable(self):return True
    def seekable(self):return True
    def close(self):self._file.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()

    @property
    def name(self):
        # Modal v1 multipart reopens this name, bypassing the bounded reader.
        # Reject before it can reopen an unbounded file; never silently fallback.
        raise RuntimeError('Multipart reopen is not supported by bounded upload')


def bounded_upload_context(volume, *, sdk_version=None):
    """Reviewed Modal1.6.2 public API plus a bounded file-object input."""
    import inspect
    if sdk_version is None:
        from importlib.metadata import version
        sdk_version=version('modal')
    if sdk_version != '1.6.2': raise RuntimeError('Bounded upload requires reviewed Modal 1.6.2')
    signature=inspect.signature(volume.batch_upload)
    if (tuple(signature.parameters) not in {('force',),('self','force')}
            or signature.parameters['force'].kind != inspect.Parameter.POSITIONAL_OR_KEYWORD
            or signature.parameters['force'].default is not False):
        raise RuntimeError('Bounded upload SDK public signature changed')
    return volume.batch_upload(force=False)


def upload_file(local, relative, timeout, checkpoint):
    # A separate finite SDK process bounds SDK connection/upload waits and keeps
    # Modal out of the downloader's resident working set. No credentials in args.
    command=[sys.executable, str(Path(__file__).resolve()), '--upload-worker',
             '--work-dir', str(local.parent), '--run-id', relative.split('/')[1],
             '--worker-basename', local.name]
    log=local.parent/('upload-'+local.stem+'.log')
    started=time.monotonic()
    with log.open('wb') as output:
        os.chmod(log,0o600)
        process=subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        checkpoint({'upload_command':command,'upload_pid':process.pid,'upload_started_at_unix':time.time(), 'upload_log':log.name})
        try:
            while process.poll() is None:
                if time.monotonic()-started > timeout: raise TimeoutError('Per-video upload deadline exceeded')
                if log.stat().st_size > 65536: raise ValueError('Bounded upload log exceeded')
                time.sleep(.25)
            if process.returncode: raise RuntimeError('Upload worker failed; reconciliation required')
        finally: stop_owned(process)



def validate_staged(entry, run):
    value=entry['staged_audio']
    pattern='audio-inputs/'+re.escape(run)+'/'+re.escape(entry['episode']['id'])+r'\.(m4a|webm|mp3|ogg|opus)'
    if (set(value) != {'relative_path','sha256','bytes'}
            or not re.fullmatch(pattern,value.get('relative_path',''))
            or not re.fullmatch(r'[a-f0-9]{64}',value.get('sha256',''))
            or type(value.get('bytes')) is not int or not 0 < value['bytes'] <= MAX_BYTES):
        raise ValueError('Invalid hash-bound staging checkpoint')


def staged_output(state):
    return [{**entry['episode'], 'staged_audio': entry['staged_audio']}
            for entry in state['episodes'] if entry['status'] == 'uploaded']


def stage(items, state_path, output_path, work, run, yt_dlp, node, limit=1,
          timeout=150, downloader=download, uploader=upload_file):
    validate_run(run)
    if not 1 <= limit <= 76 or not 1 <= timeout <= 150: raise ValueError('Bounded limit/timeout required')
    for item in items: validate_item(item)
    if len({item['id'] for item in items}) != len(items) or len({item['youtube_id'] for item in items}) != len(items):
        raise ValueError('Duplicate episode/video identity')
    source_hash = hashlib.sha256(json.dumps(items, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    state_path, output_path, work = Path(state_path), Path(output_path), Path(work)
    work.mkdir(parents=True, exist_ok=True)
    if state_path.exists():
        if state_path.stat().st_size > MAX_MANIFEST: raise ValueError('Progress exceeds 8 MiB')
        state = json.loads(state_path.read_text())
        if state['run_id'] != run or state['volume_name'] != VOLUME or state['input_sha256'] != source_hash:
            raise ValueError('Resume manifest/run mismatch')
    else:
        state = {'schema_version': 1, 'run_id': run, 'volume_name': VOLUME, 'input_sha256': source_hash,
                 'episodes': [{'episode': item, 'status': 'pending'} for item in items]}
    if (state.get('schema_version') != 1 or [entry['episode'] for entry in state['episodes']] != items
            or any(entry['status'] not in {'pending','downloading','downloaded','uploading','uploaded','failed','recovery_required'} for entry in state['episodes'])):
        raise ValueError('Resume episode/status coverage mismatch')
    for entry in state['episodes']:
        if entry['status'] in {'downloaded','uploading','uploaded','recovery_required'}: validate_staged(entry,run)
    def persist():
        atomic_json(state_path, state)
        atomic_json(output_path, staged_output(state))
    persist(); attempted = 0
    for entry in state['episodes']:
        status = entry['status']
        if status in {'uploaded','downloaded','uploading','recovery_required'}:
            validate_staged(entry, run)
        if status == 'uploaded':
            # A crash after durable upload confirmation but before cleanup is
            # safe: only delete an exact local copy with its recorded hash.
            local = work / Path(entry['staged_audio']['relative_path']).name
            if local.exists() and stream_hash(local) == (entry['staged_audio']['sha256'], entry['staged_audio']['bytes']): local.unlink()
            continue
        if status in {'uploading', 'recovery_required'}:
            entry['status'] = 'recovery_required'; persist(); break
        if status == 'failed': continue  # No implicit retries on resume.
        if attempted >= limit: break
        attempted += 1; item = entry['episode']; started = time.monotonic()
        try:
            if status == 'downloading':
                for path in media_files(work, item['id']): path.unlink()
            if status != 'downloaded':
                entry['status'] = 'downloading'; entry['attempt'] = {}; persist()
                def checkpoint(metadata): entry['attempt'].update(metadata); persist()
                command = download_command(item, work, yt_dlp, node)
                local = downloader(item, work, command, timeout, checkpoint)
                sha, size = stream_hash(local)
                relative = 'audio-inputs/' + run + '/' + local.name
                entry['staged_audio'] = {'relative_path': relative, 'sha256': sha, 'bytes': size}
                entry['status'] = 'downloaded'; persist()
            else:
                local = work / Path(entry['staged_audio']['relative_path']).name
                if stream_hash(local) != (entry['staged_audio']['sha256'], entry['staged_audio']['bytes']):
                    raise ValueError('Local downloaded checkpoint hash changed')
            remaining = timeout - (time.monotonic() - started)
            if remaining < 1: raise TimeoutError('No remaining per-video upload budget')
            entry['status'] = 'uploading'; persist()
            def upload_checkpoint(metadata): entry['attempt'].update(metadata); persist()
            uploader(local, entry['staged_audio']['relative_path'], remaining, upload_checkpoint)
            entry['status'] = 'uploaded'; entry['attempt']['seconds'] = round(time.monotonic() - started, 3)
            entry['attempt']['child_peak_rss_bytes_observed'] = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)
            persist()  # Cloud success and durable confirmation BEFORE deletion.
            local.unlink()
        except Exception as error:
            if entry['status'] == 'uploaded':
                entry['attempt']['cleanup_error_type'] = type(error).__name__
                persist()
                continue  # A local cleanup error never erases confirmed upload success.
            uncertain = entry['status'] == 'uploading'
            entry['status'] = 'recovery_required' if uncertain else 'failed'
            entry.setdefault('attempt', {}).update(error_type=type(error).__name__,
                error=str(error)[:256], seconds=round(time.monotonic()-started, 3))
            if not uncertain:
                for path in media_files(work, item['id']): path.unlink()
            persist()
            if uncertain: break
    return {'run_id': run, 'uploaded': sum(item['status']=='uploaded' for item in state['episodes']),
            'failed': sum(item['status']=='failed' for item in state['episodes']),
            'recovery_required': sum(item['status']=='recovery_required' for item in state['episodes']),
            'attempted_this_invocation': attempted}


def private_path(value):
    path = Path(value).absolute()
    if any(part.is_symlink() for part in (path,*path.parents)) or PRIVATE_ROOT.resolve() not in path.resolve().parents:
        raise ValueError('All staging inputs/outputs must remain in private workspace')
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--state', type=Path)
    parser.add_argument('--output-manifest', type=Path)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--yt-dlp', type=Path)
    parser.add_argument('--node', type=Path)
    parser.add_argument('--limit', type=int, default=1)
    parser.add_argument('--upload-worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--worker-basename', help=argparse.SUPPRESS)
    args = parser.parse_args(); validate_run(args.run_id)
    work = private_path(args.work_dir); work.mkdir(parents=True, exist_ok=True)
    if args.upload_worker:
        if not re.fullmatch(r'podcast-[a-f0-9]{20}\.(m4a|webm|mp3|ogg|opus)', args.worker_basename or ''):
            raise ValueError('Safe staged audio basename required')
        local = work / args.worker_basename; stream_hash(local)
        import modal
        volume = modal.Volume.from_name(VOLUME, create_if_missing=False, version=1)
        with BoundedAudioReader(local) as reader:
            with bounded_upload_context(volume) as batch:
                batch.put_file(reader, '/audio-inputs/' + args.run_id + '/' + local.name)
        return
    if not all((args.manifest,args.state,args.output_manifest,args.yt_dlp,args.node)):
        parser.error('Explicit manifest/state/output-manifest/yt-dlp/node required')
    manifest,state,output = map(private_path,(args.manifest,args.state,args.output_manifest))
    if len({manifest,state,output}) != 3: raise ValueError('Distinct input/state/output paths required')
    if manifest.stat().st_size > MAX_MANIFEST: raise ValueError('Manifest exceeds 8 MiB')
    items=json.loads(manifest.read_text())
    if not isinstance(items,list) or len(items)>76: raise ValueError('At most 76 declared video inputs')
    # Exclusive admission for this private staging directory; no concurrent SDK
    # uploads or downloader invocations from two copies of this command.
    import fcntl
    with (work/'staging.lock').open('a') as lock:
        os.chmod(work/'staging.lock',0o600);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        print(json.dumps(stage(items,state,output,work,args.run_id,args.yt_dlp,args.node,args.limit),indent=2))


if __name__ == '__main__': main()
