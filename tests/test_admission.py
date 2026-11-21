"""Admission, proxy identity and interrupted multipart requests use no real documents."""
import asyncio
from dataclasses import replace
from io import BytesIO
import secrets

from docx import Document
from fastapi.testclient import TestClient
import pytest
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.config import Settings
from app.main import create_app
from app.rate_limit import RateLimiter, ReceiveGate
from app.runner import ProcessingError


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


@pytest.fixture
def settings(tmp_path):
    return Settings(allowed_hosts=("testserver",))


def authorize(client):
    client.headers["X-CSRF-Token"] = client.get("/api/session").json()["csrf_token"]


def payload():
    document = Document()
    document.add_paragraph("Тестовый документ для проверки допуска запросов.")
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def test_bucket_burst_refill_and_bounded_client_table(settings):
    clock = Clock()
    limiter = RateLimiter(replace(settings, rate_limit_per_minute=12, rate_limit_burst=2,
                                  rate_limit_max_clients=2), clock=clock)
    limiter.check("192.0.2.1")
    limiter.check("::ffff:192.0.2.1")  # Same IPv4 address must share its allowance.
    with pytest.raises(ProcessingError) as limited:
        limiter.check("192.0.2.1")
    assert limited.value.status_code == 429 and limited.value.retry_after == 5
    limiter.check("192.0.2.2")
    with pytest.raises(ProcessingError):
        limiter.check("192.0.2.3")
    assert len(limiter.buckets) == 2
    clock.now = 5
    limiter.check("192.0.2.1")
    with pytest.raises(ProcessingError):
        limiter.check("192.0.2.1")
    clock.now = 16
    limiter.check("192.0.2.3")
    assert list(limiter.buckets) == ["192.0.2.3"]


def test_upload_and_fix_share_limit_without_affecting_reads_or_storage(settings):
    clock = Clock()
    with TestClient(create_app(replace(settings, rate_limit_burst=2))) as client:
        client.app.state.rate_limiter.clock = clock
        authorize(client)
        data = payload()
        response = client.post("/api/documents", files={"file": ("test.docx", data)})
        assert response.status_code == 200
        assert client.post("/api/documents/fix", files={"file": ("test.docx", data)}).status_code == 200
        rejected = client.post("/api/documents", files={"file": ("extra.docx", data)})
        assert rejected.status_code == 429
        assert rejected.headers["retry-after"] == "5"
        assert rejected.headers["cache-control"] == "no-store"
        assert rejected.json()["error"]["code"] == "rate_limited"
        assert not client.app.state.service.runner.processes
        assert client.get("/health").status_code == 200
        client.cookies.clear()
        authorize(client)
        assert client.post("/api/documents").status_code == 429
        clock.now = 5
        assert client.post("/api/documents", files={"file": ("second.docx", data)}).status_code == 200


@pytest.mark.parametrize("peer,trusted", [("172.30.76.2", True), ("192.0.2.10", False)])
def test_proxy_headers_only_change_identity_for_trusted_peer(settings, peer, trusted):
    application = create_app(replace(settings, rate_limit_burst=1))
    proxy = ProxyHeadersMiddleware(application, trusted_hosts="172.30.76.2")
    with TestClient(proxy, client=(peer, 12345)) as client:
        application.state.rate_limiter.clock = Clock()
        authorize(client)
        # Empty multipart-independent requests spend a token but create no files.
        assert client.post("/api/documents", headers={"X-Forwarded-For": "198.51.100.1"}).status_code == 422
        response = client.post("/api/documents", headers={"X-Forwarded-For": "198.51.100.2"})
        assert response.status_code == (422 if trusted else 429)
        assert client.post("/api/documents", headers={"X-Forwarded-For": "198.51.100.1"}).status_code == 429


def test_invalid_csrf_does_not_spend_tokens(settings):
    with TestClient(create_app(replace(settings, rate_limit_burst=1))) as client:
        client.app.state.rate_limiter.clock = Clock()
        authorize(client)
        assert client.post("/api/documents", headers={"X-CSRF-Token": "wrong"}).status_code == 403
        assert client.post("/api/documents").status_code == 422
        assert client.post("/api/documents").status_code == 429


