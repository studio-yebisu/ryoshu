"""Export history keeps lifetime new_only semantics without whole-history reads.

All ledgers are temporary, protected with production DPAPI/SQLCipher via support.ready_store,
and contain only fictional ids. ExportHistory does not join receipts, so plain dicts suffice.
"""
import json
import threading
from types import SimpleNamespace

import pytest

from receipt_ledger import export_history as module
from receipt_ledger.export_history import ExportHistory, ExportHistoryError
from support import PASSWORD, LedgerClient, ready_app, ready_store

LEGACY_DDL = """CREATE TABLE csv_exports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,created_at TEXT NOT NULL,profile TEXT NOT NULL,
    kind TEXT NOT NULL,month TEXT NOT NULL,sha256 TEXT NOT NULL,row_count INTEGER NOT NULL,
    receipts_json TEXT NOT NULL)"""
LEGACY_INSERT = "INSERT INTO csv_exports(created_at,profile,kind,month,sha256,row_count,receipts_json) VALUES(?,?,?,'',?,1,?)"
HISTORY_TABLES = ["csv_exports", "csv_export_snapshots", "csv_export_members", "csv_export_index"]


def rec(rid, version=1, status="confirmed", date="2023-11-02"):
    return {"id": rid, "version": version, "status": status, "draft": {"date": date}}


def export(count=1):
    return SimpleNamespace(receipt_count=count, rows=[None] * count)


def ids(rows):
    return [(r["id"], r["version"]) for r in rows]


def legacy_union(store, profile, kind):
    """What v1.4.1 filter() computes; must stay correct after a downgrade."""
    with store.connect() as db:
        rows = db.execute("SELECT receipts_json FROM csv_exports WHERE profile=? AND kind=?", (profile, kind)).fetchall()
    return {tuple(item) for row in rows for item in json.loads(row[0])}


def count(store, table):
    with store.connect() as db:
        return db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]


def dump(store):
    with store.connect() as db:
        return {t: sorted(tuple(r) for r in db.execute("SELECT * FROM " + t)) for t in HISTORY_TABLES}


def instrument(monkeypatch, store):
    """Record SQL text and SQLite VM steps for every connection the store opens."""
    seen = SimpleNamespace(sql=[], steps=0)
    original = store.database.open

    def opened(*args, **kwargs):
        db = original(*args, **kwargs)
        db.set_trace_callback(seen.sql.append)
        def step():
            seen.steps += 1
            return 0
        db.set_progress_handler(step, 1)
        return db
    monkeypatch.setattr(store.database, "open", opened)
    return seen


def test_lifetime_new_only_is_per_profile_kind_and_changed_revisions_stay_eligible(tmp_path):
    store = ready_store(tmp_path)
    history = ExportHistory(store)
    a, b = rec("a"), rec("b")
    history.record("freee", "journal", "", b"one", export(2), [a, b])
    for _ in range(3):  # later unrelated batches must not forget the first one
        history.record("freee", "journal", "", b"other", export(1), [rec("c")])
    rows, info = history.filter([a, b, rec("b", 2), rec("c"), rec("d")], "freee", "journal", new_only=True)
    assert ids(rows) == [("b", 2), ("d", 1)]
    assert info["skipped_count"] == 3 and info["new_only"] is True and info["last_created_at"]
    for profile, kind in [("mf", "journal"), ("freee", "expenses"), ("generic", "review")]:
        rows, info = history.filter([a, b], profile, kind, new_only=True)
        assert ids(rows) == [("a", 1), ("b", 1)] and info["last_created_at"] is None


