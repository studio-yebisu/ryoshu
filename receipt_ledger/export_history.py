"""CSV creation history; this never asserts the file was imported by accounting software.

Storage contract (additive, no user_version change):
- csv_exports keeps one row per successful POST export, as before.
  snapshot_sha256 IS NULL: receipts_json is the full eligible [id, version] vector
  (rows written by v1.4.1 and earlier, or the malformed-history fallback below).
  snapshot_sha256 IS NOT NULL: the full vector is csv_export_snapshots.receipts_json
  (content-addressed, shared by identical exports) and receipts_json holds only the
  pairs first exported by this event for its profile and kind.
- The union of receipts_json over a profile and kind is therefore the same set in both
  layouts, so an older Ryoshu reading this ledger still skips exactly the same rows.
- csv_export_members is a derived, indexed copy of that union. csv_export_index records
  the last csv_exports.id folded into it, so rows written by an older version after a
  downgrade are folded in incrementally. The members table can be rebuilt from
  csv_exports at any time; no history row is ever deleted or rewritten.
"""
import hashlib
import json

from .store import now

# Shared with the regression tests, which inspect the real query plans.
LAST_CREATED_SQL = "SELECT created_at FROM csv_exports WHERE profile=? AND kind=? ORDER BY id DESC LIMIT 1"
LATEST_ID_SQL = "SELECT COALESCE(MAX(id),0) FROM csv_exports"
THROUGH_SQL = "SELECT through_id FROM csv_export_index WHERE id=1"
# Pairs per membership probe: 2 variables each plus profile and kind stays far below
# the oldest SQLite default limit of 999 host parameters.
LOOKUP_CHUNK = 250


def member_sql(count):
    """Exact (receipt_id, version) probes; parameters are the flattened pairs, then profile and kind.

    CROSS JOIN fixes the candidate list as the outer loop, so each pair is one full
    primary-key search and older revisions of the same receipt are never visited.
    """
    return ("WITH candidate(receipt_id,version) AS (VALUES " + ",".join(["(?,?)"] * count) + ") "
            "SELECT csv_export_members.receipt_id,csv_export_members.version FROM candidate "
            "CROSS JOIN csv_export_members ON csv_export_members.profile=? AND csv_export_members.kind=? "
            "AND csv_export_members.receipt_id=candidate.receipt_id AND csv_export_members.version=candidate.version")


class ExportHistoryError(ValueError):
    pass


def _pairs(raw):
    try:
        items = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ExportHistoryError("CSV作成履歴のJSONを読み取れないため、未出力の明細だけを安全に選べません。正常と確認済みのバックアップから復元するか、全件を出力して原本と照合してください。") from exc
    if not isinstance(items, list):
        raise ExportHistoryError("CSV作成履歴が明細一覧の形式ではないため、未出力の明細だけを安全に選べません。正常と確認済みのバックアップから復元するか、全件を出力して原本と照合してください。")
    pairs = []
    for item in items:
        if not (isinstance(item, list) and len(item) == 2 and isinstance(item[0], str) and type(item[1]) is int):
            raise ExportHistoryError("CSV作成履歴に不正な明細ID・版の組があるため、未出力の明細だけを安全に選べません。正常と確認済みのバックアップから復元するか、全件を出力して原本と照合してください。")
        pairs.append((item[0], item[1]))
    return pairs


