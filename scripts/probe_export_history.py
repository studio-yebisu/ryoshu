"""Synthetic long-history probe for CSV export history (fictional ids only).

Prints JSON measurements; it makes no pass/fail claim. Each ledger is a new temporary
DPAPI/SQLCipher store that is deleted on exit. Storage is reported as logical row counts
and stored JSON bytes only: the encrypted main file alone is misleading in WAL mode.
Usage:
    python scripts/probe_export_history.py [--events 10 100 1000] [--ledger 200]
"""
import argparse
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from receipt_ledger.export_history import ExportHistory  # noqa: E402
from receipt_ledger.protection import LedgerKeys  # noqa: E402
from receipt_ledger.store import Store, now  # noqa: E402

PASSWORD = "fictional probe recovery password 2026"


def open_store(root):
    keys = LedgerKeys(root)
    if keys.mode == "new":
        keys.setup(PASSWORD)
    return Store(root)


def ledger_at(step, size):
    # A ledger that grows by two receipts per export and revises one receipt each time.
    count = size + 2 * step
    return [{"id": f"r{i:06}", "version": 2 if i == step % size else 1, "status": "confirmed", "draft": {"date": "2023-11-02"}}
            for i in range(count)]


def legacy_filter(store, records, profile, kind):
    """v1.4.1 algorithm, kept here only as the measurement baseline."""
    with store.connect() as db:
        rows = db.execute("SELECT created_at,receipts_json FROM csv_exports WHERE profile=? AND kind=? ORDER BY id DESC", (profile, kind)).fetchall()
    previous = {tuple(item) for row in rows for item in json.loads(row["receipts_json"])}
    return [r for r in records if (r["id"], r["version"]) not in previous]


def instrumented(store):
    seen = SimpleNamespace(sql=0, steps=0)
    original = store.database.open

    def opened(*args, **kwargs):
        db = original(*args, **kwargs)
        db.set_trace_callback(lambda _: setattr(seen, "sql", seen.sql + 1))
        def step():
            seen.steps += 1
            return 0
        db.set_progress_handler(step, 1)
        return db
    store.database.open = opened
    return seen


def measure(call, store, repeats=5):
    seen = instrumented(store)
    call()
    seen.sql = seen.steps = 0
    call()
    sql, steps = seen.sql, seen.steps
    del store.database.open
    times = []
    for _ in range(repeats):
        start = time.perf_counter()
        call()
        times.append(time.perf_counter() - start)
    return {"statements": sql, "vm_steps": steps, "median_seconds": statistics.median(times)}


def stats(store):
    with store.connect() as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        one = lambda sql: db.execute(sql).fetchone()[0] or 0
        return {"csv_exports_rows": one("SELECT COUNT(*) FROM csv_exports"),
                "receipts_json_bytes": one("SELECT SUM(LENGTH(receipts_json)) FROM csv_exports"),
                "snapshot_rows": one("SELECT COUNT(*) FROM csv_export_snapshots") if "csv_export_snapshots" in tables else 0,
                "snapshot_bytes": one("SELECT SUM(LENGTH(receipts_json)) FROM csv_export_snapshots") if "csv_export_snapshots" in tables else 0,
                "member_rows": one("SELECT COUNT(*) FROM csv_export_members") if "csv_export_members" in tables else 0}


def run(events, size, repeats_identical):
    with tempfile.TemporaryDirectory(prefix="ryoshu-probe-") as temp:
        new_store = open_store(Path(temp) / "new")
        old_store = open_store(Path(temp) / "legacy")
        history = ExportHistory(new_store)
        with old_store.connect() as db:
            db.execute("""CREATE TABLE csv_exports (id INTEGER PRIMARY KEY AUTOINCREMENT,created_at TEXT NOT NULL,profile TEXT NOT NULL,
                kind TEXT NOT NULL,month TEXT NOT NULL,sha256 TEXT NOT NULL,row_count INTEGER NOT NULL,receipts_json TEXT NOT NULL)""")
        for step in range(events):
            records = ledger_at(step // repeats_identical, size)
            history.record("freee", "journal", "", b"probe", SimpleNamespace(receipt_count=len(records), rows=records), records)
            with old_store.connect() as db:
                db.execute("INSERT INTO csv_exports(created_at,profile,kind,month,sha256,row_count,receipts_json) VALUES(?,?,?,?,?,?,?)",
                           (now(), "freee", "journal", "", "0" * 64, len(records), json.dumps([[r["id"], r["version"]] for r in records])))
        candidates = ledger_at(events, size)
        assert [r["id"] for r in legacy_filter(old_store, candidates, "freee", "journal")] == \
               [r["id"] for r in history.filter(candidates, "freee", "journal", True)[0]]
        return {"events": events, "ledger_receipts_at_end": len(candidates), "identical_repeats": repeats_identical,
                "new": {"storage": stats(new_store),
                        "filter_new_only": measure(lambda: history.filter(candidates, "freee", "journal", True), new_store),
                        "filter_all": measure(lambda: history.filter(candidates, "freee", "journal", False), new_store)},
                "legacy_v141": {"storage": stats(old_store),
                                "filter_any": measure(lambda: legacy_filter(old_store, candidates, "freee", "journal"), old_store)}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=int, nargs="+", default=[10, 100, 1000])
    parser.add_argument("--ledger", type=int, default=200)
    parser.add_argument("--identical-repeats", type=int, default=3, help="consecutive identical exports per ledger state")
    args = parser.parse_args()
    print(json.dumps({"python": sys.version.split()[0], "results": [run(n, args.ledger, args.identical_repeats) for n in args.events]}, indent=2))


if __name__ == "__main__":
    main()
