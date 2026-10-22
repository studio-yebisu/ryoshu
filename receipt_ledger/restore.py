"""Validate and restore our backup into a new directory; never overwrite a ledger."""
from contextlib import closing
import json
import hashlib
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import zipfile

from .protection import LedgerKeys, private_work
from .database import CipherDatabase

_receipt = re.compile(r"data/receipts/[0-9a-f]{32}/original\.(pdf|jpg|png|webp)")
_source = re.compile(r"data/imports/[0-9a-f]{64}/source\.pdf")


def restore_backup(archive, destination, password=None):
    from .backups import MAGIC
    from .protection import check_password
    from .restore_encrypted import restore_encrypted
    with Path(archive).open("rb") as source:
        encrypted = source.read(len(MAGIC)) == MAGIC
    if encrypted:
        check_password(password)
        return restore_encrypted(archive,destination,password,_receipt,_source)
    return _restore_legacy(archive,destination,password)


def _restore_legacy(archive, destination, password):
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError("復元先がすでに存在します。元の帳簿を守るため、新しい保存先を選んでください")
    with zipfile.ZipFile(archive) as bundle:
        from .backups import MAX_ENTRIES
        if len(bundle.infolist())>MAX_ENTRIES:
            raise ValueError("バックアップのファイル数が上限を超えています")
        names = set()
        for item in bundle.infolist():
            name = item.filename
            if item.compress_type not in {zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED} or item.flag_bits & 1:
                raise ValueError("対応していないZIPの圧縮・暗号化方式です。旧版Ryoshuで保存したZIPを選んでください")
            if name in names or "\\" in name or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts or (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("バックアップに安全でないファイル名が含まれています")
            if name not in {"manifest.json", "data/ledger.sqlite3"} and not _receipt.fullmatch(name) and not _source.fullmatch(name):
                raise ValueError("Ryoshuのバックアップ以外のファイルが含まれています")
            names.add(name)
        if not {"manifest.json", "data/ledger.sqlite3"} <= names:
            raise ValueError("帳簿データが見つかりません。Ryoshuで保存したバックアップZIPを選んでください")
        total = sum(item.file_size for item in bundle.infolist())
        if total > 20 * 1024 ** 3:
            raise ValueError("この復元機能は展開後20GBまでに対応しています")
        ancestor = destination.parent
        while not ancestor.exists():
            ancestor = ancestor.parent
        if shutil.disk_usage(ancestor).free < total + 128 * 1024 ** 2:
            raise ValueError("復元先の空き容量が足りません。空き容量を確認してください")
        if bundle.getinfo("manifest.json").file_size > 1024 * 1024:
            raise ValueError("バックアップの情報ファイルが大きすぎます")
        manifest = json.loads(bundle.read("manifest.json"))
        if manifest.get("app") not in {"Ryoshu", "ReceiptLedger"}:
            raise ValueError("Ryoshuのバックアップではありません")
        from .protection import private_directory, check_password
        check_password(password)
        created=False
        work_context=None
        work=None
        try:
            try:
                destination.mkdir(parents=True,exist_ok=False)
            except FileExistsError as exc:
                raise ValueError("復元先がすでに存在します。元の帳簿を守るため、新しい保存先を選んでください") from exc
            created=True
            private_directory(destination)
            (destination / "RESTORE_IN_PROGRESS.txt").write_text("復元処理中です。この保存先では起動しないでください。\n","utf-8")
            keys=LedgerKeys(destination / "data")
            keys.setup(password)
            cipher=CipherDatabase(destination / "data",keys)
            work_context=private_work(destination / "data")
            work=work_context.__enter__()
            database=work/"ledger.sqlite3"
            for item in bundle.infolist():
                if item.filename=="manifest.json":continue
                with bundle.open(item) as source:
                    if item.filename=="data/ledger.sqlite3":
                        with database.open("xb") as output:
                            copied=0
                            while chunk:=source.read(1024*1024):
                                copied+=len(chunk)
                                if copied>item.file_size:raise ValueError("展開したファイルの大きさが一致しません")
                                output.write(chunk)
                            if copied!=item.file_size:raise ValueError("帳簿の大きさが一致しません")
                    else:
                        if item.file_size>20*1024**2:raise ValueError("原本は1ファイル20MBまでに対応しています")
                        raw=source.read(item.file_size+1)
                        if len(raw)!=item.file_size:raise ValueError("原本の大きさが一致しません")
                        keys.write_file(destination/item.filename,raw)
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
                if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("帳簿データが破損しています。別のバックアップを選んでください")
                if db.execute("PRAGMA foreign_key_check").fetchone():
                    raise ValueError("帳簿内の参照に不整合があります。別のバックアップを選んでください")
                if db.execute("PRAGMA user_version").fetchone()[0] > 2:
                    raise ValueError("新しい版の帳簿です。Ryoshuを更新してから復元してください")
                receipts = db.execute("SELECT id,extension,sha256 FROM receipts").fetchall()
                if manifest.get("receipts") != len(receipts):
                    raise ValueError("帳簿とバックアップの件数が一致しません")
                expected = {"manifest.json", "data/ledger.sqlite3"}
                for receipt_id, extension, digest in receipts:
                    name = f"data/receipts/{receipt_id}/original{extension}"
                    if not _receipt.fullmatch(name) or name not in names or hashlib.sha256(keys.read_file(destination/name)).hexdigest() != digest:
                        raise ValueError("領収書原本の検証に失敗しました。別のバックアップを選んでください")
                    expected.add(name)
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='receipt_import_sources'").fetchone():
                    for (digest,) in db.execute("SELECT DISTINCT source_sha256 FROM receipt_import_sources"):
                        name = f"data/imports/{digest}/source.pdf"
                        if not _source.fullmatch(name) or name not in names or hashlib.sha256(keys.read_file(destination/name)).hexdigest() != digest:
                            raise ValueError("分割前の元PDFの検証に失敗しました")
                        expected.add(name)
                if names != expected:
                    raise ValueError("帳簿に対応しないファイルが含まれています")
            cipher.replace_from_plain_snapshot(database)
            # Cleanup is part of successful legacy adoption: no plaintext DB may remain.
            # Clear the handle first so a failed cleanup is not retried in finally,
            # masking its original exception or removing the failure marker.
            work=None
            work_context.__exit__(None,None,None)
            CipherDatabase(destination / "data",LedgerKeys(destination / "data"))
            (destination / "RESTORE_IN_PROGRESS.txt").unlink()
            return {"restored_data": str(destination / "data"), "receipts": len(receipts)}
        except BaseException:
            # Keep the failed attempt for diagnosis; it is never selected as the active ledger.
            if created:
                try:
                    (destination / "RESTORE_FAILED.txt").write_text("復元は完了していません。この保存先では起動しないでください。元の帳簿は変更していません。\n", "utf-8")
                except OSError:
                    pass
            raise
        finally:
            if work is not None:
                # A failed restore already has its failure marker. Preserve the
                # original diagnostic if cleanup also fails; startup can retry it.
                try:
                    work_context.__exit__(None,None,None)
                except Exception:
                    pass
