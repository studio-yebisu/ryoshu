import hashlib
import io
import json
import re
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_windows_package as bwp  # noqa: E402
import hosted_acceptance as ha  # noqa: E402
import make_source_zip as msz  # noqa: E402

sha = lambda b: hashlib.sha256(b).hexdigest()
PIN = {n: {"sha256": sha(n.encode())} for n in ha.CRT_NAMES}
BASE = r"D:\a\_temp\qa-work\QA 領収書 profile\StudioYebisu\ReceiptLedger\python\cpython-3.12.15-windows-x86_64-none"
WORKFLOW = (ROOT / ".github/workflows/hosted-windows-acceptance.yml").read_text("utf-8")


def mod(name, path, digest=None, cls="redistributable_crt"):
    return {"name": name.lower(), "path": path, "class": cls, "sha256": digest}


def owned_modules():
    return [mod(n, BASE + "\\" + n, PIN[n]["sha256"]) for n in ha.CRT_NAMES]


# --- Ollama absence ---------------------------------------------------------------------------------
def test_ollama_absent_only_when_every_probe_is_clean():
    clean = {"command": False, "install_dirs": [], "services": [], "processes": [], "listener_11434": False}
    assert ha.ollama_findings(clean) == []
    for key, value, expect in [("command", True, "command_on_path"), ("install_dirs", [r"C:\x\Ollama"], "install_dir:"),
                               ("services", ["Ollama"], "service:Ollama"), ("processes", ["ollama"], "process:ollama"),
                               ("listener_11434", True, "listener_11434")]:
        found = ha.ollama_findings({**clean, key: value})
        assert len(found) == 1 and found[0].startswith(expect)


# --- CRT provenance ---------------------------------------------------------------------------------
def test_crt_provenance_accepts_only_managed_dir_at_pinned_hashes():
    r = ha.crt_provenance(owned_modules() + [mod("kernel32.dll", r"C:\Windows\System32\kernel32.dll", cls="windows_component")],
                          PIN, BASE, ha.CRT_NAMES, r"C:\Windows")
    assert r["ok"] and set(r["owned"]) == set(ha.CRT_NAMES) and r["failures"] == []


def test_crt_from_system32_or_visual_studio_fails_even_with_identical_bytes():
    mods = owned_modules()
    mods[2] = mod("msvcp140.dll", r"C:\Windows\System32\msvcp140.dll", PIN["msvcp140.dll"]["sha256"])
    r = ha.crt_provenance(mods, PIN, BASE, ha.CRT_NAMES, r"C:\Windows")
    assert not r["ok"] and "msvcp140.dll:outside_managed_dir:system_root" in r["failures"]
    assert "msvcp140.dll:not_loaded_from_managed_dir" in r["failures"]
    vs = owned_modules()
    vs[0] = mod("vcruntime140.dll", r"C:\Program Files\Microsoft Visual Studio\2022\Enterprise\VC\vcruntime140.dll", PIN["vcruntime140.dll"]["sha256"])
    assert "vcruntime140.dll:outside_managed_dir" in ha.crt_provenance(vs, PIN, BASE, ha.CRT_NAMES, r"C:\Windows")["failures"]


def test_crt_hash_mismatch_missing_and_unexpected_redistributables_fail():
    mods = owned_modules()
    mods[1]["sha256"] = "0" * 64
    assert "vcruntime140_1.dll:hash_not_pinned" in ha.crt_provenance(mods, PIN, BASE, ha.CRT_NAMES, r"C:\Windows")["failures"]
    assert "msvcp140_1.dll:not_loaded_from_managed_dir" in ha.crt_provenance(owned_modules()[:3], PIN, BASE, ha.CRT_NAMES, r"C:\Windows")["failures"]
    extra = owned_modules() + [mod("concrt140.dll", r"C:\Windows\System32\concrt140.dll", "1" * 64)]
    assert "concrt140.dll:unexpected_redistributable:system_root" in ha.crt_provenance(extra, PIN, BASE, ha.CRT_NAMES, r"C:\Windows")["failures"]


