import hashlib
import io
import json
import re
import subprocess
import sys
import time
import types
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


# --- receipt child: fixture binding across a cwd change + allowlisted failure diagnostics ----------------------------
def _fixture_zip(path: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", json.dumps({"cases": [{"case_id": "V001"}, {"case_id": "V003"}]}))
        z.writestr("individual/V001.pdf", b"%PDF-fake-1")
        z.writestr("individual/V003.pdf", b"%PDF-fake-3")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buf.getvalue())
    return buf.getvalue()


def _child_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("hosted_child_under_test", ROOT / "scripts/hosted_acceptance_child.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)   # module level is stdlib only (no app import until run())
    return module


def test_relative_fixture_is_bound_in_the_callers_cwd_and_survives_a_child_cwd_change(tmp_path, monkeypatch):
    checkout, work = tmp_path / "checkout", tmp_path / "private work"
    data = _fixture_zip(checkout / "samples/receipts-100-v2/receipts-100.zip")
    work.mkdir()
    monkeypatch.chdir(checkout)
    fixture = ha.bind_fixture(Path("samples/receipts-100-v2/receipts-100.zip"))   # the workflow's relative argument
    assert fixture["path"].is_absolute() and fixture["path"] == (checkout / "samples/receipts-100-v2/receipts-100.zip").resolve()
    assert fixture["sha256"] == hashlib.sha256(data).hexdigest() and fixture["bytes"] == len(data)
    monkeypatch.chdir(work)   # the child's cwd: the relative path no longer points at the fixture
    assert not Path("samples/receipts-100-v2/receipts-100.zip").exists()
    cmd = ha.child_command(tmp_path / "py.exe", tmp_path / "child.py", tmp_path / "app", fixture, work / "child.json",
                           work / "child-failure.json", tmp_path / "report/synthetic-expenses.csv")
    assert cmd[cmd.index("--fixtures") + 1] == str(fixture["path"]) and cmd[cmd.index("--fixtures-sha256") + 1] == fixture["sha256"]
    child = _child_module()
    cases, pdfs = child.read_fixtures(Path(cmd[cmd.index("--fixtures") + 1]), fixture["sha256"])
    assert set(cases) == {"V001", "V003"} and pdfs["V003"] == b"%PDF-fake-3"


def test_fixture_and_child_paths_fail_closed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError):
        ha.bind_fixture(Path("samples/missing.zip"))
    (tmp_path / "folder.zip").mkdir()
    with pytest.raises(FileNotFoundError):
        ha.bind_fixture(Path("folder.zip"))
    good = _fixture_zip(tmp_path / "f.zip")
    fixture = ha.bind_fixture(tmp_path / "f.zip")
    with pytest.raises(ValueError, match="child_path_not_absolute"):
        ha.child_command(tmp_path / "py.exe", tmp_path / "c.py", tmp_path / "app", {**fixture, "path": Path("f.zip")},
                         tmp_path / "o.json", tmp_path / "x.json", tmp_path / "c.csv")
    child = _child_module()
    with pytest.raises(ValueError, match="fixtures_sha256_mismatch"):
        child.read_fixtures(tmp_path / "f.zip", "0" * 64)
    with pytest.raises(ValueError, match="fixtures_path_not_absolute"):
        child.read_fixtures(Path("f.zip"), hashlib.sha256(good).hexdigest())


# Synthetic hostile values are assembled from fragments so the public source scanner (private paths / token shapes)
# never sees a contiguous match; the runtime strings are byte-identical to the original literals (see guard test below).
FAKE_TOKEN_PREFIX = "gh" + "p_"
HOSTILE = "C:" + "\\" + "Users" + "\\someone\\AppData token=" + FAKE_TOKEN_PREFIX + "abcdefghijklmnopqrstuvwxyz0123456789" + " session=s3cr3t"


UNAPPROVED = "unapproved_exception_type"
SECRET_SHAPED = (FAKE_TOKEN_PREFIX + "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ", "session_SECRET012345", "TotallyArbitraryValidIdentifier")


def test_assembled_sentinels_equal_the_original_literals_byte_for_byte():
    # sha256/length of the ORIGINAL literal values (recorded before the fragment rewrite)
    expected = {"HOSTILE": (86, "0b02c88f48d859099dc81c0b3d5340a50882ed3d22df50718228f1900bd0d701"),
                "SECRET_SHAPED[0]": (40, "cfba07816b54a55152bc4bae1d943bb7f0bea83426665a4ef26a0f664d7064a3"),
                "SECRET_SHAPED[1]": (20, "ea0248db7c3ea55bdebdeb262494381e9958724023c2005d67eec2c4815da091"),
                "SECRET_SHAPED[2]": (31, "3782aa5d0e7f187a89af9c007e4c31bcb53db20158c2a05509aa0081bf2e9bcf")}
    actual = {"HOSTILE": HOSTILE, **{f"SECRET_SHAPED[{i}]": v for i, v in enumerate(SECRET_SHAPED)}}
    assert {k: (len(v), hashlib.sha256(v.encode()).hexdigest()) for k, v in actual.items()} == expected


