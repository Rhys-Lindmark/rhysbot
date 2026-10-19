"""Publication scanning must inspect the index and never disclose matched text."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from check_publication import audit,scan_index_blob


class PublicationTests(unittest.TestCase):
    def test_staged_secret_is_rejected_even_when_worktree_was_cleaned(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);subprocess.run(['git','init','-q',str(root)],check=True)
            token='ghp_'+'A'*31
            home='/'+'Users'+'/fixture/'+'Downloads/'+'source.txt'
            path=root/'notes.txt'
            path.write_text('é'*100000+' '+token+'\n'+home+'\n-----BEGIN PRIVATE KEY-----')
            subprocess.run(['git','-C',str(root),'add','notes.txt'],check=True)
            path.write_text('The worktree looks clean, but the staged blob still contains secrets.\n')
            flags=scan_index_blob(root,'notes.txt',1024*1024)
            self.assertEqual(flags,['local home-directory path','credential','private key'])
            self.assertNotIn(token,json.dumps(flags));self.assertNotIn(home,json.dumps(flags))

    def test_unstaged_secret_is_not_part_of_publication_and_size_guard_runs_first(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);subprocess.run(['git','init','-q',str(root)],check=True)
            path=root/'notes.txt';path.write_text('Public source text.\n')
            subprocess.run(['git','-C',str(root),'add','notes.txt'],check=True)
            path.write_text('ghp_'+'B'*31)
            self.assertEqual(scan_index_blob(root,'notes.txt',1024),[])
            path.write_bytes(b'x'*1025);subprocess.run(['git','-C',str(root),'add','notes.txt'],check=True)
            self.assertEqual(scan_index_blob(root,'notes.txt',1024),['unexpectedly large artifact'])

    def test_forbidden_raw_sources_are_rejected_without_reading_their_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);subprocess.run(['git','init','-q',str(root)],check=True)
            (root/'data/raw').mkdir(parents=True);(root/'data/raw/original.txt').write_text('unreviewed')
            (root/'audio.m4a').write_bytes(b'audio');(root/'public.txt').write_text('Reviewed source.')
            subprocess.run(['git','-C',str(root),'add','.'],check=True)
            names,errors=audit(root)
            self.assertEqual(len(names),3)
            self.assertEqual(errors,[('audio.m4a','forbidden private/bulk artifact'),
                                     ('data/raw/original.txt','forbidden private/bulk artifact')])
