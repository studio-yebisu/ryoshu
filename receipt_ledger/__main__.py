import uvicorn
import sys

from . import config
from .privacy import install

if __name__ == "__main__":
    if sys.version_info[:2]!=(3,12) or sys.version_info<(3,12,15):
        raise SystemExit("Ryoshuには64bitのPython 3.12.15以降の3.12系が必要です。「初回セットアップ.cmd」で専用の実行環境を準備してください。")
    install()
    from .server import create_app
    from .protection import clean_work, clean_incomplete_restores, private_directory
    from filelock import FileLock
    private_directory(config.DATA_DIR)
    with FileLock(str(config.DATA_DIR / ".ryoshu-open.lock"), timeout=0):
        clean_work(config.DATA_DIR)
        clean_incomplete_restores(config.DEFAULT_ROOT/"restored")
        uvicorn.run(create_app(), host=config.HOST, port=config.PORT, log_level="info", access_log=False)
