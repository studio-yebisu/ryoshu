import uvicorn

from . import config
from .server import create_app
from .privacy import install

if __name__ == "__main__":
    install()
    uvicorn.run(create_app(), host=config.HOST, port=config.PORT, log_level="info", access_log=False)
