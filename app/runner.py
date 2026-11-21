"""Concurrent subprocesses with wall-clock timeouts and cancellation cleanup."""
import asyncio
import os
import sys

from .config import ROOT


class ProcessingError(Exception):
    def __init__(self, code, message, status_code=500, *, retry_after=None):
        self.code, self.message, self.status_code = code, message, status_code
        self.retry_after = retry_after
        super().__init__(message)


class Runner:
    def __init__(self, settings):
        self.settings = settings
        self.processes = set()

    async def run(self, source, operation):
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "app.worker", operation, "--ruleset", self.settings.ruleset_version,
            "--parent-pid", str(os.getpid()),
            "--memory-mb", str(self.settings.worker_memory_mb),
            "--timeout", str(self.settings.processing_timeout),
            cwd=ROOT, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        self.processes.add(process)
        exchange = asyncio.create_task(process.communicate(source))
        try:
            try:
                output, _ = await asyncio.wait_for(asyncio.shield(exchange), timeout=self.settings.processing_timeout)
            except asyncio.TimeoutError:
                raise ProcessingError("timeout", "Превышено время обработки. Попробуйте документ меньшего размера.", 504) from None
            if process.returncode == 2:
                raise ProcessingError("invalid_document", "Не удалось прочитать DOCX. Проверьте формат файла.", 422)
            if process.returncode == 4:
                raise ProcessingError("result_too_large", "Результат превышает предел 64 МиБ.", 422)
            if process.returncode != 0:
                raise ProcessingError("worker_failed", "Не удалось завершить обработку документа.")
            metadata, separator, corrected = output.partition(b"\n")
            if not separator or bool(corrected) != (operation == "fix"):
                raise ProcessingError("worker_failed", "Не удалось получить результат обработки.")
            return metadata, corrected
        finally:
            if process.returncode is None:
                process.kill()
            await asyncio.gather(exchange, return_exceptions=True)
            self.processes.discard(process)

    async def close(self):
        processes = list(self.processes)
        for process in processes:
            if process.returncode is None:
                process.kill()
        await asyncio.gather(*(process.wait() for process in processes))
