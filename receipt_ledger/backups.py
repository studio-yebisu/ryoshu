"""Create an on-disk backup that can be streamed without loading the ZIP into RAM."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import zipfile

from . import __version__
from .store import now


def file_sha(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def create_backup(store, importer):
    temporary = tempfile.TemporaryDirectory(prefix="ryoshu-backup-")
    try:
        root = Path(temporary.name)
        snapshot = root / "ledger.sqlite3"
        with store.connect() as source:
            with closing(sqlite3.connect(snapshot)) as target:
                source.backup(target)
        with closing(sqlite3.connect(snapshot)) as db:
            originals = db.execute("SELECT id,extension,sha256 FROM receipts").fetchall()
            sources = db.execute("SELECT DISTINCT source_sha256 FROM receipt_import_sources").fetchall()
        archive = root / "backup.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as bundle:
            bundle.write(snapshot, "data/ledger.sqlite3")
            for receipt_id, extension, digest in originals:
                path = store.root / "receipts" / receipt_id / ("original" + extension)
                if not path.is_file():
                    raise ValueError("領収書の原本が見つかりません。保存先と、以前のバックアップを確認してください")
                if file_sha(path) != digest:
                    raise ValueError("原本が変更されています。保存先とバックアップを確認してください")
                bundle.write(path, f"data/receipts/{receipt_id}/original{extension}")
            for (digest,) in sources:
                path = importer.source_path(digest)
                if not path.is_file():
                    raise ValueError("分割前の元PDFが見つかりません。保存先と、以前のバックアップを確認してください")
                if file_sha(path) != digest:
                    raise ValueError("分割前の元PDFが変更されています。保存先とバックアップを確認してください")
                bundle.write(path, f"data/imports/{digest}/source.pdf")
            bundle.writestr("manifest.json", json.dumps({"app": "Ryoshu", "version": __version__, "created_at": now(),
                "receipts": len(originals), "contains": "SQLite snapshot, original receipts and unsplit source PDFs. Preview images are not included."}, ensure_ascii=False, indent=2))
        return temporary, archive
    except BaseException:
        temporary.cleanup()
        raise
