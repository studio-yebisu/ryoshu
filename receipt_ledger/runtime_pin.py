"""Read-only view of receipt_ledger/runtime_pin.json, the single source pin.

The installer writes the pin; this module only reads it. Standard library
only, so setup CheckOnly can import it without the app's dependencies.
Readiness here never starts a process, never uses the network and never
hashes the multi-GB model; full verification happens at the first start.
"""
import argparse
import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
SEGMENT = re.compile(r"[A-Za-z0-9._-]+")


class PinError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Member:
    name: str
    bytes: int
    sha256: str


@dataclass(frozen=True)
class Pin:
    sha256: str
    runtime_tag: str
    runtime_commit: str
    dir_name: str
    server_exe: str
    members: tuple
    model_repo: str
    model_revision: str
    model_file: str
    model_bytes: int
    model_sha256: str
    model_license: str
    chat_template_sha256: str

    @property
    def model_id(self):
        return self.model_repo + "/" + self.model_file


@dataclass(frozen=True)
class RuntimePaths:
    root: Path
    runtime_dir: Path
    server: Path
    members: tuple
    model: Path
    marker: Path
    owners_dir: Path
    tmp_dir: Path


def _text(value):
    if not isinstance(value, str) or not value.strip() or value.startswith("<"):
        raise PinError("pin_invalid")
    return value


def _size(value):
    if type(value) is not int or value <= 0:
        raise PinError("pin_invalid")
    return value


def _hex(value, pattern):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise PinError("pin_invalid")
    return value


def _member_name(value):
    name = _text(value)
    parts = name.split("/")
    if any(not SEGMENT.fullmatch(p) or p in {".", ".."} for p in parts):
        raise PinError("pin_invalid")
    return name


def load_pin(path):
    path = Path(path)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise PinError("pin_missing") from None
    except OSError:
        raise PinError("pin_invalid") from None
    try:
        data = json.loads(raw.decode("utf-8"))
        runtime, model = data["runtime"], data["model"]
        if data.get("schema") != 1 or not isinstance(runtime, dict) or not isinstance(model, dict):
            raise PinError("pin_invalid")
        dir_name = _text(runtime["dir_name"])
        if not SEGMENT.fullmatch(dir_name) or dir_name in {".", ".."}:
            raise PinError("pin_invalid")
        listed = runtime["members"]
        if not isinstance(listed, list) or not listed:
            raise PinError("pin_invalid")
        members = tuple(Member(_member_name(m["name"]), _size(m["bytes"]), _hex(m["sha256"], HEX64)) for m in listed)
        if len({m.name.casefold() for m in members}) != len(members):
            raise PinError("pin_invalid")
        server_exe = _member_name(runtime["server_exe"])
        if server_exe not in {m.name for m in members}:
            raise PinError("pin_invalid")
        return Pin(sha256=hashlib.sha256(raw).hexdigest(), runtime_tag=_text(runtime["tag"]),
                   runtime_commit=_hex(runtime["commit"], HEX40), dir_name=dir_name, server_exe=server_exe,
                   members=members, model_repo=_text(model["repo"]), model_revision=_hex(model["revision"], HEX40),
                   model_file=_text(model["file"]), model_bytes=_size(model["bytes"]),
                   model_sha256=_hex(model["sha256"], HEX64), model_license=_text(model["license"]),
                   chat_template_sha256=_hex(model["chat_template_sha256"], HEX64))
    except PinError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError):
        raise PinError("pin_invalid") from None


def runtime_paths(pin, root):
    root = Path(root)
    runtime_dir = root / "llama" / pin.dir_name
    model = root / "models" / (pin.model_sha256 + ".gguf")
    return RuntimePaths(root=root, runtime_dir=runtime_dir, server=runtime_dir.joinpath(*pin.server_exe.split("/")),
                        members=tuple((m, runtime_dir.joinpath(*m.name.split("/"))) for m in pin.members),
                        model=model, marker=model.with_name(model.name + ".verified.json"),
                        owners_dir=root / "runtime" / "owners", tmp_dir=root / "runtime" / "tmp")


def is_link(path):
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def absolute(path):
    """Absolute path without resolving links, so a linked root stays visible to lstat."""
    return Path(os.path.abspath(os.fspath(path)))


