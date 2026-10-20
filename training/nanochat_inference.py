"""Pure scratch-checkpoint conversation; no retrieval, Qwen or tool execution."""
import os
import sys
import time

SYSTEM = "You are Rhysbot. Answer the conversation directly."
MAX_INPUT = 384
MAX_OUTPUT = 96
_CACHE = None


def checked_history(messages):
    if not isinstance(messages, list) or not 1 <= len(messages) <= 11 or len(messages) % 2 != 1:
        raise ValueError('invalid_history')
    result = []
    size = 0
    for i, row in enumerate(messages):
        if not isinstance(row, dict) or row.get('role') != ('assistant' if i % 2 else 'user'):
            raise ValueError('invalid_history')
        text = row.get('content')
        if not isinstance(text, str) or not text.strip():
            raise ValueError('invalid_history')
        size += len(text.encode())
        result.append({'role': row['role'], 'content': text.replace('<|', '< |').replace('|>', '| >')})
    if size > 12000:
        raise ValueError('history_too_long')
    return result


def fit_history(messages, render, maximum=MAX_INPUT):
    kept = checked_history(messages)
    dropped = 0
    while True:
        tokens = render([{'role': 'system', 'content': SYSTEM}] + kept)
        if len(tokens) <= maximum:
            return tokens, dropped
        if len(kept) == 1:
            raise ValueError('tokenized_input_too_long')
        del kept[:2]
        dropped += 1


def generate(messages, target, pins, artifacts='/artifacts'):
    global _CACHE
    import torch
    from serve import checkpoint_artifacts
    start = time.monotonic()
    paths = checkpoint_artifacts(artifacts, 'nanochat', target, pins)
    key = (target['weights_sha256'], target['tokenizer_sha256'], target['metadata_sha256'])
    if _CACHE is None or _CACHE[0] != key:
        torch.set_num_threads(2)
        os.environ['NANOCHAT_BASE_DIR'] = str(paths['base'])
        sys.path.insert(0, '/opt/nanochat')
        import kernels
        kernels.has_kernel = lambda *args, **kwargs: False
        kernels.get_kernel = lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('Unpinned kernels forbidden'))
        import nanochat.common as common
        common.COMPUTE_DTYPE = torch.float32
        from nanochat.checkpoint_manager import build_model
        import nanochat.gpt as gpt
        gpt.COMPUTE_DTYPE = torch.float32
        model, tokenizer, _ = build_model(str(paths['directory']), target['step'], torch.device('cpu'), 'eval')
        _CACHE = (key, model.float().eval(), tokenizer)
    _, model, tokenizer = _CACHE
    def render(rows):
        return tokenizer.render_for_completion({'messages': rows + [{'role': 'assistant', 'content': ''}]})
    tokens, dropped = fit_history(messages, render)
    initial = len(tokens)
    endings = {tokenizer.encode_special('<|assistant_end|>'), tokenizer.get_bos_token_id(),
               tokenizer.encode_special('<|user_start|>')}
    maximum = min(MAX_OUTPUT, model.config.sequence_len - initial)
    with torch.inference_mode():
        for _ in range(maximum):
            logits = model(torch.tensor([tokens], device='cpu'))
            token = int(logits[0, -1].argmax())
            if token in endings:
                break
            tokens.append(token)
    return {'text': tokenizer.decode(tokens[initial:]), 'model': 'pure_nanochat',
            'checkpoint_run_id': target['run_id'], 'weights_sha256': target['weights_sha256'],
            'input_tokens': initial, 'output_tokens': len(tokens) - initial,
            'dropped_history_pairs': dropped, 'elapsed_seconds': time.monotonic() - start,
            'retrieval': False, 'qwen_fallback': False}
