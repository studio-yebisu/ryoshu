"""Package the curated fixtures; exclude the viewing-only combined PDF."""
from pathlib import Path
import zipfile

root = Path(__file__).resolve().parents[1] / 'samples/receipts-100-v2'
target = root / 'receipts-100.zip'
pending = root / '.receipts-100.zip.tmp'
files = [p for p in sorted(root.rglob('*')) if p.is_file() and p.name not in {'receipts-100.zip','.receipts-100.zip.tmp','receipts-100-review.pdf'} and '__pycache__' not in p.parts and p.suffix != '.pyc']
with zipfile.ZipFile(pending, 'w', zipfile.ZIP_DEFLATED) as bundle:
    for path in files:
        bundle.write(path, path.relative_to(root).as_posix())
pending.replace(target)
print('Packaged 100 individual PDFs and their curated supporting files.')
