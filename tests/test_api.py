from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from bson import ObjectId

from app.config import get_settings
from app.main import app, authenticate, repository


@pytest.fixture
def document():
    now = datetime.now(UTC)
    return {
        "_id": ObjectId(),
        "title": "Software sob medida",
        "caption": "Transforme seu negócio.",
        "hashtags": [],
        "image_brief": "Arte sobre software",
        "topic": "Software sob medida",
        "source_urls": ["https://zionsoftwarehouse.com.br/"],
        "status": "ready",
        "created_at": now,
        "updated_at": now,
        "posted_at": None,
        "origin": "website",
        "embedding": [1, 0],
        "content_hash": "segredo-interno",
    }


@pytest.fixture
async def api(monkeypatch):
    monkeypatch.setenv("API_KEY", "test-key")
    get_settings.cache_clear()
    repo = AsyncMock()
    app.dependency_overrides[repository] = lambda: repo
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"apikey": "test-key"},
    ) as client:
        yield client, repo
    app.dependency_overrides.clear()
    get_settings.cache_clear()


async def test_next_post_excludes_internal_fields(api, document):
    client, repo = api
    document["status"] = "posted"
    document["posted_at"] = datetime.now(UTC)
    repo.next_post.return_value = document
    response = await client.get("/posts/next")
    assert response.status_code == 200
    assert response.json()["id"] == str(document["_id"])
    assert response.json()["status"] == "posted"
    assert response.json()["posted_at"]
    assert "embedding" not in response.json()
    assert "content_hash" not in response.json()


async def test_empty_queue(api):
    client, repo = api
    repo.next_post.return_value = None
    response = await client.get("/posts/next")
    assert response.status_code == 200
    assert response.json() is None
    assert response.text == "null"


async def test_list_pagination_and_filter(api, document):
    client, repo = api
    repo.list_posts.return_value = ([document], 31)
    response = await client.get("/posts?status=ready&offset=10&limit=5")
    assert response.status_code == 200
    assert response.json()["total"] == 31
    repo.list_posts.assert_awaited_once_with("ready", 10, 5)


async def test_list_returns_featured_image_without_changing_status(api, document):
    client, repo = api
    document.pop("image_brief")
    document["image_url"] = "https://cms.example/featured.jpg"
    repo.list_posts.return_value = ([document], 1)
    response = await client.get("/posts?status=ready")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["image_url"] == document["image_url"]
    assert item["image_brief"] is None
    assert item["status"] == "ready"
    assert item["posted_at"] is None
    repo.next_post.assert_not_awaited()
    repo.mark_posted.assert_not_awaited()


async def test_legacy_posts_without_image_still_return(api, document):
    client, repo = api
    repo.list_posts.return_value = ([document], 1)
    response = await client.get("/posts")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["image_url"] is None
    assert item["image_brief"] == document["image_brief"]


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "status=invalid"])
async def test_invalid_list_inputs(api, query):
    client, _ = api
    assert (await client.get(f"/posts?{query}")).status_code == 422


async def test_update_posted(api, document):
    client, repo = api
    document["status"] = "posted"
    document["posted_at"] = datetime.now(UTC)
    repo.mark_posted.return_value = document
    response = await client.patch(f"/posts/{document['_id']}/status", json={"status": "posted"})
    assert response.status_code == 200
    assert response.json()["status"] == "posted"
    assert response.json()["posted_at"]


@pytest.mark.parametrize("post_id,status", [("bad-id", "posted"), (str(ObjectId()), "ready")])
async def test_invalid_status_update(api, post_id, status):
    client, repo = api
    response = await client.patch(f"/posts/{post_id}/status", json={"status": status})
    assert response.status_code == 422
    repo.mark_posted.assert_not_awaited()


async def test_update_missing_post(api):
    client, repo = api
    repo.mark_posted.return_value = None
    response = await client.patch(f"/posts/{ObjectId()}/status", json={"status": "posted"})
    assert response.status_code == 404


async def test_api_key(api, monkeypatch):
    monkeypatch.setenv("API_KEY", "test-key")
    get_settings.cache_clear()
    client, repo = api
    client.headers.pop("apikey")
    repo.next_post.return_value = None
    try:
        assert (await client.get("/posts/next")).status_code == 401
        assert (await client.get("/posts/next", headers={"apikey": "wrong"})).status_code == 401
        response = await client.get("/posts/next", headers={"apikey": "test-key"})
        assert response.status_code == 200
        assert response.json() is None
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/posts/next"),
        ("GET", "/posts"),
        ("PATCH", f"/posts/{ObjectId()}/status"),
    ],
)
async def test_all_routes_require_key(api, method, path):
    client, repo = api
    client.headers.pop("apikey")
    body = {"status": "posted"} if method == "PATCH" else None
    assert (await client.request(method, path, json=body)).status_code == 401
    assert (
        await client.request(method, path, json=body, headers={"apikey": "wrong"})
    ).status_code == 401
    assert not repo.mock_calls


async def test_missing_configuration_never_allows_access(api, monkeypatch):
    monkeypatch.setenv("API_KEY", "")
    get_settings.cache_clear()
    client, repo = api
    assert (await client.get("/posts/next")).status_code == 503
    assert not repo.mock_calls


async def test_api_refuses_startup_without_key(monkeypatch):
    from app.main import lifespan

    monkeypatch.setenv("API_KEY", "")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="Defina API_KEY"):
            async with lifespan(app):
                pass
    finally:
        get_settings.cache_clear()


def test_swagger_exposes_api_key_authorization():
    schema = app.openapi()
    assert schema["components"]["securitySchemes"]["APIKeyHeader"] == {
        "type": "apiKey",
        "in": "header",
        "name": "apikey",
        "description": "Informe a API_KEY configurada no .env",
    }
    for url, path in schema["paths"].items():
        for operation in path.values():
            if url == "/health":
                assert "security" not in operation
            else:
                assert operation["security"] == [{"APIKeyHeader": []}]


@pytest.mark.parametrize("method", ["GET", "HEAD"])
async def test_health_is_public_and_does_not_access_dependencies(method):
    def unavailable():
        raise AssertionError("Health não deve depender de autenticação ou MongoDB")

    app.dependency_overrides[repository] = unavailable
    app.dependency_overrides[authenticate] = unavailable
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.request(method, "/health")
        assert response.status_code == 200
        if method == "GET":
            assert response.json() == {"status": "ok"}
        else:
            assert response.content == b""
    finally:
        app.dependency_overrides.clear()
