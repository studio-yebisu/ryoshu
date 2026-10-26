"""Used by the Windows restore file picker; emits one machine-readable JSON result."""
import argparse
import json
from pathlib import Path
import sys
import getpass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from receipt_ledger.restore import restore_backup

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        if sys.stdin.isatty():
            password = getpass.getpass("復元用パスワードを入力（入力内容は表示されません）：")
        else:
            # The native password dialog sends it over stdin, never argv/env or logs.
            password = sys.stdin.buffer.readline(4096).decode("utf-8").rstrip("\r\n")
        result = restore_backup(args.archive, args.destination, password=password)
    except Exception as exc:
        print("復元を完了できませんでした。現在の帳簿は変更していません：" + str(exc), file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(result, ensure_ascii=True))
