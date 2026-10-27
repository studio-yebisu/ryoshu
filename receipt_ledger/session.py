"""Per-launch localhost authentication. Bootstrap secrets never enter query strings."""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import threading
import time

from . import __version__
from .protection import ProtectionError, atomic_write, dpapi, _json

SESSION_MAGIC = b"RYOSHU-SESSION\x01"


def bootstrap(secret):
    payload = str(int(time.time())) + "." + secrets.token_hex(16)
    return payload + "." + hmac.digest(secret, payload.encode(), "sha256").hex()


class Sessions:
    def __init__(self, root, port):
        self.secret = secrets.token_bytes(32)
        self.instance = secrets.token_hex(16)
        self.used = {}
        self.tokens = {}
        self.lock = threading.Lock()
        self.root = Path(root)
        self.port = port

    def publish(self):
        record = {"secret": self.secret.hex(), "instance": self.instance, "port": self.port,
                  "pid": os.getpid(), "app_version": __version__, "data_dir": str(self.root),
                  "source_dir": str(Path(__file__).resolve().parents[1])}
        atomic_write(self.root / ".ryoshu-session.dpapi",
                     SESSION_MAGIC + dpapi(json.dumps(record).encode()))

    def mint_bootstrap(self):
        return bootstrap(self.secret)

    def exchange(self, value):
        now = time.time()
        try:
            if not isinstance(value, str) or len(value) > 160:
                raise ValueError()
            timestamp, nonce, signature = value.split(".")
            payload = timestamp + "." + nonce
            if len(nonce) != 32 or len(signature) != 64 or abs(now - int(timestamp)) > 120:
                raise ValueError()
            if not hmac.compare_digest(signature, hmac.digest(self.secret, payload.encode(), "sha256").hex()):
                raise ValueError()
            with self.lock:
                self.used = {k:v for k,v in self.used.items() if now-v < 125}
                self.tokens = {k:v for k,v in self.tokens.items() if v > now}
                if value in self.used or len(self.used) >= 128:
                    raise ValueError()
                if len(self.tokens)>=32:
                    del self.tokens[min(self.tokens,key=self.tokens.get)]
                self.used[value] = now
                token = secrets.token_urlsafe(32)
                self.tokens[hashlib.sha256(token.encode()).digest()] = now + 12 * 3600
                return token
        except (ValueError, TypeError):
            raise ProtectionError("起動リンクが無効か有効期限切れです。「Ryoshu.cmd」から新しいリンクで開き直してください。") from None

    def authorized(self, header):
        if not isinstance(header, str) or not header.startswith("Bearer ") or not 40 <= len(header) <= 80:
            return False
        digest = hashlib.sha256(header[7:].encode()).digest()
        with self.lock:
            expiry = self.tokens.get(digest, 0)
            if expiry <= time.time():
                return False
            self.tokens[digest] = time.time() + 12 * 3600
            return True


def open_record(root, port):
    path = Path(root).resolve() / ".ryoshu-session.dpapi"
    if path.is_symlink() or path.stat().st_size > 16384:
        raise ProtectionError("起動情報の形式を確認できません。「Ryoshu.cmd」から起動し直してください。")
    raw = path.read_bytes()
    if not raw.startswith(SESSION_MAGIC):
        raise ProtectionError("この保存先の起動情報ではありません。「Ryoshu.cmd」から起動し直してください。")
    record = _json(dpapi(raw[len(SESSION_MAGIC):], decrypt=True))
    if record.get("port") != port or Path(record.get("data_dir", "")).resolve() != Path(root).resolve():
        raise ProtectionError("起動情報の帳簿・ポートが現在の指定と一致しません。「停止.cmd」で終了してから開き直してください。")
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    record = open_record(args.data_dir, args.port)
    value = bootstrap(bytes.fromhex(record.pop("secret")))
    record["url"] = f"http://127.0.0.1:{args.port}/#open={value}"
    print(json.dumps(record))


if __name__ == "__main__":
    main()
