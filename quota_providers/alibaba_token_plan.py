"""Alibaba Personal Token Plan via the official Bailian CLI.

`bl usage token-plan --output json` publishes per1MonthPercentage as a USED
fraction (0.125 means 12.5%), with per1MonthResetTime in Unix milliseconds.
Other per<N>Hour/Day/Week/Month windows use the same contract. No inference
calls, local token estimates, browser-cookie reads, or credential-file reads.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .base import QuotaResult, QuotaWindow, build_unavailable, opt_in_flag
from .registry import register

_PROVIDER_ID = 'alibaba-token-plan'
_TIMEOUT_S = 12.0
_MAX_OUTPUT = 256 * 1024


def _run_cli(executable):
    """Run a fixed read-only command. Never return CLI diagnostics or credentials.

    A bounded reader holds at most 256 KiB + one byte, including when a child
    floods stdout. The process and reader share one wall-clock deadline.
    """
    allowed = {'PATH', 'HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'SYSTEMROOT',
               'LANG', 'LC_ALL', 'LC_CTYPE', 'TZ', 'TMPDIR', 'TEMP', 'TMP',
               'XDG_CONFIG_HOME', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
               'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy'}
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env['PATH'] = _trusted_path()
    deadline = time.monotonic() + _TIMEOUT_S
    try:
        proc = subprocess.Popen(
            [executable, 'usage', 'token-plan', '--output', 'json'],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            shell=False, cwd=os.path.expanduser('~'), env=env,
            start_new_session=os.name == 'posix',
        )
    except FileNotFoundError:
        return None, 'cli-not-found'
    except OSError:
        return None, 'cli-error'

    output = []
    stdout = proc.stdout
    assert stdout is not None  # PIPE above guarantees the reader.

    def read_output():
        try:
            output.append(stdout.read(_MAX_OUTPUT + 1))
        except OSError:
            pass
        finally:
            stdout.close()

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    reason = None
    try:
        reader.join(max(0, deadline - time.monotonic()))
        if reader.is_alive():
            reason = 'timeout'
        elif not output:
            reason = 'cli-error'
        elif len(output[0]) > _MAX_OUTPUT:
            reason = 'output-too-large'
        else:
            try:
                code = proc.wait(timeout=max(0, deadline - time.monotonic()))
                if code:
                    reason = {3: 'not-logged-in', 5: 'timeout', 6: 'network-error'}.get(
                        code, 'cli-error')
            except subprocess.TimeoutExpired:
                reason = 'timeout'
    finally:
        # Also terminate descendants holding stdout open on POSIX. No shell is used.
        if reason:
            try:
                if os.name == 'posix':
                    os.killpg(proc.pid, signal.SIGKILL)
                elif proc.poll() is None:
                    proc.kill()
            except OSError:
                pass
        try:
            proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
        reader.join(0.5)
    if reason:
        return None, reason
    try:
        return json.loads(output[0]), None
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None, 'bad-json'


def _settings():
    try:
        from hermes_cli.config import load_config_readonly
        settings = load_config_readonly().get('plugins', {}).get('entries', {}).get(
            'quota', {}).get('settings', {})
        return settings if isinstance(settings, dict) else {}
    except Exception:
        return {}


def _project_root():
    """Find a containing checkout/package even when invoked from a subdirectory."""
    cwd = Path(os.getcwd()).resolve()
    home = Path.home().resolve()
    root = cwd
    for parent in (cwd, *cwd.parents):
        if parent == home or parent == parent.parent:
            break
        if any((parent / marker).exists() for marker in
               ('.git', 'pyproject.toml', 'package.json', 'Cargo.toml', 'go.mod')):
            root = parent
    return root


def _trusted_path():
    """Exclude containing-project directories and symlinked project paths."""
    cwd = _project_root()
    home = Path.home().resolve()
    directories = []
    for entry in os.environ.get('PATH', '').split(os.pathsep):
        if not os.path.isabs(entry):
            continue
        resolved = Path(entry).resolve()
        if cwd != home and (resolved == cwd or cwd in resolved.parents):
            continue
        directories.append(entry)
    return os.pathsep.join(directories)


def _read_usage():
    settings = _settings()
    if not opt_in_flag(settings.get('alibabaTokenPlanEnabled', False)):
        return None, 'opt-in-disabled'
    configured = settings.get('bailianCliPath')
    if configured is not None:
        if not isinstance(configured, str) or not os.path.isabs(configured):
            return None, 'config-invalid'
        executable = configured
    else:
        executable = shutil.which('bl', path=_trusted_path())
    if not executable or not os.path.isfile(executable) or not os.access(executable, os.X_OK):
        return None, 'cli-not-found'
    resolved = Path(executable).resolve()
    if resolved.suffix.lower() in {'.sh', '.bash', '.zsh', '.fish', '.cmd', '.bat', '.ps1'} or \
            resolved.name.lower() in {'sh', 'bash', 'zsh', 'fish', 'cmd.exe', 'powershell.exe'}:
        return None, 'config-invalid'
    cwd = _project_root()
    if configured is None and cwd != Path.home().resolve() and \
            (resolved == cwd or cwd in resolved.parents):
        return None, 'cli-not-found'
    return _run_cli(executable)


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, OverflowError):
        return None


def _reset_iso(value):
    milliseconds = _number(value)
    if milliseconds is None or not 0 < milliseconds < 253402300800000:
        return None
    try:
        return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


@register(_PROVIDER_ID)
def fetch_alibaba_token_plan_quota() -> QuotaResult:
    payload, reason = _read_usage()
    if reason:
        return build_unavailable(_PROVIDER_ID, reason)
    if not isinstance(payload, dict):
        return build_unavailable(_PROVIDER_ID, 'bad-response')
    if payload.get('hasQuotaData') is False:
        return build_unavailable(_PROVIDER_ID, 'no-data')
    windows = []
    unit_hours = {'Hour': 1, 'Day': 24, 'Week': 168, 'Month': 720}
    units = {'Hour': 'h', 'Day': 'd', 'Week': 'w', 'Month': 'mo'}
    for key, value in payload.items():
        match = re.fullmatch(r'per([1-9][0-9]{0,3})(Hour|Day|Week|Month)Percentage', key)
        if not match:
            continue
        ratio = _number(value)
        if ratio is None or not 0 <= ratio <= 1:
            continue
        count, unit = int(match[1]), match[2]
        label = {('Month', 1): 'Monthly', ('Week', 1): 'Weekly'}.get(
            (unit, count), str(count) + units[unit])
        reset = payload.get(key[:-len('Percentage')] + 'ResetTime')
        reset_at = _reset_iso(reset)
        windows.append((count * unit_hours[unit], QuotaWindow(
            label=label, used_percent=ratio * 100, reset_at=reset_at)))
    if not windows:
        return build_unavailable(_PROVIDER_ID, 'no-data')
    return QuotaResult(
        label=_PROVIDER_ID,
        windows=[window for _, window in sorted(windows, key=lambda item: item[0])],
        details=['Provider-reported Personal Token Plan quota via Bailian CLI'],
    )