class ExportHistory:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS csv_exports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,created_at TEXT NOT NULL,profile TEXT NOT NULL,
                kind TEXT NOT NULL,month TEXT NOT NULL,sha256 TEXT NOT NULL,row_count INTEGER NOT NULL,
                receipts_json TEXT NOT NULL
            )""")
            if "snapshot_sha256" not in {r["name"] for r in db.execute("PRAGMA table_info(csv_exports)")}:
                db.execute("ALTER TABLE csv_exports ADD COLUMN snapshot_sha256 TEXT")
            db.execute("CREATE INDEX IF NOT EXISTS csv_exports_by_target ON csv_exports(profile,kind)")
            db.execute("CREATE TABLE IF NOT EXISTS csv_export_snapshots (sha256 TEXT PRIMARY KEY,receipts_json TEXT NOT NULL)")
            db.execute("""CREATE TABLE IF NOT EXISTS csv_export_members (
                profile TEXT NOT NULL,kind TEXT NOT NULL,receipt_id TEXT NOT NULL,version INTEGER NOT NULL,
                first_export_id INTEGER NOT NULL,PRIMARY KEY(profile,kind,receipt_id,version)
            ) WITHOUT ROWID""")
            db.execute("CREATE TABLE IF NOT EXISTS csv_export_index (id INTEGER PRIMARY KEY CHECK(id=1),through_id INTEGER NOT NULL)")
        # Legacy backfill happens once here so the first export is not the slow one.
        # Malformed legacy rows must not stop the app from starting: keep the valid
        # prefix and let new_only exports report the problem.
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._catch_up(db)
            except ExportHistoryError:
                pass

    @staticmethod
    def _behind(db):
        row = db.execute(THROUGH_SQL).fetchone()
        return (row[0] if row else 0) != db.execute(LATEST_ID_SQL).fetchone()[0]

    def _catch_up(self, db):
        """Fold csv_exports rows newer than the watermark into the members index.

        Call inside BEGIN IMMEDIATE. Progress is written row by row, so a caller that
        commits after an ExportHistoryError keeps the valid prefix and stops before the bad row.
        """
        row = db.execute(THROUGH_SQL).fetchone()
        through = row[0] if row else 0
        if through > db.execute(LATEST_ID_SQL).fetchone()[0]:
            # History is older than its index (e.g. edited outside Ryoshu): rebuild the derived copy.
            db.execute("DELETE FROM csv_export_members")
            through = 0
        while True:
            batch = db.execute("SELECT id,profile,kind,receipts_json FROM csv_exports WHERE id>? ORDER BY id LIMIT 256",
                               (through,)).fetchall()
            if not batch:
                break
            for export in batch:
                pairs = _pairs(export["receipts_json"])
                db.executemany("INSERT OR IGNORE INTO csv_export_members VALUES(?,?,?,?,?)",
                               [(export["profile"], export["kind"], i, v, export["id"]) for i, v in pairs])
                through = export["id"]
                db.execute("INSERT INTO csv_export_index VALUES(1,?) ON CONFLICT(id) DO UPDATE SET through_id=excluded.through_id",
                           (through,))

    @staticmethod
    def _known(db, profile, kind, pairs):
        """Return the given pairs already exported for profile and kind, one primary-key probe per pair."""
        wanted, known = list(dict.fromkeys(pairs)), set()
        for start in range(0, len(wanted), LOOKUP_CHUNK):
            chunk = wanted[start:start + LOOKUP_CHUNK]
            params = [value for pair in chunk for value in pair] + [profile, kind]
            known.update((row[0], row[1]) for row in db.execute(member_sql(len(chunk)), params))
        return known

    def filter(self, records, profile, kind, new_only=False):
        if new_only:
            with self.store.connect() as db:
                db.execute("BEGIN")
                if self._behind(db):
                    # Only after a downgrade/legacy write or a stuck malformed row.
                    db.commit()
                    db.execute("BEGIN IMMEDIATE")
                    self._catch_up(db)
                known = self._known(db, profile, kind, [(r["id"], r["version"]) for r in records])
                last = db.execute(LAST_CREATED_SQL, (profile, kind)).fetchone()
            filtered = [row for row in records if (row["id"], row["version"]) not in known]
        else:
            with self.store.connect() as db:
                last = db.execute(LAST_CREATED_SQL, (profile, kind)).fetchone()
            filtered = records
        return filtered, {"last_created_at": last["created_at"] if last else None,
                          "skipped_count": len(records) - len(filtered), "new_only": new_only}

    def record(self, profile, kind, month, content, export, records):
        # Exactly the eligible records used to build this file, at those versions.
        eligible = [r for r in records if kind == "review" or r["status"] == "confirmed"]
        eligible = [r for r in eligible if not month or (kind == "review" and not r["draft"].get("date")) or r["draft"].get("date", "").startswith(month)]
        if not export.receipt_count:
            return
        pairs = [(r["id"], r["version"]) for r in eligible]
        full = json.dumps([[i, v] for i, v in pairs])
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            values = (now(), profile, kind, month, hashlib.sha256(content).hexdigest(), len(export.rows))
            try:
                self._catch_up(db)
            except ExportHistoryError:
                # Index is stuck before a malformed legacy row: keep the legacy full-vector
                # layout so no provenance or dedup information is lost; the watermark stays put.
                db.execute("INSERT INTO csv_exports(created_at,profile,kind,month,sha256,row_count,receipts_json) VALUES(?,?,?,?,?,?,?)",
                           (*values, full))
                return
            digest = hashlib.sha256(full.encode()).hexdigest()
            db.execute("INSERT OR IGNORE INTO csv_export_snapshots VALUES(?,?)", (digest, full))
            if db.execute("SELECT receipts_json FROM csv_export_snapshots WHERE sha256=?", (digest,)).fetchone()[0] != full:
                raise ExportHistoryError("CSV作成履歴のスナップショットがハッシュ値と一致しないため、履歴を記録できません。正常と確認済みのバックアップから復元してください。")
            known = self._known(db, profile, kind, pairs)
            delta = list(dict.fromkeys((i, v) for i, v in pairs if (i, v) not in known))
            cursor = db.execute("INSERT INTO csv_exports(created_at,profile,kind,month,sha256,row_count,receipts_json,snapshot_sha256) VALUES(?,?,?,?,?,?,?,?)",
                                (*values, json.dumps([[i, v] for i, v in delta]), digest))
            db.executemany("INSERT INTO csv_export_members VALUES(?,?,?,?,?)",
                           [(profile, kind, i, v, cursor.lastrowid) for i, v in delta])
            db.execute("INSERT INTO csv_export_index VALUES(1,?) ON CONFLICT(id) DO UPDATE SET through_id=excluded.through_id",
                       (cursor.lastrowid,))

    def event_receipts(self, export_id):
        """Full eligible [id, version] vector of one export event, in its original order."""
        with self.store.connect() as db:
            row = db.execute("SELECT e.receipts_json,e.snapshot_sha256,s.receipts_json AS snapshot FROM csv_exports e "
                             "LEFT JOIN csv_export_snapshots s ON s.sha256=e.snapshot_sha256 WHERE e.id=?", (export_id,)).fetchone()
        if row is None:
            raise KeyError(export_id)
        if row["snapshot_sha256"] is None:
            return [list(p) for p in _pairs(row["receipts_json"])]
        if row["snapshot"] is None:
            raise ExportHistoryError("CSV作成履歴が参照するスナップショットが見つからないため、履歴を読み取れません。正常と確認済みのバックアップから復元してください。")
        return [list(p) for p in _pairs(row["snapshot"])]
