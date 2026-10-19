"""Measure released text with a downloaded, pinned tokenizer (no model weights)."""
import argparse
import json
from collections import Counter
from pathlib import Path
from corpus_lib import digest, read_jsonl


def main():
    from tokenizers import Tokenizer
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tokenizer-json',required=True,type=Path)
    args=p.parse_args()
    root=Path(__file__).resolve().parents[1]
    pins=json.loads((root/'training/upstream-pins.json').read_text())
    model='Qwen/Qwen3.5-4B-Base'
    tok=Tokenizer.from_file(str(args.tokenizer_json))
    by_source=Counter()
    corpus=root/'data/public/corpus.jsonl'
    for r in read_jsonl(corpus):
        by_source[r['source_type']]+=len(tok.encode(r['text'],add_special_tokens=False).ids)
    stats_path=root/'data/public/stats.json'
    stats=json.loads(stats_path.read_text())
    stats.update(tokens=sum(by_source.values()),tokens_by_source=dict(by_source),
                 token_count_note='Exact count of released text only, without extra special tokens or source headers.',
                 tokenizer={'model':model,'revision':pins['models'][model]['revision'],'tokenizer_file_sha256':digest(args.tokenizer_json.read_text())},
                 token_count_corpus_sha256=digest(corpus.read_text()))
    stats_path.write_text(json.dumps(stats,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({'tokens':stats['tokens'],'by_source':dict(by_source)}))


if __name__=='__main__':
    main()
