"""Bound multipart disk spooling as well as individual file reads."""
from starlette.responses import JSONResponse
from starlette.formparsers import MultiPartException


class UploadTooLarge(MultiPartException):
    pass


class UploadSizeLimit:
    def __init__(self, app, max_bytes):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "POST" or scope.get("path") != "/api/receipts":
            return await self.app(scope, receive, send)
        total = 0

        async def bounded_receive():
            nonlocal total
            message = await receive()
            total += len(message.get("body", b""))
            if total > self.max_bytes:
                scope["ryoshu_upload_limit"] = True
                # Starlette closes spooled multipart files when this exception is raised.
                raise UploadTooLarge("送信量が大きすぎます")
            return message

        try:
            await self.app(scope, bounded_receive, send)
        except UploadTooLarge:
            response = JSONResponse({"detail": "送信量が大きすぎます。ファイルを分けて取り込んでください。保存済みの原本は保持しています。"}, status_code=413)
            await response(scope, receive, send)
