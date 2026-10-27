"""Read the official Microsoft VC_redist.x64.exe purely as DATA (standard library only).

The package is never executed, installed, laid out or passed to msiexec. Each end user's setup downloads it
directly from Microsoft (pinned URL/size/SHA-256); this module only:
  1. verifies the full package SHA-256 BEFORE any parsing,
  2. checks the WiX Burn '.wixburn' header and that both CAB containers sit at the pinned offsets with the
     pinned sizes and SHA-256,
  3. decompresses MSZIP/stored CAB data with strict bounds (no LZX/Quantum, no unsafe names, size limits),
  4. returns exactly the pinned entries (license text, runtime CAB, the four DLLs), each re-verified by
     size + SHA-256.
"""
from __future__ import annotations

import hashlib
import re
import struct
import zlib
from pathlib import PureWindowsPath

MAX_FOLDER_BYTES = 256 * 1024 * 1024   # decompressed bytes per CAB folder
MAX_BLOCK_BYTES = 32 * 1024 + 6144     # MSZIP block upper bound (32 KiB output, compressed slightly larger)
MAX_FILES = 4096
BURN_MAGIC = 0x00F14300


class PackageError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def burn_containers(pkg: bytes, burn_pin: dict) -> tuple[bytes, bytes]:
    """Return (ux_cab, attached_cab) only if the Burn header and both containers match the pin exactly."""
    try:
        pe = struct.unpack_from("<I", pkg, 0x3C)[0]
        if pkg[:2] != b"MZ" or pkg[pe:pe + 4] != b"PE\0\0":
            raise PackageError("package_invalid", "not PE")
        nsec = struct.unpack_from("<H", pkg, pe + 6)[0]
        opt_size = struct.unpack_from("<H", pkg, pe + 20)[0]
        sec = pe + 24 + opt_size
        if nsec > 96:
            raise PackageError("package_invalid", "sections")
        burn = None
        for i in range(nsec):
            if pkg[sec + 40 * i: sec + 40 * i + 8].rstrip(b"\0") == b".wixburn":
                burn = struct.unpack_from("<I", pkg, sec + 40 * i + 20)[0]
        if burn is None:
            raise PackageError("package_invalid", "no .wixburn")
        magic, _ver = struct.unpack_from("<II", pkg, burn)
        stub, _ck, sig_off, sig_size, fmt, count = struct.unpack_from("<6I", pkg, burn + 24)
        if magic != BURN_MAGIC or fmt != 1 or count != 2:
            raise PackageError("package_invalid", "burn header")
        sizes = struct.unpack_from("<2I", pkg, burn + 48)
    except struct.error:
        raise PackageError("package_invalid", "truncated") from None
    out = []
    for name, size in zip(("ux", "attached"), sizes):
        want = burn_pin[name]
        off = want["offset"]
        if size != want["bytes"] or off < 0 or off + size > len(pkg):
            raise PackageError("package_invalid", f"{name} container bounds")
        data = pkg[off:off + size]
        if data[:4] != b"MSCF" or struct.unpack_from("<I", data, 8)[0] != size or _sha(data) != want["sha256"]:
            raise PackageError("package_invalid", f"{name} container identity")
        out.append(data)
    if out and stub != burn_pin["ux"]["offset"]:
        raise PackageError("package_invalid", "stub offset")
    return out[0], out[1]


def cab_entries(cab: bytes, wanted: set[str]) -> dict[str, bytes]:
    """Bounded MSZIP/stored CAB reader. Returns only the requested entries (by exact name)."""
    try:
        if cab[:4] != b"MSCF":
            raise PackageError("package_invalid", "cab magic")
        coff_files = struct.unpack_from("<I", cab, 16)[0]
        n_folders, n_files, flags = struct.unpack_from("<HHH", cab, 26)
        if flags & 3 or n_files > MAX_FILES or n_folders > 64:
            raise PackageError("package_invalid", "cab header")
        pos, res_folder, res_data = 36, 0, 0
        if flags & 4:
            cb_hdr, res_folder, res_data = struct.unpack_from("<HBB", cab, 36)
            pos = 40 + cb_hdr
        folders = []
        for _ in range(n_folders):
            coff_data, n_blocks, comp = struct.unpack_from("<IHH", cab, pos)
            if comp & 0xF not in (0, 1):
                raise PackageError("package_invalid", "unsupported compression")
            folders.append((coff_data, n_blocks, comp & 0xF))
            pos += 8 + res_folder
        files = []
        p = coff_files
        for _ in range(n_files):
            size, off, ifolder, _d, _t, attrs = struct.unpack_from("<IIHHHH", cab, p)
            end = cab.index(b"\0", p + 16, p + 16 + 512)
            name = cab[p + 16:end].decode("utf-8" if attrs & 0x80 else "latin-1")
            pure = PureWindowsPath(name)
            if (not name or pure.is_absolute() or ".." in pure.parts or ":" in name or "\\" in name or "/" in name
                    or not re.fullmatch(r"[A-Za-z0-9._-]+", name)):
                raise PackageError("package_invalid", "unsafe cab name")
            if ifolder >= n_folders:
                raise PackageError("package_invalid", "cab folder index")
            files.append((name, size, off, ifolder))
            p = end + 1
        if len({f[0].casefold() for f in files}) != len(files):
            raise PackageError("package_invalid", "duplicate cab name")
        needed_folders = {f[3] for f in files if f[0] in wanted}
        streams = {}
        for i in needed_folders:
            coff_data, n_blocks, comp = folders[i]
            q, out, prev = coff_data, bytearray(), b""
            for _ in range(n_blocks):
                _ck, cb_data, cb_uncomp = struct.unpack_from("<IHH", cab, q)
                q += 8 + res_data
                if cb_data > MAX_BLOCK_BYTES or q + cb_data > len(cab):
                    raise PackageError("package_invalid", "cab block bounds")
                blk = cab[q:q + cb_data]
                q += cb_data
                if comp == 0:
                    data = blk
                else:
                    if blk[:2] != b"CK":
                        raise PackageError("package_invalid", "mszip block")
                    dec = zlib.decompressobj(-15, zdict=prev) if prev else zlib.decompressobj(-15)
                    data = dec.decompress(blk[2:], cb_uncomp + 1)
                    if dec.unconsumed_tail:
                        raise PackageError("package_invalid", "mszip overrun")
                    data += dec.flush()
                if len(data) != cb_uncomp:
                    raise PackageError("package_invalid", "cab block size")
                out += data
                if len(out) > MAX_FOLDER_BYTES:
                    raise PackageError("package_invalid", "cab folder too large")
                prev = bytes(data[-32768:]) if comp == 1 else b""
            streams[i] = bytes(out)
    except (struct.error, ValueError, zlib.error):
        raise PackageError("package_invalid", "cab parse") from None
    got = {}
    for name, size, off, ifolder in files:
        if name in wanted:
            data = streams[ifolder][off:off + size]
            if len(data) != size:
                raise PackageError("package_invalid", "cab entry truncated")
            got[name] = data
    missing = wanted - set(got)
    if missing:
        raise PackageError("package_invalid", "missing " + ",".join(sorted(missing)))
    return got


