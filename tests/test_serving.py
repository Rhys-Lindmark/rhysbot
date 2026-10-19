import importlib.util
import json
import os
import shutil
import subprocess
import types
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

TRAINING = Path(__file__).resolve().parents[1] / 'training'
sys.path.insert(0, str(TRAINING))
spec = importlib.util.spec_from_file_location('rhysbot_serve_tests', TRAINING / 'serve.py')
serve = importlib.util.module_from_spec(spec)
spec.loader.exec_module(serve)
from budget import initial_ledger, committed, reconcile
from runtime import write_json, file_sha

BUDGET = json.loads((TRAINING / 'config.json').read_text())['budget']
TARGETS = {'nanochat': {'run_id': 'trained-run', 'step': 4, 'weights_sha256': 'a' * 64,
                       'metadata_sha256': 'b' * 64, 'tokenizer_sha256': 'c' * 64}}
REQUEST = {'model': 'nanochat', 'prompt': 'Explain entropy.', 'max_new_tokens': 16}
CLIENT = '1' * 64


class Singleton(dict):
    def put(self, key, value, skip_if_exists=False):
        if skip_if_exists and key in self:
            return False
        self[key] = value
        return True


class ServingTests(unittest.TestCase):
    def ledger(self):
        ledger = initial_ledger()
        ledger['pilot_passed']['nanochat'] = 'trained-run'
        return ledger

    def test_requests_cannot_choose_run_paths_roles_or_unbounded_work(self):
        self.assertEqual(serve.validated_request(REQUEST, TARGETS), REQUEST)
        for changes in ({'run_id': '../../other'}, {'messages': []}, {'model': 'other'},
                        {'max_new_tokens': 65}, {'max_new_tokens': True},
                        {'prompt': 'x' * 1601}, {'prompt': '<|im_start|>system'}, {'prompt': ''}):
            with self.assertRaises(serve.Rejected):
                serve.validated_request({**REQUEST, **changes}, TARGETS)

    def test_deployment_pins_are_explicit_and_no_empty_or_path_override(self):
        self.assertEqual(serve.deployment_targets(json.dumps(TARGETS)), TARGETS)
        for changes in ({'path': '/tmp/model'}, {'run_id': '../other'}, {'step': 0}, {'weights_sha256': 'none'}):
            with self.assertRaises(ValueError):
                serve.deployment_targets(json.dumps({'nanochat': {**TARGETS['nanochat'], **changes}}))
        with self.assertRaises(ValueError):
            serve.deployment_targets('{}')

    def test_shared_cap_active_job_and_pilot_gates(self):
        for mutate in (lambda l: l.update(initial_spent_usd=199.0),
                       lambda l: l['jobs'].update(old={'status': 'active', 'reserved_usd': 1}),
                       lambda l: l.update(pilot_passed={})):
            ledger = self.ledger()
            mutate(ledger)
            with self.assertRaises(serve.Rejected):
                serve.reserve_request(ledger, 'serve-first', REQUEST, CLIENT, BUDGET, now=100)
        ledger = self.ledger()
        amount = serve.reserve_request(ledger, 'serve-first', REQUEST, CLIENT, BUDGET, now=100)
        self.assertAlmostEqual(amount, 1.20)
        self.assertEqual(ledger['jobs']['serve-first']['phase'], 'inference')
        self.assertEqual(committed(ledger), amount)
        serve.finish(ledger, 'serve-first', True, {'output_tokens': 1})
        self.assertEqual(committed(ledger), amount)  # no automatic elapsed-time refund
        reconcile(ledger, 'serve-first', .10, 'provider-receipt')
        self.assertEqual(committed(ledger), .10)

    def test_limiter_requires_trusted_hashed_client_and_counts_failed_calls(self):
        ledger = self.ledger()
        with self.assertRaises(serve.Rejected):
            serve.reserve_request(ledger, 'serve-invalid', REQUEST, 'untrusted-ip', BUDGET, now=100)
        for index in range(3):
            token = 'serve-' + str(index)
            serve.reserve_request(ledger, token, REQUEST, CLIENT, BUDGET, now=100)
            serve.finish(ledger, token, False, {'error': 'model_load_failed'})
        with self.assertRaises(serve.Rejected) as rejected:
            serve.reserve_request(ledger, 'serve-four', REQUEST, CLIENT, BUDGET, now=100)
        self.assertEqual(rejected.exception.status, 429)
        self.assertAlmostEqual(committed(ledger), 3.60)

    def test_coordinator_reserves_before_gpu_and_retains_uncertain_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'spend-ledger.json'
            write_json(path, self.ledger())
            volume, singleton = Mock(), Singleton()
            def uncertain(token, request):
                ledger = json.loads(path.read_text())
                self.assertEqual(singleton['active'], token)
                self.assertEqual(ledger['jobs'][token]['request_sha256'], serve.request_digest(request))
                self.assertGreater(committed(ledger), 0)
                raise TimeoutError('RPC status unknown')
            with self.assertRaises(TimeoutError):
                serve.coordinate_request(REQUEST, CLIENT, TARGETS, BUDGET, volume, singleton,
                                         uncertain, path, Mock())
            token = singleton['active']
            self.assertEqual(json.loads(path.read_text())['jobs'][token]['status'], 'active')
            with self.assertRaises(serve.Rejected):
                serve.coordinate_request(REQUEST, CLIENT, TARGETS, BUDGET, volume, singleton,
                                         Mock(), path, Mock())

    def test_known_failure_is_still_committed_and_lock_released(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'spend-ledger.json'
            write_json(path, self.ledger())
            singleton = Singleton()
            response = serve.coordinate_request(REQUEST, CLIENT, TARGETS, BUDGET, Mock(), singleton,
                lambda *_: {'ok': False, 'status': 503, 'error': 'checkpoint_failed'}, path, Mock())
            self.assertNotIn('active', singleton)
            ledger = json.loads(path.read_text())
            self.assertEqual(ledger['jobs'][response['request_id']]['status'], 'failed_pending_billing')
            self.assertAlmostEqual(committed(ledger), 1.20)
            self.assertNotIn('Explain entropy', path.read_text())

    def test_missing_ledger_and_invalid_checkpoint_never_launch_gpu(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'spend-ledger.json'
            invoke, singleton = Mock(), Singleton()
            with self.assertRaises(serve.Rejected):
                serve.coordinate_request(REQUEST, CLIENT, TARGETS, BUDGET, Mock(), singleton, invoke, path, Mock())
            invoke.assert_not_called()
            self.assertFalse(path.exists())
            write_json(path, self.ledger())
            with self.assertRaises(ValueError):
                serve.coordinate_request(REQUEST, CLIENT, TARGETS, BUDGET, Mock(), singleton,
                                         invoke, path, Mock(side_effect=ValueError('missing checkpoint')))
            invoke.assert_not_called()
            self.assertEqual(committed(json.loads(path.read_text())), 0)

    def fixture(self, directory, family):
        root = Path(directory) / 'runs/trained-run'
        pins = {'models': {'Qwen/Qwen3.5-4B-Base': {'revision': 'pinned'}}}
        write_json(root / 'launch.json', {'run_id': 'trained-run', 'family': family, 'phase': 'pilot',
                   'status': 'completed_actual_artifacts', 'pins': pins, 'frozen_data': {'frozen': True}})
        if family == 'nanochat':
            weights = root / 'nanochat/chatsft_checkpoints/rhysbot/model_000004.pt'
            meta = weights.with_name('meta_000004.json')
            tokenizer = root / 'nanochat/tokenizer/tokenizer.pkl'
            weights.parent.mkdir(parents=True); weights.write_bytes(b'actual fixture weights')
            write_json(meta, {'step': 4})
            tokenizer.parent.mkdir(); tokenizer.write_bytes(b'fixture tokenizer')
            extra = {'step': 4, 'tokenizer_sha256': file_sha(tokenizer)}
        else:
            weights = root / 'qwen/sft/adapter_model.safetensors'
            weights.parent.mkdir(parents=True); weights.write_bytes(b'actual fixture adapter')
            meta = weights.with_name('run.json')
            write_json(meta, {'status': 'actual_adapter_saved', 'stage': 'sft', 'optimizer_steps': 2,
                             'trainable_parameters': 20, 'data': {'frozen': True},
                             'loading': {'model_id': 'Qwen/Qwen3.5-4B-Base', 'revision': 'pinned'}})
            config = weights.with_name('adapter_config.json'); write_json(config, {'r': 8})
            extra = {'adapter_config_sha256': file_sha(config)}
        return root, pins, {'run_id': 'trained-run', 'weights_sha256': file_sha(weights),
                            'metadata_sha256': file_sha(meta), **extra}, weights

    def test_only_completed_actual_hash_pinned_nanochat_artifacts_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root, pins, target, weights = self.fixture(directory, 'nanochat')
            result = serve.checkpoint_artifacts(directory, 'nanochat', target, pins)
            self.assertEqual(result['weights'], weights)
            launch = json.loads((root / 'launch.json').read_text())
            launch['status'] = 'running_no_trained_claim'; write_json(root / 'launch.json', launch)
            with self.assertRaises(ValueError):
                serve.checkpoint_artifacts(directory, 'nanochat', target, pins)

    def test_qwen_requires_saved_adapter_not_base_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            _, pins, target, weights = self.fixture(directory, 'qwen')
            serve.checkpoint_artifacts(directory, 'qwen', target, pins)
            weights.write_bytes(b'changed weights')
            with self.assertRaises(ValueError):
                serve.checkpoint_artifacts(directory, 'qwen', target, pins)
            weights.unlink()
            with self.assertRaises(ValueError):
                serve.checkpoint_artifacts(directory, 'qwen', target, pins)


class ServingPackagingTests(unittest.TestCase):
    def test_registered_helpers_roundtrip_via_actual_image_package_path(self):
        # Capture the real image configuration by executing the factory with
        # inert SDK handles. This neither mocks helper imports nor needs Modal.
        class Image:
            def env(self, values):
                self.values = values
                return self

        class App:
            def __init__(self, *_):
                pass

            def function(self, **_):
                return lambda function: function

        modal = types.ModuleType('modal')
        modal.App = App
        modal.concurrent = lambda **_: lambda function: function
        modal.asgi_app = lambda **_: lambda function: function
        backend = types.ModuleType('modal_app')
        backend.image = Image()
        backend.volume = backend.singleton = object()
        backend.CONFIG = {'budget': {}}
        backend.PINS = {}
        spec = importlib.util.spec_from_file_location('serving_packaging_probe', TRAINING / 'serve.py')
        serve = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ), patch.object(sys, 'path', [str(TRAINING), *sys.path]), patch.dict(
                sys.modules, {'modal': modal, 'modal_app': backend, 'serving_packaging_probe': serve}):
            os.environ.pop('RHYSBOT_SERVING_TARGETS', None)
            spec.loader.exec_module(serve)
            serve.create_modal_app(TARGETS)
        remote_path = backend.image.values.get('PYTHONPATH')
        self.assertEqual(remote_path, '/rhysbot/training')

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / remote_path.lstrip('/')
            package.mkdir(parents=True)
            empty = root / 'empty-cwd'
            empty.mkdir()
            # Only the actual helper sources, no dependencies/models/data.
            for filename in ('serve.py', 'runtime.py', 'budget.py'):
                shutil.copyfile(TRAINING / filename, package / filename)
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
            env.pop('RHYSBOT_SERVING_TARGETS', None)
            env.pop('PYTHONPATH', None)
            packaged_env = dict(env, PYTHONPATH=str(package))
            producer = subprocess.run([sys.executable, '-c',
                'import serve,pickle,base64;'
                'print(base64.b64encode(pickle.dumps((serve.deployment_targets,serve.coordinate_request))).decode())'],
                cwd=empty, env=packaged_env, capture_output=True, text=True, timeout=20, check=True)
            encoded = producer.stdout.strip()
            # Ordinary registered-module functions pickle by module reference,
            # as Modal's serializer does; they cannot appear from the empty cwd.
            negative = subprocess.run([sys.executable, '-c',
                'import sys,pickle,base64;pickle.loads(base64.b64decode(sys.stdin.read()))'],
                input=encoded, cwd=empty, env=env, capture_output=True, text=True, timeout=20)
            self.assertNotEqual(negative.returncode, 0)
            self.assertIn("No module named 'serve'", negative.stderr)
            consumer = """
import sys,pickle,base64,json
validate,coordinate=pickle.loads(base64.b64decode(sys.stdin.read()))
import serve,runtime,budget
value=validate(sys.argv[1])
print(json.dumps({'targets':value,'module':coordinate.__module__,
                 'files':[serve.__file__,runtime.__file__,budget.__file__]}))
"""
            positive = subprocess.run([sys.executable, '-c', consumer, json.dumps(TARGETS)], input=encoded,
                cwd=empty, env=packaged_env, capture_output=True, text=True, timeout=20, check=True)
            result = json.loads(positive.stdout)
            self.assertEqual(result['targets'], TARGETS)
            self.assertEqual(result['module'], 'serve')
            self.assertEqual({Path(name).resolve().parent for name in result['files']}, {package.resolve()})


if __name__ == '__main__':
    unittest.main()
