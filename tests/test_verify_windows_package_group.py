import ctypes
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import verify_windows_package as vwp  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Shell Known Folder")


def test_qa_group_uses_the_shell_known_programs_folder_not_the_fake_profile(tmp_path, monkeypatch):
    real = vwp.known_programs_dir()
    fake = tmp_path / "isolated-profile 日本語"
    monkeypatch.setenv("USERPROFILE", str(fake))
    monkeypatch.setenv("APPDATA", str(fake / "AppData/Roaming"))
    assert vwp.known_programs_dir() == real and fake not in real.parents
    buf = ctypes.create_unicode_buffer(32768)   # independent oracle: CSIDL_PROGRAMS for the same user
    assert ctypes.windll.shell32.SHGetFolderPathW(None, 0x0002, None, 0, buf) == 0 and Path(buf.value) == real
    assert vwp.qa_group_dir("Ryoshu QA 0123456789") == real / "Ryoshu QA 0123456789"
    assert vwp.qa_group_dir("G", query=lambda: Path(r"X:\Known")) == Path(r"X:\Known\G")


def test_main_binds_shortcut_checks_to_the_installer_log_and_keeps_guards():
    text = (ROOT / "scripts/verify_windows_package.py").read_text("utf-8")
    assert "group_dir = qa_group_dir(group)" in text
    assert "Start Menu/Programs/Ryoshu" not in text and "profile / 'AppData/Roaming" not in text
    assert "'/GROUP=' + group" in text   # installer and checks use the same unique QA group
    assert "if group_dir.exists():\n        parser.error(" in text
    assert "if candidates['isolated_profile_programs'].exists():\n        parser.error(" in text
    assert "if registry_exists():\n        parser.error('An installed Ryoshu already exists; do not replace it for QA')" in text
    # exact Inno-log binding (same helper as hosted acceptance), not a glob of an assumed folder
    assert "resolve_shortcut_group(icon_entries_from_log(log_text), candidates, group_arg=group)" in text
    assert "group_dir.glob('*.lnk')" not in text and "assert shortcut['ok']" in text
    assert "group_dir = candidates[shortcut['selected_group_alias']]" in text
    hosted = (ROOT / "scripts/hosted_acceptance.py").read_text("utf-8")
    assert "from verify_windows_package import (SHORTCUT_NAMES, icon_entries_from_log, known_programs_dir," in hosted
    assert "def resolve_shortcut_group" not in hosted and "def icon_entries_from_log" not in hosted   # one implementation


LOG = """2026-10-27 12:00:02.000   -- Icon entry --
2026-10-27 12:00:02.001   Dest filename: {a}\\Ryoshu.lnk
2026-10-27 12:00:02.010   -- Icon entry --
2026-10-27 12:00:02.011   Dest filename: {b}\\Ryoshuを終了.lnk
2026-10-27 12:00:02.020   -- Icon entry --
2026-10-27 12:00:02.021   Dest filename: {a}\\Ryoshuの導入ガイド.lnk
"""


def _links(folder, names=vwp.SHORTCUT_NAMES):
    folder.mkdir(parents=True, exist_ok=True)
    for n in names:
        (folder / n).write_bytes(b"L")


@pytest.mark.parametrize("where", ["known_user_programs", "isolated_profile_programs"])
def test_legacy_verifier_accepts_one_logged_per_user_qa_group(tmp_path, where):
    group = "Ryoshu QA 0123456789"
    c = vwp.group_candidates(tmp_path / "isolated-profile", tmp_path / "known" / "Programs", tmp_path / "common" / "Programs", group)
    _links(c[where])
    d = vwp.resolve_shortcut_group(vwp.icon_entries_from_log(LOG.replace("{b}", "{a}").replace("{a}", str(c[where]))), c, group_arg=group)
    assert d["ok"] and d["selected_group_alias"] == where and d["group_arg"] == group
    assert str(tmp_path) not in str(d)


def test_legacy_verifier_rejects_common_split_missing_and_unlogged_groups(tmp_path):
    group = "Ryoshu QA 0123456789"
    c = vwp.group_candidates(tmp_path / "isolated-profile", tmp_path / "known" / "Programs", tmp_path / "common" / "Programs", group)
    _links(c["common_programs_NOT_ALLOWED"])
    common = vwp.resolve_shortcut_group(vwp.icon_entries_from_log(LOG.replace("{b}", "{a}").replace("{a}", str(c["common_programs_NOT_ALLOWED"]))), c)
    assert not common["ok"] and common["logged_group_dirs"] == ["common_programs_NOT_ALLOWED"]
    k, p = c["known_user_programs"], c["isolated_profile_programs"]
    _links(k)
    assert not vwp.resolve_shortcut_group(vwp.icon_entries_from_log(LOG.replace("{a}", str(k)).replace("{b}", str(p))), c)["ok"]
    (k / "Ryoshuの導入ガイド.lnk").unlink()
    assert not vwp.resolve_shortcut_group(vwp.icon_entries_from_log(LOG.replace("{b}", "{a}").replace("{a}", str(k))), c)["ok"]
    assert not vwp.resolve_shortcut_group([], c)["ok"]


def test_existing_registration_is_still_refused_before_any_write(tmp_path, monkeypatch):
    monkeypatch.setattr(vwp, "registry_exists", lambda: True)
    monkeypatch.setattr(vwp, "known_programs_dir", lambda: pytest.fail("must refuse before touching the Start Menu"))
    for name in ("setup.exe", "source.zip", "python.exe"):
        (tmp_path / name).write_bytes(b"x")
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["verify", "--installer", str(tmp_path / "setup.exe"), "--source-zip", str(tmp_path / "source.zip"),
                                      "--qa-python", str(tmp_path / "python.exe"), "--output-dir", str(out)])
    with pytest.raises(SystemExit):
        vwp.main()
    assert not out.exists()
