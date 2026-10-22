"""Windows-managed ledger keys and authenticated encryption of stored files.

Recovery passwords and raw keys never enter the ledger, browser storage or logs.
The data directory is portable only with its recovery password or an encrypted backup.
"""
import base64
import ctypes
import json
import os
from pathlib import Path
import secrets
import tempfile
import uuid
import time
from contextlib import contextmanager
import shutil

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from filelock import FileLock, Timeout

KEY_MAGIC = b"RYOSHU-KEY\x01"
FILE_MAGIC = b"RYOSHU-FILE\x01"
MAX_KEY_FILE = 16 * 1024
MAX_STORED_FILE = 64 * 1024 * 1024


class ProtectionError(ValueError):
    """A user-safe diagnostic without cryptographic material."""


def private_directory(path, protect_children=False):
    """Protect our directory with an inheritable, current-user Windows DACL."""
    path = Path(path)
    if path.resolve()==Path(path.resolve().anchor):
        raise ProtectionError("ドライブの直下ではなく、帳簿専用のフォルダーを選んでください。")
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt" or path.is_symlink() or path.is_junction():
        raise ProtectionError("帳簿の保存先を保護できません。Windowsの通常のフォルダーを選んでください。")
    from ctypes import wintypes
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    token = wintypes.HANDLE()
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise ProtectionError("Windowsの利用者情報を確認できません。")
    size = wintypes.DWORD()
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    try:
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise ProtectionError("Windowsの利用者情報を確認できません。")
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        text = wintypes.LPWSTR()
        advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise ProtectionError("Windowsの利用者情報を確認できません。")
        try:
            sddl = "D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;" + text.value + ")"
        finally:
            kernel.LocalFree(text)
        descriptor = ctypes.c_void_p()
        advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
        advapi.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
        if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
            raise ProtectionError("保存先のアクセス権を設定できません。")
        try:
            if not advapi.SetFileSecurityW(str(path), 0x80000004, descriptor):
                raise ProtectionError("保存先のアクセス権を設定できません。書き込み権限を確認してください。")
            if protect_children:
                owned={"ledger.sqlite3","ledger.enc.sqlite3","receipts","imports","backups"}
                pending=[item for item in path.iterdir() if item.name in owned or item.name.startswith(".ryoshu-") or item.name.startswith("ledger.sqlite3-") or item.name.startswith("ledger.enc.sqlite3-")]
                while pending:
                    item=pending.pop()
                    if item.is_symlink() or item.is_junction() or (item.is_file() and item.stat().st_nlink>1):
                        raise ProtectionError("帳簿の中に別の保存先への参照があります。保存先を確認してください。")
                    if not advapi.SetFileSecurityW(str(item),0x80000004,descriptor):
                        raise ProtectionError("保存済みの帳簿のアクセス権を設定できません。")
                    if item.is_dir():pending.extend(item.iterdir())
        finally:
            kernel.LocalFree(descriptor)
    finally:
        kernel.CloseHandle(token)
    return path


@contextmanager
def private_work(root):
    """Indra requires paths; plaintext exists only during one OCR operation."""
    parent = private_directory(Path(root) / ".ryoshu-work")
    folder=Path(tempfile.mkdtemp(prefix="ocr-",dir=parent))
    if not folder.resolve().is_relative_to(parent.resolve()):
        raise ProtectionError("作業用の保存先を確認できません。")
    lease=FileLock(str(folder/".lease"),timeout=0)
    lease.acquire()
    try:
        yield folder
    finally:
        lease.release()
        for attempt in range(5):
            try:
                if folder.is_symlink() or folder.is_junction() or not folder.resolve().is_relative_to(parent.resolve()):
                    raise ProtectionError("作業用の保存先が変更されました。")
                shutil.rmtree(folder)
                break
            except PermissionError:
                if attempt==4:
                    raise ProtectionError("作業用ファイルの片付けに失敗しました。Ryoshuを終了し、起動し直してください。") from None
                time.sleep(.1*(attempt+1))