def test_vendored_wheel_copy_is_reported_not_failed():
    wheel = mod("msvcp140-a4c2229bdc2a2a630acdc095b4d86008.dll", r"D:\x\venv\Lib\site-packages\numpy.libs\msvcp140-a4c2229bdc2a2a630acdc095b4d86008.dll", "2" * 64)
    r = ha.crt_provenance(owned_modules() + [wheel], PIN, BASE, ha.CRT_NAMES, r"C:\Windows")
    assert r["ok"] and r["vendored_wheel_copies"][0]["name"].startswith("msvcp140-")


# --- sanitizing ---------------------------------------------------------------------------------------
def test_sanitize_replaces_private_paths_and_drops_secret_keys():
    rep = {"profile": r"D:\a\_temp\qa-work\QA 領収書 profile", "work": r"D:\a\_temp\qa-work"}
    data = {"path": r"d:\A\_temp\qa-work\QA 領収書 profile\StudioYebisu\x.dll", "token": "abc", "session_key": "s", "hmac": "h",
            "nested": [{"api_key": "k", "ok": r"D:\a\_temp\qa-work\child.json"}], "raw_model_candidate_keys": ["account"]}
    out = ha.sanitize(data, {rep["profile"]: "<profile>", rep["work"]: "<work>"})
    assert out == {"path": r"<profile>\StudioYebisu\x.dll", "nested": [{"ok": r"<work>\child.json"}], "raw_model_candidate_keys": ["account"]}


# --- source ZIP == build validator ---------------------------------------------------------------------
@pytest.mark.parametrize("path,reason", [
    ("web/app.js", None), ("data/account-catalog.json", None), ("data/catalog-history/2026-10-22.1.json", None),
    ("samples/receipts-100-v2/receipts-100.zip", None), ("tests/fixture.db", "private_data"), ("data/ledger.json", "private_data"),
    ("tools/x.exe", "binary_or_model"), ("m/model.gguf", "binary_or_model"), (".local/notes.md", "private_or_runtime_folder"),
    ("a/runtime/x.txt", "private_or_runtime_folder"), ("secrets/.env.local", "private_data")])
def test_source_zip_exclusions_follow_build_rules(path, reason):
    assert msz.excluded_reason(path) == reason


def test_every_included_path_passes_the_build_validator(tmp_path):
    names = [p for p in ("LICENSE", "web/app.js", "data/account-catalog.json", "samples/x.zip", "tests/a.db", "x.dll") if msz.excluded_reason(p) is None]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for req in sorted(bwp.REQUIRED | set(names)):
            z.writestr("Ryoshu-1.4.0/" + req, b"x" if req != "receipt_ledger/__init__.py" else b'__version__ = "1.4.0"\n')
    (tmp_path / "s.zip").write_bytes(buf.getvalue())
    _, version, files = bwp.unpack_source(tmp_path / "s.zip", tmp_path / "out")
    assert version == "1.4.0" and set(names) <= set(files)


class _Done:
    def __init__(self, stdout):
        self.stdout = stdout


def test_source_zip_refuses_a_tree_that_is_not_the_git_root_or_lacks_app_files(tmp_path, monkeypatch):
    monkeypatch.setattr(msz.subprocess, "run", lambda cmd, **k: _Done(str(tmp_path) if "rev-parse" in cmd else b""))
    with pytest.raises(SystemExit):
        msz.main(["--output", str(tmp_path / "a.zip")])
    empty = io.BytesIO()
    zipfile.ZipFile(empty, "w").close()
    monkeypatch.setattr(msz.subprocess, "run", lambda cmd, **k: _Done(str(msz.ROOT) if "rev-parse" in cmd else empty.getvalue()))
    with pytest.raises(SystemExit):
        msz.main(["--output", str(tmp_path / "b.zip")])
    assert not (tmp_path / "a.zip").exists() and not (tmp_path / "b.zip").exists()


