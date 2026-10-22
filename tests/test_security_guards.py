import os
from pathlib import Path
import subprocess
import sys

import pytest

from support import ready_store as Store


def test_actual_python_udp_tcp_and_dns_are_blocked_before_external_transmission():
    program = """
import socket
from receipt_ledger.privacy import install
install()
def denied(operation):
    try:
        operation()
    except PermissionError:
        return
    raise AssertionError('External operation was not rejected')
denied(lambda: socket.getaddrinfo('example.com', 443))
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
    denied(lambda: stream.connect(('203.0.113.10', 9)))
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as datagram:
    denied(lambda: datagram.sendto(b'fictional audit probe', ('203.0.113.10', 9)))
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
    receiver.bind(('127.0.0.1', 0))
    receiver.settimeout(2)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        sender.sendto(b'local probe', receiver.getsockname())
    assert receiver.recv(32) == b'local probe'
print('external UDP/TCP/DNS rejected; loopback UDP allowed')
"""
    result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, env={**os.environ, "PYTHONUTF8": "1"}, timeout=10)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "rejected" in result.stdout


def test_failed_restore_destination_is_never_opened_as_a_fresh_ledger(tmp_path):
    (tmp_path / "RESTORE_FAILED.txt").write_text("fictional failed restore", "utf-8")
    data = tmp_path / "data"
    with pytest.raises(ValueError, match="復元が完了していない"):
        Store(data)
    assert not data.exists()


def test_seed_history_identifier_cannot_read_outside_the_known_history_directory(tmp_path, monkeypatch):
    store = Store(tmp_path)
    with store.connect() as db:
        db.execute("UPDATE catalog_meta SET value=? WHERE key='seed_version'", ("../../outside-fixture",))
    actual = Path.is_file
    def checked(path):
        assert ".." not in path.parts, "History lookup escaped its directory"
        return actual(path)
    monkeypatch.setattr(Path, "is_file", checked)
    with pytest.raises(ValueError, match="識別情報"):
        Store(tmp_path)