def test_eligibility_month_status_and_review_handling_match_previous_rules(tmp_path):
    store = ready_store(tmp_path)
    history = ExportHistory(store)
    records = [rec("ok"), rec("draft", status="review"), rec("oct", date="2023-10-01"), rec("undated", status="review", date="")]
    history.record("generic", "journal", "2023-11", b"j", export(1), records)
    assert ids(history.filter(records, "generic", "journal", True)[0]) == [("draft", 1), ("oct", 1), ("undated", 1)]
    history.record("generic", "review", "2023-11", b"r", export(3), records)
    assert ids(history.filter(records, "generic", "review", True)[0]) == [("oct", 1)]
    assert history.event_receipts(2) == [["ok", 1], ["draft", 1], ["undated", 1]]


def test_zero_receipts_records_nothing_and_new_only_false_or_empty_records_are_plain(tmp_path, monkeypatch):
    store = ready_store(tmp_path)
    history = ExportHistory(store)
    history.record("freee", "journal", "", b"", export(0), [rec("a")])
    assert count(store, "csv_exports") == 0
    assert history.filter([], "freee", "journal", True) == ([], {"last_created_at": None, "skipped_count": 0, "new_only": True})
    history.record("freee", "journal", "", b"x", export(1), [rec("a")])
    seen = instrument(monkeypatch, store)
    rows, info = history.filter([rec("a")], "freee", "journal", False)
    assert ids(rows) == [("a", 1)] and info["skipped_count"] == 0 and info["last_created_at"]
    assert not any("csv_export_members" in s or "receipts_json" in s for s in seen.sql)


def test_repeated_identical_exports_share_one_snapshot_and_keep_every_event(tmp_path):
    store = ready_store(tmp_path)
    history = ExportHistory(store)
    records = [rec(f"r{i:03}") for i in range(100)]
    for _ in range(20):
        history.record("freee", "journal", "", b"same", export(100), records)
    assert count(store, "csv_exports") == 20 and count(store, "csv_export_snapshots") == 1
    assert count(store, "csv_export_members") == 100
    with store.connect() as db:
        events = db.execute("SELECT id,receipts_json,snapshot_sha256,sha256,row_count FROM csv_exports ORDER BY id").fetchall()
    assert len(json.loads(events[0]["receipts_json"])) == 100
    assert all(e["receipts_json"] == "[]" and e["snapshot_sha256"] == events[0]["snapshot_sha256"] for e in events[1:])
    assert all(e["row_count"] == 100 and len(e["sha256"]) == 64 for e in events)
    full = [[r["id"], 1] for r in records]
    assert all(history.event_receipts(e["id"]) == full for e in events)
    assert legacy_union(store, "freee", "journal") == {(r["id"], 1) for r in records}


def test_legacy_rows_are_backfilled_kept_verbatim_and_survive_reopen_and_downgrade(tmp_path):
    store = ready_store(tmp_path)
    legacy = [("freee", "journal", [["a", 1], ["b", 1]]), ("freee", "journal", [["a", 1]]), ("mf", "journal", [["c", 3]])]
    with store.connect() as db:
        db.execute(LEGACY_DDL)
        for profile, kind, pairs in legacy:
            db.execute(LEGACY_INSERT, ("2026-01-01T00:00:00+00:00", profile, kind, "0" * 64, json.dumps(pairs)))
    with store.connect() as db:
        before = [tuple(r) for r in db.execute("SELECT * FROM csv_exports ORDER BY id")]
    history = ExportHistory(store)
    assert count(store, "csv_export_members") == 3
    with store.connect() as db:
        after = [tuple(r)[:8] for r in db.execute("SELECT * FROM csv_exports ORDER BY id")]
        assert db.execute("SELECT COUNT(*) FROM csv_exports WHERE snapshot_sha256 IS NOT NULL").fetchone()[0] == 0
    assert after == before
    assert history.event_receipts(1) == [["a", 1], ["b", 1]]
    history.record("freee", "journal", "", b"new", export(2), [rec("b"), rec("d")])
    # An older Ryoshu after a downgrade appends a legacy full-vector row ...
    with store.connect() as db:
        db.execute(LEGACY_INSERT, ("2026-02-01T00:00:00+00:00", "freee", "journal", "1" * 64, json.dumps([["e", 1]])))
    # ... and computes its own union from receipts_json, which still covers every new-format event.
    assert legacy_union(store, "freee", "journal") == {("a", 1), ("b", 1), ("d", 1), ("e", 1)}
    reopened = ExportHistory(ready_store(tmp_path))
    rows, info = reopened.filter([rec(x) for x in "abdef"], "freee", "journal", True)
    assert ids(rows) == [("f", 1)] and info["last_created_at"] == "2026-02-01T00:00:00+00:00"
    assert ids(ExportHistory(store).filter([rec("c", 3)], "mf", "journal", True)[0]) == []


