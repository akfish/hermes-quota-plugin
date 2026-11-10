"""Offline Alibaba Personal Token Plan tests. Fixtures are not live account data."""
import importlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class AlibabaTokenPlanTests(unittest.TestCase):
    def module(self):
        name = 'quota_providers.alibaba_token_plan'
        self.assertIsNotNone(importlib.util.find_spec(name), 'Alibaba Token Plan provider is missing')
        return importlib.import_module(name)

    def test_monthly_only_provider_ratio_is_converted_to_used_percent(self):
        mod = self.module()
        payload = {'per1MonthPercentage': 0.125, 'per1MonthResetTime': 1796918400000,
                   'hasQuotaData': True}
        with mock.patch.object(mod, '_read_usage', return_value=(payload, None)):
            result = mod.fetch_alibaba_token_plan_quota()
        self.assertIsNone(result.unavailable_reason)
        self.assertEqual(result.label, 'alibaba-token-plan')
        self.assertIsNone(result.plan, 'Usage endpoint does not identify the purchased tier')
        self.assertEqual(len(result.windows), 1)
        self.assertEqual(result.windows[0].label, 'Monthly')
        self.assertEqual(result.windows[0].used_percent, 12.5)
        self.assertEqual(result.windows[0].reset_at, '2026-12-10T16:00:00+00:00')


    def fetch_payload(self, payload):
        mod = self.module()
        with mock.patch.object(mod, '_read_usage', return_value=(payload, None)):
            return mod.fetch_alibaba_token_plan_quota()

    def test_discovers_reported_windows_without_inventing_absent_ones(self):
        result = self.fetch_payload({
            'per5HourPercentage': 0, 'per1WeekPercentage': 1,
            'per3DayPercentage': '0.25', 'hasQuotaData': True,
        })
        self.assertIsNone(result.unavailable_reason)
        self.assertEqual([(w.label, w.used_percent, w.reset_at) for w in result.windows],
                         [('5h', 0.0, None), ('3d', 25.0, None), ('Weekly', 100.0, None)])


    def test_unusable_payloads_do_not_claim_zero_usage_or_crash(self):
        for payload, reason in [
            ({}, 'no-data'),
            ({'hasQuotaData': False, 'per1MonthPercentage': 0}, 'no-data'),
            ([], 'bad-response'),
            ({'per1MonthResetTime': 1796918400000}, 'no-data'),
            *[({'per1MonthPercentage': value}, 'no-data')
              for value in [None, True, False, float('nan'), float('inf'), -0.1, 1.1,
                            {}, [], 'garbage', 'NaN']],
        ]:
            with self.subTest(payload=payload):
                result = self.fetch_payload(payload)
                self.assertEqual(result.unavailable_reason, reason)
                self.assertEqual(result.windows, [])
        for reset in ['garbage', True, -1, float('inf'), 1e30]:
            with self.subTest(reset=reset):
                result = self.fetch_payload({'per1MonthPercentage': .25,
                                             'per1MonthResetTime': reset})
                self.assertIsNone(result.unavailable_reason)
                self.assertEqual(result.windows[0].used_percent, 25)
                self.assertIsNone(result.windows[0].reset_at)


    def test_cli_reads_json_without_inheriting_secrets(self):
        mod = self.module()
        self.assertTrue(hasattr(mod, '_run_cli'), 'Bounded CLI runner is missing')
        import tempfile
        import os
        with tempfile.TemporaryDirectory() as directory:
            cli = Path(directory) / 'bl'
            cli.write_text('#!' + sys.executable + '\nimport json, os, sys\n'
                           'assert sys.argv[1:] == ["usage", "token-plan", "--output", "json"]\n'
                           'assert "PRIVATE_API_KEY" not in os.environ\n'
                           'print(json.dumps({"per1MonthPercentage": .25, "hasQuotaData": True}))\n')
            cli.chmod(0o700)
            with mock.patch.dict(os.environ, {'PRIVATE_API_KEY': 'not-a-real-secret'}):
                payload, reason = mod._run_cli(str(cli))
        self.assertIsNone(reason)
        self.assertEqual(payload['per1MonthPercentage'], .25)


    def test_cli_failures_are_bounded_and_never_expose_output(self):
        mod = self.module()
        import tempfile
        scripts = [
            ('import sys; print("PRIVATE_DETAIL"); sys.exit(3)', 'not-logged-in'),
            ('import sys; print("PRIVATE_DETAIL"); sys.exit(5)', 'timeout'),
            ('import sys; print("PRIVATE_DETAIL"); sys.exit(6)', 'network-error'),
            ('import sys; print("PRIVATE_DETAIL"); sys.exit(1)', 'cli-error'),
            ('print("PRIVATE_DETAIL")', 'bad-json'),
            ('print("x" * 300000)', 'output-too-large'),
            ('import time; time.sleep(5)', 'timeout'),
        ]
        for script, expected in scripts:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                cli = Path(directory) / 'bl'
                cli.write_text('#!' + sys.executable + '\n' + script + '\n')
                cli.chmod(0o700)
                with mock.patch.object(mod, '_TIMEOUT_S', .25 if 'sleep' in script else 3):
                    payload, reason = mod._run_cli(str(cli))
                self.assertIsNone(payload)
                self.assertEqual(reason, expected)
        self.assertEqual(mod._run_cli('/nonexistent/bailian/bl'), (None, 'cli-not-found'))


    def test_opt_in_and_explicit_cli_path_control_collection(self):
        mod = self.module()
        self.assertTrue(hasattr(mod, '_settings'), 'Profile settings support is missing')
        import tempfile
        with mock.patch.object(mod, '_settings', return_value={}), \
                mock.patch.object(mod, '_run_cli') as run:
            self.assertEqual(mod._read_usage(), (None, 'opt-in-disabled'))
            run.assert_not_called()
        for value in ['false', 'no', '0', False]:
            with mock.patch.object(mod, '_settings', return_value={'alibabaTokenPlanEnabled': value}):
                self.assertEqual(mod._read_usage(), (None, 'opt-in-disabled'))
        with tempfile.TemporaryDirectory() as directory:
            cli = Path(directory) / 'bl'
            cli.write_text('fixture executable')
            cli.chmod(0o700)
            settings = {'alibabaTokenPlanEnabled': True, 'bailianCliPath': str(cli)}
            with mock.patch.object(mod, '_settings', return_value=settings), \
                    mock.patch.object(mod, '_run_cli', return_value=({'hasQuotaData': False}, None)) as run:
                self.assertEqual(mod._read_usage(), ({'hasQuotaData': False}, None))
                run.assert_called_once_with(str(cli))
        with mock.patch.object(mod, '_settings', return_value={
                'alibabaTokenPlanEnabled': True, 'bailianCliPath': 'relative/bl'}):
            self.assertEqual(mod._read_usage(), (None, 'config-invalid'))


    def test_discovery_rejects_project_executables_and_shell_launchers(self):
        mod = self.module()
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            cli = project / 'bl'
            cli.write_text('fixture executable')
            cli.chmod(0o700)
            with mock.patch.object(mod, '_settings', return_value={'alibabaTokenPlanEnabled': True}), \
                    mock.patch.dict(os.environ, {'PATH': directory}), \
                    mock.patch.object(mod.os, 'getcwd', return_value=directory), \
                    mock.patch.object(mod, '_run_cli') as run:
                self.assertEqual(mod._read_usage(), (None, 'cli-not-found'))
                run.assert_not_called()
            launcher = project / 'wrapper.sh'
            launcher.write_text('fixture shell wrapper')
            launcher.chmod(0o700)
            with mock.patch.object(mod, '_settings', return_value={
                    'alibabaTokenPlanEnabled': True, 'bailianCliPath': str(launcher)}):
                self.assertEqual(mod._read_usage(), (None, 'config-invalid'))


    def test_provider_is_available_to_cache_cli_desktop_and_tui(self):
        import subprocess
        probe = subprocess.run([sys.executable, '-c',
            'from quota_providers import PROVIDER_FETCHERS; '
            'assert "alibaba-token-plan" in PROVIDER_FETCHERS'],
            cwd=ROOT, capture_output=True)
        self.assertEqual(probe.returncode, 0, 'Provider is not imported by the cache registry')
        for relative in ['commands.py', 'desktop/plugin.js', 'tui/quota-panel.mjs']:
            text = (ROOT / relative).read_text()
            self.assertIn('Alibaba Token Plan', text, relative)
            self.assertIn('alibaba-token-plan', text, relative)


    def test_nested_project_cwd_cannot_hijack_cli_or_its_interpreter(self):
        mod = self.module()
        import os
        import shutil
        import tempfile
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node is required for the env-node launcher regression')
        assert node is not None
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            project = base / 'repo'
            nested = project / 'tests'
            bin_dir = project / '.venv/bin'
            nested.mkdir(parents=True)
            bin_dir.mkdir(parents=True)
            (project / '.git').mkdir()
            for name in ['bl', 'node']:
                executable = bin_dir / name
                executable.write_text('#!' + sys.executable + '\nprint("HIJACKED")\n')
                executable.chmod(0o700)
            cli = base / 'trusted-bl'
            cli.write_text('#!/usr/bin/env node\nconsole.log(JSON.stringify({per1MonthPercentage: .25}))\n')
            cli.chmod(0o700)
            with mock.patch.object(mod.os, 'getcwd', return_value=str(nested)), \
                    mock.patch.dict(os.environ, {'PATH': str(bin_dir) + os.pathsep + str(Path(node).parent)}):
                with mock.patch.object(mod, '_settings', return_value={'alibabaTokenPlanEnabled': True}):
                    self.assertEqual(mod._read_usage(), (None, 'cli-not-found'))
                with mock.patch.object(mod, '_settings', return_value={
                        'alibabaTokenPlanEnabled': True, 'bailianCliPath': str(cli)}):
                    self.assertEqual(mod._read_usage(), ({'per1MonthPercentage': .25}, None))


if __name__ == '__main__':
    unittest.main()
