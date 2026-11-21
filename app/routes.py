"""Process each upload in memory and return its complete result."""
import asyncio
import json
import secrets

from fastapi import APIRouter, Request
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import ClientDisconnect
from starlette.responses import Response

from formal_check.report import validate_report
from .runner import ProcessingError

router = APIRouter()
UPLOAD = {"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
    "type": "object", "required": ["file"], "properties": {"file": {"type": "string", "format": "binary"}},
}}}}}
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}


@router.get("/api/session")
async def session(request: Request, response: Response):
    settings, sessions = request.app.state.settings, request.app.state.sessions
    owner = sessions.establish(request, response, settings.cookie_secure)
    return {"csrf_token": sessions.token(owner), "max_upload_bytes": settings.max_upload_bytes,
            "max_concurrent_jobs": settings.max_concurrent_jobs}


async def process(request, operation):
    state = request.app.state
    state.sessions.authorize_mutation(request)
    host = request.client.host if request.client else "unknown"
    state.rate_limiter.check(host)
    state.service.require_free_slot()
    with state.receive_gate.admit(host):
        if request.headers.get("content-type", "").split(";")[0].lower() != "multipart/form-data":
            raise ProcessingError("missing_file", "Выберите один DOCX-файл.", 422)
        parser = MultiPartParser(request.headers, request.stream(), max_files=1, max_fields=0)
        # The middleware caps the entire request. Never roll the spool onto disk.
        parser.spool_max_size = 0
        try:
            form = await parser.parse()
        except MultiPartException:
            raise ProcessingError("invalid_upload", "Выберите один DOCX-файл.", 400) from None
        try:
            file = form.get("file")
            if not isinstance(file, UploadFile) or len(form.multi_items()) != 1:
                raise ProcessingError("missing_file", "Выберите один DOCX-файл.", 422)
            if not (file.filename or "").lower().endswith(".docx"):
                raise ProcessingError("unsupported_format", "Поддерживаются только файлы .docx.", 415)
            if not file.size:
                raise ProcessingError("empty_file", "Выбран пустой файл.", 422)
            if file.size > state.settings.max_upload_bytes:
                raise ProcessingError("file_too_large", "Файл превышает допустимый размер.", 413)
            content = await file.read()
        finally:
            await form.close()
    async with state.service.slot():
        job = asyncio.create_task(state.service.runner.run(content, operation))
        try:
            while not job.done():
                await asyncio.wait({job}, timeout=0.25)
                if not job.done() and await request.is_disconnected():
                    raise ClientDisconnect()
            metadata, corrected = job.result()
        finally:
            if not job.done():
                job.cancel()
                await asyncio.gather(job, return_exceptions=True)
        validate_report(json.loads(metadata)["report"])
        if operation == "check":
            return Response(metadata, media_type="application/json")
        # Browser Response.formData() reads both parts without a ZIP/JS dependency.
        boundary = secrets.token_hex(24)
        body = b"".join([
            f'--{boundary}\r\nContent-Disposition: form-data; name="result"\r\n\r\n'.encode(), metadata,
            f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="corrected.docx"\r\nContent-Type: {DOCX}\r\n\r\n'.encode(),
            corrected, f"\r\n--{boundary}--\r\n".encode(),
        ])
        return Response(body, media_type=f"multipart/form-data; boundary={boundary}")


@router.post("/api/documents", openapi_extra=UPLOAD)
async def check(request: Request):
    return await process(request, "check")


@router.post("/api/documents/fix", openapi_extra=UPLOAD, responses={200: {"content": {"multipart/form-data": {}}}}, response_class=Response)
async def fix(request: Request):
    return await process(request, "fix")