@pytest.mark.parametrize("failure", ["total", "idle", "disconnect", "size"])
def test_interrupted_multipart_closes_spooled_file_and_releases_slot(settings, monkeypatch, failure):
    import app.middleware as middleware
    import starlette.formparsers as formparsers

    clock = Clock()
    monkeypatch.setattr(middleware, "monotonic", clock)
    files = []
    factory = formparsers.SpooledTemporaryFile

    def tracked_file(*args, **kwargs):
        file = factory(*args, **kwargs)
        files.append(file)
        return file

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", tracked_file)

    async def exercise():
        application = create_app(replace(settings, upload_timeout=3, upload_idle_timeout=0.01))
        async with application.router.lifespan_context(application):
            owner = secrets.token_urlsafe(32)
            sessions = application.state.sessions
            cookie = "docx_session=" + sessions.serializer.dumps(owner)
            scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                     "method": "POST", "scheme": "http", "path": "/api/documents", "query_string": b"",
                     "headers": [(b"host", b"testserver"), (b"content-type", b"multipart/form-data; boundary=test"),
                                 (b"cookie", cookie.encode()), (b"x-csrf-token", sessions.token(owner).encode())],
                     "client": ("192.0.2.1", 123), "server": ("testserver", 80)}
            reads = 0
            output = []

            async def receive():
                nonlocal reads
                reads += 1
                if reads == 1:
                    clock.now = 2
                    body = b'--test\r\nContent-Disposition: form-data; name="file"; filename="test.docx"\r\n\r\n'
                    return {"type": "http.request", "body": body + b"x" * (1024 * 1024 + 1), "more_body": True}
                if failure == "idle":
                    await asyncio.Event().wait()
                if failure == "disconnect":
                    return {"type": "http.disconnect"}
                if failure == "size":
                    return {"type": "http.request", "body": b"x" * (21 * 1024 * 1024), "more_body": True}
                clock.now = 4
                return {"type": "http.request", "body": b"x", "more_body": True}

            async def send(message):
                output.append(message)

            if failure == "disconnect":
                # ClientDisconnect propagates; the request must still release resources.
                from starlette.requests import ClientDisconnect
                with pytest.raises(ClientDisconnect):
                    await application(scope, receive, send)
            else:
                await application(scope, receive, send)
                status = next(item["status"] for item in output if item["type"] == "http.response.start")
                assert status == (413 if failure == "size" else 408)
            assert reads == 2
            assert files and all(file.closed and not file._rolled for file in files)
            service = application.state.service
            async with service.slot(), service.slot():
                with pytest.raises(ProcessingError) as busy:
                    async with service.slot():
                        pytest.fail("A third slot must not be available")
                assert busy.value.status_code == 503

    asyncio.run(exercise())


def multipart(data, *, closed=True):
    body = b'--test\r\nContent-Disposition: form-data; name="file"; filename="test.docx"\r\n\r\n' + data
    return body + b"\r\n--test--\r\n" if closed else body


async def post_upload(application, host, receive):
    """Send one upload straight to the ASGI app and return the response status."""
    owner = secrets.token_urlsafe(32)
    sessions = application.state.sessions
    cookie = "docx_session=" + sessions.serializer.dumps(owner)
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": "POST", "scheme": "http", "path": "/api/documents", "query_string": b"",
             "headers": [(b"host", b"testserver"), (b"content-type", b"multipart/form-data; boundary=test"),
                         (b"cookie", cookie.encode()), (b"x-csrf-token", sessions.token(owner).encode())],
             "client": (host, 123), "server": ("testserver", 80)}
    output = []

    async def send(message):
        output.append(message)

    await application(scope, receive, send)
    return next(item["status"] for item in output if item["type"] == "http.response.start")


