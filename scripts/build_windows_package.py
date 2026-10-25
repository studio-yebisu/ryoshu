"""Build the per-user Windows setup EXE from a public source ZIP (no models).

Requires official Inno Setup 7.1.0 x64. Its compiler installer SHA-256 is recorded
in packaging/windows/README.md. This script never downloads or runs toolchains.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile

REQUIRED = {
    'LICENSE', 'README.md', 'THIRD_PARTY.md', 'setup.ps1', 'start.ps1', 'stop.ps1',
    'receipt_ledger/__init__.py', 'web/app.js', 'web/processing.js',
    'data/account-catalog.json', 'requirements.lock.txt',
    'packaging/windows/ryoshu.iss', 'packaging/windows/can-modify.ps1',
    'packaging/windows/launch.ps1', 'packaging/windows/before-install.txt',
    'packaging/windows/welcome.html',
}
FORBIDDEN_PARTS = {'.git', '.local', '.venv', 'venv', 'node_modules', '__pycache__',
                   'runtime', 'cache', 'models', '.pytest_cache'}
COMPILER_SHA256 = 'd06ebd38f38e3cee60a3c50cc45bd449d77e0bc6a5cabc607ea9886808e4de1a'
RESERVED_WINDOWS = {'con', 'prn', 'aux', 'nul'} | {
    f'{prefix}{digit}' for prefix in ('com', 'lpt') for digit in range(1, 10)}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def unpack_source(archive: Path, destination: Path) -> tuple[Path, str, list[str]]:
    """Validate before extraction; private files or ambiguous names are fatal."""
    with zipfile.ZipFile(archive) as source:
        if len(source.infolist()) > 5000 or sum(i.file_size for i in source.infolist()) > 256 * 1024 * 1024:
            raise ValueError('Source archive exceeds the public source size limit')
        files: dict[str, zipfile.ZipInfo] = {}
        root_name = None
        folded: set[str] = set()
        for info in source.infolist():
            # ZipInfo normalizes backslashes on Windows; validate original bytes.
            name = info.orig_filename
            pure = PurePosixPath(name)
            if ('\\' in name or pure.is_absolute() or '..' in pure.parts or
                    ':' in name or not pure.parts or '\x00' in name):
                raise ValueError(f'Unsafe source path: {name}')
            if any(part != part.rstrip(' .') or part.split('.')[0].casefold() in RESERVED_WINDOWS
                   for part in pure.parts):
                raise ValueError(f'Unsafe Windows source path: {name}')
            if root_name is None:
                root_name = pure.parts[0]
            if pure.parts[0] != root_name:
                raise ValueError('Source ZIP must have exactly one root directory')
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError(f'Symbolic links are not accepted: {name}')
            if info.is_dir():
                continue
            relative = PurePosixPath(*pure.parts[1:]).as_posix()
            if relative == '.' or any(part.casefold() in FORBIDDEN_PARTS for part in pure.parts):
                raise ValueError(f'Private source file: {name}')
            leaf = pure.name.lower()
            if (leaf.startswith('.env') or leaf.endswith(('.sqlite3', '.sqlite', '.db',
                    '.log', '.pem', '.key', '.ryoshubak')) or
                    relative.casefold().startswith('data/') and not (
                        relative == 'data/account-catalog.json' or
                        re.fullmatch(r'data/catalog-history/[0-9.-]+\.json', relative))):
                raise ValueError(f'Private data is not distributable: {name}')
            if name.casefold() in folded:
                raise ValueError(f'Duplicate Windows source path: {name}')
            folded.add(name.casefold())
            files[relative] = info
        missing = REQUIRED - files.keys()
        if missing:
            raise ValueError(f'Missing application files: {sorted(missing)}')
        version_text = source.read(files['receipt_ledger/__init__.py']).decode('utf-8')
        version_match = re.search(r'__version__\s*=\s*"(\d+\.\d+\.\d+)"', version_text)
        if not version_match:
            raise ValueError('Missing semantic version')
        version = version_match.group(1)
        if root_name != f'Ryoshu-{version}':
            raise ValueError('Source folder name and application version disagree')
        for relative, info in files.items():
            target = destination / root_name / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(info) as incoming, target.open('xb') as outgoing:
                shutil.copyfileobj(incoming, outgoing)
        return destination / root_name, version, sorted(files)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-zip', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--iscc', type=Path, required=True)
    args = parser.parse_args()
    archive = args.source_zip.resolve(strict=True)
    compiler = args.iscc.resolve(strict=True)
    if sha256(compiler) != COMPILER_SHA256:
        parser.error('Compiler differs from the verified official Inno Setup 7.1.0 x64 executable')
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ryoshu-package-', dir=output) as scratch:
        source, version, files = unpack_source(archive, Path(scratch))
        installer = output / f'Ryoshu-{version}-windows-x64-setup.exe'
        manifest = installer.with_suffix('.build.json')
        if installer.exists() or manifest.exists():
            parser.error('Output already exists; choose a new output directory')
        command = [str(compiler), '/Qp', f'/DSourceDir={source}',
                   f'/DOutputDir={output}', f'/DAppVersion={version}',
                   str(source / 'packaging/windows/ryoshu.iss')]
        subprocess.run(command, check=True)
        if not installer.is_file():
            raise RuntimeError('Compiler did not produce the expected installer')
        evidence = {
            'app_version': version,
            'source_zip': archive.name,
            'source_sha256': sha256(archive),
            'compiler_sha256': sha256(compiler),
            'installer': installer.name,
            'installer_sha256': sha256(installer),
            'installer_bytes': installer.stat().st_size,
            'payload_files': {name: sha256(source / name) for name in files},
            'runtime_bundled': False, 'models_bundled': False,
            'code_signed': False,
        }
        manifest.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: evidence[key] for key in ('app_version', 'installer',
          'installer_sha256', 'installer_bytes')}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
