import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

from app.config import Settings
from app.main import create_app, lifespan
from app.runtime import BackgroundWorker


async def test_worker_starts_immediately_and_stops_on_shutdown(monkeypatch):
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def operation():
        started.set()
        try:
            await asyncio.Future()
        finally:
            stopped.set()

    monkeypatch.setattr("app.runtime.run", operation)
    worker = BackgroundWorker(Settings(_env_file=None, openai_api_key="test-key"))
    worker.start()
    original = worker.task
    worker.start()
    assert worker.task is original
    await asyncio.wait_for(started.wait(), timeout=1)
    await worker.stop()
    assert stopped.is_set()
    assert worker.task is None


async def test_worker_disabled_does_not_start():
    worker = BackgroundWorker(Settings(_env_file=None, worker_enabled=False))
    worker.start()
    assert worker.task is None


async def test_worker_missing_openai_key_is_reported(caplog):
    worker = BackgroundWorker(Settings(_env_file=None))
    worker.start()
    assert worker.task is None
    assert "OPENAI_API_KEY" in caplog.text


async def test_supervisor_retries_worker_failure(monkeypatch):
    retried = asyncio.Event()
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("Failed")
        retried.set()
        await asyncio.Future()

    monkeypatch.setattr("app.runtime.run", operation)
    worker = BackgroundWorker(
        Settings(_env_file=None, openai_api_key="test-key", worker_retry_seconds=1)
    )
    worker.start()
    try:
        await asyncio.wait_for(retried.wait(), timeout=2)
        assert calls == 2
    finally:
        await worker.stop()


async def test_lifespan_starts_worker_without_waiting_for_mongo(monkeypatch):
    settings = Settings(_env_file=None, api_key="test-key", openai_api_key="test-key")
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    client = MagicMock()
    client.close = AsyncMock()
    client.admin = SimpleNamespace(command=AsyncMock(side_effect=RuntimeError("Mongo unavailable")))
    monkeypatch.setattr("app.main.create_client", lambda settings: client)
    background = Mock()
    background.stop = AsyncMock()
    monkeypatch.setattr("app.main.BackgroundWorker", lambda settings: background)
    api = create_app(settings)
    async with lifespan(api):
        background.start.assert_called_once()
        assert api.state.worker is background
        client.admin.command.assert_not_awaited()
    background.stop.assert_awaited_once()
    client.close.assert_awaited_once()


async def test_passenger_can_initialize_before_first_request():
    from contextlib import asynccontextmanager

    from fastapi import FastAPI

    from app.passenger import LifespanApplication

    started = asyncio.Event()

    @asynccontextmanager
    async def context(app):
        started.set()
        yield

    app = FastAPI(lifespan=context)
    wrapper = LifespanApplication(app)
    await wrapper.start()
    assert started.is_set()
    assert wrapper.context is not None
    await wrapper.close()


def test_passenger_entrypoint_schedules_startup_at_import(monkeypatch):
    import importlib.util
    from pathlib import Path

    monkeypatch.setattr("app.main.get_settings", lambda: Settings(_env_file=None, api_key="key"))
    start = AsyncMock()
    monkeypatch.setattr("app.passenger.LifespanApplication.start", start)
    path = Path(__file__).resolve().parents[1] / "passenger_wsgi.py"
    spec = importlib.util.spec_from_file_location("test_passenger_entrypoint", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        module.startup.result(timeout=2)
        start.assert_awaited_once()
    finally:
        import atexit

        if hasattr(module, "shutdown"):
            module.shutdown()
            atexit.unregister(module.shutdown)
            module.application.loop.call_soon_threadsafe(module.application.loop.stop)
