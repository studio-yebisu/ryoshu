"""Measure listing on a synthetic database; does not run receipt OCR or AI."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import statistics
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from PIL import Image
from receipt_ledger.server import create_app
from receipt_ledger.schemas import Draft


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--count", type=int, default=2000)
    args = parser.parse_args()
    if args.out.exists() or not 1 <= args.count <= 10000:
        parser.error("新しい出力先と、1〜10000件の件数を指定してください")
    app = create_app(args.out / "data", run_workers=False)
    store = app.state.store
    first = None
    ocr = {"text": "架空の性能検査", "page_count": 1,
           "pages": [{"lines": [{"text": "架空の明細", "box": [0, 0, 20, 20]}] * 2300}]}
    ocr_json = json.dumps(ocr, ensure_ascii=False, separators=(",", ":"))
    with store.connect() as db:
        for index in range(args.count):
            buffer = io.BytesIO()
            Image.new("RGB", (2, 2), (index % 256, index // 256, 0)).save(buffer, "PNG")
            raw, receipt_id = buffer.getvalue(), uuid.uuid4().hex
            folder = store.root / "receipts" / receipt_id
            folder.mkdir(exist_ok=True)
            (folder / "original.png").write_bytes(raw)
            draft = {"date": "2023-11-02", "merchant": f"架空性能検査店{index:04d}", "total": 1000,
                     "account": "消耗品費", "description": "架空の性能検査"}
            draft = Draft(**draft).model_dump()
            db.execute("""INSERT INTO receipts(id,sha256,original_name,extension,created_at,updated_at,
                status,stage,version,draft_json,ocr_json,suggestion_json,error)
                VALUES(?,?,?,?,?,?,'review','',1,?,?,NULL,'')""",
                (receipt_id, hashlib.sha256(raw).hexdigest(), f"架空性能検査{index:04d}.png", ".png",
                 "2023-11-02T00:00:00+00:00", "2023-11-02T00:00:00+00:00",
                 json.dumps(draft, ensure_ascii=False), ocr_json))
            first = first or receipt_id
    metrics = {"scope": "Synthetic SQLite dataset and ASGI requests; no OCR or AI; not an import speed benchmark",
               "count": args.count, "ocr_json_bytes_per_receipt": len(ocr_json.encode("utf-8")), "routes": {}}
    with TestClient(app) as client:
        for route in ("/api/receipts", f"/api/receipts/{first}"):
            elapsed = []
            for _ in range(3):
                start = time.perf_counter()
                result = client.get(route)
                elapsed.append(round((time.perf_counter() - start) * 1000, 1))
                result.raise_for_status()
            if route == "/api/receipts":
                assert len(result.json()["receipts"]) == args.count
            metrics["routes"]["list" if route == "/api/receipts" else "detail"] = {
                "milliseconds": elapsed, "median_ms": statistics.median(elapsed), "response_bytes": len(result.content)}
    metrics["source_sha256"] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                for name in ("receipt_ledger/listing.py", "receipt_ledger/server.py", "scripts/measure_listing.py")}
    (args.out / "metrics.json").write_bytes((json.dumps(metrics, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(json.dumps(metrics, ensure_ascii=False))


if __name__ == "__main__":
    main()
