"""No real account login or external inference. Protocol/ledger tests use fakes."""
import copy
import io
import json
import time
import zipfile
from contextlib import nullcontext
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from receipt_ledger import openai_oauth as oauth
from receipt_ledger.chat import ChatRequest
from receipt_ledger.openai_oauth import ChatConnectionError, OpenAIConnection
from receipt_ledger.schemas import Draft
from receipt_ledger.server import create_app
from receipt_ledger.store import Conflict

HEADERS = {"X-Ledger-Request": "1"}


def row(store, content=b"fictional-chat", confirmed=False):
    item, _ = store.add("架空.png", ".png", content)
    store.complete(item["id"], {"text": "DO NOT SEND OCR", "pages": []}, {"merchant": "架空通信", "date": "2023-11-02", "total": 3300, "account": "消耗品費"})
    item = store.get(item["id"])
    if confirmed:
        item = store.save(item["id"], item["version"], Draft(**{**item["draft"], "purpose": "仕事", "payment": "owner", "business_percent": 100}), confirm=True)
    return item


class FakeChat:
    def __init__(self):
        self.active = "fake-account"
        self.calls = []
        self.answer = {"message": "変更案です。原本と確認してください。", "edits": []}
        self.http = self

    def status(self):
        return {"active": self.active}

    def models(self, expected=None):
        return [{"id": "test-model", "name": "Fake model"}]

    def access(self, expected=None):
        assert expected == self.active
        return "fictional-token"

    def generate(self, token, body):
        self.calls.append(copy.deepcopy(body))
        return json.dumps(self.answer, ensure_ascii=False)


@pytest.fixture
def app(tmp_path):
    app = create_app(tmp_path / "data", run_workers=False, auth_dir=tmp_path / "auth")
    app.state.chat.connection = FakeChat()
    return app


def request_for(rows, **values):
    return {"rows": [{"id": r["id"], "version": r["version"]} for r in rows], "message": "科目を通信費に変更して", "model": "test-model", "profile_id": "fake-account", "allow_external": True, **values}


def edit(item, field="account", value="通信費"):
    return {"id": item["id"], "changes": [{"field": field, "value": value}]}


def test_optional_off_by_default_no_startup_external_calls_or_credentials(app, tmp_path):
    with TestClient(app) as client:
        result = client.get("/api/chat/connection").json()
        assert result["profiles"] == [] and result["active"] is None
        assert not (tmp_path / "auth").exists()
        assert not app.state.chat.connection.calls
        assert client.post("/api/chat/connect", json={}).status_code == 403
        assert client.post("/api/chat/propose", headers={**HEADERS, "Sec-Fetch-Site": "cross-site"}, json={}).status_code == 403


def test_explicit_send_only_selected_fields_no_ocr_no_other_rows(app):
    a = row(app.state.store)
    other = row(app.state.store, b"other-private")
    app.state.store.business_context("DO NOT SEND BUSINESS CONTEXT")
    app.state.chat.connection.answer["edits"] = [edit(a)]
    with TestClient(app) as client:
        denied = client.post("/api/chat/propose", headers=HEADERS, json=request_for([a], allow_external=False))
        assert denied.status_code == 422 and not app.state.chat.connection.calls
        result = client.post("/api/chat/propose", headers=HEADERS, json=request_for([a])).json()
        assert result["proposal_id"] and len(result["edits"]) == 1
        assert app.state.store.get(a["id"])["draft"] == a["draft"]  # preview does not save
        call = app.state.chat.connection.calls[0]
        assert call["store"] is False and call["stream"] is True
        assert "tools" not in call and "temperature" not in call and "max_output_tokens" not in call
        serialized = json.dumps(call)
        for forbidden in ("DO NOT SEND OCR", "DO NOT SEND BUSINESS CONTEXT", other["id"], "fictional-token"):
            assert forbidden not in serialized


