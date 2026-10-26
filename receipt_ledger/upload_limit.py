"""Bound multipart disk spooling as well as individual file reads."""
import asyncio
from starlette.responses import JSONResponse
from starlette.formparsers import MultiPartException, MultiPartParser


class MemoryMultipartParser(MultiPartParser):
    # The ASGI receive limit is smaller; no file can roll over into plaintext temp.
    spool_max_size = 65 * 1024 * 1024


class UploadTooLarge(MultiPartException):
    pass


class UploadSizeLimit:
    def __init__(self, app, max_bytes):
        self.app = app
        self.max_bytes = max_bytes
        self.upload_slots = asyncio.Semaphore(2)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in {"POST", "PUT", "PATCH"} or not scope.get("path", "").startswith("/api/"):
            return await self.app(scope, receive, send)
        limit = self.max_bytes if scope.get("path") == "/api/receipts" else 2 * 1024 * 1024
        total = 0

        async def bounded_receive():
            nonlocal total
            message = await receive()
            total += len(message.get("body", b""))
            if total > limit:
                scope["ryoshu_upload_limit"] = True
                # Starlette closes spooled multipart files when this exception is raised.
                raise UploadTooLarge("アップロードの合計サイズが上限を超えました")
            return message

        try:
            if scope.get("path") == "/api/receipts":
                async with self.upload_slots:
                    await self.app(scope, bounded_receive, send)
            else:
                await self.app(scope, bounded_receive, send)
        except UploadTooLarge:
            response = JSONResponse({"detail": "一度に送信できるサイズを超えました。ファイルを分けて選び直してください。保存済みの原本は保持しています。"}, status_code=413)
            await response(scope, receive, send)
