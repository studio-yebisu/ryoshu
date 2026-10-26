"""Installed launcher diagnostics only; no GUI, downloads, or ordinary ledger."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    os.name != 'nt' or not shutil.which('powershell'),
    reason='Windows PowerShell installed launcher',
)


def write_ps(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'\xef\xbb\xbf' + text.replace('\r\n', '\n').replace('\n', '\r\n').encode('utf-8'))


def installed_source(tmp_path, *, runtime_helper=None):
    app = tmp_path / "installed 日本語 ' & app"
    launcher = app / 'packaging/windows/launch.ps1'
    launcher.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / 'packaging/windows/launch.ps1', launcher)
    (app / 'receipt_ledger').mkdir()
    (app / 'receipt_ledger/__init__.py').write_text('__version__ = "1.3.2"\n', 'utf-8')
    (app / 'web').mkdir()
    (app / 'web/processing.js').write_text('// public source\n', 'utf-8')
    for relative in ('scripts/bootstrap-uv.ps1', 'scripts/launcher-process.ps1'):
        write_ps(app / relative, "throw 'CheckOnly executed a setup or process helper'\n")
    for relative in ('setup.ps1', 'start.ps1'):
        write_ps(app / relative, "throw 'CheckOnly attempted normal startup'\n")
    if runtime_helper is not None:
        write_ps(app / 'scripts/python-runtime.ps1', runtime_helper)
    return app, launcher


def check_only(tmp_path, launcher, profile):
    profile.mkdir(exist_ok=True)
    # Every user-dependent path points inside this fixture, including the Python
    # path used by the installed entry point. Never consult the normal ledger.
    env = {**os.environ, 'USERPROFILE': str(profile), 'HOME': str(profile),
           'LOCALAPPDATA': str(profile / 'AppData/Local'),
           'APPDATA': str(profile / 'AppData/Roaming'),
           'RYOSHU_TEST_LAUNCHER': str(launcher)}
    script = r'''
$ErrorActionPreference = 'Stop'
function global:Start-Process { throw 'CheckOnly attempted to launch an app' }
function global:Invoke-WebRequest { throw 'CheckOnly attempted a download' }
function global:Invoke-RestMethod { throw 'CheckOnly attempted a network request' }
function global:Read-Host { throw 'CheckOnly attempted interactive setup' }
function global:Add-Type { throw 'CheckOnly attempted to load a GUI' }
function global:ollama { throw 'CheckOnly attempted to invoke Ollama' }
& $env:RYOSHU_TEST_LAUNCHER -CheckOnly
'''
    wrapper = tmp_path / 'check-only.ps1'
    write_ps(wrapper, script)
    before = {path.relative_to(profile): path.read_bytes()
              for path in profile.rglob('*') if path.is_file()}
    result = subprocess.run(
        ['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
         '-File', str(wrapper)], env=env, capture_output=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr.decode('utf-8', errors='replace')
    assert {path.relative_to(profile): path.read_bytes()
            for path in profile.rglob('*') if path.is_file()} == before
    observed = json.loads(result.stdout.decode('utf-8-sig'))
    assert observed['app_version'] == '1.3.2'
    assert observed['ollama_available'] is True
    return observed


@pytest.mark.parametrize('state', [
    {'exists': False, 'runnable': False, 'compatible': False, 'ready': False,
     'version': '', 'reason': 'missing'},
    {'exists': True, 'runnable': False, 'compatible': False, 'ready': False,
     'version': '', 'reason': 'launch_failed'},
    {'exists': True, 'runnable': True, 'compatible': False, 'ready': False,
     'version': '3.12.9', 'reason': 'unsupported_python'},
    {'exists': True, 'runnable': True, 'compatible': True, 'ready': False,
     'version': '3.12.15', 'reason': 'dependencies_missing'},
    {'exists': True, 'runnable': True, 'compatible': True, 'ready': True,
     'version': '3.12.15', 'reason': 'ready'},
], ids=['missing', 'broken', 'outdated', 'missing-libraries', 'ready'])
def test_check_only_reports_runtime_readiness_not_just_existence(tmp_path, state):
    helper = r'''
function Get-RyoshuPythonStatus {
    param([string]$PythonExe, [switch]$RequireLibraries)
    $expected = Join-Path $env:USERPROFILE 'StudioYebisu\ReceiptLedger\venv\Scripts\python.exe'
    if ($PythonExe -cne $expected) { throw 'Diagnostic bypassed the installed interpreter' }
    if (-not $RequireLibraries) { throw 'Diagnostic omitted application library checks' }
    return ('STATE_JSON' | ConvertFrom-Json)
}
'''.replace('STATE_JSON', json.dumps(state))
    _, launcher = installed_source(tmp_path, runtime_helper=helper)
    observed = check_only(tmp_path, launcher, tmp_path / 'isolated-profile')
    assert observed['source_complete'] is True
    assert observed['python_prepared'] is state['ready']
    for field in ('exists', 'runnable', 'compatible', 'version', 'reason'):
        assert observed['python_' + field] == state[field]


@pytest.mark.parametrize('present', [False, True], ids=['missing', 'invalid-executable'])
def test_check_only_probes_real_missing_or_broken_installed_interpreter(tmp_path, present):
    app, launcher = installed_source(tmp_path)
    shutil.copyfile(ROOT / 'scripts/python-runtime.ps1', app / 'scripts/python-runtime.ps1')
    profile = tmp_path / 'isolated-profile'
    if present:
        python = profile / 'StudioYebisu/ReceiptLedger/venv/Scripts/python.exe'
        python.parent.mkdir(parents=True)
        python.write_bytes(b'Not a runnable Windows executable.\n')
    observed = check_only(tmp_path, launcher, profile)
    assert observed['source_complete'] is True
    assert observed['python_exists'] is present
    assert observed['python_prepared'] is False
    assert observed['python_runnable'] is False
    assert observed['python_compatible'] is False
    assert observed['python_version'] == ''
    assert observed['python_reason'] == ('launch_failed' if present else 'missing')


def test_check_only_diagnoses_missing_runtime_helper(tmp_path):
    _, launcher = installed_source(tmp_path)
    observed = check_only(tmp_path, launcher, tmp_path / 'isolated-profile')
    assert observed['source_complete'] is False
    assert observed['python_prepared'] is False
    assert observed['python_reason'] == 'runtime_helper_missing'
