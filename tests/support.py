"""Explicit protected setup for fictional tests, using production cryptography."""
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
from types import SimpleNamespace
import io
import tarfile
from fastapi.testclient import TestClient

from receipt_ledger.protection import LedgerKeys
from receipt_ledger.store import Store

PASSWORD = "fictional test recovery password 2026"
ROOT = Path(__file__).resolve().parents[1]


def ready_store(root):
    root = Path(root)
    if (root / "RESTORE_FAILED.txt").exists() or (root.parent / "RESTORE_FAILED.txt").exists():
        return Store(root)
    keys = LedgerKeys(root)
    if keys.mode == "new":
        keys.setup(PASSWORD)
    return Store(root)


def ready_app(root, **kwargs):
    from receipt_ledger.server import create_app
    ready_store(root)
    return create_app(root, **kwargs)


class LedgerClient(TestClient):
    def __init__(self,app,*args,**kwargs):
        super().__init__(app,*args,**kwargs)
        token = app.state.sessions.exchange(app.state.sessions.mint_bootstrap())
        self.headers["Authorization"] = "Bearer " + token


def backup_files(content):
    from receipt_ledger.backups import read_encrypted
    def read(source):
        with tarfile.open(fileobj=source, mode="r|") as bundle:
            return {member.name:bundle.extractfile(member).read() for member in bundle}
    return read_encrypted(io.BytesIO(content), PASSWORD, read)


def legacy_fixture(root):
    """Frozen public v1.0.0 schema, journal and audit; two public fictional PDFs."""
    root.mkdir(parents=True, exist_ok=True)
    database = root / "ledger.sqlite3"
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript((ROOT / "tests/fixtures/legacy-v1.0.0.sql").read_text("utf-8"))
        connection.commit()
    for item in json.loads((ROOT / "tests/fixtures/legacy-v1.0.0-files.json").read_text("utf-8")):
        folder = root / "receipts" / item["id"]
        folder.mkdir(parents=True)
        shutil.copyfile(ROOT / item["source"], folder / "original.pdf")
    return SimpleNamespace(root=root, path=database)