def test_apply_atomic_reopens_confirmed_voids_journal_audits_and_is_idempotent(app):
    a, b = row(app.state.store, confirmed=True), row(app.state.store, b"second")
    app.state.chat.connection.answer["edits"] = [edit(a), edit(b, "business_percent", 50)]
    with TestClient(app) as client:
        proposal = client.post("/api/chat/propose", headers=HEADERS, json=request_for([a, b])).json()
        assert app.state.store.get(a["id"])["posting"]
        first = client.post("/api/chat/apply", headers=HEADERS, json={"proposal_id": proposal["proposal_id"]}).json()
        assert set(first["changed"]) == {a["id"], b["id"]} and not first["already_applied"]
        for original in (a, b):
            current = app.state.store.get(original["id"])
            assert current["status"] == "review" and current["version"] == original["version"] + 1
            assert not current["posting"]
            assert app.state.store.history(original["id"])[0]["action"] == "chat_edit"
        again = client.post("/api/chat/apply", headers=HEADERS, json={"proposal_id": proposal["proposal_id"]}).json()
        assert again["already_applied"]
        assert app.state.store.get(a["id"])["version"] == a["version"] + 1
        backup = client.get("/api/backup").content
        with zipfile.ZipFile(io.BytesIO(backup)) as bundle:
            assert not any("auth" in name or "session" in name for name in bundle.namelist())


def test_one_conflict_rolls_back_all_rows_including_postings(app):
    a, b = row(app.state.store, confirmed=True), row(app.state.store, b"second")
    app.state.chat.connection.answer["edits"] = [edit(a), edit(b)]
    proposal = app.state.chat.propose(ChatRequest(**request_for([a, b])))
    app.state.store.save(b["id"], b["version"], Draft(**{**b["draft"], "description": "別画面での変更"}))
    with pytest.raises(Conflict):
        app.state.chat.apply(proposal["proposal_id"])
    assert app.state.store.get(a["id"])["version"] == a["version"]
    assert app.state.store.get(a["id"])["posting"]
    assert app.state.store.get(b["id"])["draft"]["description"] == "別画面での変更"


@pytest.mark.parametrize("field,value", [("account", "架空の未登録科目"), ("total", -5), ("total", True), ("business_percent", 101), ("date", "2023-02-30"), ("payment", "wire"), ("status", "confirmed"), ("__sql__", "DELETE FROM receipts")])
def test_untrusted_model_output_cannot_bypass_validation(app, field, value):
    a = row(app.state.store)
    app.state.chat.connection.answer["edits"] = [edit(a, field, value)]
    with TestClient(app) as client:
        response = client.post("/api/chat/propose", headers=HEADERS, json=request_for([a]))
        assert response.status_code == 422
        assert app.state.store.get(a["id"])["draft"] == a["draft"]
        assert not app.state.store.get(a["id"])["posting"]


def test_unselected_duplicate_rows_expiry_and_conversation_scope(app):
    a, b = row(app.state.store), row(app.state.store, b"unselected")
    fake = app.state.chat.connection
    for edits in ([edit(b)], [edit(a), edit(a)]):
        fake.answer["edits"] = edits
        with pytest.raises(ValueError):
            app.state.chat.propose(ChatRequest(**request_for([a])))
    fake.answer["edits"] = [edit(a)]
    result = app.state.chat.propose(ChatRequest(**request_for([a])))
    with pytest.raises(Conflict):
        app.state.chat.propose(ChatRequest(**request_for([b], session=result["session"])))
    with app.state.store.connect() as db:
        db.execute("UPDATE chat_proposals SET expires_at=0")
    with pytest.raises(Conflict):
        app.state.chat.apply(result["proposal_id"])


def test_first_turn_requires_the_account_explicitly_selected_by_the_screen(app):
    a = row(app.state.store)
    app.state.chat.connection.active = "another-account"
    with pytest.raises(Conflict, match="接続先が変わりました"):
        app.state.chat.propose(ChatRequest(**request_for([a])))
    assert not app.state.chat.connection.calls


