import asyncio
import logging

from app.errors import failure_hint

logger = logging.getLogger(__name__)


class LifespanApplication:
    """Inicializa FastAPI no mesmo loop usado pelo adaptador WSGI do Passenger."""

    def __init__(self, app):
        self.app = app
        self.lock = asyncio.Lock()
        self.context = None

    async def __call__(self, scope, receive, send):
        try:
            await self.start()
        except Exception as exc:
            logger.error("Inicialização Passenger falhou: %s", failure_hint(exc))
            body = b'{"detail":"Inicializacao indisponivel; confira MongoDB e API_KEY"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 503,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)

    async def start(self):
        async with self.lock:
            if self.context is None:
                context = self.app.router.lifespan_context(self.app)
                await context.__aenter__()
                self.context = context

    async def close(self):
        async with self.lock:
            if self.context is not None:
                await self.context.__aexit__(None, None, None)
                self.context = None
