import copy
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

TRAINING = Path(__file__).resolve().parents[1] / "training"
sys.path.insert(0, str(TRAINING))
budget = importlib.import_module("budget")
runtime = importlib.import_module("runtime")
plan = importlib.import_module("plan")
qwen = importlib.import_module("qwen_run")
nano = importlib.import_module("nanochat_run")
CONFIG = json.loads((TRAINING / "config.json").read_text())


class CharTokenizer:
    def encode(self, value, add_special_tokens=False):
        return list(value.encode())


class TrainingSafetyTests(unittest.TestCase):
    def setUp(self):
        self.ledger = budget.initial_ledger()

    def test_cap_reservations_failed_jobs_and_no_automatic_refunds(self):
        amount = budget.reserve(self.ledger, "pilot-one", "qwen", "pilot", 900, CONFIG["budget"])
        self.assertEqual(amount, 5)
        with self.assertRaises(ValueError):
            budget.reserve(self.ledger, "pilot-two", "qwen", "pilot", 900, CONFIG["budget"])
        budget.finish(self.ledger, "pilot-one", False, {"error": "simulated failure"})
        self.assertEqual(budget.committed(self.ledger), 5)
        with self.assertRaises(ValueError):
            budget.reserve(self.ledger, "pilot-one", "qwen", "pilot", 900, CONFIG["budget"])
        self.ledger["initial_spent_usd"] = 193
        with self.assertRaises(ValueError):
            budget.reserve(self.ledger, "pilot-two", "qwen", "pilot", 900, CONFIG["budget"])

    def test_training_needs_successful_pilot_and_checkpoint_evidence(self):
        with self.assertRaises(ValueError):
            budget.reserve(self.ledger, "train-one", "nanochat", "train", 3600, CONFIG["budget"])
        budget.reserve(self.ledger, "pilot-one", "nanochat", "pilot", 900, CONFIG["budget"])
        budget.finish(self.ledger, "pilot-one", True, {"trained_checkpoint": "model.pt", "optimizer_steps": 2})
        self.assertTrue(self.ledger["pilot_passed"]["nanochat"])
        budget.reserve(self.ledger, "train-one", "nanochat", "train", 3600, CONFIG["budget"])
        self.assertLessEqual(budget.committed(self.ledger), 200)

    def test_pilot_timeout_and_spending_authorization_are_fixed(self):
        with self.assertRaises(ValueError):
            budget.reserve(self.ledger, "pilot-long", "qwen", "pilot", 901, CONFIG["budget"])
        altered = copy.deepcopy(CONFIG["budget"])
        altered["authorization_usd"] = 201
        with self.assertRaises(ValueError):
            budget.reserve(self.ledger, "pilot-large", "qwen", "pilot", 900, altered)

    def test_reconciliation_requires_terminal_job_and_provider_receipt(self):
        budget.reserve(self.ledger, "pilot-one", "qwen", "pilot", 900, CONFIG["budget"])
        with self.assertRaises(ValueError):
            budget.reconcile(self.ledger, "pilot-one", 1, "invoice-1")
        budget.finish(self.ledger, "pilot-one", False)
        with self.assertRaises(ValueError):
            budget.reconcile(self.ledger, "pilot-one", 1, "")
        budget.reconcile(self.ledger, "pilot-one", 1.25, "provider-usage-export-1")
        self.assertEqual(budget.committed(self.ledger), 1.25)

    def test_qwen_checkpoint_mapping_rejects_missing_language_weights(self):
        names = ["model.embed_tokens.weight", "model.layers.0.linear_attn.in_proj_qkv.weight"]
        source = {"model.language_model.embed_tokens.weight": "a.safetensors",
                  "model.language_model.layers.0.linear_attn.in_proj_qkv.weight": "a.safetensors",
                  "model.visual.blocks.0.weight": "b.safetensors"}
        qwen.verify_loaded_language_weights(names, source, {"missing_keys": [], "unexpected_keys": ["model.visual.blocks.0.weight"]})
        with self.assertRaises(ValueError):
            qwen.verify_loaded_language_weights(names, source, {"missing_keys": [names[0]]})
        with self.assertRaises(ValueError):
            qwen.verify_loaded_language_weights(names + ["model.norm.weight"], source, {})

    def test_real_transformers_set_diagnostics_remain_serializable(self):
        info = {'missing_keys': set(), 'unexpected_keys': {'model.visual.b', 'model.visual.a'},
                'mismatched_keys': [], 'error_msgs': []}
        normalized = qwen.json_loading_info(info)
        self.assertEqual(normalized['unexpected_keys'], ['model.visual.a', 'model.visual.b'])
        self.assertEqual(normalized['missing_keys'], [])
        self.assertEqual(json.loads(json.dumps({'loading': normalized}))['loading'], normalized)

    def test_sft_masks_system_user_and_assistant_role_prefix(self):
        tok = CharTokenizer()
        messages = [{"role": "system", "content": "instructions"}, {"role": "user", "content": "question"}, {"role": "assistant", "content": "sourced answer"}]
        ids, labels = qwen.chat_tokens(tok, messages)
        marker = tok.encode("sourced answer<|im_end|>\n")
        self.assertEqual(labels[-len(marker):], marker)
        self.assertTrue(all(x == -100 for x in labels[:-len(marker)]))
        with self.assertRaises(ValueError):
            qwen.chat_tokens(tok, messages, limit=10)

    def test_source_group_cannot_cross_splits_and_frozen_hash_detects_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for kind in ("pretrain", "sft"):
                for split in ("train", "validation", "test"):
                    row = {"id": kind + split, "split": split, "split_group_id": split, "text": "fixture"}
                    (root / f"{kind}-{split}.jsonl").write_text(json.dumps(row) + "\n")
            (root / "manifest.json").write_text("{}")
            frozen = runtime.release_manifest(root)
            runtime.verify_manifest(root, frozen)
            row["split"] = "test"
            row["split_group_id"] = "train"
            (root / "sft-test.jsonl").write_text(json.dumps(row) + "\n")
            with self.assertRaises(ValueError):
                runtime.release_manifest(root)

    def test_finite_plan_and_small_data_model(self):
        self.assertEqual(CONFIG["nanochat"]["depth"], 4)
        commands = plan.build_plan("nanochat", "pilot", "pilot-one")
        self.assertEqual([cmd[2] for cmd in commands], ["prepare", "tokenizer", "pretrain", "sft", "eval-base", "eval-sft"])
        self.assertTrue(all("--pilot" in cmd for cmd in commands))
        with self.assertRaises(ValueError):
            plan.build_plan("qwen", "eval", "eval-one")
        with self.assertRaises(ValueError):
            plan.build_plan("qwen", "train", "../../private")
        text = " ".join(str(i) for i in range(100))
        self.assertEqual(" ".join(nano.split_text(text, 30)), text)

    def test_pilot_disables_compile_for_both_upstream_call_forms(self):
        def direct(value):
            return value
        self.assertIs(nano.eager_compile(direct, dynamic=False), direct)
        decorated = nano.eager_compile(dynamic=False, fullgraph=True)(direct)
        self.assertIs(decorated, direct)

    def test_sft_prefetch_and_gradient_accumulation_do_not_end_early(self):
        for requested in (4, 80):
            maximum = nano.sft_microbatch_limit(requested, CONFIG['nanochat'])
            accumulation = CONFIG['nanochat']['total_batch_size'] // (
                CONFIG['nanochat']['device_batch_size'] * CONFIG['nanochat']['max_seq_len'])
            yielded, updates = 1, 0  # initial prefetch before the optimizer loop
            while yielded < maximum:
                yielded += accumulation
                updates += 1
                self.assertLessEqual(yielded / maximum, 1)
            self.assertEqual(updates, requested)


if __name__ == "__main__":
    unittest.main()
