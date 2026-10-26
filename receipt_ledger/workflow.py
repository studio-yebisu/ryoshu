"""Ledger creation and later review, using the existing persisted receipt drafts."""
import json


def review_before_ledger(store, value=None):
    with store.connect() as db:
        if value is not None:
            db.execute("INSERT INTO settings VALUES('review_before_ledger',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(bool(value)),))
        row = db.execute("SELECT value FROM settings WHERE key='review_before_ledger'").fetchone()
        return json.loads(row[0]) if row else False


def export_tax_mode(store, value=None):
    with store.connect() as db:
        if value is not None:
            if value not in {"reviewed", "exempt"}:
                raise ValueError("消費税の出力方法は画面の選択肢から選んでください")
            db.execute("INSERT INTO settings VALUES('export_tax_mode',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(value),))
        row = db.execute("SELECT value FROM settings WHERE key='export_tax_mode'").fetchone()
        return json.loads(row[0]) if row else "reviewed"


def ledger_records(records, month="", review_before_ledger=False):
    states = {"confirmed"} if review_before_ledger else {"queued", "processing", "review", "error", "confirmed"}
    return sorted([r for r in records if r["status"] in states and
                   (not month or not r["draft"]["date"] or r["draft"]["date"].startswith(month))],
                  key=lambda r: (r["draft"]["date"] or "9999", r["id"]))


def recover_queue(store):
    """Resume unfinished work once; a repeatedly crashing item needs manual retry."""
    interrupted = [r for r in store.list() if r["status"] in {"queued", "processing"}]
    store.recover()
    resumed = []
    for previous in interrupted:
        with store.connect() as db:
            last = db.execute("SELECT action FROM audit WHERE receipt_id=? AND action IN ('import','retry','auto_resume') ORDER BY seq DESC LIMIT 1", (previous["id"],)).fetchone()
        if previous["status"] == "processing" and last and last["action"] == "auto_resume":
            continue
        current = store.get(previous["id"])
        store.set_status(current["id"], current["version"], "retry")
        with store.connect() as db:
            store.audit(db, current["id"], "auto_resume", {"previous_status": previous["status"], "stage": previous["stage"]})
        resumed.append(current["id"])
    return resumed