def test_source_zip_writes_only_distributable_files_and_reports_exclusions(tmp_path, monkeypatch):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for req in bwp.REQUIRED:
            z.writestr(req, b"x")
        z.writestr("tests/ledger.db", b"x")
        z.writestr("tools/helper.exe", b"MZ")
    monkeypatch.setattr(msz.subprocess, "run", lambda cmd, **k: _Done(str(msz.ROOT) if "rev-parse" in cmd else buf.getvalue()))
    assert msz.main(["--output", str(tmp_path / "s.zip")]) == 0
    names = set(zipfile.ZipFile(tmp_path / "s.zip").namelist())
    v = msz.version()
    assert names == {f"Ryoshu-{v}/{r}" for r in bwp.REQUIRED}
    rep = json.loads((tmp_path / "s.source.json").read_text("utf-8"))
    assert {e["path"] for e in rep["excluded"]} == {"tests/ledger.db", "tools/helper.exe"}


# --- workflow contract (static)---------------------------------------------------------------------
def test_workflow_triggers_are_scoped_pull_request_and_manual_only():
    on = re.search(r"^on:\n((?:  .*\n|\n)+?)permissions:", WORKFLOW, re.M).group(1)
    assert set(re.findall(r"^  (\w+):", on, re.M)) == {"pull_request", "workflow_dispatch"}
    code = "\n".join(line for line in WORKFLOW.splitlines() if not line.lstrip().startswith("#"))
    assert "pull_request_target" not in code and "push:" not in code and "schedule" not in code
    paths = re.findall(r"^      - '([^']+)'", on, re.M)
    for needed in ("receipt_ledger/**", "scripts/**", "packaging/windows/**", "setup.ps1", "requirements.lock.txt",
                   ".github/workflows/hosted-windows-acceptance.yml"):
        assert needed in paths
    assert not any(p.startswith(("docs", "README", "*.md", "**")) for p in paths)   # documentation-only edits do not rerun


def test_heavy_job_runs_only_for_same_repository_prs_or_manual_runs():
    cond = re.search(r"^    if: (.+)$", WORKFLOW, re.M).group(1)
    assert cond == "github.event_name == 'workflow_dispatch' || github.event.pull_request.head.repo.full_name == github.repository"


def test_workflow_is_read_only_bounded_and_pinned():
    assert re.search(r"^permissions:\n  contents: read\n(?!  )", WORKFLOW, re.M)
    assert "secrets." not in WORKFLOW and "GITHUB_TOKEN" not in WORKFLOW
    assert re.search(r"timeout-minutes: (\d+)", WORKFLOW) and int(re.search(r"timeout-minutes: (\d+)", WORKFLOW).group(1)) <= 180
    for use in re.findall(r"uses: (\S+)", WORKFLOW):
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", use), use
    assert "persist-credentials: false" in WORKFLOW
    assert not re.search(r"(?i)(install|winget|choco|scoop)[^\n]*(ollama|lm ?studio)", WORKFLOW)


def test_workflow_compiler_pins_match_readme_and_build_script():
    readme = (ROOT / "packaging/windows/README.md").read_text("utf-8")
    inno = re.search(r"INNO_SHA256: ([0-9a-f]{64})", WORKFLOW).group(1)
    iscc = re.search(r"ISCC_SHA256: ([0-9a-f]{64})", WORKFLOW).group(1)
    assert inno in readme and iscc in readme and iscc == bwp.COMPILER_SHA256
    assert "releases/download/is-7_1_0/innosetup-7.1.0-x64.exe" in WORKFLOW and "is-7_1_0" in readme
    assert "Pyrsys B\\.V\\." in WORKFLOW   # Authenticode signer as documented


def test_workflow_uploads_only_allowlisted_metadata():
    block = WORKFLOW[WORKFLOW.index("actions/upload-artifact"):]
    uploaded = re.findall(r"qa-report/([\w.-]+)", block)
    assert set(uploaded) == set(ha.UPLOAD_ALLOWLIST) | {"installer.build.json", "source-zip.json"}
    assert not re.search(r"(?i)\.(exe|dll|gguf|onnx|sqlite3?|db|log|zip)\b", block)


