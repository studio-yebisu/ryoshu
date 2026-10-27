"""Installer input boundary checks; no Windows installation or user DB access."""
import importlib.util
from pathlib import Path
import stat
import zipfile

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / 'scripts/build_windows_package.py'
SPEC = importlib.util.spec_from_file_location('windows_package', MODULE_PATH)
package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package)


def source_zip(tmp_path, additions=(), omit=(), version='1.3.2', folder='Ryoshu-1.3.2'):
    archive = tmp_path / 'source.zip'
    with zipfile.ZipFile(archive, 'w') as writer:
        for relative in sorted(package.REQUIRED - set(omit)):
            value = f'__version__ = "{version}"\n' if relative == 'receipt_ledger/__init__.py' else 'public source\n'
            writer.writestr(f'{folder}/{relative}', value)
        for name, content in additions:
            info = zipfile.ZipInfo('placeholder')
            info.filename = name  # Preserve malicious backslashes on Windows.
            writer.writestr(info, content)
    return archive


def test_valid_source_extracts_required_files(tmp_path):
    archive = source_zip(tmp_path)
    root, version, files = package.unpack_source(archive, tmp_path / 'output')
    assert version == '1.3.2'
    assert set(files) == package.REQUIRED
    assert (root / 'web/processing.js').read_text() == 'public source\n'


@pytest.mark.parametrize('name', [
    'Ryoshu-1.3.2/../outside.txt',
    '/Ryoshu-1.3.2/outside.txt',
    'Ryoshu-1.3.2/receipt_ledger\\outside.py',
    'Ryoshu-1.3.2/C:/outside.txt',
    'AnotherRoot/file.txt',
    'Ryoshu-1.3.2/AUX.txt',
    'Ryoshu-1.3.2/document.txt.',
    'Ryoshu-1.3.2/document.txt ',
])
def test_unsafe_paths_are_rejected_before_any_extraction(tmp_path, name):
    destination = tmp_path / 'output'
    with pytest.raises(ValueError):
        package.unpack_source(source_zip(tmp_path, [(name, 'bad')]), destination)
    assert not destination.exists()


@pytest.mark.parametrize('name', [
    '.local/private.json', '.env', 'data/ledger.sqlite3', 'receipt.key',
    'cache/model.bin', 'models/weights.bin', 'venv/Scripts/python.exe',
    'node_modules/example/index.js', 'backup.ryoshubak', 'server.log',
    '.LOCAL/private.json', 'Cache/weights.bin', 'DATA/private.json',
])
def test_private_files_are_rejected(tmp_path, name):
    with pytest.raises(ValueError, match='Private'):
        package.unpack_source(source_zip(tmp_path, [(f'Ryoshu-1.3.2/{name}', 'private')]), tmp_path / 'output')


def test_case_insensitive_duplicate_is_rejected(tmp_path):
    with pytest.raises(ValueError, match='Duplicate Windows'):
        package.unpack_source(source_zip(tmp_path, [('Ryoshu-1.3.2/readme.MD', 'duplicate')]), tmp_path / 'output')


def test_symbolic_link_is_rejected(tmp_path):
    archive = source_zip(tmp_path)
    with zipfile.ZipFile(archive, 'a') as writer:
        link = zipfile.ZipInfo('Ryoshu-1.3.2/link')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        writer.writestr(link, '../private')
    with pytest.raises(ValueError, match='Symbolic links'):
        package.unpack_source(archive, tmp_path / 'output')


def test_missing_runtime_and_version_disagreement_are_rejected(tmp_path):
    with pytest.raises(ValueError, match='Missing application'):
        package.unpack_source(source_zip(tmp_path, omit=['web/processing.js']), tmp_path / 'missing')
    with pytest.raises(ValueError, match='version disagree'):
        package.unpack_source(source_zip(tmp_path, version='1.3.1'), tmp_path / 'mismatch')


@pytest.mark.parametrize('name', [
    'third_party/llama-server.exe', 'scripts/ggml-cpu-x64.dll', 'docs/Qwen3-4B-Q4_K_M.gguf',
    'samples/LLAMA.DLL', 'models.gguf', 'downloads/partial.part', 'docs/model.safetensors', 'web/x.onnx',
    'llama/b11700-cpu-x64/LICENSE-llama.cpp.txt', 'Downloads/readme.txt',
])
def test_runtime_binaries_and_model_weights_are_never_distributed(tmp_path, name):
    destination = tmp_path / 'output'
    with pytest.raises(ValueError, match='not distributable|Private'):
        package.unpack_source(source_zip(tmp_path, [(f'Ryoshu-1.3.2/{name}', 'binary')]), destination)
    assert not destination.exists()


def test_pin_preparer_and_exact_license_texts_are_required_and_legitimate_names_pass(tmp_path):
    for required in ('receipt_ledger/runtime_pin.json', 'receipt_ledger/runtime_pin.py', 'scripts/prepare_llama.py',
                     'scripts/ms_vcredist.py',
                     'third_party/llama.cpp/LICENSE', 'third_party/Qwen3-4B-GGUF/LICENSE'):
        assert required in package.REQUIRED
        with pytest.raises(ValueError, match='Missing application'):
            package.unpack_source(source_zip(tmp_path, omit=[required]), tmp_path / ('m-' + required.replace('/', '_')))
    # 'runtime' / 'llama' are forbidden only as whole path parts, never as name prefixes
    root, _, files = package.unpack_source(source_zip(tmp_path, [('Ryoshu-1.3.2/receipt_ledger/runtime.py', 'x')]), tmp_path / 'ok')
    assert 'receipt_ledger/runtime.py' in files and 'third_party/llama.cpp/LICENSE' in files


@pytest.mark.parametrize('helper', [
    'scripts/python-runtime.ps1', 'scripts/bootstrap-uv.ps1',
    'scripts/launcher-process.ps1',
])
def test_missing_launcher_runtime_helper_is_rejected(tmp_path, helper):
    destination = tmp_path / 'output'
    with pytest.raises(ValueError, match='Missing application'):
        package.unpack_source(source_zip(tmp_path, omit=[helper]), destination)
    assert not destination.exists()