def test_index_that_is_ahead_of_history_is_rebuilt_not_trusted(tmp_path):
    store = ready_store(tmp_path)
    history = ExportHistory(store)
    history.record("freee", "journal", "", b"x", export(1), [rec("a")])
    with store.connect() as db:
        db.execute("UPDATE csv_export_index SET through_id=99")
        db.execute("INSERT INTO csv_export_members VALUES('freee','journal','ghost',1,99)")
    assert ids(history.filter([rec("a"), rec("ghost")], "freee", "journal", True)[0]) == [("ghost", 1)]


def test_malformed_legacy_row_fails_closed_for_new_only_and_loses_nothing_on_record(tmp_path):
    store = ready_store(tmp_path)
    with store.connect() as db:
        db.execute(LEGACY_DDL)
        for raw in [json.dumps([["a", 1]]), "{not json", json.dumps([["b", 1]])]:
            db.execute(LEGACY_INSERT, ("t", "freee", "journal", "0" * 64, raw))
    history = ExportHistory(store)  # must not prevent the app from starting
    with store.connect() as db:
        assert db.execute(module.THROUGH_SQL).fetchone()[0] == 1
    assert history.filter([rec("b")], "freee", "journal", False)[1]["skipped_count"] == 0
    with pytest.raises(ExportHistoryError):
        history.filter([rec("b")], "freee", "journal", True)
    history.record("freee", "journal", "", b"y", export(1), [rec("c")])
    with store.connect() as db:
        last = db.execute("SELECT receipts_json,snapshot_sha256 FROM csv_exports ORDER BY id DESC LIMIT 1").fetchone()
    assert json.loads(last[0]) == [["c", 1]] and last[1] is None
    with store.connect() as db:
        db.execute("UPDATE csv_exports SET receipts_json='[]' WHERE id=2")  # owner repairs from a backup
    assert ids(history.filter([rec(x) for x in "abcd"], "freee", "journal", True)[0]) == [("d", 1)]


def test_failed_record_rolls_back_event_snapshot_members_and_watermark(tmp_path):
    store = ready_store(tmp_path)
    history = ExportHistory(store)
    history.record("freee", "journal", "", b"x", export(1), [rec("a")])
    tables = ["csv_exports", "csv_export_snapshots", "csv_export_members"]
    before = [count(store, t) for t in tables]
    with store.connect() as db:
        db.execute("CREATE TRIGGER fictional_failure BEFORE INSERT ON csv_export_members BEGIN SELECT RAISE(ABORT,'fictional failure'); END")
    with pytest.raises(Exception, match="fictional failure"):
        history.record("freee", "journal", "", b"y", export(1), [rec("b")])
    assert [count(store, t) for t in tables] == before
    with store.connect() as db:
        assert db.execute(module.THROUGH_SQL).fetchone()[0] == 1
        db.execute("DROP TRIGGER fictional_failure")
    history.record("freee", "journal", "", b"y", export(1), [rec("b")])
    assert ids(history.filter([rec("a"), rec("b"), rec("c")], "freee", "journal", True)[0]) == [("c", 1)]


