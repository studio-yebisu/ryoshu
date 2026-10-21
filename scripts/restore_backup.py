"""Used by the Windows restore file picker; emits one machine-readable JSON result."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from receipt_ledger.restore import restore_backup

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        result = restore_backup(args.archive, args.destination)
    except Exception as exc:
        print("バックアップを復元できませんでした：" + str(exc), file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(result, ensure_ascii=False))