def test_build_script_still_never_downloads_or_runs_toolchain_installers():
    text = (ROOT / "scripts/build_windows_package.py").read_text("utf-8")
    assert "urlopen" not in text and "urllib" not in text and "Invoke-WebRequest" not in text


@pytest.mark.skipif(sys.platform != "win32", reason="Shell Known Folder")
def test_start_menu_group_comes_from_the_shell_known_folder_not_the_fake_profile(tmp_path, monkeypatch):
    import ctypes
    real = ha.known_programs_dir()
    fake = tmp_path / "QA 領収書 profile"
    monkeypatch.setenv("USERPROFILE", str(fake))
    monkeypatch.setenv("APPDATA", str(fake / "AppData/Roaming"))
    after = ha.known_programs_dir()
    assert after == real and fake not in after.parents        # env change does not move the Shell folder
    # independent oracle: the documented CSIDL_PROGRAMS (0x0002) answer for the same user
    buf = ctypes.create_unicode_buffer(32768)
    assert ctypes.windll.shell32.SHGetFolderPathW(None, 0x0002, None, 0, buf) == 0
    assert Path(buf.value) == real
    assert ha.start_menu_group() == real / "Ryoshu"


INNO_LOG = """2026-10-27 12:00:01.100   -- File entry --
2026-10-27 12:00:01.101   Dest filename: {app}\\start.ps1
2026-10-27 12:00:02.000   -- Icon entry --
2026-10-27 12:00:02.001   Dest filename: {g}\\Ryoshu.lnk
2026-10-27 12:00:02.002   Creating the icon.
2026-10-27 12:00:02.003   Successfully created the icon.
2026-10-27 12:00:02.010   -- Icon entry --
2026-10-27 12:00:02.011   Dest filename: {g2}\\Ryoshuを終了.lnk
2026-10-27 12:00:02.020   -- Icon entry --
2026-10-27 12:00:02.021   Dest filename: {g}\\Ryoshuの導入ガイド.lnk
"""


def _cands(tmp_path):
    profile, known, common = tmp_path / "QA 領収書 profile", tmp_path / "real user" / "Programs", tmp_path / "all users" / "Programs"
    return profile, ha.group_candidates(profile, known, common)


def _make(group: Path, names=ha.SHORTCUT_NAMES):
    group.mkdir(parents=True, exist_ok=True)
    for n in names:
        (group / n).write_bytes(b"L")


@pytest.mark.parametrize("where", ["known_user_programs", "isolated_profile_programs"])
def test_shortcuts_resolved_from_the_installer_log_in_a_per_user_group(tmp_path, where):
    profile, c = _cands(tmp_path)
    g = c[where]
    _make(g)
    log = INNO_LOG.replace("{g2}", "{g}").replace("{g}", str(g)).replace("{app}", str(tmp_path / "app"))
    d = ha.resolve_shortcut_group(ha.icon_entries_from_log(log), c)
    assert d["ok"] and d["selected_group_alias"] == where and d["group_arg"] is None and d["group_default"] == "Ryoshu"
    assert d["logged_group_dirs"] == [where] and sorted(d["logged_basenames"]) == sorted(ha.SHORTCUT_NAMES)
    other = "isolated_profile_programs" if where == "known_user_programs" else "known_user_programs"
    assert d["observed_by_candidate"][where] == sorted(ha.SHORTCUT_NAMES) and d["observed_by_candidate"][other] is None
    assert str(tmp_path) not in json.dumps(d, ensure_ascii=False)   # aliases and basenames only


