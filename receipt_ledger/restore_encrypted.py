"""Bounded streaming restore. No archive extraction or plaintext DB snapshots."""
import base64
from contextlib import closing
import hashlib
from pathlib import Path
import shutil
import tarfile

from .backups import MAX_ARCHIVE, MAX_ENTRIES, read_encrypted
from .database import CipherDatabase, fingerprint
from .protection import LedgerKeys, ProtectionError, private_directory, _json


class StrictTarInfo(tarfile.TarInfo):
    def _proc_member(self, archive):
        # Reject PAX/GNU long names and sparse headers before tarfile reads their bodies.
        if self.type not in {tarfile.REGTYPE,tarfile.AREGTYPE} or self.size < 0 or self.size > MAX_ARCHIVE:
            raise ValueError("対応していないバックアップのレコードです。")
        return self._proc_builtin(archive)


def restore_encrypted(archive, destination, password, receipt_pattern, source_pattern):
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError("復元先がすでに存在します。元の帳簿を守るため、新しい保存先を選んでください。")
    marker = destination / "RESTORE_IN_PROGRESS.txt"
    created = False

    def consume(plain):
        nonlocal created
        try:
            destination.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise ValueError("復元先がすでに存在します。元の帳簿を守るため、新しい保存先を選んでください。") from exc
        created = True
        private_directory(destination)
        marker.write_text("復元処理中です。この保存先では起動しないでください。\n", "utf-8")
        root = destination / "data"
        keys = LedgerKeys(root)
        keys.setup(password)
        database = CipherDatabase(root, keys)
        seen, expected = set(), None
        total = 0
        manifest = None
        pending = root / ".ryoshu-restore.sqlite3"
        backup_key = None
        with tarfile.open(fileobj=plain,mode="r|",tarinfo=StrictTarInfo) as bundle:
            for member in bundle:
                name = member.name
                if not member.isreg() or name in seen or len(seen) >= MAX_ENTRIES or member.size < 0:
                    raise ValueError("バックアップのファイル情報が正しくありません。")
                if name not in {"manifest.json","data/ledger.enc.sqlite3"} and not receipt_pattern.fullmatch(name) and not source_pattern.fullmatch(name):
                    raise ValueError("バックアップに安全でないファイル名が含まれています。")
                if member.pax_headers:
                    raise ValueError("対応していないバックアップの形式です。")
                if not seen and name != "manifest.json" or len(seen) == 1 and name != "data/ledger.enc.sqlite3":
                    raise ValueError("バックアップ内の帳簿情報の順番が正しくありません。")
                seen.add(name)
                total += member.size
                if total > MAX_ARCHIVE:
                    raise ValueError("この復元機能は展開後20GBまでに対応しています。")
                if shutil.disk_usage(destination).free < member.size * 2 + 128 * 1024**2:
                    raise ValueError("復元先の空き容量が足りません。")
                source = bundle.extractfile(member)
                if source is None:
                    raise ValueError("バックアップのファイルを開けません。")
                with source:
                    if name == "manifest.json":
                        if member.size > 1024**2:
                            raise ValueError("バックアップの情報ファイルが大きすぎます。")
                        manifest = _json(source.read())
                        if not isinstance(manifest,dict) or set(manifest) != {"app","format","version","created_at","receipts","database_key","missing_files"} or manifest["app"] != "Ryoshu" or type(manifest["format"]) is not int or manifest["format"] != 1 or type(manifest["receipts"]) is not int or not 0 <= manifest["receipts"] <= MAX_ENTRIES:
                            raise ValueError("Ryoshuのバックアップ情報ではありません。")
                        missing=manifest["missing_files"]
                        if not isinstance(missing,list) or len(missing)>MAX_ENTRIES or any(not isinstance(n,str) or not (receipt_pattern.fullmatch(n) or source_pattern.fullmatch(n)) for n in missing) or len(set(missing))!=len(missing):
                            raise ValueError("不足している原本の情報が正しくありません。")
                        if not isinstance(manifest["database_key"],str) or len(manifest["database_key"])>64:
                            raise ValueError("バックアップの鍵の形式が正しくありません。")
                        backup_key = base64.b64decode(manifest["database_key"],validate=True)
                        if len(backup_key) != 32:
                            raise ValueError("バックアップの鍵を確認できません。")
                    elif name == "data/ledger.enc.sqlite3":
                        if member.size < 4096:
                            raise ValueError("帳簿データが正しくありません。")
                        with pending.open("xb") as target:
                            shutil.copyfileobj(source,target,1024**2)
                        with closing(database.open(pending,key=backup_key,readonly=True)) as db:
                            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchone() or db.execute("PRAGMA user_version").fetchone()[0] > 3:
                                raise ValueError("帳簿の整合性または対応する版を確認できません。")
                            receipts = db.execute("SELECT id,extension,sha256 FROM receipts").fetchall()
                            if len(receipts) != manifest["receipts"]:
                                raise ValueError("バックアップの件数が一致しません。")
                            expected = {"manifest.json":None,"data/ledger.enc.sqlite3":None}
                            for receipt_id,extension,digest in receipts:
                                entry = f"data/receipts/{receipt_id}/original{extension}"
                                if not receipt_pattern.fullmatch(entry) or not isinstance(digest,str) or len(digest) != 64:
                                    raise ValueError("原本の情報が正しくありません。")
                                expected[entry] = digest
                            if db.execute("SELECT 1 FROM sqlite_master WHERE name='receipt_import_sources'").fetchone():
                                for (digest,) in db.execute("SELECT DISTINCT source_sha256 FROM receipt_import_sources"):
                                    entry = f"data/imports/{digest}/source.pdf"
                                    if not source_pattern.fullmatch(entry):
                                        raise ValueError("元PDFの情報が正しくありません。")
                                    expected[entry] = digest
                            for entry in manifest["missing_files"]:
                                if entry not in expected:raise ValueError("帳簿に対応しない不足原本が指定されています。")
                                del expected[entry]
                    else:
                        if expected is None or name not in expected or member.size > 20 * 1024**2:
                            raise ValueError("帳簿に対応しない原本が含まれています。")
                        raw = source.read(member.size + 1)
                        digest = hashlib.sha256(raw).hexdigest()
                        if len(raw) != member.size or digest != expected[name]:
                            raise ValueError("原本の検証に失敗しました。別のバックアップを選んでください。")
                        keys.write_file(destination / name,raw)
            if any(bundle.fileobj.buf):
                raise ValueError("バックアップの末尾に不明なデータがあります。")
        if expected is None or seen != set(expected):
            raise ValueError("バックアップに必要な帳簿・原本がそろっていません。")
        return keys, database, pending, backup_key, manifest

    try:
        with Path(archive).open("rb") as source:
            keys, database, pending, backup_key, manifest = read_encrypted(source,password,consume,zero_tail=True)
        # Only after the entire AES-GCM stream was authenticated do we create the new key's DB.
        rekeyed = database.root / ".ryoshu-rekeyed.sqlite3"
        with closing(database.open(pending,key=backup_key,readonly=True)) as source:
            before = fingerprint(source)
            database.snapshot(rekeyed,source=source)
        with closing(database.open(rekeyed,readonly=True)) as check:
            if fingerprint(check) != before or check.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("復元前後の帳簿が一致しません。")
        rekeyed.replace(database.path)
        pending.unlink()
        database._save_state("active",files_version=1,missing_file_count=len(manifest["missing_files"]))
        # Reopen through fresh key/state loaders before declaring the new ledger usable.
        CipherDatabase(database.root, LedgerKeys(database.root))
        marker.unlink()
        return {"restored_data":str(destination / "data"),"receipts":manifest["receipts"],"missing_files":len(manifest["missing_files"])}
    except BaseException:
        if created:
            try:
                (destination / "RESTORE_FAILED.txt").write_text("復元は完了していません。この保存先では起動しないでください。元の帳簿は変更していません。\n","utf-8")
            except OSError:
                pass
        raise