def clean_work(root):
    parent = Path(root) / ".ryoshu-work"
    if parent.is_symlink() or parent.is_junction():
        raise ProtectionError("作業用の保存先を確認してください。")
    for item in parent.iterdir() if parent.exists() else []:
        if item.name.startswith("ocr-") and item.is_dir() and not item.is_symlink() and not item.is_junction() and item.resolve().is_relative_to(parent.resolve()):
            try:
                with FileLock(str(item/".lease"),timeout=0):pass
            except Timeout:
                continue
            shutil.rmtree(item)
    backups=Path(root)/".ryoshu-backups"
    if backups.exists():
        if backups.is_symlink() or backups.is_junction():
            raise ProtectionError("バックアップ作業用の保存先を確認してください。")
        for item in backups.iterdir():
            if item.name.startswith("backup-") and item.is_dir() and not item.is_symlink() and not item.is_junction() and item.resolve().is_relative_to(backups.resolve()):
                shutil.rmtree(item)


def clean_incomplete_restores(root):
    root=Path(root).resolve()
    if not root.is_dir() or root.is_symlink() or root.is_junction():return
    for folder in root.iterdir():
        if folder.is_dir() and not folder.is_symlink() and not folder.is_junction() and folder.resolve().is_relative_to(root) and any((folder/name).is_file() for name in ["RESTORE_IN_PROGRESS.txt","RESTORE_FAILED.txt"]):
            clean_work(folder/"data")


def _b64(raw):
    return base64.b64encode(raw).decode("ascii")


def _unb64(value, size=None):
    if not isinstance(value, str) or len(value) > MAX_KEY_FILE:
        raise ValueError("invalid encoding")
    raw = base64.b64decode(value, validate=True)
    if size is not None and len(raw) != size:
        raise ValueError("invalid size")
    return raw


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate field")
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=unique)
    except RecursionError as exc:
        raise ValueError("保護情報の階層が深すぎます。") from exc