def test_shortcuts_in_the_all_users_folder_or_elsewhere_are_never_accepted(tmp_path):
    profile, c = _cands(tmp_path)
    for g, alias in ((c["common_programs_NOT_ALLOWED"], "common_programs_NOT_ALLOWED"), (tmp_path / "elsewhere" / "Ryoshu", "<other>")):
        _make(g)
        log = INNO_LOG.replace("{g2}", "{g}").replace("{g}", str(g))
        d = ha.resolve_shortcut_group(ha.icon_entries_from_log(log), c)
        assert not d["ok"] and d["logged_group_dirs"] == [alias]


def test_split_groups_missing_or_unlogged_shortcuts_fail(tmp_path):
    profile, c = _cands(tmp_path)
    k, p = c["known_user_programs"], c["isolated_profile_programs"]
    _make(k)
    split = INNO_LOG.replace("{g2}", str(p)).replace("{g}", str(k))
    assert not ha.resolve_shortcut_group(ha.icon_entries_from_log(split), c)["ok"]          # two different groups
    only_two = "\n".join(l for l in INNO_LOG.replace("{g2}", "{g}").replace("{g}", str(k)).splitlines() if "導入ガイド" not in l)
    assert not ha.resolve_shortcut_group(ha.icon_entries_from_log(only_two), c)["ok"]       # log names only 2
    (k / "Ryoshuの導入ガイド.lnk").unlink()
    full = INNO_LOG.replace("{g2}", "{g}").replace("{g}", str(k))
    d = ha.resolve_shortcut_group(ha.icon_entries_from_log(full), c)
    assert not d["ok"] and d["selected_group_alias"] == "known_user_programs"                # logged but missing on disk
    assert ha.resolve_shortcut_group([], c)["logged_group_dirs"] == []


def test_main_uses_log_resolution_and_keeps_guards():
    text = (ROOT / "scripts/hosted_acceptance.py").read_text("utf-8")
    assert 'resolve_shortcut_group(icon_entries_from_log(log_text), candidates)' in text
    assert 'report["shortcut_diagnostics"] = shortcut' in text and text.index('report["shortcut_diagnostics"]') < text.index('check("start_menu_shortcuts"')
    assert 'for alias_name in ("known_user_programs", "isolated_profile_programs"):' in text and "refusing to overwrite it" in text
    assert '"/GROUP=' not in text   # the product's own DefaultGroupName is what is under test
    assert "if registry_exists():\n        ap.error(" in text


def test_acceptance_keeps_hkcu_refusal_and_never_targets_the_real_profile():
    text = (ROOT / "scripts/hosted_acceptance.py").read_text("utf-8")
    assert "if registry_exists():\n        ap.error(" in text
    assert 'os.environ.get("GITHUB_ACTIONS") != "true"' in text   # disposable runner only by default
    assert "QA 領収書 profile" in text and '"USERPROFILE": str(profile)' in text


# --- synthetic consent marker ------------------------------------------------------------------------
def test_qa_acceptance_is_recorded_as_synthetic_and_requires_exact_terms(tmp_path, monkeypatch):
    from test_prepare_llama import CRT_LICENSE, CRT_V, crt_server, write_crt_pin  # reuse the existing synthetic package
    import prepare_llama as pl
    root = tmp_path / "QA 領収書 profile/StudioYebisu/ReceiptLedger"
    base = root / "python/cpython-3.12.15-windows-x86_64-none"
    base.mkdir(parents=True)
    for n in pl.PYTHON_OWN_CRT:
        (base / n).write_bytes(b"MZ own " + n.encode())
    monkeypatch.setattr(pl, "file_version", lambda p: CRT_V)
    pin, pkg = write_crt_pin(tmp_path)
    pl.prepare_crt_only(root, pin, base, opener=crt_server(pkg), accept_terms_sha=sha(CRT_LICENSE), qa_test_acceptance=True)
    rec = json.loads(pl.consent_path(root, pl.load_crt_section(pin)).read_text("utf-8"))
    assert rec["method"] == "qa_test_flag" and rec["decision"] == "accepted"
    with pytest.raises(SystemExit):
        pl.main(["--root", str(root), "--crt-only", "--python-base", str(base), "--qa-test-acceptance"])
