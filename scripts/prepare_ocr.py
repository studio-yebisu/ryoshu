"""Download OCR weights during setup, before any private receipt is processed."""
import importlib.metadata
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from receipt_ledger import config, crt_bootstrap

# map the verified app-owned Microsoft C++ runtime before onnxruntime/NumPy/PIL load (same call as the app)
crt_bootstrap.activate(config.RUNTIME_ROOT, config.RUNTIME_PIN)
os.environ.pop("HF_HUB_OFFLINE", None)
import indra_ocr

converter = indra_ocr.load(threads=4, mode="auto", furniture=True)
print(json.dumps({"ocr_version": importlib.metadata.version("indra-ocr"), "providers": converter.ocr.det.get_providers(), "cache": str(config.CACHE_DIR)}, ensure_ascii=False))
