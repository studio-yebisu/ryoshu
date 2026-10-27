"""Create the public source ZIP (Ryoshu-<version>/...) from the files Git tracks at HEAD.

Inclusion follows build_windows_package.unpack_source exactly (same forbidden parts, suffixes and private-data
rules); every excluded tracked path is listed in the JSON report so nothing is dropped silently. File bytes come
from `git archive` (the same export Git would publish, honouring .gitattributes), never from the working tree.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_windows_package import FORBIDDEN_PARTS, FORBIDDEN_SUFFIXES, REQUIRED  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def excluded_reason(relative: str) -> str | None:
    """Why build_windows_package would refuse this public-source path (None = distributable)."""
    pure = PurePosixPath(relative)
    if any(part.casefold() in FORBIDDEN_PARTS for part in pure.parts):
        return 'private_or_runtime_folder'
    leaf = pure.name.lower()
    if leaf.endswith(FORBIDDEN_SUFFIXES):
        return 'binary_or_model'
    if (leaf.startswith('.env') or leaf.endswith(('.sqlite3', '.sqlite', '.db', '.log', '.pem', '.key', '.ryoshubak'))
            or relative.casefold().startswith('data/') and not (
                relative == 'data/account-catalog.json' or re.fullmatch(r'data/catalog-history/[0-9.-]+\.json', relative))):
        return 'private_data'
    return None


def version() -> str:
    text = (ROOT / 'receipt_ledger/__init__.py').read_text(encoding='utf-8')
    return re.search(r'__version__\s*=\s*"(\d+\.\d+\.\d+)"', text).group(1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--ref', default='HEAD')
    a = ap.parse_args(argv)
    if a.output.exists():
        ap.error('Output exists; choose a new file')
    v = version()
    top = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', '--show-toplevel'], check=True, capture_output=True, text=True).stdout.strip()
    if Path(top).resolve() != ROOT.resolve():
        ap.error('Run from a checkout whose Git root is the app root (e.g. the CI checkout)')
    raw = subprocess.run(['git', '-C', str(ROOT), 'archive', '--format=zip', a.ref], check=True, capture_output=True).stdout
    with zipfile.ZipFile(io.BytesIO(raw)) as probe:
        tracked = {i.filename for i in probe.infolist() if not i.is_dir()}
    missing = sorted(REQUIRED - tracked)
    if missing:
        ap.error(f'Tracked tree lacks required application files: {missing[:5]}')
    excluded, count = [], 0
    with zipfile.ZipFile(io.BytesIO(raw)) as src, zipfile.ZipFile(a.output, 'x', zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            if info.is_dir():
                continue
            reason = excluded_reason(info.filename)
            if reason:
                excluded.append({'path': info.filename, 'reason': reason})
                continue
            item = zipfile.ZipInfo(f'Ryoshu-{v}/{info.filename}', date_time=info.date_time)
            item.external_attr = 0o644 << 16
            item.compress_type = zipfile.ZIP_DEFLATED
            out.writestr(item, src.read(info))
            count += 1
    report = {'app_version': v, 'ref': a.ref, 'files': count, 'excluded': excluded,
              'zip': a.output.name, 'sha256': hashlib.sha256(a.output.read_bytes()).hexdigest()}
    a.output.with_suffix('.source.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('app_version', 'files', 'sha256')} | {'excluded': len(excluded)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