def linked_ancestry(path):
    """True when path or any ancestor is a symlink/reparse point."""
    path = absolute(path)
    return any(is_link(p) for p in (path, *path.parents))


def safe_mkdir(path, root):
    """Create path below root one level at a time, refusing before any link is written through."""
    path, root = Path(path), Path(root)
    if linked_ancestry(root):
        raise OSError("linked runtime root")
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if is_link(current):
            raise OSError("linked runtime directory")
        if not current.exists():
            current.mkdir()
            if is_link(current):
                raise OSError("linked runtime directory")
        elif not current.is_dir():
            raise OSError("runtime path is not a directory")
    return path


def linked(path, root):
    """True when path or any component below root is a symlink/reparse point."""
    path, root = Path(path), Path(root)
    current = root
    if is_link(current):
        return True
    for part in path.relative_to(root).parts:
        current = current / part
        if is_link(current):
            return True
    return False


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def cached_sha256(path, cache):
    info = os.stat(path)
    key = (str(path), info.st_size, info.st_mtime_ns)
    if cache is not None and key in cache:
        return cache[key]
    value = file_sha256(path)
    if cache is not None:
        cache[key] = value
    return value


def unlisted_binaries(paths):
    listed = {str(p).casefold() for _, p in paths.members}
    found = []
    for item in paths.runtime_dir.rglob("*"):
        if item.suffix.lower() in {".exe", ".dll"} and str(item).casefold() not in listed:
            found.append(item)
    return found


def runtime_issue(paths, pin, cache=None):
    if not paths.runtime_dir.is_dir() and not linked(paths.runtime_dir, paths.root):
        return "runtime_missing"
    if linked(paths.runtime_dir, paths.root):
        return "runtime_integrity"
    for member, path in paths.members:
        if linked(path, paths.root):
            return "runtime_integrity"
        if not path.is_file():
            return "runtime_missing"
        if path.stat().st_size != member.bytes:
            return "runtime_integrity"
    if unlisted_binaries(paths):
        return "runtime_integrity"
    server = next(m for m, _ in paths.members if m.name == pin.server_exe)
    if cached_sha256(paths.server, cache) != server.sha256:
        return "runtime_integrity"
    return ""


def model_issue(paths, pin):
    if linked(paths.model, paths.root) or linked(paths.marker, paths.root):
        return "model_integrity"
    if not paths.model.is_file():
        return "model_missing"
    info = paths.model.stat()
    if info.st_size != pin.model_bytes:
        return "model_integrity"
    try:
        marker = json.loads(paths.marker.read_text(encoding="utf-8"))
        fresh = (marker.get("size") == info.st_size and marker.get("mtime_ns") == info.st_mtime_ns
                 and marker.get("sha256") == pin.model_sha256)
    except (OSError, ValueError, AttributeError):
        fresh = False
    return "" if fresh else "model_unverified"


def ready_state(pin_path, root, *, hash_cache=None):
    state = {"runtime_ok": False, "model_ok": False, "reason_code": "", "runtime_version": "",
             "model_sha256": "", "model_id": "", "pin_sha256": ""}
    try:
        pin = load_pin(pin_path)
    except PinError as exc:
        state["reason_code"] = exc.code
        return state
    state.update(runtime_version=pin.runtime_tag, model_sha256=pin.model_sha256, model_id=pin.model_id, pin_sha256=pin.sha256)
    paths = runtime_paths(pin, absolute(root))
    if linked_ancestry(paths.root):
        state["reason_code"] = "runtime_integrity"
        return state
    try:
        runtime = runtime_issue(paths, pin, hash_cache)
        model = model_issue(paths, pin)
    except OSError:
        runtime, model = "runtime_integrity", "model_integrity"
    state["runtime_ok"], state["model_ok"] = not runtime, not model
    state["reason_code"] = runtime or model
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description="Report local runtime/model readiness without starting anything.")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--pin", type=Path, default=Path(__file__).resolve().with_name("runtime_pin.json"))
    args = parser.parse_args(argv)
    state = ready_state(args.pin, absolute(args.root))
    print(json.dumps(state))
    return 0 if state["runtime_ok"] and state["model_ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
