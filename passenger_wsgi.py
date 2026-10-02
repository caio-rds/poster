"""cPanel: startup file passenger_wsgi.py; entry point application."""

import asyncio
import atexit
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

from a2wsgi import ASGIMiddleware  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.cors import cors_options  # noqa: E402
from app.main import create_app  # noqa: E402
from app.passenger import LifespanApplication  # noqa: E402

app = create_app(enable_cors=False)
lifespan_app = LifespanApplication(app)
# CORS externo responde ao preflight antes de inicializar a conexão MongoDB.
cors_app = CORSMiddleware(lifespan_app, **cors_options(get_settings()))
application = ASGIMiddleware(cors_app, wait_time=10)
startup = asyncio.run_coroutine_threadsafe(lifespan_app.start(), application.loop)


def startup_finished(future):
    try:
        future.result()
    except Exception as exc:
        logging.getLogger(__name__).error("Inicialização Passenger (%s)", type(exc).__name__)


startup.add_done_callback(startup_finished)


def shutdown():
    try:
        asyncio.run_coroutine_threadsafe(lifespan_app.close(), application.loop).result(timeout=5)
    except Exception as exc:
        logging.getLogger(__name__).warning("Encerramento Passenger (%s)", type(exc).__name__)


atexit.register(shutdown)
