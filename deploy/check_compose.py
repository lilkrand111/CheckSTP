"""Run an isolated local Compose check; never uses the project's real storage.

Usage: .venv/bin/python deploy/check_compose.py --image normacheck:check
The image must already be built. Only synthetic inputs are used; test configuration and diagnostics are retained
in a private temporary directory. Only this script's Compose project is stopped.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from email import policy
from email.parser import BytesParser
from http.cookiejar import CookieJar
from io import BytesIO
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import build_opener, HTTPCookieProcessor, Request

from docx import Document

ROOT = Path(__file__).resolve().parent.parent


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Client:
    def __init__(self, url):
        self.url = url
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))
        status, _, body = self.request("/api/session")
        assert status == 200
        self.token = json.loads(body)["csrf_token"]

    def request(self, path, *, method="GET", data=None, headers=None):
        extra = dict(headers or {})
        if method == "POST":
            extra.setdefault("X-CSRF-Token", self.token)
        request = Request(self.url + path, data=data, headers=extra, method=method)
        try:
            response = self.opener.open(request, timeout=205)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read()

    def upload(self, data, *, fix=False):
        boundary = secrets.token_hex(16)
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="check.docx"\r\n'
                'Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document\r\n\r\n').encode()
        body += data + f"\r\n--{boundary}--\r\n".encode()
        status, headers, response = self.request("/api/documents/fix" if fix else "/api/documents", method="POST", data=body,
                                         headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        assert status == 200, status
        if fix:
            parts = BytesParser(policy=policy.default).parsebytes(
                b"Content-Type: " + headers["Content-Type"].encode() + b"\r\n\r\n" + response)
            result, file = [part.get_payload(decode=True) for part in parts.iter_parts()]
            return json.loads(result), file
        return json.loads(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="normacheck:check")
    parser.add_argument("--keep-running", action="store_true", help="Keep the isolated project for browser checks")
    args = parser.parse_args()
    work = Path(tempfile.mkdtemp(prefix="normacheck-compose-"))
    project = "nkcheck-" + secrets.token_hex(4)
    port, tls_port = free_port(), free_port()
    while tls_port == port:
        tls_port = free_port()
    env = {
        "DOMAIN": "localhost", "SITE_ADDRESS": "http://localhost", "COOKIE_SECURE": "false",
        "APP_SECRET": secrets.token_urlsafe(48), "APP_IMAGE": args.image,
        "HTTP_BIND": "127.0.0.1",
        "HTTP_PORT": str(port), "HTTPS_PORT": str(tls_port),
        "DOCKER_SUBNET": "172.30.77.0/29", "PROXY_IP": "172.30.77.2", "APP_IP": "172.30.77.3",
        # Functional checks need multiple mutations without waiting for refill.
        "RATE_LIMIT_BURST": "64", "RATE_LIMIT_PER_MINUTE": "12",
        "RATE_LIMIT_MAX_CLIENTS": "4096", "MAX_CONCURRENT_JOBS": "2",
        "MAX_UPLOAD_MB": "20", "WORKER_MEMORY_MB": "768",
        "PROCESSING_TIMEOUT_SECONDS": "60", "UPLOAD_TIMEOUT_SECONDS": "120",
        "UPLOAD_IDLE_TIMEOUT_SECONDS": "30", "APP_MEMORY_LIMIT": "640m",
        "APP_CPU_LIMIT": "0.9",
        "PROXY_MEMORY_LIMIT": "96m",
        "MAX_REQUEST_BODY_BYTES": "22020096", "PROXY_UPLOAD_TIMEOUT": "130s",
        "PROXY_RESPONSE_TIMEOUT": "200s", "CADDY_IMAGE": "caddy:2-alpine",
    }
    env_file = work / "check.env"

    def write_env():
        env_file.write_text("".join(f"{key}={value}\n" for key, value in env.items()))
        env_file.chmod(0o600)

    write_env()
    compose = ["docker", "compose", "--env-file", str(env_file), "-p", project,
               "-f", str(ROOT / "compose.yaml")]
    log_file = work / "compose.log"

    def command(*arguments, capture=False):
        with log_file.open("a") as log:
            result = subprocess.run([*compose, *arguments], cwd=ROOT, check=True,
                                    stdout=subprocess.PIPE if capture else log, stderr=log, text=True,
                                    env={**os.environ, **env})
        return result.stdout

    url = f"http://localhost:{port}"

    def wait_http():
        for _ in range(60):
            try:
                if Client(url).request("/health")[0] == 200:
                    return
            except (URLError, AssertionError, ConnectionError):
                time.sleep(0.25)
        raise RuntimeError("Local proxy did not become ready")

    print(f"Isolated check directory: {work}", flush=True)
    passed = False
    try:
        command("config", "--quiet")
        command("up", "-d", "--no-build", "--wait", "--wait-timeout", "90")
        command("exec", "-T", "caddy", "caddy", "validate", "--config", "/etc/caddy/Caddyfile")
        wait_http()
        print("Compose, healthcheck and Caddy configuration: OK", flush=True)
        assert command("exec", "-T", "app", "id", "-u", capture=True).strip() == "10001"
        client = Client(url)
        status, headers, page = client.request("/")
        assert status == 200 and b"/assets/app.js" in page
        assert "script-src 'self'" in headers["Content-Security-Policy"]
        assert client.request("/assets/app.js")[0] == 200
        assert client.request("/storage/.session-secret")[0] == 404
        assert client.request("/", headers={"Host": "attacker.example"})[0] >= 400
        document = Document()
        document.add_heading("Введение", 1)
        for index in range(300):
            document.add_paragraph(f"Проверка контейнерного запуска. Абзац {index}.")
        stream = BytesIO()
        document.save(stream)
        # Fill an unreferenced ZIP part to exercise the configured upload limit.
        from zipfile import ZipFile
        with ZipFile(stream, "a") as archive:
            archive.writestr("synthetic-padding.bin", bytes(20 * 1024 * 1024 - stream.tell() - 8192))
        data = stream.getvalue()
        initial = client.upload(data)
        assert initial["state"] == "checked" and "document_id" not in initial
        fixed, copy = client.upload(data, fix=True)
        assert fixed["state"] == "fixed"
        Document(BytesIO(copy))
        assert client.upload(copy)["report"] == fixed["report"]
        assert client.request("/api/documents/fix", method="POST", headers={"X-CSRF-Token": "wrong"})[0] == 403
        for service in ("app", "caddy"):
            assert command("exec", "-T", service, "cat", "/sys/fs/cgroup/memory.swap.max", capture=True).strip() == "0"
        config = json.loads(command("config", "--format", "json", capture=True))
        assert not config["services"]["app"].get("volumes")
        assert config["services"]["app"]["read_only"]
        for service in ("app", "caddy"):
            assert config["services"][service]["logging"]["driver"] == "none"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: Client(url).upload(data, fix=True), range(2)))
        assert all(result["report"] == fixed["report"] for result, _ in results)
        print("Checked RAM-only configuration, two concurrent corrections and valid downloads.", flush=True)
        command("stats", "--no-stream")
        env["RATE_LIMIT_BURST"] = "1"
        write_env()
        command("up", "-d", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "90", "app")
        wait_http()
        assert client.request("/api/documents", method="POST", data=b"")[0] == 422
        status, headers, body = client.request("/api/documents", method="POST", data=b"",
                                             headers={"X-Forwarded-For": "203.0.113.99"})
        assert status == 429 and int(headers["Retry-After"]) > 0, (status, body[:300])
        assert client.request("/api/documents/previous")[0] == 404
        print("Rate limit before multipart, spoofed IP rejected, no stored report: OK", flush=True)
        print(f"All Compose checks passed. Diagnostics retained at {work}", flush=True)
        passed = True
        if args.keep_running:
            print(f"Browser check: {url}/", flush=True)
            print("Stop with: " + " ".join([*compose, "down"]), flush=True)
    finally:
        if not args.keep_running or not passed:
            command("down", "--volumes", "--timeout", "220")


if __name__ == "__main__":
    main()
