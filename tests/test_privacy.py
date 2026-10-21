import subprocess
import sys


def test_app_process_blocks_real_outbound_connect_before_network_io():
    script = """
import socket
from receipt_ledger.privacy import install
install()
blocked = 0
try:
    socket.create_connection(('1.1.1.1', 443), timeout=1)
except PermissionError:
    blocked += 1
try:
    socket.getaddrinfo('example.com', 443)
except PermissionError:
    blocked += 1
assert blocked == 2, blocked
print('external connect and DNS blocked')
"""
    result = subprocess.run([sys.executable, "-c", script], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "blocked" in result.stdout