def verified(data: bytes, spec: dict, what: str) -> bytes:
    if len(data) != spec["bytes"] or _sha(data) != spec["sha256"]:
        raise PackageError("package_integrity", what)
    return data


def read_license(pkg: bytes, crt: dict) -> bytes:
    """Exact Microsoft runtime license RTF carried in the package UX container (verified by pinned SHA-256)."""
    if _sha(pkg) != crt["package"]["sha256"] or len(pkg) != crt["package"]["bytes"]:
        raise PackageError("package_integrity", "package")
    ux, _ = burn_containers(pkg, crt["burn"])
    lic = crt["license"]
    return verified(cab_entries(ux, {lic["ux_entry"]})[lic["ux_entry"]], lic, "license")


def read_runtime_files(pkg: bytes, crt: dict) -> dict[str, bytes]:
    """The four pinned DLLs from the package's x64 minimum-runtime CAB, each verified by size + SHA-256."""
    if _sha(pkg) != crt["package"]["sha256"] or len(pkg) != crt["package"]["bytes"]:
        raise PackageError("package_integrity", "package")
    _, attached = burn_containers(pkg, crt["burn"])
    cab_spec = crt["cab"]
    inner = verified(cab_entries(attached, {cab_spec["attached_entry"]})[cab_spec["attached_entry"]], cab_spec, "runtime cab")
    entries = cab_entries(inner, {f["cab_entry"] for f in crt["files"]})
    return {f["name"]: verified(entries[f["cab_entry"]], f, f["name"]) for f in crt["files"]}


MAX_DOCX_PART = 4 * 1024 * 1024


def docx_to_text(docx: bytes) -> str:
    """Plain-text view of Microsoft's official terms DOCX (paragraph text of word/document.xml only; the hash-pinned
    DOCX bytes stay the record). Bounded: one member, size-capped, no DTD/entities, nothing executed."""
    import io
    import zipfile
    from xml.etree import ElementTree as ET
    try:
        with zipfile.ZipFile(io.BytesIO(docx)) as z:
            info = z.getinfo("word/document.xml")
            if info.file_size > MAX_DOCX_PART or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise PackageError("license_invalid", "document part")
            xml = z.read(info)
        if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
            raise PackageError("license_invalid", "dtd")
        doc = ET.fromstring(xml)
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        raise PackageError("license_invalid", "docx") from None
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paras = ["".join(t.text or "" for t in p.iter(w + "t")) for p in doc.iter(w + "p")]
    return "\n".join(p for p in paras if p.strip()) + "\n"


def rtf_to_text(rtf: bytes) -> str:
    """Plain-text view of the license RTF for display (characters only; the RTF bytes stay the record)."""
    t = rtf.decode("latin-1")
    # drop RTF destination groups (font/color tables, stylesheet, info, \* ignorable) by brace depth
    out, i, skip_depth, depth = [], 0, None, 0
    while i < len(t):
        ch = t[i]
        if ch == "{":
            depth += 1
            m = re.match(r"\{\\(\*|fonttbl|colortbl|stylesheet|info|generator)", t[i:i + 14])
            if skip_depth is None and m:
                skip_depth = depth
        elif ch == "}":
            if skip_depth == depth:
                skip_depth = None
                depth -= 1
                i += 1
                continue
            depth -= 1
        if skip_depth is None:
            out.append(ch)
        i += 1
    t = "".join(out)
    t = re.sub(r"\\pard?\b", "\n", t)
    t = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes([int(m.group(1), 16)]).decode("cp1252", "replace"), t)
    t = re.sub(r"\{\\\*[^{}]*\}", "", t)
    t = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", t)
    t = re.sub(r"[{}]", "", t)
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip() + "\n"
