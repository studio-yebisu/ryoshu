"""Mandatory SQLCipher connections and restartable migration from legacy SQLite.

The legacy filename remains a data-free version guard, so an old Ryoshu cannot
create a second empty ledger. Only this module opens plaintext SQLite guards.
"""
from contextlib import closing
import base64
import hashlib
import hmac
import json
from pathlib import Path
import shutil
import uuid

from filelock import FileLock
import sqlcipher3 as sqlite

from .protection import ProtectionError, atomic_write, _json

GUARD_VERSION = 99


def _quoted(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def fingerprint(connection):
    """Hash every persisted table and logical row without logging its contents."""
    result = {}
    tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    for (table,) in tables:
        if table == "sqlite_stat1" or table.startswith("sqlite_stat"):
            continue
        rows = []
        columns = connection.execute("PRAGMA table_info(" + _quoted(table) + ")").fetchall()
        for row in connection.execute("SELECT * FROM " + _quoted(table)):
            values = [{"blob": base64.b64encode(v).decode()} if isinstance(v, bytes) else v for v in row]
            rows.append(hashlib.sha256(json.dumps(values, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest())
        result[table] = {"columns": [list(c) for c in columns], "rows": len(rows),
                         "sha256": hashlib.sha256("".join(sorted(rows)).encode()).hexdigest()}
    return result


class CipherDatabase:
    def __init__(self, root, keys):
        self.root = Path(root).resolve()
        self.keys = keys
        self.path = self.root / "ledger.enc.sqlite3"
        self.legacy_path = self.root / "ledger.sqlite3"
        self.state_path = self.root / ".ryoshu-vault.json"
        if keys.mode != "unlocked":
            raise ProtectionError("帳簿がロックされています。保護を設定するか、復元用パスワードで開いてください。")
        with FileLock(str(self.root / ".ryoshu-database.lock"), timeout=15):
            self._prepare()

    def open(self, path=None, key=None, readonly=False):
        path = Path(path or self.path)
        if path.is_symlink():
            raise ProtectionError("帳簿ファイルがリンクになっています。保存先を確認し、バックアップから復元してください。")
        key = key if key is not None else self.keys.derive("database")
        if not isinstance(key, bytes) or len(key) != 32:
            raise ProtectionError("帳簿の鍵を導出できません。保護情報を確認してください。")
        location = path.resolve().as_uri() + "?mode=ro" if readonly else str(path)
        db = sqlite.connect(location, timeout=15, uri=readonly)
        try:
            db.execute('PRAGMA key = "x\'' + key.hex() + '\'"')
            if not db.execute("PRAGMA cipher_version").fetchone():
                raise ProtectionError("暗号化ライブラリを確認できません。初回セットアップを実行してください。")
            db.execute("PRAGMA cipher_memory_security=ON")
            db.execute("PRAGMA temp_store=MEMORY")
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA secure_delete=ON")
            db.execute("SELECT count(*) FROM sqlite_master").fetchone()
            db.row_factory = sqlite.Row
            return db
        except BaseException as exc:
            db.close()
            if isinstance(exc, sqlite.DatabaseError):
                raise ProtectionError("帳簿を開けません。鍵と帳簿ファイルが一致しないか、データが破損しています。保存先とバックアップを確認してください。") from exc
            raise

    def _state(self):
        if not self.state_path.exists():
            return None
        try:
            if self.state_path.is_symlink() or self.state_path.stat().st_size > 16 * 1024:
                raise ValueError("invalid state size")
            envelope = _json(self.state_path.read_bytes())
            if not isinstance(envelope, dict) or set(envelope) != {"state", "mac"}:
                raise ValueError("invalid state envelope")
            raw = json.dumps(envelope["state"], sort_keys=True, separators=(",", ":")).encode()
            expected = hmac.digest(self.keys.derive("state"), raw, "sha256").hex()
            if not isinstance(envelope["mac"], str) or not hmac.compare_digest(expected, envelope["mac"]):
                raise ValueError("state authentication failed")
            state = envelope["state"]
            if type(state["version"]) is not int or state["version"] != 1 or state["ledger_id"] != self.keys.ledger_id or state["phase"] not in {"creating", "exporting", "exported", "active"}:
                raise ValueError("invalid state")
            return state
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise ProtectionError("帳簿の移行情報を確認できません。空の帳簿を作らずに停止しました。バックアップを確認してください。") from exc

    def _save_state(self, phase, **details):
        state = {"version": 1, "ledger_id": self.keys.ledger_id, "phase": phase, **details}
        raw = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
        envelope = {"state": state, "mac": hmac.digest(self.keys.derive("state"), raw, "sha256").hex()}
        encoded = json.dumps(envelope, separators=(",", ":")).encode()
        if len(encoded) > 16 * 1024:
            raise ProtectionError("帳簿の保護情報が大きすぎます。保護情報を更新せずに停止しました。")
        atomic_write(self.state_path, encoded)

    def _legacy_version(self):
        if not self.legacy_path.exists():
            return None
        if self.legacy_path.is_symlink():
            raise ProtectionError("旧帳簿の保存先を確認してください。")
        try:
            # Read/write mode permits SQLite to roll back a hot journal after power loss.
            # mode=rw still refuses to create a missing replacement file.
            with closing(sqlite.connect(self.legacy_path.resolve().as_uri() + "?mode=rw", uri=True)) as db:
                return db.execute("PRAGMA user_version").fetchone()[0]
        except sqlite.DatabaseError as exc:
            raise ProtectionError("旧帳簿を開けません。保存先とバックアップを確認してください。") from exc

    def _prepare(self):
        state, version = self._state(), self._legacy_version()
        if version is not None and version < GUARD_VERSION:
            if (state and state["phase"] == "active") or (state is None and self.path.exists()):
                raise ProtectionError("旧帳簿と暗号化帳簿の両方があります。どちらも上書きせず停止しました。保存先とバックアップを確認してください。")
            self._migrate()
            return
        if version == GUARD_VERSION:
            if not state or state["phase"] not in {"active", "exported", "creating"} or not self.path.is_file():
                raise ProtectionError("暗号化帳簿を開けません。新しい空の帳簿は作らず停止しました。バックアップから復元してください。")
            with closing(self.open(readonly=True)) as db:
                if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ProtectionError("帳簿の整合性検査に失敗しました。バックアップから復元してください。")
            with closing(sqlite.connect(str(self.legacy_path))) as guard:
                identity = guard.execute("SELECT ledger_id FROM ryoshu_protected").fetchone()
                if not identity or identity[0] != self.keys.ledger_id:
                    raise ProtectionError("旧版向けの保護情報が帳簿と一致しません。")
                if guard.execute("PRAGMA freelist_count").fetchone()[0] or self.legacy_path.stat().st_size > 16384:
                    guard.execute("PRAGMA secure_delete=ON")
                    guard.execute("VACUUM")
            if state["phase"] == "exported":
                with self.path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                if digest != state.get("cipher_sha256"):
                    raise ProtectionError("移行した帳簿の検証値が違います。バックアップを確認してください。")
                self._save_state("active")
            elif state["phase"] == "creating":
                self._save_state("active")
            return
        if version is not None:
            raise ProtectionError("この帳簿は対応していない形式です。新しい版のRyoshuで開くか、対応するバックアップを選んでください。")
        if state and state["phase"] == "creating" and self.path.exists():
            with closing(self.open(readonly=True)) as db:
                if db.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] != 0:
                    raise ProtectionError("作成途中の帳簿を確認できません。上書きせずに停止しました。")
            with closing(sqlite.connect(str(self.legacy_path))) as guard:
                self._make_guard(guard)
            self._save_state("active")
            return
        if (state and state["phase"] != "creating") or self.path.exists():
            raise ProtectionError("帳簿の一部が見つかりません。新しい空の帳簿を作らずに停止しました。")
        if any((self.root / name).exists() for name in ["receipts", "imports"]):
            raise ProtectionError("原本はありますが帳簿が見つかりません。バックアップから復元してください。")
        self._save_state("creating")
        with closing(self.open()) as db:
            db.execute("PRAGMA user_version=0")
            db.commit()
        with closing(sqlite.connect(str(self.legacy_path))) as guard:
            self._make_guard(guard)
        self._save_state("active")

    def _make_guard(self, connection):
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("PRAGMA secure_delete=ON")
        connection.execute("BEGIN EXCLUSIVE")
        objects = connection.execute("SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY CASE type WHEN 'trigger' THEN 0 WHEN 'view' THEN 1 ELSE 2 END").fetchall()
        for kind, name in objects:
            if kind in {"table", "view", "trigger"}:
                connection.execute("DROP " + kind.upper() + " IF EXISTS " + _quoted(name))
        connection.execute("CREATE TABLE ryoshu_protected(format_version INTEGER NOT NULL, ledger_id TEXT NOT NULL)")
        connection.execute("INSERT INTO ryoshu_protected VALUES(1,?)", (self.keys.ledger_id,))
        connection.execute("PRAGMA user_version=" + str(GUARD_VERSION))
        connection.commit()
        # A legacy freelist can contain records deleted before secure_delete was set.
        connection.execute("VACUUM")

    def _migrate(self):
        self._save_state("exporting")
        pending = self.root / (".ryoshu-encrypted-" + uuid.uuid4().hex + ".sqlite3")
        source = sqlite.connect(str(self.legacy_path), timeout=5)
        try:
            source.execute("PRAGMA trusted_schema=OFF")
            source.execute("PRAGMA temp_store=MEMORY")
            if source.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] != 0:
                raise ProtectionError("旧版が帳簿を使用中です。旧版のRyoshuを終了してから移行してください。")
            if source.execute("PRAGMA journal_mode=DELETE").fetchone()[0].lower() != "delete":
                raise ProtectionError("旧帳簿への書き込みを止められません。旧版を終了してから再試行してください。")
            source.execute("PRAGMA locking_mode=EXCLUSIVE")
            source.execute("BEGIN EXCLUSIVE")
            version = source.execute("PRAGMA user_version").fetchone()[0]
            if version > 2:
                raise ProtectionError("新しい版の帳簿です。対応する版で開いてください。")
            if shutil.disk_usage(self.root).free < self.legacy_path.stat().st_size * 3 + 128 * 1024**2:
                raise ProtectionError("保護への移行に必要な空き容量がありません。空き容量を確認してください。")
            expected = fingerprint(source)
            self._check_legacy_originals(source)
            source.execute("ATTACH DATABASE ? AS encrypted KEY ?", (str(pending), "x'" + self.keys.derive("database").hex() + "'"))
            source.execute("SELECT sqlcipher_export('encrypted')")
            source.execute("PRAGMA encrypted.user_version=" + str(version))
            source.commit()
            source.execute("DETACH DATABASE encrypted")
            # EXCLUSIVE locking_mode keeps the original locked across this commit.
            with closing(self.open(pending, readonly=True)) as candidate:
                if fingerprint(candidate) != expected or candidate.execute("PRAGMA quick_check").fetchone()[0] != "ok" or candidate.execute("PRAGMA foreign_key_check").fetchone():
                    raise ProtectionError("移行前後の帳簿が一致しません。旧帳簿は変更していません。")
            pending.replace(self.path)
            backup_dir = self.root / "backups"
            backup_dir.mkdir(exist_ok=True)
            recovery = backup_dir / ("before-encryption-" + uuid.uuid4().hex + ".sqlite3")
            shutil.copyfile(self.path, recovery)
            with self.path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            self._save_state("exported", cipher_sha256=digest, recovery_file=recovery.name)
            self._make_guard(source)
            self._save_state("active")
        except sqlite.DatabaseError as exc:
            raise ProtectionError("旧帳簿の暗号化を完了できませんでした。旧版を終了し、保存先とバックアップを確認して再試行してください。") from exc
        finally:
            source.close()
            pending.unlink(missing_ok=True)

    def _check_legacy_originals(self, connection):
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='receipts'").fetchone():
            if (self.root / "receipts").exists() and any((self.root / "receipts").iterdir()):
                raise ProtectionError("原本はありますが旧帳簿の明細がありません。旧帳簿を変更せずに停止しました。")
            return
        for receipt_id,extension,digest in connection.execute("SELECT id,extension,sha256 FROM receipts"):
            path=self.root / "receipts" / receipt_id / ("original"+extension)
            if not path.resolve().is_relative_to(self.root) or path.is_symlink() or not path.is_file():
                raise ProtectionError("移行前の原本が見つかりません。旧帳簿は変更していません。保存先の原本を戻すか、バックアップから復元してください。")
            with path.open("rb") as raw:
                if hashlib.file_digest(raw,"sha256").hexdigest()!=digest:
                    raise ProtectionError("移行前の原本と帳簿のSHA-256が一致しません。旧帳簿は変更していません。原本とバックアップを確認してください。")

    def snapshot(self, destination, source=None, key=None):
        if Path(destination).exists():
            raise ProtectionError("バックアップ用の内部スナップショットが既に存在するため上書きしません。前の処理が残っていないか確認して再試行してください。")
        own_source = source is None
        source = source or self.open(readonly=True)
        try:
            if source.in_transaction:
                raise ProtectionError("帳簿の変更を保存中です。完了してからバックアップしてください。")
            with closing(self.open(destination, key=key)) as target:
                source.backup(target)
        finally:
            if own_source:
                source.close()

    def replace_from_plain_snapshot(self, path):
        """Restore a verified legacy DB from a private temporary workspace."""
        pending=self.root/(".ryoshu-restore-"+uuid.uuid4().hex+".sqlite3")
        try:
            with closing(sqlite.connect(Path(path).resolve().as_uri()+"?mode=rw",uri=True)) as source:
                source.execute("PRAGMA trusted_schema=OFF")
                source.execute("PRAGMA temp_store=MEMORY")
                expected=fingerprint(source)
                version=source.execute("PRAGMA user_version").fetchone()[0]
                source.execute("ATTACH DATABASE ? AS encrypted KEY ?",(str(pending),"x'"+self.keys.derive("database").hex()+"'"))
                source.execute("SELECT sqlcipher_export('encrypted')")
                source.execute("PRAGMA encrypted.user_version="+str(version))
                source.commit()
                source.execute("DETACH DATABASE encrypted")
            with closing(self.open(pending,readonly=True)) as candidate:
                if fingerprint(candidate)!=expected or candidate.execute("PRAGMA quick_check").fetchone()[0]!="ok" or candidate.execute("PRAGMA foreign_key_check").fetchone():
                    raise ProtectionError("復元後の帳簿がバックアップと一致しません。復元先は開かず、別のバックアップを確認してください。")
            pending.replace(self.path)
            self._save_state("active",files_version=1)
        finally:
            pending.unlink(missing_ok=True)

    def protect_legacy_files(self):
        """A restartable, one-time conversion; active reads never accept plaintext."""
        from .protection import FILE_MAGIC
        state = self._state()
        if state.get("files_version") == 1:
            return
        for name in ["receipts", "imports"]:
            parent = self.root / name
            if not parent.exists():
                continue
            for path in parent.rglob("*"):
                if path.is_symlink() or path.is_junction():
                    raise ProtectionError("原本の保存先が別の場所を参照しています。")
                if path.is_file():
                    if path.name.startswith(".ryoshu-write-"):
                        # A crashed atomic replacement is bound to its intended name,
                        # not this temporary name. The original remains authoritative.
                        path.unlink()
                        continue
                    with path.open("rb") as source:
                        protected = source.read(len(FILE_MAGIC)) == FILE_MAGIC
                    if protected:
                        self.keys.read_file(path)  # Validate a previously completed conversion.
                    else:
                        self.keys.write_file(path, path.read_bytes())
        backups = self.root / "backups"
        for path in backups.glob("before-*.sqlite3") if backups.exists() else []:
            with path.open("rb") as source:
                plain = source.read(16) == b"SQLite format 3\0"
            if not plain:
                with closing(self.open(path, readonly=True)) as db:
                    if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise ProtectionError("移行前のバックアップを確認できません。")
                continue
            pending = path.with_name(".ryoshu-backup-" + uuid.uuid4().hex + ".sqlite3")
            with closing(sqlite.connect(str(path))) as old:
                expected = fingerprint(old)
                old.execute("ATTACH DATABASE ? AS encrypted KEY ?", (str(pending), "x'" + self.keys.derive("database").hex() + "'"))
                old.execute("SELECT sqlcipher_export('encrypted')")
                old.execute("PRAGMA encrypted.user_version=" + str(old.execute("PRAGMA main.user_version").fetchone()[0]))
                old.commit()
                old.execute("DETACH DATABASE encrypted")
            with closing(self.open(pending, readonly=True)) as candidate:
                if fingerprint(candidate) != expected:
                    raise ProtectionError("暗号化後のバックアップが元の帳簿と一致しません。元のファイルは保持しています。")
            pending.replace(path)
        with closing(self.open(readonly=True)) as db:
            rows = db.execute("SELECT id,extension,sha256 FROM receipts") if db.execute("SELECT 1 FROM sqlite_master WHERE name='receipts'").fetchone() else []
            for row in rows:
                original = self.root / "receipts" / row["id"] / ("original" + row["extension"])
                if hashlib.sha256(self.keys.read_file(original)).hexdigest() != row["sha256"]:
                    raise ProtectionError("原本のSHA-256が帳簿と一致しません。原本を修復するか、バックアップから復元してください。")
        self._save_state("active", files_version=1)
