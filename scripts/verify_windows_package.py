"""Install a release EXE into a new isolated QA folder, then remove only its code.

Windows only. Refuses an existing Ryoshu installation in HKCU. The test keeps the
ordinary app/ledger running: the installed app uses serve_browser_qa.py and a new
port/database. No new OCR/AI inference and no normal ledger access occur.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from urllib.request import Request, urlopen
import uuid
import zipfile

REGISTRY_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{665552B0-0919-4E95-A791-2C0EDFD7EB1E}_is1'


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_sha(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): sha(p) for p in sorted(root.rglob('*')) if p.is_file()}


def run(command, **kwargs):
    return subprocess.run([str(item) for item in command], check=True,
                          capture_output=True, text=True, encoding='utf-8', **kwargs)


def registry_exists() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY):
            return True
    except FileNotFoundError:
        return False


FOLDERID_PROGRAMS = '{A77F5D77-2E2B-44C3-A6A2-ABA601054A51}'
FOLDERID_COMMON_PROGRAMS = '{0139D44E-6AFE-49F2-8690-3DAFCAE6FFB8}'
SHORTCUT_NAMES = ('Ryoshu.lnk', 'Ryoshuを終了.lnk', 'Ryoshuの導入ガイド.lnk')


def known_programs_dir(folder_id: str = FOLDERID_PROGRAMS) -> Path:
    """Start Menu Programs folder from SHGetKnownFolderPath (default FOLDERID_Programs = current user; pass
    FOLDERID_COMMON_PROGRAMS for all users); independent of this process's USERPROFILE/APPDATA variables.
    Read-only: no Known Folder or environment setting is changed."""
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [('Data1', wintypes.DWORD), ('Data2', wintypes.WORD), ('Data3', wintypes.WORD), ('Data4', ctypes.c_ubyte * 8)]

    g = uuid.UUID(folder_id)
    fid = GUID(g.fields[0], g.fields[1], g.fields[2], (ctypes.c_ubyte * 8)(*g.bytes[8:]))
    shell32, ole32 = ctypes.WinDLL('shell32'), ctypes.WinDLL('ole32')
    shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
    shell32.SHGetKnownFolderPath.restype = ctypes.HRESULT
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    out = ctypes.c_wchar_p()
    shell32.SHGetKnownFolderPath(ctypes.byref(fid), 0x00004000, None, ctypes.byref(out))  # KF_FLAG_DONT_VERIFY; raises on failure
    try:
        return Path(out.value)
    finally:
        ole32.CoTaskMemFree(ctypes.cast(out, ctypes.c_void_p))


def qa_group_dir(group: str, query=known_programs_dir) -> Path:
    return query() / group


# Pure Inno-log shortcut binding, shared with hosted_acceptance.py (which imports it from here; no circular import).
def icon_entries_from_log(log_text: str) -> list[str]:
    """Shortcut paths Inno Setup actually wrote, from its /LOG ('-- Icon entry --' followed by 'Dest filename: <path>')."""
    out, in_icon = [], False
    for line in log_text.splitlines():
        body = re.sub(r'^\S+\s+\S+\s+', '', line, count=1) if re.match(r'^\d{4}-\d{2}-\d{2}\s', line) else line
        body = body.strip()
        if body.startswith('-- ') and body.endswith(' --'):
            in_icon = body == '-- Icon entry --'
            continue
        if in_icon and body.startswith('Dest filename:'):
            out.append(body.split(':', 1)[1].strip())
            in_icon = False
    return out


def group_candidates(profile: Path, known_programs: Path, common_programs: Path | None, group_name: str = 'Ryoshu') -> dict[str, Path]:
    """Per-user group locations a per-user install may legitimately use, plus all-users (diagnostic only, never accepted)."""
    c = {'known_user_programs': known_programs / group_name,
         'isolated_profile_programs': profile / 'AppData' / 'Roaming' / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs' / group_name}
    if common_programs is not None:
        c['common_programs_NOT_ALLOWED'] = common_programs / group_name
    return c


def resolve_shortcut_group(logged: list[str], candidates: dict[str, Path], listdir=lambda p: sorted(x.name for x in p.glob('*.lnk')),
                           group_arg: str | None = None, group_default: str = 'Ryoshu') -> dict:
    """Exactly the 3 named shortcuts, all in ONE per-user group dir that the installer log names, all present on disk."""
    norm = lambda p: os.path.normcase(os.path.normpath(str(p)))
    alias = {norm(p): a for a, p in candidates.items()}
    named = [p for p in logged if Path(p).name in SHORTCUT_NAMES]
    parents = sorted({norm(Path(p).parent) for p in named})
    diag = {'expected_names': list(SHORTCUT_NAMES), 'group_arg': group_arg, 'group_default': group_default,
            'logged_basenames': sorted(Path(p).name for p in logged),
            'logged_group_dirs': [alias.get(d, '<other>') for d in parents],
            'observed_by_candidate': {a: (listdir(p) if p.is_dir() else None) for a, p in candidates.items()}}
    ok, chosen = False, None
    if len(named) == 3 and sorted(Path(p).name for p in named) == sorted(SHORTCUT_NAMES) and len(parents) == 1:
        chosen = alias.get(parents[0])
        if chosen in ('known_user_programs', 'isolated_profile_programs'):
            ok = diag['observed_by_candidate'][chosen] == sorted(SHORTCUT_NAMES)
    diag.update(selected_group_alias=chosen, ok=ok)
    return diag


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installer', type=Path, required=True)
    parser.add_argument('--source-zip', type=Path, required=True)
    parser.add_argument('--qa-python', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('This verification runs on Windows only')
    if registry_exists():
        parser.error('An installed Ryoshu already exists; do not replace it for QA')
    installer = args.installer.resolve(strict=True)
    source_zip = args.source_zip.resolve(strict=True)
    python = args.qa_python.resolve(strict=True)
    out = args.output_dir.resolve()
    if out.exists() and any(out.iterdir()):
        parser.error('Choose a new empty QA output directory')
    default_root = (Path.home() / 'StudioYebisu/ReceiptLedger').resolve()
    if out == default_root or out.is_relative_to(default_root):
        parser.error('QA must not use the ordinary ledger root')
    out.mkdir(parents=True, exist_ok=True)
    install = out / 'installed-app'
    profile = out / 'isolated-profile'
    data = out / 'isolated-ledger/data'
    group = 'Ryoshu QA ' + uuid.uuid4().hex[:10]
    # Inno's {group} is <current user's Shell Known Folder FOLDERID_Programs>\<group>; a changed USERPROFILE does
    # not redirect it. The unique QA group name keeps the test shortcuts apart from any ordinary Ryoshu group.
    group_dir = qa_group_dir(group)
    if group_dir.exists():
        parser.error('The QA Start Menu group already exists; refusing to overwrite it')
    # Where Inno actually wrote {group} is taken from its own /LOG and must be ONE of these two per-user candidates;
    # the all-users folder is classified for diagnostics but never accepted. No Known Folder/profile setting is changed.
    try:
        common = known_programs_dir(FOLDERID_COMMON_PROGRAMS)
    except OSError:
        common = None
    candidates = group_candidates(profile, group_dir.parent, common, group)
    if candidates['isolated_profile_programs'].exists():
        parser.error('The isolated-profile QA Start Menu group already exists; refusing to overwrite it')
    env = dict(os.environ, USERPROFILE=str(profile), PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    # The application being tested is in a fresh folder and has never owned 8766.
    # Synthetic launch metadata models the unrelated server already using that
    # port; the normal user's launch metadata is never opened by this test.
    listeners = run(['powershell.exe', '-NoProfile', '-Command',
        '@(Get-NetTCPConnection -State Listen -LocalPort 8766 -ErrorAction SilentlyContinue) | Select-Object -ExpandProperty OwningProcess -Unique | ConvertTo-Json -Compress'])
    value = json.loads(listeners.stdout) if listeners.stdout.strip() else None
    pid = value[0] if isinstance(value, list) and value else value
    if pid:
        record = profile / 'StudioYebisu/ReceiptLedger/runtime/server.json'
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps({'pid': int(pid), 'source': str(out / 'unrelated-running-app')}), encoding='utf-8-sig')
    checks = []
    process = None
    boot = None
    try:
        run([installer, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
             '/DIR=' + str(install), '/GROUP=' + group, '/TASKS=',
             '/LOG=' + str(out / 'install.log')], env=env)
        if not registry_exists():
            raise AssertionError('Installer registration missing')
        with zipfile.ZipFile(source_zip) as source:
            payload = {item.filename.split('/', 1)[1]: hashlib.sha256(source.read(item)).hexdigest()
                       for item in source.infolist() if not item.is_dir()}
        actual = {name: sha(install / name) for name in payload}
        assert actual == payload, 'Installed payload differs from source ZIP'
        checks.append(f'Installed {len(payload)} public source files byte-for-byte from the source ZIP')
        log_text = (out / 'install.log').read_text(encoding='utf-8-sig', errors='replace')
        shortcut = resolve_shortcut_group(icon_entries_from_log(log_text), candidates, group_arg=group)
        assert shortcut['ok'], 'Start Menu shortcuts not bound to one per-user group in the installer log: ' + json.dumps(
            {k: shortcut[k] for k in ('selected_group_alias', 'logged_group_dirs', 'logged_basenames')}, ensure_ascii=False)
        group_dir = candidates[shortcut['selected_group_alias']]
        checks.append('Three Start Menu shortcuts created in one per-user QA group named by the installer log ('
                      + shortcut['selected_group_alias'] + ')')
        diagnostic = run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                          '-File', install / 'packaging/windows/launch.ps1', '-CheckOnly'], env=env)
        readiness = json.loads(diagnostic.stdout)
        assert readiness['source_complete'] and readiness['app_version'] in installer.name
        checks.append('Installed launcher executed on Windows PowerShell without opening any ledger')
        # Check install/update ownership guards using controlled metadata only.
        guard = install / 'packaging/windows/can-modify.ps1'
        run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', guard, '-AppRoot', install], env=env)
        if pid:
            record.write_text(json.dumps({'pid': int(pid), 'source': str(install)}), encoding='utf-8-sig')
            rejected = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(guard), '-AppRoot', str(install)], env=env, capture_output=True)
            assert rejected.returncode == 2
            record.write_text(json.dumps({'pid': int(pid), 'source': str(out / 'unrelated-running-app')}), encoding='utf-8-sig')
            checks.append('Same-source running guard refuses modification; unrelated-source guard permits it')
        process = subprocess.Popen([str(python), str(install / 'scripts/serve_browser_qa.py'),
                                    '--data-dir', str(data)], cwd=install, env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding='utf-8')
        ready = threading.Event()
        def read_boot():
            nonlocal boot
            for line in process.stdout:
                try:
                    info = json.loads(line)
                    if info.get('qa_url'):
                        boot = info
                        ready.set()
                        return
                except ValueError:
                    pass
        threading.Thread(target=read_boot, daemon=True).start()
        if not ready.wait(45):
            raise RuntimeError('Installed app failed to start within the bounded test time')
        for _ in range(80):
            try:
                with urlopen(boot['qa_url'] + '/api/health', timeout=2) as response:
                    health = json.load(response)
                break
            except OSError:
                time.sleep(.1)
        else:
            raise RuntimeError('Installed app health did not become ready')
        assert health['app'] == 'receipt-ledger' and health['app_version'] == readiness['app_version']
        with urlopen(boot['qa_url'] + '/app.js', timeout=5) as response:
            assert hashlib.sha256(response.read()).hexdigest() == payload['web/app.js']
        with urlopen(boot['qa_url'] + '/processing.js', timeout=5) as response:
            assert hashlib.sha256(response.read()).hexdigest() == payload['web/processing.js']
        checks.append('Installed application served the release version and exact account/progress UI on a new local port')
        with urlopen(Request(boot['qa_url'] + '/_qa/stop', method='POST',
                             headers={'x-qa-token': boot['stop_token']}), timeout=5):
            pass
        process.wait(15)
        process = None
        before = tree_sha(data)
        assert before and (data / 'ledger.enc.sqlite3').read_bytes()[:16] != b'SQLite format 3\0'
        (install / 'user-added-file.txt').write_text('This file was not installed by Ryoshu.\n', encoding='utf-8')
        run([install / 'unins000.exe', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
             '/LOG=' + str(out / 'uninstall.log')], env=env)
        for _ in range(50):
            if not (install / 'unins000.exe').exists():
                break
            time.sleep(.1)
        assert not (install / 'start.ps1').exists()
        assert (install / 'user-added-file.txt').is_file()
        assert tree_sha(data) == before, 'Uninstall changed the encrypted QA ledger'
        assert not group_dir.exists(), 'QA shortcuts remain after uninstall'
        assert not any(candidates[n].exists() for n in ('known_user_programs', 'isolated_profile_programs')), 'QA shortcuts remain after uninstall'
        assert not registry_exists(), 'QA uninstall registration remains'
        checks.append('Uninstall removed app/shortcuts/registration while preserving encrypted ledger and user-added files byte-for-byte')
        proof = {'status': 'passed', 'checks': checks, 'installer_sha256': sha(installer),
                 'source_zip_sha256': sha(source_zip), 'payload_files_verified': len(payload),
                 'payload_manifest_sha256': hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest(),
                 'app_version': readiness['app_version'], 'new_ocr_or_ai_inference': False,
                 'ordinary_ledger_accessed': False, 'qa_listener_stopped': True,
                 'qa_ledger_unchanged_on_uninstall': True}
        (out / 'verification.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(proof, ensure_ascii=False))
        return 0
    finally:
        if process and process.poll() is None:
            if boot:
                try:
                    urlopen(Request(boot['qa_url'] + '/_qa/stop', method='POST',
                                    headers={'x-qa-token': boot['stop_token']}), timeout=3).close()
                    process.wait(10)
                except Exception:
                    process.terminate()
                    process.wait(10)
            else:
                process.terminate()
                process.wait(10)
        if registry_exists() and (install / 'unins000.exe').exists():
            run([install / 'unins000.exe', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'], env=env)


if __name__ == '__main__':
    raise SystemExit(main())
