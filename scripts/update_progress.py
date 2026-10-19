"""Generate a self-contained local progress page from the durable checklist."""
from pathlib import Path
import html
import json
import re

root=Path(__file__).resolve().parents[1]
state=(root/'PROJECT_STATE.md').read_text()
history=[json.loads(line) for line in (root/'progress.jsonl').read_text().splitlines() if line.strip()]
total=len(re.findall(r'^- \[[ x]\]',state,re.M))
done=len(re.findall(r'^- \[x\]',state,re.M))
points=' '.join(f'{20+i*760/max(1,len(history)-1):.1f},{90-80*len(h.get("completed",[]))/max(1,total):.1f}' for i,h in enumerate(history))
page=f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Rhysbot progress</title>
<style>body{{max-width:850px;margin:3rem auto;padding:0 1rem;font:16px/1.6 system-ui;background:#f7f5ee;color:#24322c}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}svg{{width:100%;height:110px}}</style>
<h1>Rhysbot progress</h1><p>{done} / {total} acceptance items complete. Partial and blocked items remain unchecked.</p>
<svg viewBox="0 0 800 100" role="img" aria-label="Completed checklist items over time"><polyline points="{points}" fill="none" stroke="#27735b" stroke-width="3"/></svg><pre>{html.escape(state)}</pre></html>'''
(root/'progress.html').write_text(page)
print(f'Progress: {done}/{total} accepted items')