@pytest.mark.parametrize("marker,returncode,result,expected_stage,expected_type", [
    ({"schema": 1, "stage": "fixtures", "exception_type": "FileNotFoundError", "message": HOSTILE}, 1, False, "fixtures", "FileNotFoundError"),
    ({"schema": 1, "stage": "fixtures", "exception_type": "ValueError"}, 1, False, "fixtures", "ValueError"),
    ({"schema": 1, "stage": "receipts", "exception_type": HOSTILE}, 1, False, "receipts", UNAPPROVED),
    ({"schema": 1, "stage": "fixtures", "exception_type": SECRET_SHAPED[0]}, 1, False, "fixtures", UNAPPROVED),
    ({"schema": 1, "stage": "receipts", "exception_type": SECRET_SHAPED[1]}, 1, False, "receipts", UNAPPROVED),
    ({"schema": 1, "stage": "csv_export", "exception_type": SECRET_SHAPED[2]}, 1, False, "csv_export", UNAPPROVED),
    ({"schema": 1, "stage": "receipts", "exception_type": 12345}, 1, False, "receipts", UNAPPROVED),
    ({"schema": 1, "stage": "receipts"}, 1, False, "receipts", None),
    ({"schema": 1, "stage": HOSTILE, "exception_type": "ValueError"}, 1, False, "unknown_before_marker", None),
    ({"schema": 2, "stage": "fixtures", "exception_type": "ValueError"}, 1, False, "unknown_before_marker", None),
    (None, 1, False, "unknown_before_marker", None),
    ("not json " + HOSTILE, 1, False, "unknown_before_marker", None),
    (None, 0, False, "unknown_before_marker", None),
])
def test_child_failure_summary_is_allowlisted_and_static(tmp_path, marker, returncode, result, expected_stage, expected_type):
    path = tmp_path / "child-failure.json"
    if marker is not None:
        path.write_text(marker if isinstance(marker, str) else json.dumps(marker), "utf-8")
    raw_before = path.read_bytes() if path.exists() else None
    summary = ha.child_failure_summary(path, returncode, result)
    assert summary["stage"] == expected_stage and summary["exit_code"] == returncode
    assert summary["exception_type"] == expected_type
    assert summary["exception_type"] in ha.APPROVED_EXCEPTION_TYPES | {UNAPPROVED, None}
    assert summary["reason"] in set(ha.CHILD_REASONS.values()) | {"child exited without a result",
                                                                  "child exited before writing an allowlisted diagnostic marker"}
    text = json.dumps(summary)
    for leak in ("Users", "ghp_", "s3cr3t", "token", "session", "SECRET", "TotallyArbitrary", "message", "12345"):
        assert leak not in text
    assert (path.read_bytes() if path.exists() else None) == raw_before   # the private marker is never rewritten
    assert ha.child_failure_summary(path, 0, True) is None


def test_exception_type_vocabulary_never_echoes_unapproved_values():
    for name in ("FileNotFoundError", "ValueError", "ModuleNotFoundError", "CrtActivationError", "RuntimeFailure"):
        assert ha.public_exception_type(name) == name
    for value in (*SECRET_SHAPED, "", "fileNotFoundError", "ValueError ", "Value\nError", ["ValueError"], {"a": 1}, 0):
        assert ha.public_exception_type(value) == UNAPPROVED
    assert ha.public_exception_type(None) is None
    assert all(not any(s in name for s in ("ghp_", "session", "token", "secret", "key")) for name in ha.APPROVED_EXCEPTION_TYPES)
    assert "EXCEPTION_TYPE = re.compile" not in (ROOT / "scripts/hosted_acceptance.py").read_text("utf-8")   # no regex echo path left


def test_real_child_failing_before_any_result_writes_only_the_allowlisted_marker(tmp_path):
    import subprocess
    work, app = tmp_path / "private work", tmp_path / "empty app"
    work.mkdir()
    app.mkdir()
    fixture = ha.bind_fixture(tmp_path / "f.zip") if _fixture_zip(tmp_path / "f.zip") else None
    out, failure, csv_out = work / "child.json", work / "child-failure.json", tmp_path / "report/synthetic-expenses.csv"
    cmd = ha.child_command(Path(sys.executable).resolve(), (ROOT / "scripts/hosted_acceptance_child.py").resolve(), app, fixture,
                           out, failure, csv_out)
    r = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=60)   # no receipt_ledger in the app dir
    assert r.returncode != 0 and not out.exists() and not csv_out.exists()
    marker = json.loads(failure.read_text("utf-8"))
    assert marker == {"schema": 1, "stage": "start", "exception_type": "ModuleNotFoundError"}
    summary = ha.child_failure_summary(failure, r.returncode, out.is_file())
    assert summary == {"stage": "start", "exception_type": "ModuleNotFoundError", "reason": ha.CHILD_REASONS["start"],
                       "exit_code": r.returncode}
    assert str(tmp_path) not in json.dumps(summary)


