"""Extract actual optimizer logs and render reproducible Matplotlib figures.

Extract is stdlib-only; render runs locally or on a small remote CPU container.
No invented intermediate validation samples or model-quality scores.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def extract(evidence, output):
    curves, provenance = {}, []
    for family, run, stages in [('nanochat', 'nanochat-train-20261019a', {'pretrain': 2, 'sft': 3}),
                                ('qwen', 'qwen-train-20261019a', {'cpt': 0, 'sft': 1})]:
        for stage, number in stages.items():
            path = evidence / run / ('stage-%02d.log' % number)
            raw = path.read_bytes()
            loss, validation = [], []
            for line in raw.decode().splitlines():
                if family == 'qwen':
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    if item.get('stage') == stage and 'optimizer_step' in item and 'loss' in item:
                        loss.append({'step': item['optimizer_step'], 'loss': item['loss']})
                else:
                    match = re.search(r'^step\s+(\d+)(?:/\d+)?\s+.*?\| loss:\s*([\d.]+|nan|inf)', line)
                    if match:
                        loss.append({'step': int(match[1]), 'loss': None if match[2] in {'nan', 'inf'} else float(match[2]),
                                     'logged_nonfinite': match[2] if match[2] in {'nan', 'inf'} else None})
                    match = re.search(r'^Step\s+(\d+)\s*\| Validation bpb:\s*([\d.]+)', line)
                    if match:
                        validation.append({'step': int(match[1]), 'bits_per_byte': float(match[2])})
            curves[family + '_' + stage] = {'run_id': run, 'training': loss, 'validation': validation,
                'loss_note': 'Logged upstream training loss; nanochat display may use smoothing. Qwen is per-optimizer-step minibatch loss.',
                'objective': 'assistant-token-only instruction loss' if stage == 'sft' else 'next-token prediction'}
            provenance.append({'run_id': run, 'log': path.name, 'sha256': hashlib.sha256(raw).hexdigest(),
                               'training_points': len(loss), 'validation_points': len(validation)})
    expected = {'qwen_cpt': 1000, 'qwen_sft': 400, 'nanochat_sft': 80}
    for name, count in expected.items():
        if len(curves[name]['training']) != count:
            raise ValueError('Missing actual logged optimizer updates: ' + name)
    data = {'schema_version': 1, 'curves': curves, 'log_provenance': provenance,
            'interpretation': 'Lower prediction loss does not establish factual accuracy or persona fidelity. Different objectives/tokenizers are not directly comparable.'}
    output.write_text(json.dumps(data, indent=2) + '\n')
    return data


def render(data, comparison, stats, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 11, 'axes.spines.top': False,
                         'axes.spines.right': False, 'svg.fonttype': 'none', 'figure.facecolor': '#faf9f5',
                         'axes.facecolor': '#faf9f5', 'axes.titleweight': 'bold'})
    output.mkdir(parents=True, exist_ok=True)
    def save(fig, name):
        for extension in ('svg', 'png'):
            fig.savefig(output / (name + '.' + extension), bbox_inches='tight', dpi=140)
        plt.close(fig)
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
    labels = {'nanochat_pretrain': 'Nanochat · from scratch', 'nanochat_sft': 'Nanochat · instruction tuning',
              'qwen_cpt': 'Qwen · continued pretraining', 'qwen_sft': 'Qwen · instruction tuning'}
    for ax, key in zip(axes.flat, labels):
        curve = data['curves'][key]
        samples = curve['training']
        x, y = [r['step'] for r in samples], [r['loss'] if r['loss'] is not None else float('nan') for r in samples]
        ax.plot(x, y, alpha=.35, lw=.7, color='#327a59', label='Logged loss')
        window = 25 if len(y) > 100 else 5
        ax.plot(x, [sum(y[max(0, i-window+1):i+1]) / min(window, i+1) for i in range(len(y))],
                color='#246144', lw=1.8, label='%d-step trailing mean' % window)
        ax.set(title=labels[key], xlabel='Logged optimizer step', ylabel='Training cross-entropy (nats/token)')
        bad = [r['step'] for r in samples if r['loss'] is None]
        if bad:
            ax.axvspan(min(bad), max(x), alpha=.12, color='#bb632f')
            ax.text(.97, .92, 'Nonfinite logged loss from step %d\n(%d NaN samples; not drawn)' % (min(bad), len(bad)),
                    transform=ax.transAxes, ha='right', va='top', color='#93431c', fontsize=9)
        ax.grid(alpha=.15); ax.legend(fontsize=8)
    fig.suptitle('Actual training runs · each panel has its own objective and scale', fontsize=15)
    save(fig, 'training-loss')

    fig, axes = plt.subplots(1, 2, figsize=(12, 4), constrained_layout=True)
    for ax, key in zip(axes, ['nanochat_pretrain', 'nanochat_sft']):
        rows = data['curves'][key]['validation']
        ax.plot([r['step'] for r in rows], [r['bits_per_byte'] for r in rows], 'o-', color='#327a59')
        best = min(rows, key=lambda r: r['bits_per_byte'])
        ax.scatter([best['step']], [best['bits_per_byte']], s=85, facecolors='none', edgecolors='#bb632f', lw=2)
        ax.set(title=labels[key], xlabel='Optimizer step', ylabel='Validation bits per byte · lower is better')
        ax.grid(alpha=.15)
    fig.suptitle('Nanochat validation · observed checkpoints only', fontsize=15)
    save(fig, 'nanochat-validation')

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5), constrained_layout=True)
    keys = ['base', 'instruction_baseline', 'cpt', 'sft']
    names = ['Untouched\nbase', 'Instruction\nbaseline', 'After\nCPT', 'After\nCPT + SFT']
    for ax, metric, title in zip(axes, ['heldout', 'assistant_only'], ['32 held-out text excerpts', '10 held-out study questions · shared ChatML']):
        values = [comparison['variants'][key][metric]['nll_per_token'] for key in keys]
        bars = ax.bar(names, values, color=['#b3b8b4', '#8c9da3', '#71a78b', '#327a59'])
        ax.bar_label(bars, fmt='%.3f', padding=3, fontsize=10)
        ax.set(title=title, ylabel='Negative log-likelihood · nats/token', ylim=(0, max(values) * 1.18))
        ax.grid(axis='y', alpha=.15); ax.set_axisbelow(True)
    fig.suptitle('Qwen diagnostic evaluation · lower loss is not an accuracy score', fontsize=15)
    save(fig, 'qwen-evaluation')

    names = {'anki': 'Anki', 'writing': 'Writing', 'hee': 'How Everything Evolved',
             'podcasts_youtube': 'Podcasts / YouTube', 'x': 'X posts'}
    groups = sorted(stats['collection_metrics'], key=lambda k: stats['collection_metrics'][k]['text_utf8_bytes'])
    values = [stats['collection_metrics'][k]['text_utf8_bytes'] / 1e6 for k in groups]
    fig, ax = plt.subplots(figsize=(10, 4.5), constrained_layout=True)
    bars = ax.barh([names[k] for k in groups], values, color='#327a59')
    ax.bar_label(bars, labels=['%.2f MB' % v for v in values], padding=5)
    ax.set(title='Research corpus size · actual UTF-8 text bytes', xlabel='Decimal megabytes · 1 MB = 1,000,000 bytes', xlim=(0, max(values) * 1.2))
    ax.grid(axis='x', alpha=.15); ax.set_axisbelow(True)
    save(fig, 'corpus-bytes')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--render', action='store_true')
    parser.add_argument('--output', type=Path, default=ROOT / 'site/charts')
    args = parser.parse_args()
    path = ROOT / 'results/training-curves.json'
    if args.evidence:
        extract(args.evidence, path)
    if args.render:
        render(json.loads(path.read_text()), json.loads((ROOT / 'results/qwen-comparison.json').read_text()),
               json.loads((ROOT / 'data/public/stats.json').read_text()), args.output)
