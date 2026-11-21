"""Bound request bodies before multipart parsing starts consuming disk."""
import asyncio
from time import monotonic

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse


class BodyLimitMiddleware:
    def __init__(self, app, max_bytes, upload_timeout=120, idle_timeout=30, *, clock=None):
        self.app, self.max_bytes = app, max_bytes
        self.upload_timeout, self.idle_timeout, self.clock = upload_timeout, idle_timeout, clock or monotonic

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        deadline = self.clock() + self.upload_timeout
        headers = dict(scope.get("headers", []))
        try:
            too_large = int(headers.get(b"content-length", b"0")) > self.max_bytes
        except ValueError:
            response = JSONResponse({"detail": "Некорректный размер запроса."}, status_code=400)
            return await response(scope, receive, send)
        if too_large:
            response = JSONResponse({"detail": "Файл превышает допустимый размер."}, status_code=413)
            return await response(scope, receive, send)
        total = 0
        body_complete = False

        async def limited_receive():
            nonlocal total, body_complete
            if body_complete:
                # The deadlines bound the request body only. A response may take
                # longer: a streamed download waits here for a disconnect.
                return await receive()
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise HTTPException(408, "Превышено общее время загрузки файла. Повторите загрузку при стабильном соединении.")
            try:
                message = await asyncio.wait_for(receive(), min(self.idle_timeout, remaining))
            except asyncio.TimeoutError:
                raise HTTPException(408, "Превышено время загрузки файла.") from None
            if self.clock() >= deadline:
                raise HTTPException(408, "Превышено общее время загрузки файла. Повторите загрузку при стабильном соединении.")
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > self.max_bytes:
                    raise HTTPException(413, "Файл превышает допустимый размер.")
                body_complete = not message.get("more_body", False)
            else:
                body_complete = True
            return message

        async def secured_send(message):
            if message["type"] == "http.response.start":
                extra = [(b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"same-origin"),
                         (b"x-frame-options", b"DENY"),
                         (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")]
                path = scope["path"]
                if path.startswith("/api/"):
                    extra.append((b"cache-control", b"no-store"))
                elif path.startswith("/assets/fonts/") and message["status"] in (200, 206, 304):
                    # The font file never changes under its name.
                    extra.append((b"cache-control", b"public, max-age=31536000, immutable"))
                elif path.startswith("/assets/"):
                    # Always revalidate by ETag: a release must not mix old and new modules.
                    extra.append((b"cache-control", b"no-cache"))
                message["headers"] = list(message.get("headers", [])) + extra
            await send(message)

        await self.app(scope, limited_receive, secured_send)