def test_main_binds_fixture_before_install_and_reports_child_failure_before_the_gate():
    text = (ROOT / "scripts/hosted_acceptance.py").read_text("utf-8")
    assert "str(a.fixtures)" not in text and "a.report_dir /" not in text
    assert text.index("fixture = bind_fixture(a.fixtures)") < text.index('report["stage"] = "install"')
    assert text.index('report["child_failure"] = summary') < text.index('check("receipt_child_succeeded"')
    assert 'check("receipt_child_succeeded", child.returncode == 0 and summary is None' in text
    assert "child.stderr" not in text.split('report["child_failure"]', 1)[1].split("check(", 1)[0]


# --- format-aware OCR gate ------------------------------------------------------------------------------------------
MODEL_SHA = "7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5"
PIN_SHA = "02f6dafb061af1de041d2632063c4a2df8d48aae5c2740632f2b9eb2640d2008"
# LOCAL root integration rows (fictional, not the 100 benchmark, NOT remote CI); only non-sensitive fields kept.
LOCAL_V001_PDF = {"case_id": "V001", "input": "V001.pdf", "status": "review", "error_present": False, "ocr_engine": "Indra OCR",
                  "ocr_page_sources": ["text"], "ocr_lines": 25, "ocr_text_chars": 232, "native_pdf_text_chars": 256,
                  "origin": "model", "raw_model_candidate_is_object": True, "model_digest": MODEL_SHA, "runtime_pin_sha256": PIN_SHA}
LOCAL_V003_JPEG = {"case_id": "V003", "input": "V003-rendered.jpg", "status": "review", "error_present": False, "ocr_engine": "Indra OCR",
                   "ocr_page_sources": ["ocr"], "ocr_lines": 38, "ocr_text_chars": 278, "native_pdf_text_chars": 0,
                   "origin": "model", "raw_model_candidate_is_object": True, "model_digest": MODEL_SHA, "runtime_pin_sha256": PIN_SHA}


def _route(row):
    return ha.ocr_route(ha.project_receipt(row, MODEL_SHA, PIN_SHA))


@pytest.mark.parametrize("row,route", [
    (LOCAL_V001_PDF, "pdf_text"),                                                   # native text-layer PDF (local actual)
    (LOCAL_V003_JPEG, "image_ocr"),                                                 # real image OCR (local actual)
    ({**LOCAL_V001_PDF, "ocr_page_sources": ["ocr"], "native_pdf_text_chars": 0}, "pdf_ocr"),     # scanned PDF
    ({**LOCAL_V001_PDF, "ocr_page_sources": ["hybrid"]}, "pdf_hybrid"),             # text layer + OCR on one page
    ({**LOCAL_V001_PDF, "ocr_page_sources": ["text", "ocr"]}, "pdf_hybrid"),        # mixed pages
])
def test_valid_indra_routes_pass_per_format(row, route):
    assert _route(row) == (True, route, "ok")


@pytest.mark.parametrize("row,reason", [
    ({**LOCAL_V003_JPEG, "ocr_page_sources": ["text"]}, "image_input_without_image_ocr"),
    ({**LOCAL_V003_JPEG, "ocr_page_sources": ["hybrid"]}, "image_input_without_image_ocr"),
    ({**LOCAL_V003_JPEG, "native_pdf_text_chars": 5}, "image_input_has_native_pdf_text"),
    ({**LOCAL_V001_PDF, "ocr_lines": 0}, "empty_or_malformed_counts"),
    ({**LOCAL_V003_JPEG, "ocr_text_chars": 0}, "empty_or_malformed_counts"),
    ({**LOCAL_V001_PDF, "ocr_lines": "25"}, "empty_or_malformed_counts"),
    ({**LOCAL_V001_PDF, "ocr_text_chars": -1}, "empty_or_malformed_counts"),
    ({**LOCAL_V001_PDF, "ocr_lines": True}, "empty_or_malformed_counts"),
    ({**LOCAL_V001_PDF, "native_pdf_text_chars": None}, "empty_or_malformed_counts"),
    ({**LOCAL_V001_PDF, "ocr_page_sources": []}, "missing_page_sources"),
    ({**LOCAL_V001_PDF, "ocr_page_sources": "text"}, "missing_page_sources"),
    ({**LOCAL_V001_PDF, "ocr_page_sources": ["error"]}, "unknown_or_error_page_source"),
    ({**LOCAL_V001_PDF, "ocr_page_sources": ["text", "vision"]}, "unknown_or_error_page_source"),
    ({**LOCAL_V001_PDF, "native_pdf_text_chars": 0}, "text_route_without_native_pdf_text"),
    ({**LOCAL_V001_PDF, "ocr_engine": "tesseract"}, "wrong_ocr_engine"),
    ({**LOCAL_V001_PDF, "input": "V001.jpg"}, "unexpected_case_or_input"),
    ({**LOCAL_V003_JPEG, "input": "V001.pdf"}, "unexpected_case_or_input"),
    ({**LOCAL_V001_PDF, "case_id": "V999"}, "unexpected_case_or_input"),
    ("not a dict", "unexpected_case_or_input"),
])
def test_invalid_ocr_evidence_fails_closed(row, reason):
    ok, _, why = _route(row)
    assert not ok and why == reason


