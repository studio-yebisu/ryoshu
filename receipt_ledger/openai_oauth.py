"""Optional Sign in with ChatGPT for OSS, using the documented public endpoints.

No Codex/ChatGPT credential files or environment API keys are read. Tokens stay
in a separate vault (Windows DPAPI; Unix owner-only files), never in SQLite,
backups or browser storage.
"""
import base64
import ctypes
import hashlib
import importlib.util
import json
import os
import re
import secrets
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from filelock import FileLock

from .privacy import openai_connection

ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
AUTHORIZE = ISSUER + "/api/accounts/authorize"
TOKEN = ISSUER + "/api/accounts/oauth/token"
DISCOVERY = ISSUER + "/.well-known/openid-configuration"
JWKS = ISSUER + "/.well-known/jwks.json"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PERMISSION = "chatgpt.tokens.use.direct"
TOKEN_KEYS = ("access_token", "refresh_token", "id_token", "expires_at", "earliest_refresh_at", "scopes")


class ChatConnectionError(Exception):
    """A user-safe diagnostic. Never include tokens or raw provider responses."""


def _protect(raw, decrypt=False):
    if os.name != "nt":
        return raw
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    if decrypt:
        call = crypt.CryptUnprotectData
        call.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = call(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        call = crypt.CryptProtectData
        call.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = call(ctypes.byref(source), "Ryoshu OAuth", None, None, None, 1, ctypes.byref(target))
    if not ok:
        raise ChatConnectionError("認証情報を保護・復号できません。このPCの同じ利用者で接続し直してください。")
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


class Vault:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / "openai-session.bin"

    def lock(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        return FileLock(self.root / "openai-session.lock", timeout=3)

    def read(self):
        if not self.path.exists():
            return {"host_id": "", "active": None, "profiles": {}}
        try:
            data = json.loads(_protect(self.path.read_bytes(), decrypt=True))
            if not isinstance(data, dict) or not isinstance(data.get("profiles"), dict) or "active" not in data or "host_id" not in data:
                raise ValueError("vault shape")
            if not isinstance(data.get("registration", {}), dict) or any(
                not isinstance(p, dict) or not isinstance(p.get("client_id"), str) or not isinstance(p.get("subject"), str)
                for p in data["profiles"].values()
            ):
                raise ValueError("registration shape")
            return data
        except (OSError, ValueError) as exc:
            raise ChatConnectionError("認証情報を読めません。保存先の権限を確認してください。") from exc

    def write(self, data):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        raw = _protect(json.dumps(data, ensure_ascii=False).encode("utf-8"))
        fd, name = tempfile.mkstemp(prefix="session-", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as output:
                os.chmod(name, 0o600)
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)


def _failure(response=None, event=None):
    status = response.status_code if response is not None else None
    body = event or {}
    if response is not None:
        try:
            body = response.json()
        except ValueError:
            body = {}
    error = body.get("error", body) if isinstance(body, dict) else {}
    code = error.get("code", "") if isinstance(error, dict) else error
    code = code if isinstance(code, str) and re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", code) else ""
    if "usage_limit_exceeded" in code or status == 429:
        message = "ChatGPTの利用上限に達しました。ChatGPTの使用量設定を確認してください。"
    elif code == "subscription_sharing_user_not_eligible":
        message = "このアカウント・ワークスペースではChatGPTプランの連携を利用できません。"
    elif status == 401:
        message = "接続を確認できません。ChatGPTに接続し直してください。"
    elif code in {"invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired", "refresh_token_invalidated", "refresh_token_invalid", "refresh_token_reused"}:
        message = "認証の有効期限が切れたか、接続が解除されています。ChatGPTに接続し直してください。"
    elif status == 403:
        message = "ChatGPT連携の権限・利用地域・アカウント設定を確認してください。"
    else:
        message = "OpenAIの応答を受け取れませんでした。時間をおいて再度お試しください。"
    rid = response.headers.get("x-request-id", "") if response is not None else ""
    rid = rid if re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", rid) else ""
    info = " / ".join(str(v) for v in [status, code, rid] if v)
    return ChatConnectionError(message + (f"（{info}）" if info else ""))


class OpenAIHTTP:
    def json(self, method, url, **kwargs):
        host = urlsplit(url).hostname
        try:
            with openai_connection(host), httpx.Client(trust_env=False, follow_redirects=False, timeout=25) as client:
                response = client.request(method, url, **kwargs)
                if not response.is_success:
                    raise _failure(response)
                value = response.json() if response.content else {}
                if not isinstance(value, dict):
                    raise ValueError("response shape")
                return value
        except (httpx.HTTPError, ValueError, OSError) as exc:
            raise ChatConnectionError("OpenAIに接続できません。ネット接続を確認してください。帳簿は変更していません。") from exc

    def generate(self, access_token, body):
        text, completed, size = [], False, 0
        def consume(data):
            nonlocal completed
            serialized = "\n".join(data)
            if serialized == "[DONE]":
                return
            event = json.loads(serialized)
            if not isinstance(event, dict):
                raise ValueError("event shape")
            kind = event.get("type")
            if kind == "response.output_text.delta":
                if not isinstance(event.get("delta"), str):
                    raise ValueError("text delta shape")
                text.append(event.get("delta", ""))
            elif kind in ("error", "response.failed", "response.incomplete"):
                raise _failure(event=event.get("response", event))
            elif kind == "response.completed":
                result = event.get("response", {})
                if not isinstance(result, dict):
                    raise ValueError("completion shape")
                if result.get("status") != "completed":
                    raise ChatConnectionError("AIの処理が完了していません。帳簿は変更していません。")
                if not text:
                    output = result.get("output", [])
                    if not isinstance(output, list):
                        raise ValueError("output shape")
                    for item in output:
                        if not isinstance(item, dict) or not isinstance(item.get("content", []), list):
                            raise ValueError("content shape")
                        for part in item.get("content", []):
                            if not isinstance(part, dict):
                                raise ValueError("part shape")
                            if part.get("type") == "output_text":
                                if not isinstance(part.get("text"), str):
                                    raise ValueError("text shape")
                                text.append(part["text"])
                completed = True
        try:
            with openai_connection("api.openai.com"), httpx.Client(trust_env=False, timeout=120, follow_redirects=False) as client:
                with client.stream("POST", RESOURCE + "/responses", headers={"Authorization": "Bearer " + access_token}, json=body) as response:
                    if not response.is_success:
                        response.read()
                        raise _failure(response)
                    data = []
                    for line in response.iter_lines():
                        size += len(line)
                        if size > 2_000_000:
                            raise ChatConnectionError("AIの応答が大きすぎます。対象の明細を減らしてください。")
                        if line.startswith("data:"):
                            data.append(line[5:].lstrip())
                        elif not line and data:
                            consume(data)
                            data = []
                    if data:
                        consume(data)
                    if not completed:
                        raise ChatConnectionError("AIの応答が途中で切れました。帳簿は変更していません。")
            return "".join(text)
        except (httpx.HTTPError, ValueError, OSError) as exc:
            raise ChatConnectionError("AIの応答を最後まで確認できませんでした。帳簿は変更していません。") from exc


class OpenAIConnection:
    def __init__(self, root, http=None):
        self.vault = Vault(root)
        self.http = http or OpenAIHTTP()
        self.mutex = threading.RLock()
        self.start_mutex = threading.Lock()
        self.pending = None
        self.listener = None
        self.listener_attempt = None
        self.timer = None
        self.login_message = ""

    def status(self):
        with self.mutex:
            data = self.vault.read()
            profiles = [{"id": key, "label": (p.get("email") or "ChatGPT") + " · " + key[:6],
                         "signed_in": bool(p.get("access_token")),
                         "plan_enabled": PERMISSION in p.get("scopes", [])} for key, p in data["profiles"].items()]
            return {"active": data["active"], "profiles": profiles, "pending": bool(self.pending),
                    "registration_pending": bool(data.get("registration")),
                    "message": self.login_message, "usage_url": "https://chatgpt.com/settings/usage"}

    def close(self, attempt=None, message=None):
        with self.mutex:
            if attempt is not None and self.listener_attempt != attempt:
                return
            if message and self.pending:
                self.login_message = message
            self.pending = None
            listener, self.listener = self.listener, None
            self.listener_attempt = None
            if self.timer:
                self.timer.cancel()
                self.timer = None
        if listener:
            listener.shutdown()
            listener.server_close()

    def start(self, profile_id=None, reconsent=False):
        with self.start_mutex:
            return self._start(profile_id, reconsent)

    def _start(self, profile_id, reconsent):
        if importlib.util.find_spec("jwt") is None or importlib.util.find_spec("cryptography") is None:
            raise ChatConnectionError("ChatGPT接続に必要なライブラリがありません。アプリを停止し、「初回セットアップ.cmd」を実行してから起動し直してください。")
        self.close()
        with self.mutex, self.vault.lock():
            data = self.vault.read()
            data["host_id"] = data["host_id"] or "urn:uuid:" + str(uuid.uuid4())
            self.vault.write(data)
            profile = data["profiles"].get(profile_id) if profile_id else None
            if profile_id and not profile:
                raise ChatConnectionError("接続先を選び直してください。")
            parent = self

            class Callback(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass  # OAuth codes, state and query strings must never enter logs.

                def do_GET(self):
                    url = urlsplit(self.path)
                    if url.path != "/auth/callback":
                        self.send_error(404)
                        return
                    try:
                        query = {k: v[0] for k, v in parse_qs(url.query).items() if len(v) == 1}
                        parent.finish(query)
                        message = "接続の確認が終わりました。Ryoshuの画面へ戻ってください。"
                    except ChatConnectionError:
                        message = "接続を完了できませんでした。Ryoshuの画面で状態を確認してください。"
                    raw = ("<!doctype html><html lang='ja'><meta charset='utf-8'><title>Ryoshu</title><p>" + message + "</p></html>").encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    threading.Thread(target=parent.close, args=(query.get("state"),), daemon=True).start()

            self.listener = ThreadingHTTPServer(("127.0.0.1", 0), Callback)
            self.listener.daemon_threads = True
            redirect = f"http://127.0.0.1:{self.listener.server_port}/auth/callback"
            verifier = secrets.token_urlsafe(48)
            pending = {"state": secrets.token_urlsafe(32), "nonce": secrets.token_urlsafe(32),
                       "verifier": verifier, "redirect": redirect, "deadline": time.time() + 600,
                       "profile_id": profile_id, "client_id": profile["client_id"] if profile else data.get("registration", {}).get("client_id")}
            self.pending = pending
            self.listener_attempt = pending["state"]
            query = {"client_id": pending["client_id"] or "dynamic_agent_client", "ext_agent_host_id": data["host_id"],
                     "response_type": "code", "redirect_uri": redirect, "scope": SCOPES, "resource": RESOURCE,
                     "state": pending["state"], "nonce": pending["nonce"], "code_challenge_method": "S256",
                     "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")}
            if not pending["client_id"]:
                query["agent_name_hint"] = "Ryoshu"
            if profile and profile.get("email"):
                query["login_hint"] = profile["email"]
            if reconsent:
                query["prompt"] = "consent"
            self.login_message = "ブラウザーで接続してください。接続待ちは10分で終了します。"
            threading.Thread(target=self.listener.serve_forever, daemon=True).start()
            self.timer = threading.Timer(600, self.close, args=(pending["state"],), kwargs={
                "message": "接続待ちの有効時間（10分）が過ぎました。チャット画面の接続ボタンからやり直してください。"})
            self.timer.daemon = True
            self.timer.start()
            return {"authorization_url": AUTHORIZE + "?" + urlencode(query)}

    def _identity(self, encoded, client_id, nonce=None):
        try:
            import jwt
        except ImportError as exc:
            raise ChatConnectionError("認証用ライブラリを読み込めません。「初回セットアップ.cmd」で更新してから接続し直してください。") from exc
        try:
            metadata = self.http.json("GET", DISCOVERY)
            if metadata.get("issuer") != ISSUER or metadata.get("jwks_uri") != JWKS:
                raise ValueError("issuer")
            header = jwt.get_unverified_header(encoded)
            if header.get("alg") != "RS256":
                raise ValueError("algorithm")
            keys = self.http.json("GET", JWKS)["keys"]
            key = next(k for k in keys if k.get("kid") == header.get("kid") and k.get("kty") == "RSA")
            claims = jwt.decode(encoded, jwt.PyJWK.from_dict(key, algorithm="RS256"), algorithms=["RS256"],
                                audience=client_id, issuer=ISSUER, leeway=5,
                                options={"require": ["sub", "exp", "iat"]})
            if not isinstance(claims["sub"], str) or not claims["sub"] or (nonce is not None and claims.get("nonce") != nonce):
                raise ValueError("identity")
            return claims
        except (ValueError, KeyError, StopIteration, jwt.PyJWTError) as exc:
            raise ChatConnectionError("OpenAIの署名・本人情報を検証できません。もう一度接続してください。") from exc

    def _tokens(self, old, tokens):
        if not isinstance(tokens.get("access_token"), str) or not tokens["access_token"] or str(tokens.get("token_type", "")).lower() != "bearer":
            raise ChatConnectionError("認証情報を確認できません。接続し直してください。")
        duration = tokens.get("expires_in")
        if type(duration) not in (int, float) or not 0 < duration < 86400:
            raise ChatConnectionError("認証の有効期限を確認できません。")
        if "scope" in tokens and not isinstance(tokens["scope"], str):
            raise ChatConnectionError("利用許可の応答を確認できません。")
        result = {**old, "access_token": tokens["access_token"], "expires_at": time.time() + duration,
                  "scopes": tokens.get("scope", " ".join(old.get("scopes", []))).split()}
        for key in ("refresh_token", "id_token", "earliest_refresh_at"):
            if key in tokens:
                result[key] = tokens[key]
        return result

    def finish(self, query):
        with self.mutex:
            pending = self.pending
            if not pending or pending["deadline"] <= time.time() or not secrets.compare_digest(query.get("state", ""), pending["state"]):
                raise ChatConnectionError("接続待ちを確認できません。接続をやり直してください。")
            self.pending = None  # The verified attempt is single-use, even on failure.
            try:
                if query.get("error"):
                    raise ChatConnectionError("接続が許可されませんでした。ローカル機能は引き続き使えます。")
                client_id = query.get("client_id") or pending["client_id"]
                if not client_id or client_id == "dynamic_agent_client" or not re.fullmatch(r"[a-zA-Z0-9_-]{3,200}", client_id):
                    raise ChatConnectionError("アプリの登録が完了していません。接続し直してください。")
                if pending["client_id"] and client_id != pending["client_id"]:
                    raise ChatConnectionError("選んだ接続先と異なる登録が返されました。")
                code = query.get("code", "")
                if not code or len(code) > 8192:
                    raise ChatConnectionError("認証コードを確認できません。")
                if not pending["profile_id"]:
                    # Keep only the unverified client registration, never an identity
                    # or credentials. Retry invalid_grant using this issued client ID.
                    with self.vault.lock():
                        data = self.vault.read()
                        data["registration"] = {"client_id": client_id}
                        self.vault.write(data)
                tokens = self.http.json("POST", TOKEN, data={"grant_type": "authorization_code", "client_id": client_id,
                    "code": code, "code_verifier": pending["verifier"], "redirect_uri": pending["redirect"], "resource": RESOURCE})
                identity = self._identity(tokens.get("id_token", ""), client_id, pending["nonce"])
                key = hashlib.sha256(client_id.encode()).hexdigest()[:24]
                with self.vault.lock():
                    data = self.vault.read()
                    prior = data["profiles"].get(key)
                    if prior and prior["subject"] != identity["sub"]:
                        raise ChatConnectionError("以前の接続先と本人情報が一致しません。")
                    if pending["profile_id"] and pending["profile_id"] != key:
                        raise ChatConnectionError("選んだ接続先が一致しません。")
                    profile = {"client_id": client_id, "subject": identity["sub"], "email": str(identity.get("email", ""))[:320]}
                    data["profiles"][key] = self._tokens(profile, tokens)
                    data["active"] = key
                    if data.get("registration", {}).get("client_id") == client_id:
                        data.pop("registration", None)
                    self.vault.write(data)
                self.login_message = "接続しました。" if PERMISSION in tokens.get("scope", "").split() else "ログインしましたが、ChatGPTプラン利用の許可がありません。「利用を許可」で再接続してください。"
            except ChatConnectionError as exc:
                self.login_message = str(exc)
                raise

    def reset_registration(self):
        self.close()
        with self.mutex, self.vault.lock():
            data = self.vault.read()
            data.pop("registration", None)
            self.vault.write(data)

    def select(self, profile_id):
        with self.mutex, self.vault.lock():
            data = self.vault.read()
            if profile_id not in data["profiles"]:
                raise ChatConnectionError("接続先を選び直してください。")
            data["active"] = profile_id
            self.vault.write(data)

    def access(self, expected_profile=None):
        with self.mutex, self.vault.lock():
            data = self.vault.read()
            key = data["active"]
            if expected_profile is not None and key != expected_profile:
                raise ChatConnectionError("接続先が変わりました。選び直してから送信してください。")
            profile = data["profiles"].get(key, {})
            if not profile.get("access_token"):
                raise ChatConnectionError("先にChatGPTへ接続してください。")
            if PERMISSION not in profile.get("scopes", []):
                raise ChatConnectionError("ChatGPTプラン利用の許可が必要です。「利用を許可」で接続し直してください。")
            if profile["expires_at"] <= time.time() + 60:
                if not profile.get("refresh_token"):
                    raise ChatConnectionError("認証の有効期限が切れました。接続し直してください。")
                try:
                    tokens = self.http.json("POST", TOKEN, data={"grant_type": "refresh_token", "client_id": profile["client_id"],
                        "refresh_token": profile["refresh_token"], "resource": RESOURCE})
                    if tokens.get("id_token"):
                        identity = self._identity(tokens["id_token"], profile["client_id"])
                        if identity["sub"] != profile["subject"]:
                            raise ChatConnectionError("更新時の本人情報が一致しません。")
                    profile = self._tokens(profile, tokens)
                    data["profiles"][key] = profile
                    self.vault.write(data)
                except ChatConnectionError as exc:
                    if any(code in str(exc) for code in ("invalid_grant", "invalid_refresh_token", "refresh_token_expired", "refresh_token_invalidated", "refresh_token_reused", "token_expired")):
                        for name in TOKEN_KEYS:
                            profile.pop(name, None)
                        self.vault.write(data)
                    raise
            return profile["access_token"]

    def models(self, expected_profile=None):
        data = self.http.json("GET", RESOURCE + "/models", headers={"Authorization": "Bearer " + self.access(expected_profile)})
        return [{"id": m["slug"], "name": m.get("display_name", m["slug"])} for m in data.get("models", [])
                if m.get("visibility") == "list" and isinstance(m.get("slug"), str)]

    def disconnect(self, profile_id):
        self.close()
        revoked = True
        with self.mutex, self.vault.lock():
            data = self.vault.read()
            profile = data["profiles"].get(profile_id)
            if not profile:
                raise ChatConnectionError("接続先を選び直してください。")
            if profile.get("refresh_token"):
                try:
                    endpoint = self.http.json("GET", DISCOVERY).get("revocation_endpoint")
                    if endpoint != ISSUER + "/api/accounts/oauth/revoke":
                        raise ChatConnectionError("失効先を確認できません。")
                    self.http.json("POST", endpoint, data={"token": profile["refresh_token"], "token_type_hint": "refresh_token", "client_id": profile["client_id"]})
                except ChatConnectionError:
                    revoked = False
            for name in TOKEN_KEYS:
                profile.pop(name, None)
            if data["active"] == profile_id:
                data["active"] = None
            self.vault.write(data)
        return {"revocation_confirmed": revoked, "message": "接続を解除しました。" if revoked else "このPCの接続を解除しました。OpenAI側の失効は確認できなかったため、ChatGPTの設定でもアプリの接続を解除してください。"}