def test_concurrent_records_serialize_and_each_pair_is_first_exported_once(tmp_path):
    store = ready_store(tmp_path)
    history = ExportHistory(store)
    shared = [rec(f"s{i}") for i in range(30)]
    errors = []

    def worker(n):
        try:
            history.record("freee", "journal", "", b"c", export(31), shared + [rec(f"own{n}")])
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)
    threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and count(store, "csv_exports") == 6
    with store.connect() as db:
        deltas = [tuple(p) for (raw,) in db.execute("SELECT receipts_json FROM csv_exports") for p in json.loads(raw)]
    assert len(deltas) == len(set(deltas)) == 36 and count(store, "csv_export_members") == 36


def test_membership_probes_more_pairs_than_one_chunk_and_duplicate_pairs(tmp_path):
    store = ready_store(tmp_path)
    history = ExportHistory(store)
    many = [rec(f"r{i:04}") for i in range(module.LOOKUP_CHUNK * 2 + 7)]
    history.record("freee", "journal", "", b"m", export(len(many)), many + many[:3])  # duplicates in one vector
    assert count(store, "csv_export_members") == len(many)
    assert history.event_receipts(1)[-3:] == [[r["id"], 1] for r in many[:3]]
    rows, info = history.filter(many + [rec("r0000", 2)], "freee", "journal", True)
    assert ids(rows) == [("r0000", 2)] and info["skipped_count"] == len(many)


def test_hot_queries_use_indexes_not_history_scans(tmp_path):
    store = ready_store(tmp_path)
    history = ExportHistory(store)
    history.record("freee", "journal", "", b"x", export(1), [rec("a")])
    with store.connect() as db:
        plan = [r["detail"] for r in db.execute("EXPLAIN QUERY PLAN " + module.member_sql(3),
                                                ("a", 1, "b", 1, "c", 2, "freee", "journal"))]
        # The only scan allowed is over the bound candidate list; members are reached by full key.
        assert any(d.startswith("SEARCH csv_export_members USING PRIMARY KEY (profile=? AND kind=? AND receipt_id=? AND version=?)")
                   for d in plan), plan
        assert not any(("csv_export" in d and d.startswith("SCAN")) or "TEMP B-TREE" in d or "AUTOMATIC" in d for d in plan), plan
        for sql, params in [(module.LAST_CREATED_SQL, ("freee", "journal")), (module.THROUGH_SQL, ())]:
            plan = [r["detail"] for r in db.execute("EXPLAIN QUERY PLAN " + sql, params)]
            assert plan and not any(d.startswith("SCAN") or "TEMP B-TREE" in d for d in plan), (sql, plan)
            assert any("PRIMARY KEY" in d or "csv_exports_by_target" in d for d in plan), (sql, plan)


def measure_filter(monkeypatch, store, history, candidates):
    seen = instrument(monkeypatch, store)
    result = {}
    for new_only in (True, False):
        seen.sql.clear()
        seen.steps = 0
        rows, _ = history.filter(candidates, "freee", "journal", new_only)
        result[new_only] = (ids(rows), list(seen.sql), seen.steps)
    monkeypatch.undo()
    return result


def assert_history_independent(short, long):
    for new_only in (True, False):
        assert short[new_only][0] == long[new_only][0]
        assert short[new_only][1] == long[new_only][1]  # identical statements, none per history row
        assert not any("receipts_json" in s or "csv_export_snapshots" in s for s in long[new_only][1])
        # A history or revision scan would add thousands of VM steps; B-tree depth adds a handful.
        assert long[new_only][2] <= short[new_only][2] + 64, (short[new_only][2], long[new_only][2])


def test_hot_filter_work_does_not_grow_with_history_length(tmp_path, monkeypatch):
    """VM-step and SQL-shape comparison; deterministic, no wall-clock threshold."""
    candidates = [rec(f"r{i:03}") for i in range(50)] + [rec("new")]

    def measure(root, events):
        store = ready_store(root)
        history = ExportHistory(store)
        for n in range(events):
            history.record("freee", "journal", "", b"x", export(51), [rec(f"r{i:03}") for i in range(50)] + [rec(f"old{n}")])
        history.filter(candidates, "freee", "journal", True)  # settle the watermark
        return measure_filter(monkeypatch, store, history, candidates)

    short, long = measure(tmp_path / "short", 2), measure(tmp_path / "long", 300)
    assert_history_independent(short, long)
    assert short[True][0] == [("new", 1)]


