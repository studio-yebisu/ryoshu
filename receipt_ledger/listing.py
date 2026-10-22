"""Small list records: OCR page geometry is only loaded for an opened receipt."""
import re
import unicodedata
import json


def duplicate_warnings(store):
    with store.connect() as db:
        rows = db.execute("SELECT id,status,draft_json FROM receipts WHERE status NOT IN ('queued','processing','excluded')").fetchall()
    return possible_duplicates([{"id": row["id"], "status": row["status"], "draft": json.loads(row["draft_json"])} for row in rows])


def list_summaries(store):
    with store.connect() as db:
        rows = db.execute("""SELECT r.id,r.sha256,r.original_name,r.extension,r.created_at,r.updated_at,
            r.status,r.stage,r.version,r.draft_json,NULL AS ocr_json,r.suggestion_json,r.error,
            a.code AS account_code,a.kind AS account_kind,
            COALESCE(json_extract(r.ocr_json,'$.page_count'),json_array_length(r.ocr_json,'$.pages'),1) AS preview_count,
            s.source_name,s.source_sha256,s.page AS source_page,s.page_count AS source_page_count FROM receipts r
            LEFT JOIN account_master a ON a.name=json_extract(r.draft_json,'$.account')
            LEFT JOIN receipt_import_sources s ON s.rowid=(SELECT min(rowid) FROM receipt_import_sources WHERE receipt_id=r.id)
            ORDER BY r.created_at DESC,r.id""").fetchall()
        result = []
        for raw in rows:
            row = store.decode(raw, db)
            source = {"name": row.pop("source_name"), "sha256": row.pop("source_sha256"),
                      "page": row.pop("source_page"), "page_count": row.pop("source_page_count")}
            row["source_document"] = source if source["sha256"] else None
            result.append(row)
        return result


def possible_duplicates(records):
    groups = {}
    for row in records:
        draft = row["draft"]
        if row["status"] in {"queued", "processing", "excluded"} or not draft.get("date") or not draft.get("total") or not draft.get("merchant"):
            continue
        merchant = re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", draft["merchant"]).casefold())
        if not merchant:
            continue
        groups.setdefault((draft["date"], draft["total"], merchant), []).append(row["id"])
    result = {}
    for ids in groups.values():
        if len(ids) > 1:
            # The UI needs a warning, not an N-by-N payload for a repeated batch.
            examples = sorted(ids)[:11]
            for receipt_id in ids:
                result[receipt_id] = [other for other in examples if other != receipt_id][:10]
    return result
