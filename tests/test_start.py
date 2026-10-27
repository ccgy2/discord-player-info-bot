"""Launcher tests use temporary model placeholders and mock installers only."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'start_automod.py'
spec = importlib.util.spec_from_file_location('automod_start_test', SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class StartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict(module.os.environ, {'AUTOMOD_OCR_MODEL_DIR': ''})
        self.env.start()
        self.addCleanup(self.env.stop)

    def create_models(self):
        directory = self.root / 'data' / 'ocr_models'
        directory.mkdir(parents=True, exist_ok=True)
        for name in module.REQUIRED_MODELS:
            (directory / name).write_bytes(b'placeholder-not-real-model')

    def test_absolute_and_relative_paths_match_cog(self):
        self.assertEqual(module.model_directory(self.root), self.root / 'data' / 'ocr_models')
        with patch.dict(module.os.environ, {'AUTOMOD_OCR_MODEL_DIR': 'other/models'}):
            self.assertEqual(module.model_directory(self.root), self.root / 'other' / 'models')
        with patch.dict(module.os.environ, {'AUTOMOD_OCR_MODEL_DIR': str(self.root / 'volume')}):
            self.assertEqual(module.model_directory(self.root), self.root / 'volume')

    def test_existing_nonempty_models_skip_download(self):
        self.create_models()
        with patch.object(module.subprocess, 'run') as run:
            self.assertTrue(module.prepare_models(self.root))
            run.assert_not_called()

    def test_missing_models_call_real_install_entrypoint(self):
        def run(args, **kwargs):
            self.assertEqual(args[1], str(self.root / 'cogs' / 'automod.py'))
            self.assertIn('--install-ocr-models', args)
            self.assertEqual(kwargs['timeout'], 600)
            self.create_models()
            return types.SimpleNamespace(returncode=0)
        with patch.object(module.subprocess, 'run', side_effect=run):
            self.assertTrue(module.prepare_models(self.root))

    def test_installer_nonzero_not_claimed_success(self):
        with patch.object(module.subprocess, 'run', return_value=types.SimpleNamespace(returncode=1)):
            self.assertFalse(module.prepare_models(self.root))

    def test_zero_exit_without_files_not_claimed_success(self):
        with patch.object(module.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0)):
            self.assertFalse(module.prepare_models(self.root))

    def test_empty_weights_trigger_installer(self):
        self.create_models()
        (self.root / 'data' / 'ocr_models' / module.REQUIRED_MODELS[0]).write_bytes(b'')
        with patch.object(module.subprocess, 'run', return_value=types.SimpleNamespace(returncode=1)) as run:
            self.assertFalse(module.prepare_models(self.root))
            run.assert_called_once()

    def test_timeout_has_no_unbounded_retry(self):
        with patch.object(module.subprocess, 'run', side_effect=subprocess.TimeoutExpired('installer',600)) as run:
            self.assertFalse(module.prepare_models(self.root))
            run.assert_called_once()

    def test_main_replaces_launcher_with_bot_even_when_model_install_fails(self):
        (self.root/'cogs').mkdir()
        (self.root/'cogs'/'automod.py').write_text('')
        (self.root/'bot.py').write_text('')
        with patch.object(module, 'ROOT', self.root), patch.object(module, 'prepare_models', return_value=False), \
             patch.object(module.os, 'chdir'), patch.object(module.os, 'execv') as execute:
            self.assertEqual(module.main(), 0)
            execute.assert_called_once_with(module.sys.executable, [module.sys.executable, str(self.root/'bot.py')])

    def test_no_bot_file_refuses_to_exec(self):
        with patch.object(module, 'ROOT', self.root), patch.object(module.os, 'execv') as execute:
            self.assertEqual(module.main(), 2)
            execute.assert_not_called()

if __name__ == '__main__':
    unittest.main(verbosity=2)