def atomic_write(path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".ryoshu-write-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def dpapi(raw, decrypt=False):
    if os.name != "nt":
        raise ProtectionError("この版の帳簿保護はWindows専用です。未対応の環境で平文保存へ切り替えることはありません。")
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    if decrypt:
        call = crypt.CryptUnprotectData
        call.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        okay = call(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        call = crypt.CryptProtectData
        call.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        okay = call(ctypes.byref(source), "Ryoshu local ledger", None, None, None, 1, ctypes.byref(target))
    if not okay:
        raise ProtectionError("このWindowsアカウントでは帳簿の鍵を開けません。復元用パスワード、またはバックアップから復元してください。")
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


def check_password(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 256 or not password.strip() or any(ord(c)<32 or ord(c)==127 for c in password):
        raise ProtectionError("復元用パスワードは12〜256文字で設定してください。")


def wrap_secret(secret, password, label):
    check_password(password)
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    key = Scrypt(salt=salt, length=32, n=2**17, r=8, p=1).derive(password.encode("utf-8"))
    return {"version": 1, "salt": _b64(salt), "nonce": _b64(nonce),
            "ciphertext": _b64(AESGCM(key).encrypt(nonce, secret, label))}


def unwrap_secret(envelope, password, label):
    check_password(password)
    try:
        if not isinstance(envelope, dict) or set(envelope) != {"version", "salt", "nonce", "ciphertext"} or type(envelope["version"]) is not int or envelope["version"] != 1:
            raise ValueError("invalid version")
        salt, nonce = _unb64(envelope["salt"], 16), _unb64(envelope["nonce"], 12)
        ciphertext = _unb64(envelope["ciphertext"])
        if not 16 <= len(ciphertext) <= 4096:
            raise ValueError("invalid payload")
        key = Scrypt(salt=salt, length=32, n=2**17, r=8, p=1).derive(password.encode("utf-8"))
        return AESGCM(key).decrypt(nonce, ciphertext, label)
    except (ValueError, TypeError, KeyError, InvalidTag) as exc:
        raise ProtectionError("復元用パスワードが違うか、保護情報が破損しています。帳簿は変更していません。") from exc


class LedgerKeys:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.key_path = self.root / ".ryoshu-key.dpapi"
        self.recovery_path = self.root / ".ryoshu-recovery.json"
        self.master = None
        self.ledger_id = None
        self.mode = "new"
        private_directory(self.root,protect_children=True)
        with self.lock():
            self._load()

    def lock(self):
        return FileLock(str(self.root / ".ryoshu-key.lock"), timeout=15)

    def _read_small(self, path):
        if path.is_symlink() or path.stat().st_size > MAX_KEY_FILE:
            raise ProtectionError("帳簿の保護情報を確認できません。バックアップと保存先を確認してください。")
        with path.open("rb") as source:
            return source.read(MAX_KEY_FILE + 1)

    def _load(self):
        if not self.key_path.exists():
            if self.recovery_path.exists() or (self.root / "ledger.enc.sqlite3").exists() or (self.root / ".ryoshu-vault.json").exists() or self._has_protected_guard():
                self.mode = "locked"
            return
        try:
            raw = self._read_small(self.key_path)
            if not raw.startswith(KEY_MAGIC):
                raise ValueError("invalid protected file")
            data = _json(dpapi(raw[len(KEY_MAGIC):], decrypt=True))
            if not isinstance(data, dict) or set(data) != {"version", "ledger_id", "master"} or type(data["version"]) is not int or data["version"] != 1:
                raise ValueError("invalid protected payload")
            ledger_id = data["ledger_id"]
            if not isinstance(ledger_id, str) or uuid.UUID(hex=ledger_id).hex != ledger_id:
                raise ValueError("invalid ledger id")
            self.master, self.ledger_id = _unb64(data["master"], 32), ledger_id
            self.mode = "unlocked"
        except (OSError, ValueError, TypeError, KeyError, ProtectionError):
            self.master, self.ledger_id, self.mode = None, None, "locked"

    def _has_protected_guard(self):
        """Read only the SQLite header; do not create a key for a known protected book."""
        path = self.root / "ledger.sqlite3"
        if not path.exists():
            return False
        try:
            with path.open("rb") as source:
                header = source.read(100)
            return header.startswith(b"SQLite format 3\0") and len(header) == 100 and int.from_bytes(header[60:64], "big") == 99
        except OSError as exc:
            raise ProtectionError("旧帳簿の保護情報を確認できません。保存先とバックアップを確認してください。") from exc

    def _persist_windows_key(self):
        package = json.dumps({"version": 1, "ledger_id": self.ledger_id, "master": _b64(self.master)}, separators=(",", ":")).encode()
        atomic_write(self.key_path, KEY_MAGIC + dpapi(package))

    def setup(self, password):
        check_password(password)
        with self.lock():
            if self.mode != "new" or self.key_path.exists() or self.recovery_path.exists() or (self.root / "ledger.enc.sqlite3").exists() or (self.root / ".ryoshu-vault.json").exists() or self._has_protected_guard():
                raise ProtectionError("既存の帳簿に新しい鍵を上書きできません。復元用パスワードで開いてください。")
            self.master, self.ledger_id = secrets.token_bytes(32), uuid.uuid4().hex
            label = b"Ryoshu recovery v1\0" + self.ledger_id.encode()
            envelope = wrap_secret(self.master, password, label)
            if unwrap_secret(envelope, password, label) != self.master:
                raise ProtectionError("保護情報の確認に失敗しました。帳簿は変更していません。")
            recovery = {"ledger_id": self.ledger_id, "envelope": envelope}
            atomic_write(self.recovery_path, json.dumps(recovery, separators=(",", ":")).encode())
            self._persist_windows_key()
            self.mode = "unlocked"

    def recover(self, password, validate):
        with self.lock():
            try:
                data = _json(self._read_small(self.recovery_path))
                if set(data) != {"ledger_id", "envelope"} or uuid.UUID(hex=data["ledger_id"]).hex != data["ledger_id"]:
                    raise ValueError("invalid recovery information")
                identity = data["ledger_id"]
                master = unwrap_secret(data["envelope"], password, b"Ryoshu recovery v1\0" + identity.encode())
                if len(master) != 32:
                    raise ValueError("invalid recovered key")
            except (OSError, ValueError, TypeError, KeyError) as exc:
                raise ProtectionError("復元用パスワードか保護情報を確認できません。帳簿は変更していません。") from exc
            validate(master, identity)
            self.master, self.ledger_id = master, identity
            self._persist_windows_key()
            self.mode = "unlocked"

    def verify_password(self, password):
        data = _json(self._read_small(self.recovery_path))
        if data.get("ledger_id") != self.ledger_id:
            raise ProtectionError("復元情報と帳簿が一致しません。")
        candidate = unwrap_secret(data.get("envelope"), password,
                                  b"Ryoshu recovery v1\0" + self.ledger_id.encode())
        if not secrets.compare_digest(candidate, self.master):
            raise ProtectionError("復元情報と帳簿が一致しません。")

    def reset_password(self,password):
        check_password(password)
        self.derive("state")  # An unlocked Windows-managed key is required.
        with self.lock():
            label=b"Ryoshu recovery v1\0"+self.ledger_id.encode()
            envelope=wrap_secret(self.master,password,label)
            if unwrap_secret(envelope,password,label)!=self.master:
                raise ProtectionError("新しい復元情報を確認できません。")
            atomic_write(self.recovery_path,json.dumps({"ledger_id":self.ledger_id,"envelope":envelope},separators=(",", ":")).encode())

    def derive(self, purpose):
        if self.mode != "unlocked" or self.master is None:
            raise ProtectionError("帳簿の保護を設定するか、復元用パスワードで開いてください。")
        return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                    info=("Ryoshu " + purpose + " v1").encode()).derive(self.master)

    def _aad(self, path):
        if self.mode != "unlocked" or not self.ledger_id:
            raise ProtectionError("帳簿の保護を設定するか、復元用パスワードで開いてください。")
        path = Path(path)
        if path.is_symlink():
            raise ProtectionError("帳簿の外にあるファイルは開けません。")
        try:
            relative = path.resolve().relative_to(self.root).as_posix()
        except ValueError as exc:
            raise ProtectionError("帳簿の外にあるファイルは開けません。") from exc
        return FILE_MAGIC + self.ledger_id.encode() + b"\0" + relative.encode("utf-8")

    def write_file(self, path, content):
        if len(content) > MAX_STORED_FILE:
            raise ProtectionError("保存するファイルが大きすぎます。")
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self.derive("files")).encrypt(nonce, content, self._aad(path))
        atomic_write(path, FILE_MAGIC + nonce + ciphertext)

    def read_file(self, path):
        path = Path(path)
        aad = self._aad(path)
        if not path.is_file():
            raise ProtectionError("領収書の原本が見つかりません。保存先と以前のバックアップを確認してください。")
        if path.stat().st_size > MAX_STORED_FILE + len(FILE_MAGIC) + 28:
            raise ProtectionError("保存されたファイルが大きすぎます。")
        with path.open("rb") as source:
            raw = source.read(MAX_STORED_FILE + len(FILE_MAGIC) + 29)
        if not raw.startswith(FILE_MAGIC) or len(raw) < len(FILE_MAGIC) + 28:
            raise ProtectionError("暗号化された原本を確認できません。バックアップを確認してください。")
        start = len(FILE_MAGIC)
        try:
            return AESGCM(self.derive("files")).decrypt(raw[start:start + 12], raw[start + 12:], aad)
        except InvalidTag as exc:
            raise ProtectionError("原本が変更されたか、帳簿の鍵が違います。帳簿は変更していません。") from exc
