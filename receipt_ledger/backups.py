"""Password-protected AES-256-GCM stream; no plaintext archive is written to disk."""
from contextlib import closing
import base64
import hashlib
import io
import json
from pathlib import Path
import secrets
import struct
import tarfile
import tempfile

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from . import __version__
from .protection import ProtectionError, private_directory, wrap_secret, unwrap_secret, _json
from .store import now

MAGIC = b"RYOSHU-BACKUP\x01"
MAX_ARCHIVE = 20 * 1024**3
MAX_ENTRIES = 100_000
STREAM_LIMIT=MAX_ARCHIVE+MAX_ENTRIES*1024+10240


def file_sha(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def write_encrypted(output, password, produce):
    """Standard AES-256-GCM with a random key and authenticated bounded header."""
    key,nonce=secrets.token_bytes(32),secrets.token_bytes(12)
    payload=json.dumps({"version":1,"key":wrap_secret(key+nonce,password,MAGIC)},separators=(",", ":")).encode()
    header=MAGIC+struct.pack(">I",len(payload))+payload
    output.write(header)
    encryptor=Cipher(algorithms.AES(key),modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(header)

    class EncryptedWriter:
        def __init__(self):self.total=0
        def write(self,raw):
            self.total+=len(raw)
            if self.total > STREAM_LIMIT:
                raise ValueError("バックアップは展開後20GBまでに対応しています。")
            output.write(encryptor.update(raw))
            return len(raw)
        def flush(self):output.flush()

    produce(EncryptedWriter())
    output.write(encryptor.finalize())
    output.write(encryptor.tag)


def read_header(source,password):
    if source.read(len(MAGIC)) != MAGIC:
        raise ProtectionError("Ryoshuの暗号化バックアップではありません。")
    size=source.read(4)
    if len(size)!=4 or not 1 <= struct.unpack(">I",size)[0] <= 4096:
        raise ProtectionError("バックアップの保護情報が正しくありません。")
    payload=source.read(struct.unpack(">I",size)[0])
    try:
        header=_json(payload)
        if not isinstance(header,dict) or set(header)!={"version","key"} or type(header["version"]) is not int or header["version"]!=1:
            raise ValueError()
        secret=unwrap_secret(header["key"],password,MAGIC)
        if len(secret)!=44:raise ValueError()
        return secret[:32],secret[32:],MAGIC+size+payload
    except (ValueError,TypeError,KeyError) as exc:
        raise ProtectionError("復元用パスワードが違うか、バックアップが破損しています。") from exc


def read_encrypted(source,password,consume,zero_tail=False):
    key,nonce,header=read_header(source,password)
    start=source.tell()
    source.seek(0,2)
    size=source.tell()-start-16
    if not 0 <= size <= STREAM_LIMIT:
        raise ProtectionError("バックアップのサイズが正しくありません。")
    source.seek(-16,2)
    tag=source.read(16)

    class PlainReader:
        def __init__(self):
            source.seek(start)
            self.remaining=size
            self.decryptor=Cipher(algorithms.AES(key),modes.GCM(nonce,tag)).decryptor()
            self.decryptor.authenticate_additional_data(header)
            self.finished=False
        def read(self,length=-1):
            if length<0:raise ValueError("復元は大きさを制限して読み取ります。")
            if length==0 or self.finished:return b""
            raw=source.read(min(length,self.remaining))
            if len(raw)!=min(length,self.remaining):
                raise ProtectionError("バックアップが途中で切れています。")
            self.remaining-=len(raw)
            plain=self.decryptor.update(raw)
            if self.remaining==0 and not self.finished:
                try:plain+=self.decryptor.finalize()
                except InvalidTag as exc:
                    raise ProtectionError("バックアップが変更されたか、破損しています。元の帳簿は変更していません。") from exc
                self.finished=True
            return plain

    # Verify the whole payload before parsing a single archive record or creating a directory.
    verified=PlainReader()
    while verified.read(65536):pass
    # Read twice without a plaintext temporary archive; authenticate again before adoption
    # to catch a change to the opened backup between verification and restoration.
    reader=PlainReader()
    result=consume(reader)
    while chunk:=reader.read(65536):
        if zero_tail and any(chunk):raise ProtectionError("バックアップの末尾に不明なデータがあります。")
    return result


def create_backup(store, importer, password, allow_missing=False):
    store.keys.verify_password(password)
    work = private_directory(store.root / ".ryoshu-backups")
    temporary = tempfile.TemporaryDirectory(prefix="backup-", dir=work)
    try:
        root = Path(temporary.name)
        snapshot = root / "ledger.enc.sqlite3"
        backup_key = secrets.token_bytes(32)
        store.database.snapshot(snapshot, key=backup_key)
        with closing(store.database.open(snapshot,key=backup_key,readonly=True)) as db:
            originals = db.execute("SELECT id,extension,sha256 FROM receipts").fetchall()
            sources = db.execute("SELECT DISTINCT source_sha256 FROM receipt_import_sources").fetchall() if db.execute("SELECT 1 FROM sqlite_master WHERE name='receipt_import_sources'").fetchone() else []
        records=[(f"data/receipts/{receipt_id}/original{extension}",store.root/"receipts"/receipt_id/("original"+extension),digest)
                 for receipt_id,extension,digest in originals]
        records.extend((f"data/imports/{digest}/source.pdf",importer.source_path(digest),digest) for (digest,) in sources)
        missing=[]
        def original_bytes(path,digest):
            raw=store.keys.read_file(path)
            if len(raw)>20*1024**2:raise ProtectionError("バックアップの原本は1ファイル20MBまでに対応しています。")
            if hashlib.sha256(raw).hexdigest()!=digest:
                raise ProtectionError("原本の検証値が違います。保存先とバックアップを確認してください。")
            return raw
        for name,path,digest in records:
            try:original_bytes(path,digest)
            except (ProtectionError,OSError):
                if not allow_missing:raise
                missing.append(name)
        manifest = {"app":"Ryoshu","format":1,"version":__version__,"created_at":now(),
                    "receipts":len(originals),"database_key":base64.b64encode(backup_key).decode(),"missing_files":missing}
        manifest_bytes = json.dumps(manifest,ensure_ascii=False).encode()
        if len(manifest_bytes) > 1024**2:
            raise ValueError("不足している原本の一覧がバックアップの上限を超えています。原本を修復してから、もう一度保存してください。")
        total = snapshot.stat().st_size
        count = 2 + len(originals) + len(sources)
        if count > MAX_ENTRIES:
            raise ValueError("このバックアップのファイル数は上限を超えています。")

        def produce(output):
            nonlocal total
            def add(bundle,name,raw):
                nonlocal total
                total += len(raw)
                if total > MAX_ARCHIVE:
                    raise ValueError("バックアップは展開後20GBまでに対応しています。")
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(raw), 0o600
                bundle.addfile(info, io.BytesIO(raw))
            with tarfile.open(fileobj=output, mode="w|",format=tarfile.USTAR_FORMAT) as bundle:
                add(bundle,"manifest.json",manifest_bytes)
                info = tarfile.TarInfo("data/ledger.enc.sqlite3")
                info.size, info.mode = snapshot.stat().st_size, 0o600
                with snapshot.open("rb") as database:
                    bundle.addfile(info,database)
                omitted=set(missing)
                for name,path,digest in records:
                    if name not in omitted:add(bundle,name,original_bytes(path,digest))
        archive = root / "backup.ryoshubak"
        with archive.open("xb") as output:
            write_encrypted(output,password,produce)
        with archive.open("rb") as source:
            read_encrypted(source,password,lambda reader:None)
        return temporary, archive
    except BaseException:
        temporary.cleanup()
        raise
