"""Bound concurrent operations without keeping documents between requests."""
import asyncio
from contextlib import asynccontextmanager

from .runner import ProcessingError, Runner


class Service:
    def __init__(self, settings):
        self.runner = Runner(settings)
        self.slots = asyncio.Semaphore(settings.max_concurrent_jobs)

    def require_free_slot(self):
        if self.slots.locked():
            raise ProcessingError("busy", "Все обработчики заняты. Повторите попытку немного позже.", 503)

    @asynccontextmanager
    async def slot(self):
        self.require_free_slot()
        await self.slots.acquire()
        try:
            yield
        finally:
            self.slots.release()
