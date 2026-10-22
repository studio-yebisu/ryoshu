"""Optional page-per-receipt import, with immutable source PDFs and provenance."""
import hashlib
import io
from pathlib import Path
from contextlib import closing
import uuid

from . import config
from .pipeline import validate_file
from .pdf_runtime import PDF_LOCK


class Importer:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            columns = db.execute("PRAGMA table_info(receipt_import_sources)").fetchall()
            if any(column["name"] == "receipt_id" and column["pk"] for column in columns):
                backup = store.root / "backups" / ("before-source-provenance-v2-" + uuid.uuid4().hex + ".sqlite3")
                backup.parent.mkdir(exist_ok=True)
                store.database.snapshot(backup, source=db)
                db.execute("BEGIN IMMEDIATE")
                db.execute("ALTER TABLE receipt_import_sources RENAME TO receipt_import_sources_old")
            db.execute("""CREATE TABLE IF NOT EXISTS receipt_import_sources (
                receipt_id TEXT NOT NULL REFERENCES receipts(id),
                source_sha256 TEXT NOT NULL, source_name TEXT NOT NULL,
                page INTEGER NOT NULL, page_count INTEGER NOT NULL,
                PRIMARY KEY(source_sha256,page)
            )""")
            if any(column["name"] == "receipt_id" and column["pk"] for column in columns):
                db.execute("INSERT INTO receipt_import_sources SELECT * FROM receipt_import_sources_old")
                db.execute("DROP TABLE receipt_import_sources_old")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_receipt_per_source_page ON receipt_import_sources(source_sha256,page)")
            db.execute("CREATE INDEX IF NOT EXISTS source_receipt_lookup ON receipt_import_sources(receipt_id)")

    def sources(self, receipt_id):
        with self.store.connect() as db:
            rows = db.execute("SELECT * FROM receipt_import_sources WHERE receipt_id=? ORDER BY rowid", (receipt_id,)).fetchall()
        return [{"name": row["source_name"], "sha256": row["source_sha256"],
                 "page": row["page"], "page_count": row["page_count"]} for row in rows]

    def source(self, receipt_id, digest=None):
        rows = self.sources(receipt_id)
        return next((row for row in rows if digest is None or row["sha256"] == digest), None)

    def source_path(self, digest):
        # Digests originate in SHA-256 calculations or our own SQLite snapshot.
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("元PDFの識別情報が正しくありません")
        return self.store.root / "imports" / digest / "source.pdf"

    def add(self, name, raw, mode="document"):
        """Yield saved rows; caller serializes imports and enqueues new rows."""
        if mode != "pages" or not raw.startswith(b"%PDF-"):
            extension = validate_file(raw)
            row, duplicate = self.store.add(name, extension, raw)
            yield row, duplicate
            return
        if not raw or len(raw) > config.MAX_BYTES:
            raise ValueError("1ファイル20MBまでのPDFを選んでください")
        import pypdfium2 as pdfium
        with PDF_LOCK:
            try:
                document = pdfium.PdfDocument(raw)
            except Exception as exc:
                raise ValueError("PDFを開けません。破損やパスワード設定を確認してください") from exc
        try:
            with PDF_LOCK, document:
                count = len(document)
                if count == 0:
                    raise ValueError("PDFにページが見つかりません。空のPDFでないか確認し、パスワード付きの場合は解除してから取り込んでください")
                if not 1 <= count <= config.MAX_SPLIT_PAGES:
                    raise ValueError(f"ページごとの取り込みは1〜{config.MAX_SPLIT_PAGES}ページのPDFに対応しています")
                digest = hashlib.sha256(raw).hexdigest()
                original = self.source_path(digest)
                original.parent.mkdir(parents=True, exist_ok=True)
                if original.exists():
                    if hashlib.sha256(self.store.keys.read_file(original)).hexdigest() != digest:
                        raise ValueError("保存済みの元PDFが変更されています。バックアップを確認してください")
                else:
                    self.store.keys.write_file(original, raw)
                for index in range(count):
                    with self.store.connect() as db:
                        existing = db.execute("SELECT receipt_id FROM receipt_import_sources WHERE source_sha256=? AND page=?", (digest, index + 1)).fetchone()
                    if existing:
                        yield self.store.get(existing["receipt_id"]), True
                        continue
                    page_file = original.parent / f"page-{index + 1:04d}.pdf"
                    if count == 1:
                        # A one-page document is the same receipt in either import mode.
                        page_raw = raw
                    elif not page_file.exists():
                        with pdfium.PdfDocument.new() as page:
                            page.import_pages(document, [index])
                            output = io.BytesIO()
                            page.save(output)
                        self.store.keys.write_file(page_file, output.getvalue())
                    if count > 1:
                        # Cached page bytes keep SHA-based duplicate detection stable on retry.
                        page_raw = self.store.keys.read_file(page_file)
                    validate_file(page_raw)
                    label = f"{Path(name).stem[:170]} — {index + 1}ページ.pdf"
                    row, duplicate = self.store.add(label, ".pdf", page_raw)
                    with self.store.connect() as db:
                        db.execute("INSERT OR IGNORE INTO receipt_import_sources VALUES(?,?,?,?,?)",
                                   (row["id"], digest, name, index + 1, count))
                    yield row, duplicate
        except pdfium.PdfiumError as exc:
            raise ValueError("PDFの一部を分割できません。破損やパスワード設定を確認し、別のPDFとして保存し直してください。取り込み済みのページは保持しています。") from exc
