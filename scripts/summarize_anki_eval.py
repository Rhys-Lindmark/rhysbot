"""Validate collected paired evaluation artifacts and aggregate measured metrics."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path


def jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def summarize(directory, dataset):
    directory, dataset = Path(directory), Path(dataset)
    report = json.loads((directory/'report.json').read_text())
    manifest = json.loads((dataset/'manifest.json').read_text())
    raw = (dataset/'anki.jsonl').read_bytes()
    if report['status'] != 'measured' or hashlib.sha256(raw).hexdigest() != report['qa_sha256'] or report['qa_sha256'] != manifest['qa_sha256'] or report['corpus_sha256'] != manifest['corpus_sha256']:
        raise ValueError('Measured report or dataset provenance mismatch')
    cards = {row['id']: row for row in jsonl(dataset/'anki.jsonl')}
    scores, samples = jsonl(directory/'scores.jsonl'), jsonl(directory/'samples.jsonl')
    ids = [row['id'] for row in scores]
    skipped = [row['id'] for row in report['skipped']]
    if len(ids) != len(set(ids)) or len(skipped) != len(set(skipped)) or set(ids)&set(skipped) or not set(ids+skipped) <= cards.keys():
        raise ValueError('Invalid score/skip coverage')
    if len(scores) != report['scored_records'] or len(ids+skipped) != report['input_records']:
        raise ValueError('Coverage counts disagree')
    if not report['pilot'] and set(ids+skipped) != cards.keys():
        raise ValueError('Full evaluation omitted cards without reporting skips')
    cohorts = {}
    for cohort in ['train', 'validation', 'test']:
        rows = [row for row in scores if row['frozen_sft_split'] == cohort]
        for row in rows:
            if cards[row['id']]['frozen_sft_split'] != cohort or type(row['target_tokens']) is not int or not 0 < row['target_tokens'] <= row['full_target_tokens'] or any(not math.isfinite(row[k]) or row[k] < 0 for k in ['rhys_nll', 'qwen_base_nll']):
                raise ValueError('Invalid per-card score')
        tokens = sum(row['target_tokens'] for row in rows)
        winners = Counter('ties' if abs(row['rhys_nll']-row['qwen_base_nll']) <= 1e-7 else 'rhys_lower_loss' if row['rhys_nll'] < row['qwen_base_nll'] else 'base_lower_loss' for row in rows)
        cohorts[cohort] = {'count': len(rows), 'target_tokens': tokens, 'truncated': sum(row['truncated'] for row in rows), 'image_bearing_cards': sum(cards[row['id']].get('source_has_images', False) for row in rows), **{k: winners[k] for k in ['rhys_lower_loss', 'base_lower_loss', 'ties']}, **{k: sum(row[k]*row['target_tokens'] for row in rows)/tokens if tokens else None for k in ['rhys_nll', 'qwen_base_nll']}}
    if sum(row['count'] for row in cohorts.values()) != len(scores):
        raise ValueError('Unknown cohort')
    if len(samples) != report['generation_samples'] or len({row['id'] for row in samples}) != len(samples):
        raise ValueError('Generation sample count mismatch')
    for row in samples:
        if row['id'] not in ids or any(row[k] != cards[row['id']][k] for k in ['qa_sha256', 'question', 'reference', 'frozen_sft_split']) or cards[row['id']].get('source_has_images'):
            raise ValueError('Generation sample provenance mismatch')
        for key in ['rhys', 'qwen_base']:
            answer = row[key]
            if type(answer['normalized_exact_match']) is not bool or not math.isfinite(answer['token_f1']) or not 0 <= answer['token_f1'] <= 1:
                raise ValueError('Invalid generated metric')
    generation = {'count': len(samples), 'cohorts': dict(Counter(row['frozen_sft_split'] for row in samples))}
    for key in ['rhys', 'qwen_base']:
        generation[key] = {'exact_match': sum(row[key]['normalized_exact_match'] for row in samples)/len(samples) if samples else None, 'token_f1': sum(row[key]['token_f1'] for row in samples)/len(samples) if samples else None}
    return {**{k: v for k, v in report.items() if k != 'loading'}, 'cohorts': cohorts, 'generation': generation, 'aggregation': 'Target-token-weighted NLL; per-card lower-loss counts; unweighted generation exact-match and token-F1 means. These are reference agreement metrics, not verified factual accuracy.', 'limitations': manifest['limitations']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--dataset', type=Path, default=Path(__file__).resolve().parents[1]/'data/evaluation')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.directory, args.dataset)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'status': result['status'], 'scored': result['scored_records'], 'generated_pairs': result['generation']['count']}))


if __name__ == '__main__':
    main()
