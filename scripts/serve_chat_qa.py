"""Isolated synthetic OAuth/chat UI server. Never contacts OpenAI or runs AI."""
import argparse
import io
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import time
from urllib.parse import parse_qs, urlencode, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Use a new, empty output directory")
    output.mkdir(parents=True, exist_ok=True)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    os.environ["RECEIPT_LEDGER_PORT"] = str(port)
    os.environ["RECEIPT_LEDGER_ENV_LABEL"] = "検証用：架空のOAuth・AI応答。OpenAIへの送信なし"

    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from PIL import Image
    import uvicorn
    from fastapi import HTTPException, Request
    from receipt_ledger import server
    from receipt_ledger.openai_oauth import ISSUER, DISCOVERY, JWKS, TOKEN, RESOURCE, SCOPES
    from receipt_ledger.privacy import install
    from receipt_ledger.schemas import Draft

    class FakeHTTP:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        def json(self, method, url, **kwargs):
            if url == DISCOVERY:
                return {"issuer": ISSUER, "jwks_uri": JWKS, "revocation_endpoint": ISSUER + "/api/accounts/oauth/revoke"}
            if url == JWKS:
                public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
                return {"keys": [{**public, "kid": "qa-only"}]}
            if url == TOKEN:
                pending = connection.test_pending
                token = jwt.encode({"sub": "synthetic-user", "iss": ISSUER, "aud": "oaiapp_qa_only", "iat": time.time(), "exp": time.time() + 3600, "nonce": pending["nonce"], "email": "架空接続@example.invalid"}, self.key, algorithm="RS256", headers={"kid": "qa-only"})
                return {"access_token": "synthetic-access", "refresh_token": "synthetic-refresh", "id_token": token, "token_type": "Bearer", "scope": SCOPES, "expires_in": 3600}
            if url == RESOURCE + "/models":
                return {"models": [{"slug": "synthetic-model", "display_name": "検査用の架空モデル（実推論なし）", "visibility": "list"}]}
            return {}

        def generate(self, token, body):
            payload = json.loads(body["input"][-1]["content"])
            calls.append(body)
            if payload["request"] == "応答を待つ":
                time.sleep(.8)
            return json.dumps({"message": "検査用の変更案です。実際のAI判定ではありません。", "edits": [
                {"id": r["id"], "changes": [{"field": "account", "value": "通信費"}, {"field": "business_percent", "value": 50}]}
                for r in payload["selected_data"]["receipts"]]}, ensure_ascii=False)

    calls = []
    holding = {"enabled": False}
    server.local_models = lambda: [{"name": "qwen3:4b"}]
    app = server.create_app(output / "data", run_workers=False, auth_dir=output / "auth")
    # Routes close over the real connection object; replace only its synthetic transport/start.
    app.state.openai.http = FakeHTTP()
    real = app.state.openai
    connection = real
    original_start = real.start

    def fake_start(profile_id=None, reconsent=False):
        result = original_start(profile_id, reconsent)
        query = {k: v[0] for k, v in parse_qs(urlsplit(result["authorization_url"]).query).items()}
        real.test_pending = {"nonce": query["nonce"]}
        if holding["enabled"]:
            return {"authorization_url": f"http://127.0.0.1:{port}/_qa/hold"}
        return {"authorization_url": query["redirect_uri"] + "?" + urlencode({"state": query["state"], "code": "synthetic-code", "client_id": "oaiapp_qa_only"})}

    real.start = fake_start
    app.state.chat.connection = real
    identifiers = []
    for index in range(3):
        image = io.BytesIO()
        Image.new("RGB", (120, 120), (200 + index, 210, 220)).save(image, format="PNG")
        item, _ = app.state.store.add(f"架空通信{index+1}.png", ".png", image.getvalue())
        app.state.store.complete(item["id"], {"text": "送信しないOCR本文", "page_count": 1}, {"merchant": f"架空通信{index+1}", "date": "2023-11-02", "total": 3300 + index, "account": "消耗品費"})
        current = app.state.store.get(item["id"])
        if index == 2:
            app.state.store.save(item["id"], current["version"], Draft(**{**current["draft"], "purpose": "業務", "payment": "owner", "business_percent": 100}), confirm=True)
        identifiers.append(item["id"])

    stop_token = secrets.token_urlsafe(24)
    runner = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, access_log=False, log_level="error"))
    static_mount = app.router.routes.pop()

    @app.get("/_qa/state")
    def qa_state():
        return {"ids": identifiers, "rows": app.state.store.list(), "calls": calls}

    @app.post("/_qa/switch-account")
    def switch_account():
        with real.mutex, real.vault.lock():
            data = real.vault.read()
            source = data["profiles"][data["active"]]
            data["profiles"]["qa-second"] = {**source, "client_id": "oaiapp_qa_second", "subject": "synthetic-user-2", "email": "架空の別接続@example.invalid"}
            data["active"] = "qa-second"
            real.vault.write(data)
        return {"switched": True}

    @app.post("/_qa/hold-login")
    def hold_login():
        holding["enabled"] = True
        return {"holding": True}

    @app.get("/_qa/hold")
    def hold_page():
        return {"scope": "Synthetic waiting page; no real login"}

    @app.post("/_qa/stop")
    async def stop(request: Request):
        if request.headers.get("x-qa-stop") != stop_token:
            raise HTTPException(403)
        runner.should_exit = True
        return {"stopping": True}

    app.router.routes.append(static_mount)

    install()
    print(json.dumps({"qa_url": f"http://127.0.0.1:{port}", "stop_token": stop_token}), flush=True)
    runner.run(sockets=[sock])


if __name__ == "__main__":
    main()