def test_projection_is_a_fixed_allowlist_that_never_echoes_free_values():
    hostile = "C:" + "\\" + "Users" + "\\someone\\AppData " + "gh" + "p_" + "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    row = {**LOCAL_V001_PDF, "status": hostile, "ocr_page_sources": ["text", hostile], "ocr_engine": hostile, "input": hostile,
           "case_id": hostile, "model_digest": hostile, "account": "消耗品費", "raw_model_candidate_keys": [hostile],
           "ocr_text": hostile, "path": hostile, "error_present": "yes", "ocr_lines": 2 ** 40}
    p = ha.project_receipt(row, MODEL_SHA, PIN_SHA)
    assert set(p) == {"case_id", "input_kind", "ocr_engine_is_indra", "ocr_page_sources", "ocr_lines", "ocr_text_chars",
                      "native_pdf_text_chars", "status", "error_present", "origin_is_model", "raw_model_candidate_is_object",
                      "model_digest_matches_pin", "runtime_pin_matches"}
    assert p["case_id"] == "unknown_case" and p["input_kind"] == "unexpected_input" and p["status"] == "unknown_status"
    assert p["ocr_page_sources"] == ["text", "unknown_source"] and p["error_present"] is None and not p["model_digest_matches_pin"]
    text = json.dumps(p, ensure_ascii=False)
    for leak in ("Users", "gh" + "p_", "消耗品費", "AppData"):
        assert leak not in text
    local = [ha.project_receipt(r, MODEL_SHA, PIN_SHA) for r in (LOCAL_V001_PDF, LOCAL_V003_JPEG)]
    assert all(q["model_digest_matches_pin"] and q["runtime_pin_matches"] and q["origin_is_model"] and q["status"] == "review"
               for q in local)


def test_main_persists_projection_before_any_receipt_assertion_and_keeps_model_checks():
    text = (ROOT / "scripts/hosted_acceptance.py").read_text("utf-8")
    body = text.split('report["stage"] = "receipts"', 1)[1]
    persist = body.index('report["receipts"] = [project_receipt(')
    assert persist < body.index('check("receipts_present_for_every_case"') < body.index('check("strict_crt_activation_active"')
    assert persist < body.index('check(f"indra_ocr_route_') < body.index('check(f"local_model_structured_output_')
    assert 'report["receipts"] = res["receipts"]' not in text            # the full private rows are never published
    assert 'any(s not in ("text",) for s in r["ocr_page_sources"])' not in text   # the false image-only rule is gone
    # unchanged strict model/pin/status, JPEG native-text and CSV checks
    assert ('r["status"] == "review" and not r["error_present"]\n                  and r["origin"] == "model" and r["raw_model_candidate_is_object"]\n'
            '                  and r["model_digest"] == pin["model"]["sha256"] and r["runtime_pin_sha256"] == pin_sha)') in text
    assert 'check("image_input_has_no_native_pdf_text", jpg["native_pdf_text_chars"] == 0)' in text
    assert 'check("csv_exported", res["csv"]["data_rows"] == len(res["receipts"]), **res["csv"])' in text


# --- check collector contract + full mocked orchestration (SYNTHETIC; not Windows acceptance) --------------------------
def test_old_collector_shape_reproduces_the_ci4_collision_and_the_new_one_namespaces_it():
    py = ha.crt_provenance([], {}, "X:/managed", ha.CRT_NAMES, "X:/Windows")   # the real protocol dict: has 'ok'
    assert "ok" in py and py["ok"] is False

    def old_check(name, ok, **detail):    # the pre-fix collector signature
        return {"check": name, "ok": bool(ok), **detail}
    with pytest.raises(TypeError, match="multiple values for argument 'ok'"):
        old_check("python_crt_maps_from_managed_python", py["ok"], **py)
    assert old_check("csv_exported", False, check="forged")["check"] == "forged"   # old shape let metadata forge identity
    record = ha.check_record("python_crt_maps_from_managed_python", py["ok"], **py)
    assert record["check"] == "python_crt_maps_from_managed_python" and record["ok"] is False and record["detail"]["ok"] is False
    forged = ha.check_record("csv_exported", False, ok=True, check="forged", name="forged", data_rows=2)
    assert forged["check"] == "csv_exported" and forged["ok"] is False and forged["detail"]["check"] == "forged"
    with pytest.raises(ValueError):
        ha.check_record("", True)


def _sha_bytes(b):
    return hashlib.sha256(b).hexdigest()