def test_receive_gate_bounds_one_address_and_the_total(settings):
    gate = ReceiveGate(replace(settings, max_concurrent_jobs=1))
    with gate.admit("192.0.2.1"):
        with pytest.raises(ProcessingError) as same_address:
            with gate.admit("::ffff:192.0.2.1"):
                pytest.fail("One address must not exceed its share")
        assert same_address.value.status_code == 429 and same_address.value.retry_after == 5
        with gate.admit("192.0.2.2"), gate.admit("192.0.2.3"), gate.admit("192.0.2.4"):
            with pytest.raises(ProcessingError) as full:
                with gate.admit("192.0.2.5"):
                    pytest.fail("The total must stay bounded")
            assert full.value.status_code == 503
    assert not gate.active


def test_stalled_uploads_hold_no_processing_slot(settings):
    from starlette.requests import ClientDisconnect

    async def exercise():
        application = create_app(settings)
        async with application.router.lifespan_context(application):
            service, gate = application.state.service, application.state.receive_gate
            release = asyncio.Event()

            def stalled():
                started = False

                async def receive():
                    nonlocal started
                    if not started:
                        started = True
                        return {"type": "http.request", "body": multipart(b"PK", closed=False), "more_body": True}
                    await release.wait()
                    return {"type": "http.disconnect"}
                return receive

            async def complete():
                return {"type": "http.request", "body": multipart(payload()), "more_body": False}

            # One address stalls as many uploads as there are processing slots.
            tasks = [asyncio.create_task(post_upload(application, "192.0.2.1", stalled()))
                     for _ in range(settings.max_concurrent_jobs)]
            for _ in range(500):
                if gate.active.total() == len(tasks):
                    break
                await asyncio.sleep(0.01)
            assert gate.active.total() == len(tasks)
            assert not service.slots.locked()
            assert await post_upload(application, "192.0.2.1", complete) == 429
            assert await post_upload(application, "192.0.2.2", complete) == 200
            release.set()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            assert all(isinstance(result, ClientDisconnect) for result in results)
            assert not gate.active

    asyncio.run(exercise())


def test_busy_workers_after_receiving_reject_without_storing(settings):
    async def exercise():
        application = create_app(settings)
        async with application.router.lifespan_context(application):
            service = application.state.service

            async def receive():
                return {"type": "http.request", "body": multipart(payload()), "more_body": False}

            async def receive_while_workers_fill():
                # Every worker becomes busy only after this request was admitted.
                for _ in range(settings.max_concurrent_jobs):
                    await service.slots.acquire()
                return await receive()

            assert await post_upload(application, "192.0.2.1", receive_while_workers_fill) == 503
            assert not application.state.receive_gate.active
            for _ in range(settings.max_concurrent_jobs):
                service.slots.release()
            assert await post_upload(application, "192.0.2.1", receive) == 200

    asyncio.run(exercise())


@pytest.mark.parametrize("name", ["processing_timeout", "upload_timeout", "upload_idle_timeout", "rate_limit_per_minute"])
@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_invalid_timing_and_rate_settings_are_rejected(settings, name, value):
    with pytest.raises(ValueError):
        replace(settings, **{name: value})


def test_disconnect_during_processing_cancels_job_and_releases_slot(settings, monkeypatch):
    from starlette.requests import ClientDisconnect

    async def exercise():
        application = create_app(settings)
        async with application.router.lifespan_context(application):
            service = application.state.service
            started, stopped = asyncio.Event(), asyncio.Event()

            async def run(source, operation):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()

            monkeypatch.setattr(service.runner, "run", run)

            async def receive():
                if started.is_set():
                    return {"type": "http.disconnect"}
                return {"type": "http.request", "body": multipart(payload()), "more_body": False}

            with pytest.raises(ClientDisconnect):
                await asyncio.wait_for(post_upload(application, "192.0.2.1", receive), 3)
            assert stopped.is_set() and not service.slots.locked()
            assert not application.state.receive_gate.active

    asyncio.run(exercise())
