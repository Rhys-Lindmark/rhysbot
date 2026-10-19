import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import stage_youtube_audio as staging

RUN='speech-youtube-20261019a'

def item(number=1):
    video='abcdefghij'+str(number)
    return {'id':'podcast-'+f'{number:020x}','title':'Public educational video','audio_source_kind':'youtube_audio',
            'youtube_id':video,'youtube_url':'https://www.youtube.com/watch?v='+video,
            'source_url':'https://www.youtube.com/watch?v='+video,'enclosure_url':None,'duration_seconds':927}


class StagingTests(unittest.TestCase):
    def test_exact_identity_and_canonical_urls_only(self):
        staging.validate_item(item())
        for change in [{'id':'../../private'}, {'youtube_id':'bad'}, {'source_url':'https://example.com/file'},
                       {'youtube_url':item()['youtube_url']+'&token=secret'}, {'enclosure_url':'https://example.com/audio'},
                       {'staged_audio':{}}, {'audio_source_kind':'rss_audio'}]:
            with self.assertRaises(ValueError):staging.validate_item({**item(),**change})
        for run in ['../run','speech-youtube-a/b','other-run']:
            with self.assertRaises(ValueError):staging.validate_run(run)

    def test_command_has_no_config_cache_auth_retry_or_postprocessing(self):
        command=staging.download_command(item(),Path('/private/test'),'yt-dlp','node24')
        self.assertNotIn('--cookies',command);self.assertNotIn('-x',command)
        for flag in ['--retries','--fragment-retries','--extractor-retries','--file-access-retries']:
            self.assertEqual(command[command.index(flag)+1],'0')
        self.assertIn('--ignore-config',command);self.assertIn('--no-cache-dir',command)
        self.assertLess(command.index('--no-js-runtimes'),command.index('--js-runtimes'))
        self.assertEqual(command[command.index('--js-runtimes')+1],'node:node24')
        self.assertEqual(command[-1],item()['youtube_url'])
        self.assertEqual(command[command.index('-o')+1],'/private/test/'+item()['id']+'.%(ext)s')

    def test_success_durable_before_delete_resume_skips_and_limit_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory);state=work/'progress.json';output=work/'uploaded.json';calls=[]
            def download(episode,work,command,timeout,checkpoint):
                calls.append(episode['id']);checkpoint({'pid':42,'command':command})
                path=work/(episode['id']+'.m4a');path.write_bytes(b'public audio fixture');return path
            def upload(path,relative,timeout,checkpoint):
                self.assertTrue(path.exists());self.assertEqual(json.loads(state.read_text())['episodes'][0]['status'],'uploading')
                self.assertTrue(relative.startswith('audio-inputs/'+RUN+'/'));checkpoint({'upload_pid':43})
            result=staging.stage([item(),item(2)],state,output,work,RUN,'yt','node',1,downloader=download,uploader=upload)
            self.assertEqual(result['uploaded'],1);self.assertEqual(len(calls),1)
            self.assertFalse((work/(item()['id']+'.m4a')).exists())
            published=json.loads(output.read_text());self.assertEqual(len(published),1)
            self.assertEqual(set(published[0]['staged_audio']),{'relative_path','sha256','bytes'})
            self.assertEqual(published[0]['staged_audio']['sha256'],hashlib.sha256(b'public audio fixture').hexdigest())
            self.assertNotIn('attempt',published[0]);self.assertEqual(state.stat().st_mode & 0o777,0o600)
            staging.stage([item(),item(2)],state,output,work,RUN,'yt','node',1,downloader=download,uploader=lambda *args:None)
            self.assertEqual(calls,[item()['id'],item(2)['id']])
            def forbidden(*args):raise AssertionError('Uploaded input must not repeat')
            staging.stage([item(),item(2)],state,output,work,RUN,'yt','node',1,downloader=forbidden,uploader=forbidden)

    def test_failed_download_cleans_only_own_media_and_never_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory);unrelated=work/'unrelated.m4a';unrelated.write_bytes(b'keep')
            def fail(episode,work,*args):
                (work/(episode['id']+'.m4a')).write_bytes(b'incomplete');raise TimeoutError('deadline')
            result=staging.stage([item()],work/'state.json',work/'out.json',work,RUN,'yt','node',downloader=fail)
            self.assertEqual(result['failed'],1);self.assertTrue(unrelated.exists())
            self.assertFalse((work/(item()['id']+'.m4a')).exists());self.assertEqual(json.loads((work/'out.json').read_text()),[])
            staging.stage([item()],work/'state.json',work/'out.json',work,RUN,'yt','node',downloader=lambda *args:self.fail('retry'))

    def test_uncertain_upload_retains_local_copy_and_blocks_all_following_work(self):
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory);state=work/'state.json';calls=[]
            def download(episode,work,*args):
                calls.append(episode['id']);path=work/(episode['id']+'.webm');path.write_bytes(b'audio');return path
            def fail(*args):raise TimeoutError('upload acknowledgement uncertain')
            result=staging.stage([item(),item(2)],state,work/'out.json',work,RUN,'yt','node',2,downloader=download,uploader=fail)
            self.assertEqual(result['recovery_required'],1);self.assertEqual(len(calls),1)
            self.assertTrue((work/(item()['id']+'.webm')).exists())
            staging.stage([item(),item(2)],state,work/'out.json',work,RUN,'yt','node',2,downloader=lambda *args:self.fail('uncertainty must block'))
            self.assertEqual(json.loads((work/'out.json').read_text()),[])

    def test_changed_input_or_unsafe_resume_path_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory);state=work/'state.json'
            def download(episode,work,*args):
                path=work/(episode['id']+'.m4a');path.write_bytes(b'audio');return path
            staging.stage([item()],state,work/'out.json',work,RUN,'yt','node',downloader=download,uploader=lambda *args:None)
            with self.assertRaises(ValueError):staging.stage([item(2)],state,work/'out.json',work,RUN,'yt','node')
            saved=json.loads(state.read_text());saved['episodes'][0]['staged_audio']['relative_path']='../../secret.m4a';state.write_text(json.dumps(saved))
            with self.assertRaises(ValueError):staging.stage([item()],state,work/'out.json',work,RUN,'yt','node')

    def test_bounded_public_sdk_adapter_version_signature_and_no_overwrite(self):
        class Volume:
            calls=[]
            def batch_upload(self,force=False):self.calls.append(force);return 'public-context'
        volume=Volume()
        self.assertEqual(staging.bounded_upload_context(volume,sdk_version='1.6.2'),'public-context')
        self.assertEqual(volume.calls,[False])
        with self.assertRaises(RuntimeError):staging.bounded_upload_context(volume,sdk_version='1.6.3')
        self.assertEqual(volume.calls,[False])
        class Drift:
            def batch_upload(self,force=True):raise AssertionError('Drifted SDK must not be invoked')
        with self.assertRaises(RuntimeError):staging.bounded_upload_context(Drift(),sdk_version='1.6.2')

    def test_bounded_file_object_preserves_large_hash_and_rejects_full_read_and_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'audio.m4a';block=b'abcde'*200000
            with path.open('wb') as output:
                for _ in range(7):output.write(block)
            expected=staging.stream_hash(path)[0]
            with staging.BoundedAudioReader(path) as reader:
                with self.assertRaises(RuntimeError):reader.read()
                with self.assertRaises(RuntimeError):getattr(reader,'name')
                digest=hashlib.sha256();size=0
                while chunk:=reader.read(16*1024*1024):
                    self.assertLessEqual(len(chunk),1024*1024);digest.update(chunk);size+=len(chunk)
                self.assertEqual(digest.hexdigest(),expected);self.assertEqual(size,path.stat().st_size)
                self.assertEqual(reader.max_read_bytes,1024*1024)
                reader.seek(0);self.assertEqual(reader.tell(),0)
            tiny=Path(directory)/'short.m4a';tiny.write_bytes(b'public audio')
            with staging.BoundedAudioReader(tiny) as reader:self.assertEqual(reader.read(),b'public audio')

    def test_cleanup_failure_preserves_confirmed_upload_and_local_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory);state=work/'state.json';audio=work/(item()['id']+'.m4a')
            def download(*args):audio.write_bytes(b'audio');return audio
            original=Path.unlink
            def blocked(path,*args,**kwargs):
                if path==audio:raise PermissionError('cleanup denied')
                return original(path,*args,**kwargs)
            with patch.object(Path,'unlink',blocked):
                result=staging.stage([item()],state,work/'out.json',work,RUN,'yt','node',downloader=download,uploader=lambda *args:None)
            self.assertEqual(result['uploaded'],1);self.assertTrue(audio.exists())
            self.assertEqual(json.loads(state.read_text())['episodes'][0]['attempt']['cleanup_error_type'],'PermissionError')
            self.assertEqual(len(json.loads((work/'out.json').read_text())),1)

    def test_heap_options_are_child_only_and_override_parent_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory);target=work/(item()['id']+'.m4a')
            command=[sys.executable,'-c','from pathlib import Path; import os,json; Path('+repr(str(target))+').write_bytes(b"audio"); print(json.dumps({"NODE_OPTIONS":os.environ.get("NODE_OPTIONS")}))']
            import os
            with patch.dict(os.environ,{'NODE_OPTIONS':'parent-value-must-remain'}):
                result=staging.download(item(),work,command,5,lambda metadata:None)
                self.assertEqual(os.environ['NODE_OPTIONS'],'parent-value-must-remain')
            value=json.loads((work/('download-'+item()['id']+'.log')).read_text())
            self.assertEqual(value['NODE_OPTIONS'],'--max-old-space-size=64 --max-semi-space-size=4')
            self.assertEqual(result.read_bytes(),b'audio')

    def test_real_child_timeout_is_reaped_and_output_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory);seen={}
            command=[sys.executable,'-c','import time; time.sleep(10)']
            with self.assertRaises(TimeoutError):
                staging.download(item(),work,command,.05,seen.update)
            import os
            with self.assertRaises(ProcessLookupError):os.kill(seen['pid'],0)
            target=work/(item()['id']+'.m4a')
            command=[sys.executable,'-c','from pathlib import Path; import time; Path('+repr(str(target))+').write_bytes(b"large"); time.sleep(10)']
            with patch.object(staging,'MAX_BYTES',3):
                with self.assertRaises((RuntimeError,ValueError)):
                    staging.download(item(),work,command,2,seen.update)
            self.assertLessEqual(target.stat().st_size,3)

    def test_symlink_and_byte_ceiling_fail_without_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory);target=work/'target';target.write_bytes(b'large fixture')
            link=work/(item()['id']+'.m4a');link.symlink_to(target)
            with self.assertRaises(ValueError):staging.media_files(work,item()['id'])
            with self.assertRaises(ValueError):staging.stream_hash(link)
            with patch.object(staging,'MAX_BYTES',3):
                with self.assertRaises(ValueError):staging.stream_hash(target)
            self.assertTrue(target.exists())


if __name__=='__main__':unittest.main()
