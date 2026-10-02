import asyncio
import contextlib
import logging

from app.config import Settings
from app.errors import failure_hint
from app.worker import run

logger = logging.getLogger(__name__)


class BackgroundWorker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.task: asyncio.Task | None = None

    def start(self):
        if not self.settings.worker_enabled or self.task is not None:
            return
        if not self.settings.openai_api_key.get_secret_value():
            logger.warning("Worker automático desativado: defina OPENAI_API_KEY")
            return
        self.task = asyncio.create_task(self.supervise(), name="zion-content-worker")
        logger.info("Worker iniciado junto com a aplicação")

    async def supervise(self):
        while True:
            try:
                await run()
            except Exception as exc:
                logger.error("Worker será reiniciado: %s", failure_hint(exc))
            await asyncio.sleep(self.settings.worker_retry_seconds)

    async def stop(self):
        if self.task is not None:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
            self.task = None
