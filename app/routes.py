"""Session-owned document API with permanent storage and no deletion routes."""
import asyncio
import json
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Request
from starlette.datastructures import UploadFile
from starlette.responses import JSONResponse, StreamingResponse

from .runner import ProcessingError

router = APIRouter()


@router.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}


@router.get("/api/session")
async def session(request: Request):
    settings, sessions = request.app.state.settings, request.app.state.sessions
    response = JSONResponse({})
    owner = sessions.establish(request, response, settings.cookie_secure)
    response.body = json.dumps({
        "csrf_token": sessions.token(owner),
        "max_upload_bytes": settings.max_upload_bytes,
        "max_concurrent_jobs": settings.max_concurrent_jobs,
        "retention_hours": None,
    }).encode()
    response.headers["content-length"] = str(len(response.body))
    return response


@router.post("/api/documents", status_code=201, openapi_extra={
    "requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
        "type": "object", "required": ["file"],
        "properties": {"file": {"type": "string", "format": "binary"}},
    }}}},
})
async def upload(request: Request):
    owner = request.app.state.sessions.authorize_mutation(request)
    host = request.client.host if request.client else "unknown"
    request.app.state.rate_limiter.check(host)
    service = request.app.state.service
    # Admission happens before multipart parsing, including its disk spooling.
    # No body is read while every worker is busy, but a processing slot is taken
    # only after the body has arrived: a stalled upload must not hold one.
    service.require_free_slot()
    with request.app.state.receive_gate.admit(host):
        form = await request.form(max_files=1, max_fields=0, max_part_size=1024 * 1024)
    try:
        file = form.get("file")
        if not isinstance(file, UploadFile) or len(form.multi_items()) != 1:
            raise ProcessingError("missing_file", "Выберите один DOCX-файл.", 422)
        async with service.slot():
            return await service.upload(file, owner)
    finally:
        await form.close()


@router.get("/api/documents/{document_id}")
async def document(document_id: str, request: Request):
    owner = request.app.state.sessions.owner(request)
    service = request.app.state.service
    return service.view(service.access(document_id, owner))


@router.post("/api/documents/{document_id}/fix")
async def fix(document_id: str, request: Request):
    owner = request.app.state.sessions.authorize_mutation(request)
    request.app.state.rate_limiter.check(request.client.host if request.client else "unknown")
    return await request.app.state.service.fix(document_id, owner)


@router.get("/api/documents/{document_id}/download")
async def download(document_id: str, request: Request):
    owner = request.app.state.sessions.owner(request)
    service = request.app.state.service
    metadata, stream = service.begin_download(document_id, owner)

    async def chunks():
        try:
            while chunk := await asyncio.to_thread(stream.read, 256 * 1024):
                yield chunk
        finally:
            service.end_download(document_id, stream)

    filename = Path(metadata["original_filename"]).stem + "-исправлено.docx"
    return StreamingResponse(
        chunks(), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": "attachment; filename=corrected.docx; filename*=UTF-8''" + quote(filename, safe="")},
    )
