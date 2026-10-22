"""Restrict the running app process to loopback network connections.

Setup downloads run as a separate process and do not install this audit hook.
"""
import sys
import socket
from contextlib import contextmanager
from contextvars import ContextVar

LOOPBACK = {"127.0.0.1", "::1", "localhost", b"127.0.0.1", b"::1", b"localhost"}
OPENAI_HOSTS = frozenset({"auth.openai.com", "api.openai.com"})
_permission = ContextVar("ryoshu_openai_network", default=None)


@contextmanager
def openai_connection(host):
    """Only the explicit OAuth/chat request may resolve and contact this HTTPS host.

    The permission is thread/context local. OCR, Ollama and unrelated requests
    retain the loopback-only policy, including while a chat request is running.
    """
    if host not in OPENAI_HOSTS:
        raise PermissionError("許可されていない接続先です")
    token = _permission.set((host, frozenset()))
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        _permission.set((host, frozenset(item[4][0] for item in addresses)))
        yield
    finally:
        _permission.reset(token)


def _audit(event, args):
    if event == "socket.connect":
        address = args[1]
        allowed = _permission.get()
        if isinstance(address, tuple) and address[0] not in LOOPBACK and not (
            allowed and address[0] in allowed[1] and address[1] == 443
        ):
            raise PermissionError("Ryoshuは外部ネットワークへ接続しません")
    elif event in {"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr"}:
        host = args[0]
        if isinstance(host, bytes):
            host = host.decode("ascii", errors="replace")
        allowed = _permission.get()
        if host is not None and host not in LOOPBACK and not (allowed and host == allowed[0]):
            raise PermissionError("Ryoshuは外部ホストを照会しません")


def install():
    sys.addaudithook(_audit)