class FakeWindows:
    """Synthetic side effects for a full main() run inside tmp_path: no installer, servers, registry or native process."""
    DLL = {n: b"MZ synthetic " + n.encode() for n in ha.CRT_NAMES}

    def __init__(self, tmp, monkeypatch, child_mutator=None):
        self.tmp, self.registered, self.calls, self.child_mutator = tmp, False, [], child_mutator
        self.pin = {"model": {"sha256": MODEL_SHA}, "runtime": {"tag": "b11700", "dir_name": "b11700-cpu-x64"},
                    "crt": {"version": "14.51.36247.0", "license": {"sha256": "1" * 64}, "package": {"sha256": "2" * 64},
                            "files": [{"name": n, "sha256": _sha_bytes(b)} for n, b in self.DLL.items()]}}
        self.payload = {"start.ps1": b"start", "stop.ps1": b"stop", "setup.ps1": b"setup",
                        "receipt_ledger/runtime_pin.json": json.dumps(self.pin).encode()}
        self.source_zip, self.installer, self.fixtures = tmp / "source.zip", tmp / "Ryoshu-setup.exe", tmp / "receipts-100.zip"
        with zipfile.ZipFile(self.source_zip, "w") as z:
            for name, data in self.payload.items():
                z.writestr("ryoshu/" + name, data)
        self.installer.write_bytes(b"MZ synthetic installer")
        _fixture_zip(self.fixtures)
        self.build = tmp / "build.json"
        self.build.write_text(json.dumps({"installer_sha256": _sha_bytes(self.installer.read_bytes()),
                                          "source_sha256": _sha_bytes(self.source_zip.read_bytes()), "app_version": "1.4.0",
                                          "payload_files": {n: _sha_bytes(b) for n, b in self.payload.items()}}), "utf-8")
        self.known = tmp / "known user" / "Programs"
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("RYOSHU_ALLOW_LOCAL_ACCEPTANCE", "1")
        monkeypatch.setenv("SystemRoot", str(tmp / "SysRoot"))
        monkeypatch.setattr(ha, "registry_exists", lambda: self.registered)
        monkeypatch.setattr(ha, "known_programs_dir", lambda folder_id=None: (tmp / "all users" / "Programs") if folder_id else self.known)
        monkeypatch.setattr(ha, "probe_ollama", lambda: {"command": False, "install_dirs": [], "services": [], "processes": [], "listener_11434": False})
        monkeypatch.setattr(ha, "runner_facts", lambda: {"kind": "synthetic_mock_orchestration"})
        monkeypatch.setattr(ha, "vc_redist_registrations", lambda: [])
        monkeypatch.setattr(ha.subprocess, "run", self.run)
        self.popen = []   # (script, kwargs) of every file-backed launch; the REAL run_file_backed opens the files
        fake = self

        class Popen:
            def __init__(p, argv, **kw):
                fake.popen.append((fake.script(argv), kw))
                if fake.script(argv) == "start.ps1":
                    fake.start_argv = argv
                assert subprocess.PIPE not in (kw.get("stdout"), kw.get("stderr")) and kw.get("stdin") == subprocess.DEVNULL
                p.returncode = fake.run(argv, **kw).returncode
                kw["stdout"].write(b"[RYOSHU synthetic.slot] mock output\n")

            def wait(p, timeout=None):
                return p.returncode

            def kill(p):
                pass
        monkeypatch.setattr(ha.subprocess, "Popen", Popen)
        self.health = {"app": "receipt-ledger", "app_version": "1.4.0"}
        monkeypatch.setattr(ha, "urlopen", lambda url, timeout=0: io.BytesIO(json.dumps(self.health).encode()))

    @staticmethod
    def script(argv):
        argv = [str(a) for a in argv]
        return Path(argv[0]).name if not argv[0].lower().endswith("powershell.exe") else next(Path(a).name for a in argv if a.endswith(".ps1"))

    def paths(self, work):
        profile = work / "QA 領収書 profile"
        return profile, profile / "AppData/Local/Programs/StudioYebisu/Ryoshu", profile / "StudioYebisu/ReceiptLedger"

    def run(self, argv, **kw):
        argv = [str(a) for a in argv]
        self.calls.append(self.script(argv))
        done = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[0] == str(self.installer):
            install = Path(next(a for a in argv if a.startswith("/DIR="))[5:])
            log = Path(next(a for a in argv if a.startswith("/LOG="))[5:])
            profile = install.parents[4]
            for name, data in self.payload.items():
                (install / name).parent.mkdir(parents=True, exist_ok=True)
                (install / name).write_bytes(data)
            (install / "unins000.exe").write_bytes(b"MZ")
            group = profile / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Ryoshu"
            group.mkdir(parents=True)
            lines = []
            for n in ha.SHORTCUT_NAMES:
                (group / n).write_bytes(b"L")
                lines += ["2026-10-27 12:00:00.000   -- Icon entry --", f"2026-10-27 12:00:00.001   Dest filename: {group / n}"]
            log.write_text("\n".join(lines), "utf-8")
            self.registered = True
        elif "setup.ps1" in " ".join(argv):
            root = Path(kw["env"]["USERPROFILE"]) / "StudioYebisu/ReceiptLedger"
            crt = self.pin["crt"]
            (root / "runtime").mkdir(parents=True)
            (root / "runtime/setup-state.json").write_text(json.dumps({"state": "complete", "inference_state": "complete",
                "runtime_pin_sha256": _sha_bytes(self.payload["receipt_ledger/runtime_pin.json"])}), "utf-8")
            (root / "crt").mkdir()
            (root / f"crt/consent-{crt['version']}.json").write_text(json.dumps({"decision": "accepted", "method": "qa_test_flag",
                "eula_sha256": crt["license"]["sha256"], "package_sha256": crt["package"]["sha256"], "version": crt["version"]}), "utf-8")
            (root / "runtime/qa-event-crt-terms.json").write_text(json.dumps({"schema": 1, "event": "crt_terms_qa_test_acceptance",
                "method": "qa_test_flag", "eula_sha256": crt["license"]["sha256"], "version": crt["version"]}), "utf-8")
            for folder in (root / "python/cpython-3.12.15-windows-x86_64-none", root / "llama/b11700-cpu-x64"):
                folder.mkdir(parents=True)
                for n, b in self.DLL.items():
                    (folder / n).write_bytes(b)
            for d in ("venv", "models"):
                (root / d).mkdir()
        elif argv[0].endswith("python.exe") and "--out" in argv:
            out, csv_out = Path(argv[argv.index("--out") + 1]), Path(argv[argv.index("--csv") + 1])
            root = Path(kw["env"]["USERPROFILE"]) / "StudioYebisu/ReceiptLedger"
            (root / "qa-ledger/data").mkdir(parents=True)
            mods = lambda folder, names: [{"name": n, "path": str(folder / n), "class": "redistributable_crt",
                                           "sha256": _sha_bytes(self.DLL[n])} for n in names]
            pin_sha = _sha_bytes(self.payload["receipt_ledger/runtime_pin.json"])
            res = {"activation": {"state": "active"},
                   "receipts": [{**LOCAL_V001_PDF, "runtime_pin_sha256": pin_sha}, {**LOCAL_V003_JPEG, "runtime_pin_sha256": pin_sha}],
                   "csv": {"sha256": "3" * 64, "bytes": 10, "data_rows": 2},
                   "python_modules": mods(root / "python/cpython-3.12.15-windows-x86_64-none", ha.CRT_NAMES),
                   "llama_server": {"modules": mods(root / "llama/b11700-cpu-x64", ha.LLAMA_CRT)},
                   "runtime_status": {"state": "ready"}, "cpu_args": ["-t", "6"]}
            if self.child_mutator:
                self.child_mutator(res)
            out.write_text(json.dumps(res), "utf-8")
            csv_out.write_text("header\nrow1\nrow2\n", "utf-8")
        elif "start.ps1" in " ".join(argv):
            root = Path(kw["env"]["USERPROFILE"]) / "StudioYebisu/ReceiptLedger"
            (root / "runtime/server.json").write_text("{}", "utf-8")
        elif "stop.ps1" in " ".join(argv):
            root = Path(kw["env"]["USERPROFILE"]) / "StudioYebisu/ReceiptLedger"
            (root / "runtime/server.json").unlink(missing_ok=True)
        elif argv[0].endswith("unins000.exe"):
            install = Path(argv[0]).parent
            for p in sorted(install.rglob("*"), reverse=True):
                p.unlink() if p.is_file() else p.rmdir()
            profile = install.parents[4]
            group = profile / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Ryoshu"
            for p in group.glob("*"):
                p.unlink()
            group.rmdir()
            self.registered = False
        return done

    def main(self, work, report):
        return ha.main(["--installer", str(self.installer), "--build-report", str(self.build), "--source-zip", str(self.source_zip),
                        "--fixtures", str(self.fixtures), "--work-dir", str(work), "--report-dir", str(report)])


