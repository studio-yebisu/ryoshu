"""Download OCR weights during setup, before any private receipt is processed."""
import importlib.metadata
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from receipt_ledger import config

os.environ.pop("HF_HUB_OFFLINE", None)
import indra_ocr

converter = indra_ocr.load(threads=4, mode="auto", furniture=True)
print(json.dumps({"ocr_version": importlib.metadata.version("indra-ocr"), "providers": converter.ocr.det.get_providers(), "cache": str(config.CACHE_DIR)}, ensure_ascii=False))
