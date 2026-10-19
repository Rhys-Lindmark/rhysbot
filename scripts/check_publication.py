"""Audit files selected for Git publication. Never prints matching secret values."""
import re
import subprocess
from pathlib import Path

PATTERNS = [
    ('local home-directory path',re.compile(r'/Users/[^/\s]+/(?:Downloads|Documents|Library)/')),
    ('credential',re.compile(r'\b(?:ghp_[A-Za-z0-9]{30,}|sk-(?:proj-)?[A-Za-z0-9_-]{30,}|ak-[A-Za-z0-9]{20,})\b')),
    ('private key',re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')),
]


def scan_index_blob(root,name,limit_bytes):
    """Stream staged records instead of retaining a whole corpus plus Unicode copy.

    All scanner patterns stay within a physical line. Reading complete records
    preserves matches without splitting UTF-8 characters or boundary patterns.
    """
    prefix=['git','-C',str(root)]
    size=int(subprocess.check_output([*prefix,'cat-file','-s',':'+name]))
    if size>limit_bytes:return ['unexpectedly large artifact']
    patterns=PATTERNS[:1] if name.startswith(('tests/','scripts/corpus_lib.py')) else PATTERNS
    found=set();observed=0
    with subprocess.Popen([*prefix,'show',':'+name],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL) as process:
        for line in process.stdout:
            observed+=len(line)
            text=line.decode('utf-8',errors='replace')
            for kind,pattern in patterns:
                if kind not in found and pattern.search(text):found.add(kind)
        if process.wait()!=0:raise RuntimeError('Unable to read staged publication blob')
    if observed!=size:return ['staged artifact changed during audit']
    return [kind for kind,_ in patterns if kind in found]


def audit(root):
    files=subprocess.check_output(['git','-C',str(root),'ls-files','-z']).decode().split('\0')
    banned_suffixes=('.apkg','.colpkg','.anki2','.sqlite','.db','.safetensors','.pt','.mp4','.mp3','.m4a')
    errors=[]
    for name in filter(None,files):
        if any(x in Path(name).parts for x in ('private','raw','checkpoints','.venv','node_modules')) or name.endswith(banned_suffixes):
            errors.append((name,'forbidden private/bulk artifact'))
            continue
        # Check the staged blob, not merely the working-tree file.
        # The reviewed corpus and its train split intentionally exceed 40 MiB.
        # Other artifacts retain the tighter accidental-bulk guard.
        limit_mib = 64 if name in {'data/public/corpus.jsonl', 'data/public/train.jsonl'} else 40
        errors.extend((name,kind) for kind in scan_index_blob(root,name,limit_mib*1024*1024))
    return list(filter(None,files)),errors


def main():
    files,errors=audit(Path(__file__).resolve().parents[1])
    if errors:
        for name,kind in errors:print('REJECTED',name,kind)
        raise SystemExit(1)
    print('Publication audit passed:',len(files),'staged text/code artifacts; no raw archives or detected credentials.')


if __name__=='__main__':main()
