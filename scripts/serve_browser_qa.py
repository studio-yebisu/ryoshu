"""Isolated UI test server. Replays saved observations; never runs OCR or AI."""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--first-run",action="store_true",help="Exercise actual initial protection setup in the browser")
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
    replay_gate=threading.Event()
    replay_gate.set()

    def replay(store, receipt_id):
        row = store.get(receipt_id)
        store.stage(receipt_id, "ocr")
        if not replay_gate.wait(30):
            raise RuntimeError("QA replay was held beyond its bounded test window")
        time.sleep(.08)
        source = Importer(store).source(receipt_id)
        old = cases.get(row["sha256"])
        if old is None and source and source["page"] <= 100:
            old = report["cases"][source["page"] - 1]
        if old:
            actual = old["actual"]
            thumbnail = ROOT / "samples/receipts-100-v2/thumbnails" / (old["id"] + ".jpg")
            store.keys.write_file(store.folder(receipt_id) / "preview-0.jpg",thumbnail.read_bytes())
            store.complete(receipt_id, {"text": actual.get("description", ""), "page_count": 1,
                                       "version": "saved-observation-replay"}, actual)
        else:
            store.complete(receipt_id, {"text": "架空のUI検査", "page_count": 1},
                           {"merchant": "架空UI検査店", "date": "2023-11-02", "total": 1000,
                            "account": "消耗品費", "description": "業務用紙"})

    server.process = replay
    from receipt_ledger.protection import LedgerKeys
    keys=LedgerKeys(config.DATA_DIR)
    if not args.first_run:
        keys.setup("fictional browser test recovery password")
    app = server.create_app(config.DATA_DIR)
    runner = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level="warning"))
    token = secrets.token_urlsafe(32)

    @app.post("/_qa/stop")
    def stop(request: Request):
        if request.headers.get("x-qa-token") != token:
            raise HTTPException(403)
        replay_gate.set()
        runner.should_exit = True
        return {"stopping": True}

    @app.post("/_qa/replay-hold")
    def replay_hold(request: Request, held: bool=True):
        if request.headers.get("x-qa-token") != token:
            raise HTTPException(403)
        replay_gate.clear() if held else replay_gate.set()
        return {"held":held}

    # The production catch-all static mount is already registered. Keep these
    # isolated test controls reachable before it; they require a per-run token.
    controls=[route for route in app.router.routes if getattr(route,"path","").startswith("/_qa/")]
    app.router.routes[:]=controls+[route for route in app.router.routes if route not in controls]

    base=f"http://127.0.0.1:{port}"
    print(json.dumps({"qa_url":base,"open_url":base+"/#open="+app.state.sessions.mint_bootstrap(),"stop_token":token}),flush=True)
    runner.run(sockets=[sock])


if __name__ == "__main__":
    main()
