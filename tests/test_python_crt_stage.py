import hashlib
import json
import os
from pathlib import Path

import pytest

from test_runtime_bootstrap import ROOT, run_ps

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell helper")
NAMES = ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll")
V = "14.51.36247.0"
# Adopted Kimi copy shown by scripts/python-runtime.ps1 (setup.crt_python_stage_invalid / _not_owned / _apply_failed).
STAGE_INVALID = "準備したDLLまたは配置情報の整合性を確認できないため、Pythonを変更していません。「初回セットアップ.cmd」で再試行してください。"
NOT_OWNED = "Ryoshu専用のPython保存先を確認できないため、DLLを変更していません。「初回セットアップ.cmd」を実行してPythonを準備し直してください。"
APPLY_FAILED = "Ryoshu専用PythonのDLLを更新できませんでした。変更したファイルはバックアップから元に戻しています。「停止.cmd」でRyoshuを終了し、「初回セットアップ.cmd」で再試行してください。準備済みのDLLは再試行用に残しています。詳細："
sha = lambda b: hashlib.sha256(b).hexdigest()
NEW = {n: b"MZ 14.51 " + n.encode() for n in NAMES}
OLD = {n: b"MZ old python " + n.encode() for n in NAMES[:2]}


def write_pin(path, files=None, version=V, python_files=NAMES):
    files = NEW if files is None else files
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": 1, "runtime": {"members": []}, "crt": {
        "version": version, "python_files": list(python_files),
        "files": [{"name": n, "bytes": len(b), "sha256": sha(b), "file_version": version} for n, b in files.items()]}}), "utf-8")
    return path


def plan_text(pin, base, files=None, version=V, pin_sha=None):
    files = NEW if files is None else files
    return json.dumps({"schema": 1, "source_pin_sha256": pin_sha or sha(pin.read_bytes()), "version": version, "python_base": str(base),
                       "created_at": "2026-10-27T00:00:00Z",
                       "files": {n: {"bytes": len(b), "sha256": sha(b), "current_sha256": sha(OLD[n]) if n in OLD else None}
                                 for n, b in files.items()}})


def seed(tmp_path, *, base=None):
    root = tmp_path / "profile 日本語 '& space/StudioYebisu/ReceiptLedger"
    base = base or root / "python/cpython-3.12.15-windows-x86_64-none"
    base.mkdir(parents=True, exist_ok=True)
    for n, b in OLD.items():
        (base / n).write_bytes(b)
    prior = root / "crt/python-backup-20260101000000-prior000"   # an earlier backup must survive any rejection
    prior.mkdir(parents=True)
    (prior / "vcruntime140.dll").write_bytes(b"MZ older backup")
    pin = write_pin(tmp_path / "source/receipt_ledger/runtime_pin.json")
    stage = root / "crt/python-stage"
    stage.mkdir(parents=True)
    for n, b in NEW.items():
        (stage / n).write_bytes(b)
    (stage / "plan.json").write_text(plan_text(pin, base), "utf-8")
    return root, base, pin, stage


def snapshot(root, base):
    return ({p.relative_to(base).as_posix(): p.read_bytes() for p in base.rglob("*") if p.is_file()},
            {p.relative_to(root / "crt").as_posix(): p.read_bytes() for p in (root / "crt").rglob("*")
             if p.is_file() and "python-stage" not in p.parts})


def apply(tmp_path, root, base, pin, hold=None):
    script = r'''
$ErrorActionPreference = 'Stop'
. (Join-Path $env:RYOSHU_SRC 'scripts\python-runtime.ps1')
$held = $null
if ($env:RYOSHU_HOLD) { $held = [System.IO.File]::Open($env:RYOSHU_HOLD, 'Open', 'Read', 'Read') }
# held like a mapped image: readable, not replaceable
$out = $null; $err = $null
$pinArg = if ($env:RYOSHU_PIN -eq '<none>') { '' } else { $env:RYOSHU_PIN }
try { $out = Invoke-RyoshuPythonCrtStage -TaskRoot $env:RYOSHU_ROOT -PythonBase $env:RYOSHU_BASE -SourcePin $pinArg } catch { $err = $_.Exception.Message }
finally { if ($held) { $held.Dispose() } }
@{result=$out; error=$err} | ConvertTo-Json | Set-Content -LiteralPath $env:RYOSHU_TEST_RESULT -Encoding UTF8
'''
    return run_ps(tmp_path, script, {"RYOSHU_SRC": str(ROOT), "RYOSHU_ROOT": str(root), "RYOSHU_BASE": str(base),
                                     "RYOSHU_PIN": str(pin) if pin else "<none>", "RYOSHU_HOLD": str(hold) if hold else ""})


def test_stage_bound_to_source_pin_applies_all_four_and_keeps_replaced_bytes(tmp_path):
    root, base, pin, stage = seed(tmp_path)
    r = apply(tmp_path, root, base, pin)
    assert r["error"] is None and r["result"] == "applied"
    assert {n: (base / n).read_bytes() for n in NAMES} == NEW
    new_backups = [p for p in (root / "crt").glob("python-backup-*") if "prior" not in p.name]
    (backup,) = new_backups
    man = json.loads((backup / "backup.json").read_text("utf-8-sig"))
    assert {n: (backup / n).read_bytes() for n in OLD} == OLD
    assert man["replaced"] == {n: (sha(OLD[n]) if n in OLD else None) for n in NAMES}
    assert (root / "crt/python-backup-20260101000000-prior000/vcruntime140.dll").read_bytes() == b"MZ older backup"
    assert not stage.exists() and (backup / "applied-stage/plan.json").is_file()
    assert apply(tmp_path, root, base, pin)["result"] == "none"


