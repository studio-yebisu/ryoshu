"""Isolated UI test server. Replays saved observations; never runs OCR or AI."""
import argparse
import json
import math
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
    parser.add_argument("--legacy-account-collision",action="store_true",help="Recreate a fictional old custom name matching a newer account code")
    parser.add_argument("--processing-step-delay",type=float,default=0,help="Delay each replayed OCR/model stage for progress-UI checks (0 to3 seconds)")
    parser.add_argument("--processing-fail-once",choices=[f"V{i:03d}.pdf" for i in range(1,101)],help="Fail one named fictional receipt once, then allow retry")
    args = parser.parse_args()
    if not math.isfinite(args.processing_step_delay) or not 0 <= args.processing_step_delay <= 3:
        parser.error("工程の待ち時間は0秒から3秒の範囲で指定してください")
    data_dir = args.data_dir.resolve()
    default_data = (Path.home() / "StudioYebisu" / "ReceiptLedger" / "data").resolve()
    if data_dir == default_data or data_dir.is_relative_to(default_data):
        parser.error("検証には通常の帳簿とは別の保存先を指定してください")
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
    if config.DATA_DIR != data_dir or config.PORT != port:
        parser.error("検証用の保存先・ポートとアプリの設定が一致しないため停止しました")

    report = json.loads((ROOT / "docs/testing/results.json").read_text("utf-8"))
    cases = {row["sha256"]: row for row in report["cases"]}
    replay_gate=threading.Event()
    replay_gate.set()
    failed_once=set()

    def replay(store, receipt_id, runtime=None):
        # Same call contract as receipt_ledger.pipeline.process(store, receipt_id, runtime); the runtime is never used:
        # saved observations are replayed, no OCR or model call happens here.
        row = store.get(receipt_id)
        store.stage(receipt_id, "ocr")
        if not replay_gate.wait(30):
            raise RuntimeError("QA replay was held beyond its bounded test window")
        time.sleep(args.processing_step_delay or .08)
        if args.processing_fail_once == row["original_name"] and receipt_id not in failed_once:
            failed_once.add(receipt_id)
            store.complete(receipt_id,None,{},error="架空の一時読取エラーです。再試行で成功する検査用の明細です。")
            return
        if args.processing_step_delay:
            store.stage(receipt_id,"model")
            time.sleep(args.processing_step_delay)
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
    # Explicit inference-disabled runtime: the replay server can never start or consult llama.cpp/Ollama or read the
    # user's runtime/model roots. This is UI replay of saved observations, not inference evidence.
    from receipt_ledger.runtime import DisabledRuntime
    app = server.create_app(config.DATA_DIR, runtime=DisabledRuntime())
    if not isinstance(app.state.runtime, DisabledRuntime):
        raise RuntimeError("QA replay must not use a real inference runtime")
    if app.state.store.root != data_dir:
        raise RuntimeError("検証用の保存先と実際の帳簿の保存先が一致しません")
    if args.legacy_account_collision:
        from receipt_ledger.catalog import account_key
        custom = app.state.store.add_account('架空旧版独自科目', 'expense',
            '旧版から保存されていた名前と新規コードの衝突を再現', [], [], category_id='learning')
        with app.state.store.connect() as db:
            db.execute('UPDATE account_master SET name=?,name_key=? WHERE code=?',
                       ('E129', account_key('E129'), custom['code']))
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
