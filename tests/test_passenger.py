import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
from a2wsgi import ASGIMiddleware
from bson import ObjectId
from fastapi import FastAPI

from app.main import app, repository
from app.passenger import LifespanApplication


def make_application(*, fail_first=False):
    calls = {"startup": 0, "shutdown": 0}

    @asynccontextmanager
    async def lifespan(app):
        calls["startup"] += 1
        if fail_first and calls["startup"] == 1:
            raise RuntimeError("Unavailable")
        app.state.initialized = True
        yield
        calls["shutdown"] += 1

    api = FastAPI(lifespan=lifespan)

    @api.get("/test")
    async def endpoint():
        assert api.state.initialized
        return {"ok": True}

    return LifespanApplication(api), calls


async def test_passenger_initializes_once_for_concurrent_requests():
    wrapped, calls = make_application()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=wrapped), base_url="http://test"
    ) as client:
        responses = await asyncio.gather(*(client.get("/test") for _ in range(10)))
    assert all(response.status_code == 200 for response in responses)
    assert calls["startup"] == 1
    await wrapped.close()
    assert calls["shutdown"] == 1


async def test_passenger_retries_failed_startup():
    wrapped, calls = make_application(fail_first=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=wrapped), base_url="http://test"
    ) as client:
        assert (await client.get("/test")).status_code == 503
        assert (await client.get("/test")).status_code == 200
    assert calls["startup"] == 2
    await wrapped.close()


def test_real_wsgi_adapter_with_authentication(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("API_KEY", "wsgi-test-key")
    get_settings.cache_clear()
    repo = AsyncMock()
    repo.list_posts.return_value = ([], 0)
    now = datetime.now(UTC)
    document = {
        "_id": ObjectId(),
        "title": "Título",
        "caption": "Legenda #Zion #Sites #Software",
        "hashtags": ["#Zion", "#Sites", "#Software"],
        "image_brief": "Arte",
        "topic": "Software",
        "source_urls": ["https://zionsoftwarehouse.com.br/"],
        "status": "ready",
        "origin": "blog",
        "created_at": now,
        "updated_at": now,
        "posted_at": None,
    }
    repo.next_post.return_value = document
    repo.mark_posted.return_value = {**document, "status": "posted", "posted_at": now}
    calls = []

    @asynccontextmanager
    async def lifespan(api):
        api.state.repository = repo
        calls.append(asyncio.get_running_loop())
        yield

    monkeypatch.setattr(app.router, "lifespan_context", lifespan)
    app.dependency_overrides[repository] = lambda: repo
    wrapped = LifespanApplication(app)
    wsgi = ASGIMiddleware(wrapped)
    try:
        with httpx.Client(
            transport=httpx.WSGITransport(app=wsgi), base_url="http://test"
        ) as client:
            assert client.get("/posts").status_code == 401
            response = client.get("/posts", headers={"apikey": "wsgi-test-key"})
            assert response.status_code == 200
            assert response.json()["items"] == []
            headers = {"apikey": "wsgi-test-key"}
            assert client.get("/posts/next", headers=headers).json()["status"] == "ready"
            response = client.patch(
                f"/posts/{document['_id']}/status", headers=headers, json={"status": "posted"}
            )
            assert response.status_code == 200
            assert response.json()["status"] == "posted"
            assert calls == [wsgi.loop]
    finally:
        asyncio.run_coroutine_threadsafe(wrapped.close(), wsgi.loop).result(timeout=5)
        wsgi.loop.call_soon_threadsafe(wsgi.loop.stop)
        app.dependency_overrides.clear()
        get_settings.cache_clear()
