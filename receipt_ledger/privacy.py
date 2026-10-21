"""Restrict the running app process to loopback network connections.

Setup downloads run as a separate process and do not install this audit hook.
"""
import sys

LOOPBACK = {"127.0.0.1", "::1", "localhost", b"127.0.0.1", b"::1", b"localhost"}


def _audit(event, args):
    if event == "socket.connect":
        address = args[1]
        if isinstance(address, tuple) and address[0] not in LOOPBACK:
            raise PermissionError("Ryoshuは外部ネットワークへ接続しません")
    elif event in {"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr"}:
        host = args[0]
        if host is not None and host not in LOOPBACK:
            raise PermissionError("Ryoshuは外部ホストを照会しません")


def install():
    sys.addaudithook(_audit)
