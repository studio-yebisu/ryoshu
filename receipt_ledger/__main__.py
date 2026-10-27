import sys

# Standard library only up to crt_bootstrap.activate(): no native extension may load before Ryoshu's
# verified Microsoft C++ runtime is mapped (uvicorn, onnxruntime, PIL, NumPy, PyPDFium, SQLCipher).
from . import config
from . import crt_bootstrap
from .privacy import install

if __name__ == "__main__":
    if sys.version_info[:2]!=(3,12) or sys.version_info<(3,12,15):
        raise SystemExit("Ryoshuには64bitのPython 3.12.15以降の3.12系が必要です。「初回セットアップ.cmd」で専用の実行環境を準備してください。")
    # Not a model gate: an unprepared runtime is reported and manual ledger/CSV keep working.
    crt_bootstrap.activate(config.RUNTIME_ROOT, config.RUNTIME_PIN)
    install()
    import uvicorn
    from .server import create_app
    from .protection import clean_work, clean_incomplete_restores, private_directory
    from .runtime import LlamaRuntime
    from filelock import FileLock
    private_directory(config.DATA_DIR)
    with FileLock(str(config.DATA_DIR / ".ryoshu-open.lock"), timeout=0):
        clean_work(config.DATA_DIR)
        clean_incomplete_restores(config.DEFAULT_ROOT/"restored")
        # This launch owns one runtime; it starts lazily in the receipt worker.
        runtime = LlamaRuntime()
        try:
            uvicorn.run(create_app(runtime=runtime), host=config.HOST, port=config.PORT, log_level="info", access_log=False)
        finally:
            runtime.close()
