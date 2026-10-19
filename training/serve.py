"""Scale-to-zero, authenticated inference from explicitly pinned trained artifacts.

No deployment occurs on import. Set RHYSBOT_SERVING_TARGETS to a reviewed JSON
checkpoint allowlist before `modal deploy training/serve.py`. See docs/serving.md.
Pure validation/accounting helpers are stdlib-only and locally testable.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
import uuid

# Serialized Modal functions import helpers from the explicitly copied package.
HERE = Path('/rhysbot/training') if Path('/rhysbot/training/runtime.py').is_file() else Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from runtime import read_json, write_json, file_sha, safe_run_id
from budget import CAP, committed, reserve, finish

ORIGIN = 'https://ai.rhyslindmark.com'
GPU_TIMEOUT = 180
MAX_BODY_BYTES = 8192
MAX_PROMPT_BYTES = 1600
MAX_INPUT_TOKENS = 384
MAX_NEW_TOKENS = 64
SYSTEM = ("You are Rhysbot, an experimental model trained on Rhys Lindmark's public writing "
          "and curated notes. You are not Rhys. Distinguish guest views and quotations from "
          "Rhys's views. Do not invent private facts. Your outputs may be incorrect.")
IDENTITY = {
    'nanochat': 'Rhysbot scratch experiment: tiny nanochat checkpoint, limited training; not a capable general chat model.',
    'qwen': 'Rhysbot Qwen adaptation: pinned Qwen base plus actual saved CPT/SFT adapter; not Rhys or a validated personality model.'}


class Rejected(ValueError):
    def __init__(self, status, code):
        super().__init__(code)
        self.status, self.code = status, code


def deployment_targets(raw):
    targets = json.loads(raw)
    if not isinstance(targets, dict) or not targets or set(targets) - {'nanochat', 'qwen'}:
        raise ValueError('Explicit nonempty nanochat/qwen checkpoint allowlist required')
    for family, target in targets.items():
        expected = {'run_id', 'weights_sha256', 'metadata_sha256'} | (
            {'step', 'tokenizer_sha256'} if family == 'nanochat' else {'adapter_config_sha256'})
        if not isinstance(target, dict) or set(target) != expected:
            raise ValueError('Unexpected deployment fields; no arbitrary paths or model IDs')
        safe_run_id(target['run_id'])
        if family == 'nanochat' and (type(target['step']) is not int or target['step'] < 1):
            raise ValueError('Explicit completed nanochat checkpoint step required')
        for key in expected - {'run_id', 'step'}:
            if not isinstance(target[key], str) or not re.fullmatch('[a-f0-9]{64}', target[key]):
                raise ValueError('Artifact SHA256 required')
    return targets


def validated_request(body, targets):
    if not isinstance(body, dict) or set(body) - {'model', 'prompt', 'max_new_tokens'}:
        raise Rejected(400, 'invalid_request_fields')
    family, prompt = body.get('model'), body.get('prompt')
    if not isinstance(family, str) or family not in targets:
        raise Rejected(400, 'model_not_enabled')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode()) > MAX_PROMPT_BYTES:
        raise Rejected(400, 'invalid_prompt_length')
    # User text cannot introduce ChatML/nanochat roles or tool delimiters.
    if '<|' in prompt or '|>' in prompt:
        raise Rejected(400, 'reserved_token_delimiter')
    maximum = body.get('max_new_tokens', MAX_NEW_TOKENS)
    if type(maximum) is not int or not 1 <= maximum <= MAX_NEW_TOKENS:
        raise Rejected(400, 'invalid_output_limit')
    return {'model': family, 'prompt': prompt, 'max_new_tokens': maximum}


def request_digest(request):
    return hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()


def checked_file(path, expected, run_root):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or run_root.resolve() not in path.resolve().parents:
        raise ValueError('Missing or redirected deployment artifact')
    if file_sha(path) != expected:
        raise ValueError('Deployment artifact hash mismatch')
    return path


def checkpoint_artifacts(artifacts, family, target, pins):
    """Fail before model load on incomplete runs, missing adapters or stale pins."""
    run_root = Path(artifacts) / 'runs' / safe_run_id(target['run_id'])
    if run_root.is_symlink():
        raise ValueError('Redirected checkpoint run directory')
    launch = read_json(run_root / 'launch.json')
    if (launch.get('run_id') != target['run_id'] or launch.get('family') != family
            or launch.get('phase') not in {'pilot', 'train'}
            or launch.get('status') != 'completed_actual_artifacts' or launch.get('pins') != pins):
        raise ValueError('Only completed, pinned training runs may be served')
    if family == 'nanochat':
        base = run_root / 'nanochat'
        directory = base / 'chatsft_checkpoints/rhysbot'
        step = target['step']
        weights = checked_file(directory / ('model_%06d.pt' % step), target['weights_sha256'], run_root)
        metadata = checked_file(directory / ('meta_%06d.json' % step), target['metadata_sha256'], run_root)
        checked_file(base / 'tokenizer/tokenizer.pkl', target['tokenizer_sha256'], run_root)
        if read_json(metadata).get('step') != step:
            raise ValueError('Nanochat optimizer step mismatch')
        return {'root': run_root, 'base': base, 'directory': directory, 'weights': weights, 'launch': launch}
    directory = run_root / 'qwen/sft'
    weights = checked_file(directory / 'adapter_model.safetensors', target['weights_sha256'], run_root)
    metadata = checked_file(directory / 'run.json', target['metadata_sha256'], run_root)
    checked_file(directory / 'adapter_config.json', target['adapter_config_sha256'], run_root)
    report = read_json(metadata)
    model_id = 'Qwen/Qwen3.5-4B-Base'
    if (report.get('status') != 'actual_adapter_saved' or report.get('stage') != 'sft'
            or report.get('optimizer_steps', 0) < 1 or report.get('trainable_parameters', 0) < 1
            or report.get('data') != launch.get('frozen_data')
            or report.get('loading', {}).get('model_id') != model_id
            or report.get('loading', {}).get('revision') != pins['models'][model_id]['revision']):
        raise ValueError('Completed Qwen SFT adapter provenance required; no base-only fallback')
    return {'root': run_root, 'directory': directory, 'weights': weights, 'launch': launch}


def reserve_request(ledger, token, request, client, budget, now=None):
    """Called while holding the SAME distributed singleton as training/billing."""
    if not re.fullmatch('[a-f0-9]{64}', client or ''):
        raise Rejected(403, 'trusted_proxy_client_required')
    now = time.time() if now is None else now
    hour, day = int(now // 3600), int(now // 86400)
    limits = ledger.get('serving_limits', {})
    person = limits.get('clients', {}).get(client, {})
    hourly = person.get('hour_count', 0) if person.get('hour') == hour else 0
    daily = person.get('day_count', 0) if person.get('day') == day else 0
    total = limits.get('day_count', 0) if limits.get('day') == day else 0
    if hourly >= 3 or daily >= 6 or total >= 25:
        raise Rejected(429, 'serving_rate_limit')
    # Reuse the existing $200/pilot/active-job/overhead checks. The accounting
    # tariff is eval; labeling afterward avoids changing training budget.py.
    try:
        amount = reserve(ledger, token, request['model'], 'eval', GPU_TIMEOUT, budget)
    except ValueError as error:
        raise Rejected(503, 'budget_or_training_gate_closed') from error
    ledger['jobs'][token].update(phase='inference', request_sha256=request_digest(request),
                                checkpoint_family=request['model'])
    limits.setdefault('clients', {})[client] = {'hour': hour, 'hour_count': hourly + 1,
                                               'day': day, 'day_count': daily + 1}
    limits.update(day=day, day_count=total + 1)
    ledger['serving_limits'] = limits
    assert committed(ledger) <= CAP
    return amount


def generate_checkpoint(request, target, paths, pins):
    import torch
    maximum = request['max_new_tokens']
    messages = [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': request['prompt']}]
    family = request['model']
    if family == 'nanochat':
        os.environ['NANOCHAT_BASE_DIR'] = str(paths['base'])
        sys.path.insert(0, '/opt/nanochat')
        import kernels
        kernels.has_kernel = lambda *args, **kwargs: False
        kernels.get_kernel = lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('Unpinned kernels forbidden'))
        from nanochat.checkpoint_manager import build_model
        from nanochat.common import COMPUTE_DTYPE
        model, tokenizer, _ = build_model(str(paths['directory']), target['step'], torch.device('cuda'), 'eval')
        # Pinned tokenizer requires an empty assistant message which it pops.
        tokens = tokenizer.render_for_completion({'messages': messages + [{'role': 'assistant', 'content': ''}]})
        if len(tokens) > MAX_INPUT_TOKENS or len(tokens) + maximum > model.config.sequence_len:
            raise Rejected(400, 'tokenized_input_too_long')
        initial = len(tokens)
        endings = {tokenizer.encode_special('<|assistant_end|>'), tokenizer.get_bos_token_id()}
        # Greedy text generation deliberately does not use upstream Engine's
        # calculator/tool state machine. No model-produced code is executed.
        with torch.inference_mode(), torch.autocast('cuda', dtype=COMPUTE_DTYPE):
            for _ in range(maximum):
                logits = model(torch.tensor([tokens], device='cuda'))
                next_token = int(logits[0, -1].argmax())
                if next_token in endings:
                    break
                tokens.append(next_token)
        return tokenizer.decode(tokens[initial:]), initial, len(tokens) - initial
    from qwen_run import load_model, chat_prompt
    model_id = 'Qwen/Qwen3.5-4B-Base'
    model, tokenizer, _ = load_model(model_id, pins['models'][model_id]['revision'], adapter=paths['directory'])
    # Verify every saved adapter tensor was actually restored, rather than
    # accepting PEFT missing-key warnings or an accidental unadapted base.
    from peft.utils.save_and_load import get_peft_model_state_dict
    from safetensors.torch import load_file
    saved, loaded = load_file(str(paths['weights'])), get_peft_model_state_dict(model)
    if set(saved) != set(loaded) or not all(torch.equal(loaded[k].detach().cpu(), saved[k].to(loaded[k].dtype)) for k in saved):
        raise ValueError('Qwen adapter tensors were not completely restored')
    model.eval()
    encoded = tokenizer(chat_prompt(tokenizer, messages), return_tensors='pt', add_special_tokens=False).to('cuda')
    initial = encoded['input_ids'].shape[1]
    if initial > MAX_INPUT_TOKENS or initial + maximum > 512:
        raise Rejected(400, 'tokenized_input_too_long')
    with torch.inference_mode():
        output = model.generate(**encoded, do_sample=False, use_cache=True, max_new_tokens=maximum,
                                eos_token_id=tokenizer.convert_tokens_to_ids('<|im_end|>'),
                                pad_token_id=tokenizer.pad_token_id)
    completion = output[0, initial:]
    return tokenizer.decode(completion, skip_special_tokens=True), initial, len(completion)


def coordinate_request(request, client, targets, budget, volume, singleton, invoke, ledger_path, verify_target):
    request = validated_request(request, targets)
    token = 'serve-' + uuid.uuid4().hex
    if not singleton.put('active', token, skip_if_exists=True):
        raise Rejected(409, 'training_or_inference_busy')
    reserved, terminal = False, False
    try:
        volume.reload()
        path = Path(ledger_path)
        # Serving must never initialize/reset the training spend ledger.
        if not path.is_file():
            raise Rejected(503, 'training_ledger_missing')
        ledger = read_json(path)
        verify_target(request['model'], targets[request['model']])
        amount = reserve_request(ledger, token, request, client, budget)
        reserved = True  # persistence failure is uncertain; retain lock for inspection
        ledger['jobs'][token]['checkpoint_run_id'] = targets[request['model']]['run_id']
        write_json(path, ledger)
        volume.commit()
        # A lost/timeout RPC has uncertain termination. Preserve both lock
        # and active reservation until an operator confirms the GPU stopped.
        result = invoke(token, request)
        volume.reload()
        ledger = read_json(path)
        evidence = {k: result[k] for k in ('model', 'checkpoint_run_id', 'weights_sha256',
                    'input_tokens', 'output_tokens', 'latency_seconds', 'error') if k in result}
        finish(ledger, token, result['ok'], evidence)
        write_json(path, ledger)
        volume.commit()
        terminal = True
        result.update(request_id=token, reserved_usd=amount, committed_usd=committed(ledger),
                      reservation_status='pending_provider_billing')
        return result
    finally:
        if (not reserved or terminal) and singleton.get('active') == token:
            singleton.pop('active')


def create_modal_app(targets):
    import modal
    from modal_app import image, volume, CONFIG, PINS
    app = modal.App('rhysbot-serving')
    # Frozen deployment metadata, not an HTTP parameter or a mutable user path.
    serving_image = image.env({'RHYSBOT_SERVING_TARGETS': json.dumps(targets, sort_keys=True),
                               'PYTHONPATH': '/rhysbot/training'})

    @app.function(name='infer', image=serving_image, gpu='A100-80GB', cpu=(2, 4), memory=(16384, 32768),
                  timeout=GPU_TIMEOUT, retries=0, serialized=True, min_containers=0, max_containers=1,
                  scaledown_window=2, volumes={'/artifacts': volume})
    def infer(token, request):
        import modal
        singleton = modal.Dict.from_name('rhysbot-training-singleton')
        if singleton.get('active') != token:
            raise ValueError('No shared budget singleton owns inference')
        volume.reload()
        ledger = read_json('/artifacts/spend-ledger.json')
        job = ledger['jobs'].get(token, {})
        request = validated_request(request, targets)
        if (job.get('status') != 'active' or job.get('phase') != 'inference'
                or job.get('request_sha256') != request_digest(request)
                or job.get('checkpoint_run_id') != targets[request['model']]['run_id']):
            raise ValueError('No matching active inference reservation')
        started = time.monotonic()
        os.environ.update(HF_HOME='/tmp/rhysbot-serving-hf', TOKENIZERS_PARALLELISM='false', OMP_NUM_THREADS='2')
        try:
            family = request['model']
            paths = checkpoint_artifacts('/artifacts', family, targets[family], PINS)
            text, input_tokens, output_tokens = generate_checkpoint(request, targets[family], paths, PINS)
            return {'ok': True, 'text': text, 'model': family, 'checkpoint_run_id': targets[family]['run_id'],
                    'weights_sha256': targets[family]['weights_sha256'], 'identity': IDENTITY[family],
                    'input_tokens': input_tokens, 'output_tokens': output_tokens,
                    'latency_seconds': time.monotonic() - started, 'tools_executed': False}
        except Rejected as error:
            return {'ok': False, 'status': error.status, 'error': error.code}
        except Exception:
            return {'ok': False, 'status': 503, 'error': 'checkpoint_inference_failed_no_fallback'}
        finally:
            shutil.rmtree('/tmp/rhysbot-serving-hf', ignore_errors=True)

    def coordinate(request, client):
        import modal
        singleton = modal.Dict.from_name('rhysbot-training-singleton')
        return coordinate_request(request, client, targets, CONFIG['budget'], volume, singleton,
                                  infer.remote, Path('/artifacts/spend-ledger.json'),
                                  lambda family, target: checkpoint_artifacts('/artifacts', family, target, PINS))

    @app.function(name='api', image=serving_image, cpu=0.25, memory=512, timeout=GPU_TIMEOUT + 60,
                  serialized=True, min_containers=0, max_containers=1, scaledown_window=2,
                  volumes={'/artifacts': volume})
    @modal.concurrent(max_inputs=1)
    @modal.asgi_app(requires_proxy_auth=True)
    def api():
        async def respond(send, status, payload, cors=False):
            headers = [(b'content-type', b'application/json'), (b'cache-control', b'no-store')]
            if cors:
                headers += [(b'access-control-allow-origin', ORIGIN.encode()), (b'vary', b'Origin')]
            await send({'type': 'http.response.start', 'status': status, 'headers': headers})
            await send({'type': 'http.response.body', 'body': json.dumps(payload).encode()})

        async def asgi(scope, receive, send):
            if scope['type'] == 'lifespan':
                while True:
                    message = await receive()
                    if message['type'] == 'lifespan.startup':
                        await send({'type': 'lifespan.startup.complete'})
                    elif message['type'] == 'lifespan.shutdown':
                        await send({'type': 'lifespan.shutdown.complete'})
                        return
            headers = {key.lower(): value for key, value in scope.get('headers', [])}
            cors = headers.get(b'origin', b'').decode() == ORIGIN
            if not cors:
                return await respond(send, 403, {'error': 'origin_not_allowed'})
            if scope.get('path') != '/generate' or scope.get('method') != 'POST':
                return await respond(send, 405, {'error': 'post_generate_required'}, True)
            if headers.get(b'content-type', b'').split(b';')[0] != b'application/json':
                return await respond(send, 415, {'error': 'json_required'}, True)
            body = bytearray()
            try:
                while True:
                    message = await asyncio.wait_for(receive(), timeout=5)
                    if message['type'] == 'http.disconnect':
                        return
                    body.extend(message.get('body', b''))
                    if len(body) > MAX_BODY_BYTES:
                        raise Rejected(413, 'request_too_large')
                    if not message.get('more_body'):
                        break
                request = validated_request(json.loads(body), targets)
                client = headers.get(b'x-rhysbot-client', b'').decode()
                result = await asyncio.to_thread(coordinate, request, client)
                return await respond(send, 200 if result['ok'] else result['status'], result, True)
            except Rejected as error:
                return await respond(send, error.status, {'error': error.code}, True)
            except (ValueError, UnicodeError, asyncio.TimeoutError):
                return await respond(send, 400, {'error': 'invalid_request'}, True)
            except Exception:
                return await respond(send, 503, {'error': 'execution_uncertain_operator_check_required'}, True)
        return asgi
    return app


# Default import is inert and does not require Modal, torch or credentials.
app = create_modal_app(deployment_targets(os.environ['RHYSBOT_SERVING_TARGETS'])) if os.environ.get('RHYSBOT_SERVING_TARGETS') else None
