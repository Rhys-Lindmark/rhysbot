"""Build a private D1 import from the already-reviewed public corpus. No new source ingestion."""
import hashlib,json,re,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
def quote(value):return "'"+str(value or '').replace("'","''")+"'"
def build(output):
 count=0;counts={};h=hashlib.sha256()
 with Path(output).open('w') as out:
  out.write("CREATE VIRTUAL TABLE IF NOT EXISTS rb_sources USING fts5(id UNINDEXED,title,kind UNINDEXED,url UNINDEXED,attribution UNINDEXED,text,tokenize='unicode61');\nDELETE FROM rb_sources;\n")
  with (root/'data/public/corpus.jsonl').open('rb') as source:
   for line in source:
    h.update(line);r=json.loads(line);text=r['text'];kind=r['source_type'];attribution=r['authorship']
    if r.get('speaker_transcript'):
     text='\n'.join(re.findall(r'(?:^|\n)\[Rhys Lindmark [^\]]+\] ([\s\S]*?)(?=\n\[[^\]\n]+\] |\Z)',text)).strip()
     attribution='Rhys voice match, automatic transcript; quotes may be others'
     if len(text)<40:continue
    elif kind in {'youtube_transcript','podcast_transcript'}:continue
    if kind=='anki':attribution='Rhys-curated study note; not necessarily his views'
    row=[r['id'],r['title'],kind,r.get('source_url'),attribution,text]
    out.write('INSERT INTO rb_sources(id,title,kind,url,attribution,text) VALUES('+','.join(map(quote,row))+');\n');count+=1;counts[kind]=counts.get(kind,0)+1
 return {'source_corpus_sha256':h.hexdigest(),'indexed_excerpts':count,'by_source':counts,'import_bytes':Path(output).stat().st_size,'import_md5':hashlib.md5(Path(output).read_bytes()).hexdigest()}
if __name__=='__main__':print(json.dumps(build(sys.argv[1]),indent=2))