def _acceptance(report_dir):
    return json.loads((report_dir / "acceptance.json").read_text("utf-8"))


def test_full_mocked_orchestration_reaches_passed_through_crt_provenance_launch_and_uninstall(tmp_path, monkeypatch):
    fake = FakeWindows(tmp_path, monkeypatch)
    rc = fake.main(tmp_path / "work", tmp_path / "report")
    out = _acceptance(tmp_path / "report")
    assert rc == 0 and out["status"] == "passed" and out["stage"] == "done", out.get("error")
    names = [c["check"] for c in out["checks"]]
    for expected in ("fixtures_bound_before_child", "start_menu_shortcuts", "receipt_child_succeeded", "indra_ocr_route_V001_pdf",
                     "indra_ocr_route_V003_jpeg", "local_model_structured_output_V001", "csv_exported",
                     "python_crt_maps_from_managed_python", "llama_crt_maps_from_managed_runtime", "start_no_browser",
                     "health_is_installed_version", "stop_succeeded", "uninstall_removed_code_shortcuts_registration",
                     "uninstall_preserved_ledger_and_sentinel", "ollama_absent_after"):
        assert expected in names
    assert all(c["ok"] is True and set(c) == {"check", "ok", "detail"} for c in out["checks"])
    py = next(c for c in out["checks"] if c["check"] == "python_crt_maps_from_managed_python")
    assert py["detail"]["ok"] is True and set(py["detail"]["owned"]) == set(ha.CRT_NAMES)      # protocol dict kept as evidence
    assert [p["ocr_route"] for p in out["receipts"]] == ["pdf_text", "image_ocr"]
    assert fake.calls[0] == "Ryoshu-setup.exe" and fake.calls[-1] == "unins000.exe" and not fake.registered
    assert sorted(p.name for p in (tmp_path / "report").iterdir()) == ["acceptance.json", "synthetic-expenses.csv"]
    assert str(tmp_path) not in json.dumps(out, ensure_ascii=False)
    # file-backed transport: setup/child/start/stop write to private regular files, never pipes
    assert [s for s, _ in fake.popen] == ["setup.ps1", "python.exe", "start.ps1", "stop.ps1"]
    logs = tmp_path / "work" / "logs"
    for _, kw in fake.popen:
        for stream in ("stdout", "stderr"):
            assert Path(kw[stream].name).parent == logs and kw[stream].closed
    start_argv = [str(a) for a in fake.start_argv]
    assert "-NoBrowser" in start_argv
    assert start_argv[start_argv.index("-DataDir") + 1] == str(fake.paths((tmp_path / "work").resolve())[2] / "qa-ledger/data")
    assert any(c["check"] == "start_data_dir_is_receipt_child_book" for c in out["checks"])
    assert sorted(p.name for p in logs.iterdir()) == sorted(f"{n}-0.{s}.log" for n in ("setup", "child", "start", "stop")
                                                            for s in ("stdout", "stderr"))
    assert "mock output" in (logs / "start-0.stdout.log").read_text("utf-8")
    assert "mock output" not in json.dumps(out) and "logs" not in json.dumps(out)   # raw logs never reach the report


