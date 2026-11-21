"""Integration tests exercise the real DOCX modules and isolated worker processes."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from io import BytesIO
import json
import re
import threading
from zipfile import ZipFile

from docx import Document
from docx.shared import Mm
from fastapi.testclient import TestClient
import pytest

from app.config import Settings
from app.main import create_app



@pytest.fixture
def settings(tmp_path):
    return Settings(allowed_hosts=("testserver", "localhost", "127.0.0.1"))


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as instance:
        instance.headers["X-CSRF-Token"] = instance.get("/api/session").json()["csrf_token"]
        yield instance


@pytest.fixture
def docx_bytes():
    document = Document()
    document.add_heading("Введение", 1)
    document.add_paragraph("Документ описывает разработку программного обеспечения и порядок проверки.")
    document.add_heading("1 Основная часть", 1)
    paragraph = document.add_paragraph("Система выполняет формальную проверку технической документации.")
    paragraph.paragraph_format.line_spacing = 1.5
    document.sections[0].page_width = Mm(216)
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()


def upload(client, data, filename="Пояснительная записка.docx"):
    return client.post("/api/documents", files={"file": (filename, data, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})


def test_complete_flow_preserves_original_and_rechecks(client, docx_bytes, monkeypatch, tmp_path):
    from email import policy
    from email.parser import BytesParser
    from formal_check import check_document

    # A real file large enough to cross Starlette's default disk spool threshold.
    original = BytesIO(docx_bytes)
    with ZipFile(original, "a") as archive:
        archive.writestr("synthetic-padding.bin", b"x" * (2 * 1024 * 1024))
    payload = original.getvalue()
    import tempfile
    monkeypatch.setattr(tempfile.SpooledTemporaryFile, "rollover",
                        lambda self: pytest.fail("Upload spilled to disk"))
    monkeypatch.chdir(tmp_path)
    response = upload(client, payload)
    assert response.status_code == 200, response.text
    data = response.json()
    assert "document_id" not in data and "filename" not in data
    assert data["report"] == check_document(BytesIO(payload))
    assert len(data["limitations"]) == 31
    fixed = client.post("/api/documents/fix", files={"file": ("document.docx", payload)})
    assert fixed.status_code == 200
    assert fixed.headers["cache-control"] == "no-store"
    parts = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: " + fixed.headers["content-type"].encode() + b"\r\n\r\n" + fixed.content)
    result, file = [part.get_payload(decode=True) for part in parts.iter_parts()]
    assert json.loads(result)["report"] == check_document(BytesIO(file))
    corrected = Document(BytesIO(file))
    assert abs(corrected.sections[0].page_width.mm - 210) < 0.1
    assert [p.text for p in corrected.paragraphs] == [p.text for p in Document(BytesIO(payload)).paragraphs]
    assert not list(tmp_path.iterdir())


def test_csrf_and_removed_routes(client):
    client.headers["X-CSRF-Token"] = "invalid"
    for path in ("/api/documents", "/api/documents/fix"):
        assert client.post(path).status_code == 403
    assert client.get("/api/documents/old-id").status_code == 404
    assert client.get("/api/documents/old-id/download").status_code == 404


@pytest.mark.parametrize("name,data,status", [("file.pdf", b"text", 415), ("file.docx", b"", 422), ("file.docx", b"not a zip", 422)])
def test_invalid_uploads(client, name, data, status):
    response = upload(client, data, name)
    assert response.status_code == status, response.text
    assert "error" in response.json()


def test_upload_limit_and_chunked_body(settings):
    with TestClient(create_app(replace(settings, max_upload_bytes=100))) as client:
        client.headers["X-CSRF-Token"] = client.get("/api/session").json()["csrf_token"]
        assert upload(client, b"a" * 101).status_code == 413
        assert upload(client, b"a" * (1024 * 1024 + 101)).status_code == 413


def test_missing_and_multiple_files(client, docx_bytes):
    assert client.post("/api/documents").status_code == 422
    response = client.post("/api/documents", files=[("file", ("1.docx", docx_bytes)), ("file", ("2.docx", docx_bytes))])
    assert response.status_code == 400


def test_two_documents_are_processed_concurrently_and_capacity_is_bounded(client, docx_bytes, monkeypatch):
    service = client.app.state.service
    original_run = service.runner.run
    started, release = threading.Event(), threading.Event()
    active = []

    async def barrier(source, operation):
        active.append(id(source))
        if len(active) == 2:
            started.set()
        assert await asyncio.to_thread(release.wait, 10)
        return await original_run(source, operation)

    monkeypatch.setattr(service.runner, "run", barrier)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(upload, client, docx_bytes, f"document-{i}.docx") for i in range(2)]
        try:
            assert started.wait(10), "Both independent documents must reach processing before either completes"
            assert len(set(active)) == 2
            assert client.get("/health").status_code == 200
            busy = upload(client, docx_bytes)
            assert busy.status_code == 503 and busy.headers["retry-after"] == "5"
        finally:
            release.set()
        results = [future.result(timeout=15) for future in futures]
    assert all(result.status_code == 200 for result in results)


def test_timeout_kills_worker(settings, docx_bytes):
    with TestClient(create_app(replace(settings, processing_timeout=0.001))) as client:
        client.headers["X-CSRF-Token"] = client.get("/api/session").json()["csrf_token"]
        assert upload(client, docx_bytes).status_code == 504
        assert not client.app.state.service.runner.processes


def test_path_traversal_and_private_storage_are_unavailable(client):
    assert client.get("/api/documents/not-a-uuid").status_code == 404
    assert client.get("/storage/.session-secret").status_code == 404
    assert client.get("/static/../storage/.session-secret").status_code == 404
    assert client.get("/", headers={"Host": "attacker.example"}).status_code == 400


def test_frontend_is_served_with_csp_and_api_stays_available(client):
    page = client.get("/")
    assert page.status_code == 200
    assert 'lang="ru"' in page.text
    script = re.search(r'<script[^>]+src="(/assets/app\.js(?:\?[^\"]*)?)"', page.text)
    assert script, "The page must load the local application module"
    assert client.get(script.group(1)).status_code == 200
    assert "script-src 'self'" in page.headers["content-security-policy"]
    assert page.headers["cache-control"] == "no-cache"
    for asset in ("app.js", "api.mjs", "documents.mjs", "views.mjs", "report.mjs",
                  "styles.css", "landing.css", "workspace.css", "favicon.svg",
                  "fonts/InterVariable.woff2", "images/clouds-landing.webp",
                  "images/clouds-workspace.webp", "images/clouds-footer.webp"):
        response = client.get(f"/assets/{asset}")
        assert response.status_code == 200
        # Scripts and styles are revalidated on every load; only the font is cached for long.
        expected = "public, max-age=31536000, immutable" if asset.startswith("fonts/") else "no-cache"
        assert response.headers["cache-control"] == expected
    cached = client.get("/assets/app.js", headers={"If-None-Match": client.get("/assets/app.js").headers["etag"]})
    assert cached.status_code == 304 and cached.headers["cache-control"] == "no-cache"
    assert "max-age" not in client.get("/assets/fonts/missing.woff2").headers.get("cache-control", "")
    for path in ("/assets/../app/security.py", "/assets/%2e%2e/storage/.session-secret", "/assets/index.html", "/storage/documents"):
        assert client.get(path).status_code == 404
    assert client.get("/static/app.js").status_code == 404
    schema = client.get("/openapi.json").json()
    assert all("delete" not in methods for methods in schema["paths"].values())
    assert "/api/documents" in schema["paths"]
    request_body = schema["paths"]["/api/documents"]["post"]["requestBody"]
    assert "file" in request_body["content"]["multipart/form-data"]["schema"]["properties"]


def test_duplicate_zip_entries_are_rejected(client, docx_bytes):
    output = BytesIO()
    with ZipFile(BytesIO(docx_bytes)) as original, ZipFile(output, "w") as modified:
        for entry in original.infolist():
            modified.writestr(entry, original.read(entry))
        with pytest.warns(UserWarning):
            modified.writestr("word/document.xml", original.read("word/document.xml"))
    assert upload(client, output.getvalue()).status_code == 422


def test_configurable_four_parallel_documents(settings, docx_bytes, monkeypatch):
    with TestClient(create_app(replace(settings, max_concurrent_jobs=4))) as client:
        client.headers["X-CSRF-Token"] = client.get("/api/session").json()["csrf_token"]
        assert client.get("/api/session").json()["max_concurrent_jobs"] == 4
        service = client.app.state.service
        original_run = service.runner.run
        started, release = threading.Event(), threading.Event()
        active = []

        async def barrier(source, operation):
            active.append(id(source))
            if len(active) == 4:
                started.set()
            assert await asyncio.to_thread(release.wait, 10)
            return await original_run(source, operation)

        monkeypatch.setattr(service.runner, "run", barrier)
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(upload, client, docx_bytes) for _ in range(4)]
            try:
                assert started.wait(10)
                assert len(set(active)) == 4
            finally:
                release.set()
            assert all(future.result(timeout=15).status_code == 200 for future in futures)


def test_invalid_recheck_never_returns_corrected_file(settings, monkeypatch):
    async def invalid(source, operation):
        return b'{"report":{}}', b"private-result"

    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        monkeypatch.setattr(client.app.state.service.runner, "run", invalid)
        client.headers["X-CSRF-Token"] = client.get("/api/session").json()["csrf_token"]
        response = client.post("/api/documents/fix", files={"file": ("document.docx", b"input")})
        assert response.status_code == 500
        assert b"private-result" not in response.content


def test_chunked_upload_limit_is_enforced_without_content_length(settings):
    async def exercise():
        application = create_app(replace(settings, max_upload_bytes=100))
        async with application.router.lifespan_context(application):
            messages = []
            request_count = 0

            async def receive():
                nonlocal request_count
                request_count += 1
                return {"type": "http.request", "body": b"x" * (1024 * 1024 + 101), "more_body": False}

            async def send(message):
                messages.append(message)

            # Obtain a valid session directly from its serializer for this raw
            # ASGI request; no Content-Length header is supplied.
            sessions = application.state.sessions
            import secrets
            owner = secrets.token_urlsafe(32)
            cookie = "docx_session=" + sessions.serializer.dumps(owner)
            scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                     "method": "POST", "scheme": "http", "path": "/api/documents", "raw_path": b"/api/documents", "query_string": b"",
                     "headers": [(b"host", b"testserver"), (b"content-type", b"multipart/form-data; boundary=test"),
                                 (b"cookie", cookie.encode()), (b"x-csrf-token", sessions.token(owner).encode())],
                     "client": ("127.0.0.1", 123), "server": ("testserver", 80)}
            await application(scope, receive, send)
            assert next(message for message in messages if message["type"] == "http.response.start")["status"] == 413
            assert request_count == 1

    asyncio.run(exercise())


def test_restart_leaves_no_document_or_log_markers(settings, tmp_path, monkeypatch, caplog):
    marker = "synthetic-private-content-1703"
    filename = "synthetic-private-filename.docx"
    document = Document()
    document.core_properties.author = "synthetic-private-author"
    document.add_paragraph(marker)
    output = BytesIO()
    document.save(output)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    for _ in range(2):
        with TestClient(create_app(settings)) as instance:
            instance.headers["X-CSRF-Token"] = instance.get("/api/session").json()["csrf_token"]
            assert upload(instance, output.getvalue(), filename).status_code == 200
            assert upload(instance, marker.encode(), filename).status_code == 422
            assert instance.get("/api/documents/previous").status_code == 404
        assert not list(tmp_path.iterdir())
    for value in (marker, filename, document.core_properties.author):
        assert value not in caplog.text
