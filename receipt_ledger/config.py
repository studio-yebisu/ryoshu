import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
SAMPLE_DIR = APP_DIR / "samples" / "receipts-100-v2"
DEFAULT_ROOT = Path.home() / "StudioYebisu" / "ReceiptLedger"
DATA_DIR = Path(os.environ.get("RECEIPT_LEDGER_DATA", DEFAULT_ROOT / "data")).resolve()
CACHE_DIR = DEFAULT_ROOT / "cache"
# Local inference: one source pin, files under a task root (override for isolated QA before import).
RUNTIME_PIN = Path(__file__).resolve().parent / "runtime_pin.json"
# Absolute but not resolved: a linked root must stay visible so the runtime can refuse it.
RUNTIME_ROOT = Path(os.path.abspath(os.environ.get("RECEIPT_LEDGER_RUNTIME_ROOT", DEFAULT_ROOT)))
RUNTIME_THREADS = 6
RUNTIME_CTX = 8192
HOST = "127.0.0.1"
PORT = int(os.environ.get("RECEIPT_LEDGER_PORT", "8766"))
MAX_BYTES = 20 * 1024 * 1024
MAX_PAGES = 10
MAX_FILES = 50
MAX_SPLIT_PAGES = 300
MAX_REQUEST_BYTES = 64 * 1024 * 1024

# No credentials, telemetry, or remote inference are used by this application.
os.environ["HF_HOME"] = str(CACHE_DIR / "huggingface")
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"

PAYMENTS = {
    "owner": {"label": "個人のお金で立替", "credit": "事業主借"},
    "cash": {"label": "事業用の現金", "credit": "現金"},
    "bank": {"label": "事業用の預金", "credit": "普通預金"},
    "card": {"label": "事業用カード（未払計上）", "credit": "未払金"},
}
TAX_LABELS = {"unknown": "記載なし・未確認", "10": "10%の記載", "8": "8%の記載", "mixed": "8%・10%の混在", "exempt": "非課税・不課税の記載"}