def test_false_llama_provenance_fails_closed_as_a_recorded_check_not_a_typeerror(tmp_path, monkeypatch):
    def drop_llama_msvcp(res):
        res["llama_server"]["modules"] = [m for m in res["llama_server"]["modules"] if m["name"] != "msvcp140.dll"]
    fake = FakeWindows(tmp_path, monkeypatch, child_mutator=drop_llama_msvcp)
    rc = fake.main(tmp_path / "work", tmp_path / "report")
    out = _acceptance(tmp_path / "report")
    assert rc == 1 and out["status"] == "failed" and out["stage"] == "receipts"
    assert out["error_type"] == "AssertionError" and out["error"] == "llama_crt_maps_from_managed_runtime"
    last = out["checks"][-1]
    assert last["check"] == "llama_crt_maps_from_managed_runtime" and last["ok"] is False and last["detail"]["ok"] is False
    assert "msvcp140.dll:not_loaded_from_managed_dir" in last["detail"]["failures"]
    assert any(c["check"] == "python_crt_maps_from_managed_python" and c["ok"] for c in out["checks"])
    assert not fake.registered   # the cleanup path still uninstalled


def test_child_metadata_cannot_forge_a_check_identity_or_outcome(tmp_path, monkeypatch):
    def forge(res):
        res["csv"] = {"ok": True, "check": "forged_pass", "name": "forged", "data_rows": 999}
    fake = FakeWindows(tmp_path, monkeypatch, child_mutator=forge)
    rc = fake.main(tmp_path / "work", tmp_path / "report")
    out = _acceptance(tmp_path / "report")
    assert rc == 1 and out["error_type"] == "AssertionError" and out["error"] == "csv_exported"
    last = out["checks"][-1]
    assert last["check"] == "csv_exported" and last["ok"] is False and last["detail"]["ok"] is True
    assert "forged_pass" not in [c["check"] for c in out["checks"]]


def test_failed_health_runs_file_backed_cleanup_stop_and_keeps_logs_private(tmp_path, monkeypatch):
    fake = FakeWindows(tmp_path, monkeypatch)
    fake.health = {"app": "receipt-ledger", "app_version": "0.0.0"}
    rc = fake.main(tmp_path / "work", tmp_path / "report")
    out = _acceptance(tmp_path / "report")
    assert rc == 1 and out["stage"] == "launch_no_browser" and out["error"] == "health_is_installed_version"
    assert [s for s, _ in fake.popen][-1] == "stop.ps1" and fake.calls[-1] == "unins000.exe" and not fake.registered
    assert (tmp_path / "work/logs/cleanup-stop-0.stdout.log").is_file()      # the finally stop is file-backed too
    assert "mock output" not in json.dumps(out)


def test_start_timeout_and_nonzero_exit_stay_failing_gates(tmp_path, monkeypatch):
    fake = FakeWindows(tmp_path, monkeypatch)
    real_run = fake.run

    def start_times_out(argv, **kw):
        if fake.script(argv) == "start.ps1":
            raise subprocess.TimeoutExpired("start", 900)
        return real_run(argv, **kw)
    monkeypatch.setattr(fake, "run", start_times_out)
    assert fake.main(tmp_path / "w1", tmp_path / "r1") == 1
    out = _acceptance(tmp_path / "r1")
    assert out["stage"] == "launch_no_browser" and out["error_type"] == "TimeoutExpired" and not fake.registered

    (tmp_path / "b").mkdir()
    fake2 = FakeWindows(tmp_path / "b", monkeypatch)
    real_run2 = fake2.run

    def stop_fails(argv, **kw):
        done = real_run2(argv, **kw)
        return types.SimpleNamespace(returncode=5, stdout="", stderr="") if fake2.script(argv) == "stop.ps1" else done
    monkeypatch.setattr(fake2, "run", stop_fails)
    assert fake2.main(tmp_path / "w2", tmp_path / "r2") == 1
    last = _acceptance(tmp_path / "r2")["checks"][-1]
    assert last == {"check": "stop_succeeded", "ok": False, "detail": {"exit_code": 5}}