def test_hot_filter_work_does_not_grow_with_revisions_of_the_same_receipt(tmp_path, monkeypatch):
    """One receipt id with thousands of exported revisions; candidate count is fixed."""
    candidates = [rec("same", 1), rec("same", 2), rec("same", 9999), rec("other")]

    def measure(root, revisions):
        store = ready_store(root)
        with store.connect() as db:  # one legacy event per revision, as a long-lived ledger would hold
            db.execute(LEGACY_DDL)
            db.executemany(LEGACY_INSERT, [("2026-01-01T00:00:00+00:00", "freee", "journal", "0" * 64, json.dumps([["same", v]]))
                                           for v in range(1, revisions + 1)])
        history = ExportHistory(store)  # backfills every revision into members
        assert count(store, "csv_export_members") == revisions
        return measure_filter(monkeypatch, store, history, candidates)

    short, long = measure(tmp_path / "short", 2), measure(tmp_path / "long", 3000)
    assert_history_independent(short, long)
    assert short[True][0] == [("same", 9999), ("other", 1)]


def test_encrypted_backup_restore_keeps_members_watermark_snapshots_legacy_rows_and_provenance(tmp_path):
    from receipt_ledger.restore import restore_backup
    app = ready_app(tmp_path / "original", run_workers=False)
    with LedgerClient(app) as client:
        store = app.state.store
        history = ExportHistory(store)
        history.record("freee", "journal", "", b"one", export(2), [rec("a"), rec("b")])
        history.record("freee", "journal", "", b"one", export(2), [rec("a"), rec("b")])  # shared snapshot, empty delta
        with store.connect() as db:  # downgrade-era legacy row (id 3)
            db.execute(LEGACY_INSERT, ("2026-02-01T00:00:00+00:00", "freee", "journal", "1" * 64, json.dumps([["c", 1]])))
        history.record("freee", "journal", "", b"two", export(2), [rec("a"), rec("d")])  # folds the legacy row in
        history.record("generic", "review", "", b"three", export(1), [rec("a", status="review")])
        before = dump(store)
        assert before["csv_export_index"] == [(1, 5)] and len(before["csv_export_snapshots"]) == 3
        assert ("freee", "journal", "c", 1, 3) in before["csv_export_members"] and len(before["csv_export_members"]) == 5
        result = client.post("/api/backup", headers={"X-Ledger-Request": "1"}, json={"password": PASSWORD})
        result.raise_for_status()
    archive = tmp_path / "backup.ryoshubak"
    archive.write_bytes(result.content)
    assert b"csv_export_members" not in archive.read_bytes()
    restored_root = restore_backup(archive, tmp_path / "restored", PASSWORD)["restored_data"]
    restored = ready_store(restored_root)
    assert not restored.path.read_bytes().startswith(b"SQLite format 3")
    assert dump(restored) == before
    restored_history = ExportHistory(restored)
    assert dump(restored) == before  # nothing was rebuilt or rewritten on open
    for export_id in range(1, 6):
        assert restored_history.event_receipts(export_id) == history.event_receipts(export_id)
    assert restored_history.event_receipts(2) == [["a", 1], ["b", 1]] and restored_history.event_receipts(3) == [["c", 1]]
    candidates = [rec(x) for x in "abcde"]
    rows, info = restored_history.filter(candidates, "freee", "journal", True)
    assert ids(rows) == [("e", 1)] and (rows, info) == history.filter(candidates, "freee", "journal", True)
    assert ids(restored_history.filter([rec("a", status="review")], "generic", "review", True)[0]) == []
