"""Isolated UI test server. Replays saved observations; never runs OCR or AI."""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    data_dir = args.data_dir.resolve()
    if data_dir.exists() and any(data_dir.iterdir()):
        parser.error("検査には空の新しいデータフォルダーを指定してください")
    os.environ["RECEIPT_LEDGER_DATA"] = str(data_dir)
    os.environ["RECEIPT_LEDGER_ENV_LABEL"] = "検証用・保存済みの架空データを再現（新たなOCR・AI実行ではありません）"
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    os.environ["RECEIPT_LEDGER_PORT"] = str(port)

    from fastapi import HTTPException, Request
    import uvicorn
    from receipt_ledger import config, server
    from receipt_ledger.importing import Importer

    report = json.loads((ROOT / "docs/testing/results.json").read_text("utf-8"))
    cases = {row["sha256"]: row for row in report["cases"]}

    def replay(store, receipt_id):
        row = store.get(receipt_id)
        store.stage(receipt_id, "ocr")
        time.sleep(.08)
        source = Importer(store).source(receipt_id)
        old = cases.get(row["sha256"])
        if old is None and source and source["page"] <= 100:
            old = report["cases"][source["page"] - 1]
        if old:
            actual = old["actual"]
            thumbnail = ROOT / "samples/receipts-100-v2/thumbnails" / (old["id"] + ".jpg")
            shutil.copyfile(thumbnail, store.folder(receipt_id) / "preview-0.jpg")
            store.complete(receipt_id, {"text": actual.get("description", ""), "page_count": 1,
                                       "version": "saved-observation-replay"}, actual)
        else:
            store.complete(receipt_id, {"text": "架空のUI検査", "page_count": 1},
                           {"merchant": "架空UI検査店", "date": "2023-11-02", "total": 1000,
                            "account": "消耗品費", "description": "業務用紙"})

    server.process = replay
    app = server.create_app(config.DATA_DIR)
    runner = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level="warning"))
    token = secrets.token_urlsafe(32)

    @app.post("/_qa/stop")
    def stop(request: Request):
        if request.headers.get("x-qa-token") != token:
            raise HTTPException(403)
        runner.should_exit = True
        return {"stopping": True}

    print(json.dumps({"qa_url": f"http://127.0.0.1:{port}", "stop_token": token}), flush=True)
    runner.run(sockets=[sock])


if __name__ == "__main__":
    main()
