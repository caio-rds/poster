import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest
from a2wsgi import ASGIMiddleware
from fastapi.middleware.cors import CORSMiddleware
from pymongo.errors import ServerSelectionTimeoutError

from app.config import Settings, get_settings
from app.cors import cors_options
from app.main import create_app, repository
from app.passenger import LifespanApplication

ORIGIN = "https://zionsoftwarehouse.com.br"


@pytest.fixture
async def cors_api(monkeypatch):
    monkeypatch.setenv("API_KEY", "cors-test-key")
    get_settings.cache_clear()
    api = create_app(Settings(_env_file=None, cors_allow_origins=[ORIGIN]))
    repo = AsyncMock()
    repo.list_posts.return_value = ([], 0)
    api.dependency_overrides[repository] = lambda: repo
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api), base_url="http://test"
    ) as client:
        yield client, repo
    get_settings.cache_clear()


def preflight_headers(origin=ORIGIN, method="PATCH", headers="apikey,content-type"):
    return {
        "Origin": origin,
        "Access-Control-Request-Method": method,
        "Access-Control-Request-Headers": headers,
    }


async def test_preflight_requires_no_api_key(cors_api):
    client, repo = cors_api
    response = await client.options("/posts/123/status", headers=preflight_headers())
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert "apikey" in response.headers["access-control-allow-headers"]
    assert "PATCH" in response.headers["access-control-allow-methods"]
    assert response.headers["access-control-max-age"] == "600"
    assert "access-control-allow-credentials" not in response.headers
    assert not repo.mock_calls


@pytest.mark.parametrize(
    "headers",
    [
        preflight_headers(origin="https://unknown.example"),
        preflight_headers(method="POST"),
        preflight_headers(headers="X-API-Key"),
    ],
)
async def test_disallowed_preflight_is_rejected(cors_api, headers):
    client, repo = cors_api
    response = await client.options("/posts", headers=headers)
    assert response.status_code == 400
    assert not repo.mock_calls


async def test_authenticated_request_has_cors_headers(cors_api):
    client, _ = cors_api
    response = await client.get("/posts", headers={"Origin": ORIGIN, "apikey": "cors-test-key"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN


async def test_auth_error_has_cors_headers(cors_api):
    client, repo = cors_api
    response = await client.get("/posts", headers={"Origin": ORIGIN})
    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert not repo.mock_calls


async def test_database_error_has_cors_headers(cors_api):
    client, repo = cors_api
    repo.list_posts.side_effect = ServerSelectionTimeoutError("unavailable")
    response = await client.get("/posts", headers={"Origin": ORIGIN, "apikey": "cors-test-key"})
    assert response.status_code == 503
    assert response.headers["access-control-allow-origin"] == ORIGIN


@pytest.mark.parametrize(
    "headers,query",
    [
        ({"X-API-Key": "cors-test-key"}, ""),
        ({}, "?apikey=cors-test-key"),
    ],
)
async def test_key_must_use_apikey_header(cors_api, headers, query):
    client, repo = cors_api
    assert (await client.get("/posts" + query, headers=headers)).status_code == 401
    assert not repo.mock_calls


def test_passenger_preflight_bypasses_database_initialization():
    settings = Settings(_env_file=None, cors_allow_origins=[ORIGIN])
    api = create_app(enable_cors=False)
    calls = []

    @asynccontextmanager
    async def unavailable(app):
        calls.append("startup")
        raise ServerSelectionTimeoutError("unavailable")
        yield

    api.router.lifespan_context = unavailable
    lazy = LifespanApplication(api)
    cors = CORSMiddleware(lazy, **cors_options(settings))
    wsgi = ASGIMiddleware(cors)
    try:
        with httpx.Client(
            transport=httpx.WSGITransport(app=wsgi), base_url="http://test"
        ) as client:
            response = client.options("/posts", headers=preflight_headers(method="GET"))
            assert response.status_code == 200
            assert calls == []
            response = client.get("/posts", headers={"Origin": ORIGIN})
            assert response.status_code == 503
            assert response.headers["access-control-allow-origin"] == ORIGIN
            assert calls == ["startup"]
    finally:
        asyncio.run_coroutine_threadsafe(lazy.close(), wsgi.loop).result(timeout=5)
        wsgi.loop.call_soon_threadsafe(wsgi.loop.stop)
