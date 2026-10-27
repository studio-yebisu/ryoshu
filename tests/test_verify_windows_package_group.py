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


def test_main_binds_shortcut_checks_to_the_queried_group_and_keeps_guards():
    text = (ROOT / "scripts/verify_windows_package.py").read_text("utf-8")
    assert "group_dir = qa_group_dir(group)" in text
    assert "Start Menu/Programs/Ryoshu" not in text and "profile / 'AppData/Roaming" not in text
    assert "'/GROUP=' + group" in text   # installer and checks use the same unique QA group
    assert "if group_dir.exists():\n        parser.error(" in text
    assert "if registry_exists():\n        parser.error('An installed Ryoshu already exists; do not replace it for QA')" in text


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