def _consistent_but_foreign(root, base, pin, stage):
    # plan + DLL modified together: self-consistent, but not what the source pin prescribes
    evil = b"MZ attacker msvcp140"
    (stage / "msvcp140.dll").write_bytes(evil)
    (stage / "plan.json").write_text(plan_text(pin, base, files={**NEW, "msvcp140.dll": evil}), "utf-8")


def _dup_key(root, base, pin, stage):
    # duplicate "msvcp140.dll" key replacing msvcp140_1.dll's slot: ConvertFrom-Json would merge it silently
    good = json.loads(plan_text(pin, base))
    f = good["files"]
    body = ", ".join(f'"{n}": {json.dumps(f[n])}' for n in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "msvcp140.dll"))
    txt = plan_text(pin, base)
    (stage / "plan.json").write_text(txt[:txt.index('"files": {') + 10] + body + "}}", "utf-8")


CASES = {
    "stale-or-changed-source-pin": lambda root, base, pin, stage: pin.write_text(pin.read_text("utf-8").replace('"runtime"', '"runtime2"'), "utf-8"),
    "plan-pin-sha-mismatch": lambda root, base, pin, stage: (stage / "plan.json").write_text(plan_text(pin, base, pin_sha="0" * 64), "utf-8"),
    "plan-version-changed": lambda root, base, pin, stage: (stage / "plan.json").write_text(plan_text(pin, base, version="14.44.35211.0"), "utf-8"),
    "pin-version-changed": lambda root, base, pin, stage: write_pin(pin, version="14.52.0.0"),
    "staged-dll-tampered": lambda root, base, pin, stage: (stage / "vcruntime140.dll").write_bytes(b"MZ tampered"),
    "plan-and-dll-consistently-replaced": _consistent_but_foreign,
    "plan-hash-tampered": lambda root, base, pin, stage: (stage / "plan.json").write_text(
        plan_text(pin, base).replace(sha(NEW["msvcp140_1.dll"]), "f" * 64), "utf-8"),
    "plan-bytes-tampered": lambda root, base, pin, stage: (stage / "plan.json").write_text(
        plan_text(pin, base).replace(f'"bytes": {len(NEW["msvcp140.dll"])}', '"bytes": 1', 1), "utf-8"),
    "missing-staged-dll": lambda root, base, pin, stage: (stage / "msvcp140_1.dll").unlink(),
    "extra-stage-entry": lambda root, base, pin, stage: (stage / "ucrtbase.dll").write_bytes(b"MZ extra"),
    "extra-plan-entry": lambda root, base, pin, stage: (stage / "plan.json").write_text(
        plan_text(pin, base, files={**NEW, "concrt140.dll": b"MZ"}), "utf-8"),
    "missing-plan-entry": lambda root, base, pin, stage: (stage / "plan.json").write_text(
        plan_text(pin, base, files={n: b for n, b in NEW.items() if n != "vcruntime140_1.dll"}), "utf-8"),
    "duplicate-plan-key": _dup_key,
    "malformed-plan": lambda root, base, pin, stage: (stage / "plan.json").write_text("{not json", "utf-8"),
    "pin-missing-member": lambda root, base, pin, stage: write_pin(pin, files={n: b for n, b in NEW.items() if n != "msvcp140.dll"}),
    "pin-python-files-short": lambda root, base, pin, stage: write_pin(pin, python_files=NAMES[2:]),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_stage_not_matching_the_source_pin_is_rejected_before_any_write(tmp_path, case):
    root, base, pin, stage = seed(tmp_path)
    CASES[case](root, base, pin, stage)
    before = snapshot(root, base)
    r = apply(tmp_path, root, base, pin)
    assert r["result"] is None and STAGE_INVALID in (r["error"] or ""), r
    assert snapshot(root, base) == before          # managed Python bytes AND every backup untouched; no new backup
    assert not (base / "msvcp140.dll").exists()


def test_without_the_callers_source_pin_nothing_is_applied(tmp_path):
    root, base, pin, stage = seed(tmp_path)
    before = snapshot(root, base)
    r = apply(tmp_path, root, base, None)
    assert STAGE_INVALID in r["error"] and snapshot(root, base) == before


def test_interpreter_outside_root_python_is_never_touched(tmp_path):
    shared = tmp_path / "shared python"
    root, base, pin, stage = seed(tmp_path, base=shared)
    before = snapshot(root, base)
    r = apply(tmp_path, root, base, pin)
    assert r["result"] is None and NOT_OWNED in r["error"]
    assert snapshot(root, base) == before


def test_in_use_dll_fails_and_restores_every_replaced_file(tmp_path):
    root, base, pin, stage = seed(tmp_path)
    r = apply(tmp_path, root, base, pin, hold=base / "vcruntime140_1.dll")  # second file in order is locked
    assert APPLY_FAILED in r["error"]
    assert {n: (base / n).read_bytes() for n in OLD} == OLD
    assert not (base / "msvcp140.dll").exists() and not list(base.glob(".*.tmp"))
    assert (root / "crt/python-stage/plan.json").is_file()  # stage kept for a retry after the process exits
    assert (root / "crt/python-backup-20260101000000-prior000/vcruntime140.dll").read_bytes() == b"MZ older backup"
