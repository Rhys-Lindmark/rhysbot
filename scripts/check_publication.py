"""Audit files selected for Git publication. Never prints matching secret values."""
import re
import subprocess
from pathlib import Path


def main():
    root=Path(__file__).resolve().parents[1]
    files=subprocess.check_output(['git','-C',str(root),'ls-files','-z']).decode().split('\0')
    banned_suffixes=('.apkg','.colpkg','.anki2','.sqlite','.db','.safetensors','.pt','.mp4','.mp3','.m4a')
    errors=[]
    for name in filter(None,files):
        if any(x in Path(name).parts for x in ('private','raw','checkpoints','.venv','node_modules')) or name.endswith(banned_suffixes):
            errors.append((name,'forbidden private/bulk artifact'))
            continue
        # Check the staged blob, not merely the working-tree file.
        data=subprocess.check_output(['git','-C',str(root),'show',':'+name])
        if len(data)>40*1024*1024:
            errors.append((name,'unexpectedly large artifact'))
        text=data.decode('utf-8',errors='replace')
        if re.search(r'/Users/[^/\s]+/(?:Downloads|Documents|Library)/',text):
            errors.append((name,'local home-directory path'))
        if not name.startswith(('tests/','scripts/corpus_lib.py')):
            for kind,pattern in [
                ('credential',r'\b(?:ghp_[A-Za-z0-9]{30,}|sk-(?:proj-)?[A-Za-z0-9_-]{30,}|ak-[A-Za-z0-9]{20,})\b'),
                ('private key',r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
            ]:
                if re.search(pattern,text):errors.append((name,kind))
    if errors:
        for name,kind in errors:print('REJECTED',name,kind)
        raise SystemExit(1)
    print('Publication audit passed:',len(list(filter(None,files))),'staged text/code artifacts; no raw archives or detected credentials.')


if __name__=='__main__':main()
