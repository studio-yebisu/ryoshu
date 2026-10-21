"""Ledger creation and later review, using the existing persisted receipt drafts."""
import json


def review_before_ledger(store, value=None):
    with store.connect() as db:
        if value is not None:
            db.execute("INSERT INTO settings VALUES('review_before_ledger',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(bool(value)),))
        row = db.execute("SELECT value FROM settings WHERE key='review_before_ledger'").fetchone()
        return json.loads(row[0]) if row else False


def ledger_records(records, month="", review_before_ledger=False):
    states = {"confirmed"} if review_before_ledger else {"queued", "processing", "review", "error", "confirmed"}
    return sorted([r for r in records if r["status"] in states and
                   (not month or not r["draft"]["date"] or r["draft"]["date"].startswith(month))],
                  key=lambda r: (r["draft"]["date"] or "9999", r["id"]))