def test_background_launch_paths_never_capture_pipes():
    text = (ROOT / "scripts/hosted_acceptance.py").read_text("utf-8")
    main_body = text.split("def main(", 1)[1]
    assert "capture_output" not in main_body and "PIPE" not in main_body and ".communicate(" not in text
    for label in ('"setup"', '"child"', '"start"', '"stop"', '"cleanup-stop"'):
        assert f"logs, {label}" in main_body
    assert '"-NoBrowser", "-DataDir", str(data_dir)]' in main_body and 'data_dir = root / "qa-ledger/data"' in main_body
    assert 'RECEIPT_LEDGER_DATA=str(root / "qa-ledger/data")' in main_body   # the child and start open the same book


# --- file-backed transport vs inherited pipe (TRANSPORT test with short Python processes; NOT Windows app acceptance) --
TRANSPORT_PARENT = r'''
import subprocess, sys, time
from pathlib import Path
d = Path(sys.argv[1])
child = subprocess.Popen([sys.executable, "-c", sys.argv[2], str(d)], stdout=sys.stdout, stderr=sys.stderr, close_fds=False)
(d / "descendant.pid").write_text(str(child.pid))
deadline = time.monotonic() + 20
while not (d / "ready").exists() and time.monotonic() < deadline:
    time.sleep(0.05)
print("parent done", flush=True)
'''
TRANSPORT_DESCENDANT = r'''
import sys, time
from pathlib import Path
d = Path(sys.argv[1])
(d / "ready").write_text("1")
deadline = time.monotonic() + 30          # bounded maximum lifetime even if the test never releases it
while not (d / "release").exists() and time.monotonic() < deadline:
    time.sleep(0.05)
(d / "exited").write_text("1")
'''


def _release_descendant(d: Path):
    (d / "release").write_text("1")
    deadline = time.monotonic() + 35
    while not (d / "exited").exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not (d / "exited").exists() and (d / "descendant.pid").exists():   # strict own cleanup: only the pid we spawned
        pid = int((d / "descendant.pid").read_text())
        if sys.platform == "win32":
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(1, False, pid)
            if h:
                ctypes.windll.kernel32.TerminateProcess(h, 1)
                ctypes.windll.kernel32.CloseHandle(h)
        else:
            import os
            import signal
            os.kill(pid, signal.SIGKILL)
    return (d / "exited").exists()


def _transport_argv(d):
    return [sys.executable, "-c", TRANSPORT_PARENT, str(d), TRANSPORT_DESCENDANT]


def test_captured_pipe_waits_on_a_descendant_that_outlives_its_parent(tmp_path):
    d = tmp_path / "pipe"
    d.mkdir()
    proc = subprocess.Popen(_transport_argv(d), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert proc.wait(timeout=30) == 0 and (d / "ready").exists()      # the direct parent has exited...
        with pytest.raises(subprocess.TimeoutExpired):
            proc.communicate(timeout=2)                                     # ...but EOF never comes while the descendant lives
        assert not (d / "exited").exists()
    finally:
        assert _release_descendant(d)
        out, _ = proc.communicate(timeout=30)                               # released: EOF arrives, bounded
    assert b"parent done" in out


def test_file_backed_helper_returns_after_the_parent_while_the_descendant_still_holds_the_handles(tmp_path):
    d, logs = tmp_path / "file", tmp_path / "logs"
    d.mkdir()
    try:
        t = time.monotonic()
        done = ha.run_file_backed(_transport_argv(d), logs, "start", timeout=30)   # the ACTUAL helper, real processes
        elapsed = time.monotonic() - t
        assert done.returncode == 0 and "parent done" in done.stdout and done.stderr == ""
        assert (d / "ready").exists() and not (d / "exited").exists()       # descendant still alive and holding the files
        assert elapsed < 25
    finally:
        assert _release_descendant(d)
    assert sorted(p.name for p in logs.iterdir()) == ["start-0.stderr.log", "start-0.stdout.log"]


def test_file_backed_helper_timeout_exit_code_no_overwrite_and_bounded_read(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "probe-0.stdout.log").write_text("unrelated", "utf-8")
    t = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as e:
        ha.run_file_backed([sys.executable, "-c", "import time; time.sleep(30)"], logs, "probe", timeout=1)
    assert e.value.cmd == "probe" and str(tmp_path) not in str(e.value) and time.monotonic() - t < 20
    assert (logs / "probe-0.stdout.log").read_text("utf-8") == "unrelated"   # never overwritten
    done = ha.run_file_backed([sys.executable, "-c", "import sys; print('x' * 5000); sys.stderr.write('err'); sys.exit(3)"],
                              logs, "probe", timeout=30)
    assert done.returncode == 3 and done.stdout.startswith("x" * 5000) and done.stderr == "err"
    assert (logs / "probe-2.stdout.log").is_file()
    monkeypatch.setattr(ha, "OUTPUT_READ_LIMIT", 100)
    assert len(ha.read_bounded(logs / "probe-2.stdout.log")) == 100


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
