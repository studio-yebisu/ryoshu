"""CSV creation history; this never asserts the file was imported by accounting software."""
import hashlib
import json

from .store import now


class ExportHistory:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS csv_exports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,created_at TEXT NOT NULL,profile TEXT NOT NULL,
                kind TEXT NOT NULL,month TEXT NOT NULL,sha256 TEXT NOT NULL,row_count INTEGER NOT NULL,
                receipts_json TEXT NOT NULL
            )""")

    def filter(self, records, profile, kind, new_only=False):
        with self.store.connect() as db:
            rows = db.execute("SELECT created_at,receipts_json FROM csv_exports WHERE profile=? AND kind=? ORDER BY id DESC", (profile, kind)).fetchall()
        previous = {tuple(item) for row in rows for item in json.loads(row["receipts_json"])}
        filtered = [row for row in records if (row["id"], row["version"]) not in previous] if new_only else records
        return filtered, {"last_created_at": rows[0]["created_at"] if rows else None,
                          "skipped_count": len(records) - len(filtered), "new_only": new_only}

    def record(self, profile, kind, month, content, export, records):
        # Exactly the eligible records used to build this file, at those versions.
        eligible = [r for r in records if kind == "review" or r["status"] == "confirmed"]
        eligible = [r for r in eligible if not month or (kind == "review" and not r["draft"].get("date")) or r["draft"].get("date", "").startswith(month)]
        if not export.receipt_count:
            return
        with self.store.connect() as db:
            db.execute("INSERT INTO csv_exports(created_at,profile,kind,month,sha256,row_count,receipts_json) VALUES(?,?,?,?,?,?,?)",
                       (now(), profile, kind, month, hashlib.sha256(content).hexdigest(), len(export.rows),
                        json.dumps([[r["id"], r["version"]] for r in eligible])))