class IdentityHTTP:
    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        self.public["kid"] = "test-signing-key"
        self.nonce = ""
        self.scope = oauth.SCOPES
        self.calls = []
        self.token_values = {}
        self.refresh_error = None

    def id_token(self, client="oaiapp_fictional", **changes):
        return jwt.encode({"sub": "subject-1", "iss": oauth.ISSUER, "aud": client, "iat": time.time(), "exp": time.time() + 3600, "nonce": self.nonce, "email": "fictional@example.invalid", **changes}, self.key, algorithm="RS256", headers={"kid": self.public["kid"]})

    def json(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if url == oauth.DISCOVERY:
            return {"issuer": oauth.ISSUER, "jwks_uri": oauth.JWKS, "revocation_endpoint": oauth.ISSUER + "/api/accounts/oauth/revoke"}
        if url == oauth.JWKS:
            return {"keys": [self.public]}
        if url == oauth.TOKEN:
            if kwargs["data"]["grant_type"] == "refresh_token" and self.refresh_error:
                raise ChatConnectionError(self.refresh_error)
            return {"access_token": "fictional-access", "refresh_token": "fictional-refresh", "id_token": self.id_token(), "token_type": "Bearer", "expires_in": 3600, "scope": self.scope, **self.token_values}
        return {}


@pytest.fixture
def auth(tmp_path):
    connection = OpenAIConnection(tmp_path / "auth", IdentityHTTP())
    yield connection
    connection.close()


def begin(auth, profile=None):
    query = parse_qs(urlsplit(auth.start(profile)["authorization_url"]).query)
    auth.http.nonce = query["nonce"][0]
    return {k: v[0] for k, v in query.items()}


def complete(auth):
    query = begin(auth)
    auth.finish({"state": query["state"], "code": "fictional-code", "client_id": "oaiapp_fictional"})
    return query


def test_real_signature_pkce_state_nonce_client_binding_and_no_frontend_tokens(auth):
    query = complete(auth)
    assert query["client_id"] == "dynamic_agent_client" and query["agent_name_hint"] == "Ryoshu"
    assert query["code_challenge_method"] == "S256"
    issued = next(c[2]["data"] for c in auth.http.calls if c[1] == oauth.TOKEN)
    assert issued["client_id"] == "oaiapp_fictional" and issued["redirect_uri"] == query["redirect_uri"]
    status = auth.status()
    assert status["profiles"][0]["signed_in"] and status["profiles"][0]["plan_enabled"]
    assert "fictional-access" not in json.dumps(status) and "id_token" not in json.dumps(status)
    with pytest.raises(ChatConnectionError):
        auth.finish({"state": query["state"], "code": "replay", "client_id": "oaiapp_fictional"})
    key = status["active"]
    later = begin(auth, key)
    assert later["ext_agent_host_id"] == query["ext_agent_host_id"]
    assert later["client_id"] == "oaiapp_fictional" and "agent_name_hint" not in later and "id_token_hint" not in later
    with pytest.raises(ChatConnectionError):
        auth.finish({"state": later["state"], "code": "x", "client_id": "oaiapp_other"})
    assert auth.access() == "fictional-access"  # failed new login preserves the active session


@pytest.mark.parametrize("claims", [{"iss": "https://other.invalid"}, {"aud": "other-client"}, {"nonce": "wrong"}, {"exp": 0}, {"sub": ""}])
def test_invalid_id_identity_never_stored(auth, claims):
    query = begin(auth)
    auth.http.token_values["id_token"] = auth.http.id_token(**claims)
    with pytest.raises(ChatConnectionError):
        auth.finish({"state": query["state"], "code": "fictional-code", "client_id": "oaiapp_fictional"})
    assert not auth.status()["profiles"]


def test_wrong_signature_and_state_no_token_exchange(auth):
    query = begin(auth)
    with pytest.raises(ChatConnectionError):
        auth.finish({"state": "wrong", "code": "x", "client_id": "oaiapp_fictional"})
    assert not auth.http.calls
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    payload = jwt.decode(auth.http.id_token(), options={"verify_signature": False})
    auth.http.token_values["id_token"] = jwt.encode(payload, key, algorithm="RS256", headers={"kid": "test-signing-key"})
    with pytest.raises(ChatConnectionError):
        auth.finish({"state": query["state"], "code": "x", "client_id": "oaiapp_fictional"})
    assert not auth.status()["profiles"]


def test_failed_first_exchange_retries_issued_client_without_trusting_identity(auth):
    query = begin(auth)
    normal = auth.http.json
    def unavailable(method, url, **kw):
        if url == oauth.TOKEN:
            raise ChatConnectionError("invalid_grant")
        return normal(method, url, **kw)
    auth.http.json = unavailable
    with pytest.raises(ChatConnectionError):
        auth.finish({"state": query["state"], "code": "fictional", "client_id": "oaiapp_fictional"})
    assert auth.status()["registration_pending"]
    assert not auth.status()["profiles"] and not auth.status()["active"]
    retry = begin(auth)
    assert retry["client_id"] == "oaiapp_fictional" and "agent_name_hint" not in retry
    assert retry["ext_agent_host_id"] == query["ext_agent_host_id"]
    auth.reset_registration()
    assert not auth.status()["registration_pending"]
    assert begin(auth)["client_id"] == "dynamic_agent_client"


def test_old_callback_cleanup_does_not_close_a_new_sign_in_attempt(auth):
    old = begin(auth)
    new = begin(auth)
    auth.close(old["state"])
    assert auth.pending["state"] == new["state"] and auth.listener is not None


def test_login_timeout_explains_retry_and_rejects_the_expired_callback(auth):
    query = begin(auth)
    timer = auth.timer
    timer.function(*timer.args, **timer.kwargs)
    status = auth.status()
    assert not status["pending"] and not status["profiles"]
    assert "有効時間" in status["message"] and "やり直" in status["message"]
    assert auth.listener is None
    with pytest.raises(ChatConnectionError):
        auth.finish({"state": query["state"], "code": "expired", "client_id": "oaiapp_fictional"})
    assert not auth.http.calls


def test_old_login_timer_cannot_replace_a_new_attempt_or_its_message(auth):
    begin(auth)
    old_timer = auth.timer
    latest = begin(auth)
    current_message = auth.status()["message"]
    old_timer.function(*old_timer.args, **old_timer.kwargs)
    assert auth.pending["state"] == latest["state"]
    assert auth.status()["message"] == current_message and auth.listener is not None


def test_login_timer_cannot_report_expiry_after_successful_identity_validation(auth):
    query = begin(auth)
    timer = auth.timer
    auth.finish({"state": query["state"], "code": "fictional-code", "client_id": "oaiapp_fictional"})
    message = auth.status()["message"]
    timer.function(*timer.args, **timer.kwargs)
    status = auth.status()
    assert status["profiles"][0]["signed_in"]
    assert status["message"] == message and "有効時間" not in message


def test_cancelling_login_updates_the_visible_status_and_allows_retry(app):
    with TestClient(app) as client:
        assert client.post("/api/chat/connect", headers=HEADERS, json={}).status_code == 200
        assert client.get("/api/chat/connection").json()["pending"]
        assert client.post("/api/chat/cancel-login", headers=HEADERS).status_code == 200
        status = client.get("/api/chat/connection").json()
        assert not status["pending"] and "終了しました" in status["message"]
        assert client.post("/api/chat/connect", headers=HEADERS, json={}).status_code == 200
        status = client.get("/api/chat/connection").json()
        assert status["pending"] and "終了しました" not in status["message"]


def test_missing_oauth_dependency_explains_setup_before_opening_listener(auth, monkeypatch):
    monkeypatch.setattr(oauth.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(ChatConnectionError, match="初回セットアップ"):
        auth.start()
    assert auth.listener is None and not auth.pending and not auth.http.calls


def test_corrupt_pending_registration_is_rejected_without_overwriting_the_vault(auth):
    auth.vault.write({"host_id": "urn:uuid:fictional", "profiles": {}, "active": None, "registration": "damaged"})
    with pytest.raises(ChatConnectionError, match="認証情報を読めません"):
        auth.status()


@pytest.mark.parametrize("output", [[None], [{"content": [None]}], [{"content": [{"type": "output_text"}]}], "invalid-list"])
def test_malformed_completed_stream_is_a_safe_error(monkeypatch, output):
    event = {"type": "response.completed", "response": {"status": "completed", "output": output}}
    factory = httpx.Client
    monkeypatch.setattr(oauth, "openai_connection", lambda host: nullcontext())
    monkeypatch.setattr(httpx, "Client", lambda **kw: factory(**kw, transport=httpx.MockTransport(lambda r: httpx.Response(200, content=("data: " + json.dumps(event) + "\n\n").encode()))))
    with pytest.raises(ChatConnectionError):
        oauth.OpenAIHTTP().generate("fictional", {})


def test_network_exception_is_scoped_to_only_the_openai_thread_and_https(monkeypatch):
    import socket
    from concurrent.futures import ThreadPoolExecutor
    from receipt_ledger.privacy import _audit, openai_connection
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.7", 443))])
    def blocked():
        with pytest.raises(PermissionError):
            _audit("socket.connect", (None, ("203.0.113.7", 443)))
    with openai_connection("api.openai.com"):
        _audit("socket.connect", (None, ("203.0.113.7", 443)))
        _audit("socket.getaddrinfo", (b"api.openai.com", 443))
        for host in ("example.invalid", "auth.openai.com"):
            with pytest.raises(PermissionError):
                _audit("socket.getaddrinfo", (host, 443))
        with pytest.raises(PermissionError):
            _audit("socket.connect", (None, ("203.0.113.7", 80)))
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(blocked).result()
    blocked()
    with pytest.raises(PermissionError):
        with openai_connection("other.invalid"):
            pass


def test_declined_plan_never_infers_and_refresh_keeps_rotating_tokens_atomic(auth):
    auth.http.scope = "openid profile email"
    complete(auth)
    with pytest.raises(ChatConnectionError, match="利用の許可"):
        auth.access()
    # The issued mapping survives sign out; the host ID is not reset.
    key = auth.status()["active"]
    host = auth.vault.read()["host_id"]
    auth.disconnect(key)
    assert not auth.status()["profiles"][0]["signed_in"]
    assert auth.vault.read()["host_id"] == host
    auth.http.scope = oauth.SCOPES
    complete(auth)
    data = auth.vault.read()
    data["profiles"][key]["expires_at"] = 0
    auth.vault.write(data)
    auth.http.token_values = {"access_token": "rotated-access", "refresh_token": "rotated-refresh"}
    assert auth.access() == "rotated-access"
    saved = auth.vault.read()["profiles"][key]
    assert saved["refresh_token"] == "rotated-refresh" and saved["expires_at"] > time.time()
    data = auth.vault.read(); data["profiles"][key]["expires_at"] = 0; auth.vault.write(data)
    auth.http.refresh_error = "invalid_grant"
    with pytest.raises(ChatConnectionError):
        auth.access()
    assert not auth.vault.read()["profiles"][key].get("access_token")
    assert auth.vault.read()["profiles"][key]["client_id"] == "oaiapp_fictional"


@pytest.mark.parametrize("terminal", ["response.completed", "response.failed", "response.incomplete", "truncated", "completed_done", "completed_no_blank", "error_top_code"])
def test_stream_must_complete_even_after_text_delta(monkeypatch, terminal):
    events = [{"type": "response.output_text.delta", "delta": '{"message":"案","edits":[]}'}]
    if terminal != "truncated":
        kind = "response.completed" if terminal.startswith("completed_") else terminal
        events.append({"type": kind, "response": {"status": "completed" if kind == "response.completed" else "failed", "error": {"code": "subscription_sharing_usage_limit_exceeded"}}})
    if terminal == "error_top_code":
        events[-1] = {"type": "error", "code": "subscription_sharing_usage_limit_exceeded"}
    content = "".join("data: " + json.dumps(e) + "\n\n" for e in events).encode()
    if terminal == "completed_done":
        content += b"data: [DONE]\n\n"
    if terminal == "completed_no_blank":
        content = content.rstrip(b"\n")
    factory = httpx.Client
    monkeypatch.setattr(oauth, "openai_connection", lambda host: nullcontext())
    monkeypatch.setattr(httpx, "Client", lambda **kw: factory(**kw, transport=httpx.MockTransport(lambda r: httpx.Response(200, content=content))))
    if terminal == "response.completed" or terminal.startswith("completed_"):
        assert json.loads(oauth.OpenAIHTTP().generate("fictional", {}))["edits"] == []
    else:
        with pytest.raises(ChatConnectionError, match="利用上限" if terminal == "error_top_code" else None):
            oauth.OpenAIHTTP().generate("fictional", {})
