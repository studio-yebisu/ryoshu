"""Called by the native launcher; output stays in its process, never a log."""
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from receipt_ledger.session import main

if __name__ == "__main__":
    main()
