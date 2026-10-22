from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from bson import ObjectId

from app.config import Settings, client_settings
from app.main import create_app


def document(title):
    now = datetime.now(UTC)
    return {
        "_id": ObjectId(), "title": title, "caption": "Texto do cliente.",
        "topic": title, "image_brief": "Arte", "source_urls": ["https://example.com"],
        "image_url": "https://blog.cortevisagismo.com.br/wp-content/uploads/corte.png",
        "status": "ready", "origin": "blog", "created_at": now,
        "updated_at": now, "posted_at": None,
    }


@pytest.fixture
async def clients():
    settings = Settings(_env_file=None, api_key="shared", public_api_url="https://api.example")
    api = create_app(settings)
    repos = {name: AsyncMock() for name in ("zion", "corte")}
    for name, repo in repos.items():
        item = document(name)
        repo.list_posts.return_value = ([item], 1)
        repo.next_post.return_value = item
        repo.mark_posted.return_value = item
        repo.image_post.return_value = {"image_url": item["image_url"]}
        repo.cached_image.return_value = name.encode()
    api.state.repositories = repos
    api.state.repository = repos["zion"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api), base_url="http://test",
        headers={"apikey": "shared"},
    ) as client:
        yield client, repos


@pytest.mark.parametrize("name", ["zion", "corte"])
async def test_routes_select_only_requested_client(clients, name):
    client, repos = clients
    other = "corte" if name == "zion" else "zion"
    response = await client.get(f"/{name}/posts?status=ready&offset=0&limit=5")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["title"] == name
    assert item["image_url"] == f"https://api.example/{name}/posts/{item['id']}/image.jpg"
    repos[name].list_posts.assert_awaited_once_with("ready", 0, 5)
    assert not repos[other].mock_calls
    assert (await client.get(f"/{name}/posts/next")).json()["title"] == name
    assert (await client.patch(
        f"/{name}/posts/{item['id']}/status", json={"status": "posted"}
    )).json()["title"] == name
    assert (await client.get(f"/{name}/posts/{item['id']}/image.jpg")).content == name.encode()
    assert not repos[other].mock_calls


async def test_legacy_routes_remain_zion(clients):
    client, repos = clients
    assert (await client.get("/posts/next")).json()["title"] == "zion"
    assert not repos["corte"].mock_calls


async def test_unknown_clients_and_missing_auth_never_access_data(clients):
    client, repos = clients
    assert (await client.get("/unknown/posts")).status_code == 404
    client.headers.pop("apikey")
    assert (await client.get("/corte/posts")).status_code == 401
    assert (await client.get("/zion/posts")).status_code == 401
    assert all(not repo.mock_calls for repo in repos.values())


async def test_id_from_another_client_is_not_found(clients):
    client, repos = clients
    repos["corte"].mark_posted.return_value = None
    repos["corte"].image_post.return_value = None
    post_id = str(repos["zion"].next_post.return_value["_id"])
    assert (await client.patch(
        f"/corte/posts/{post_id}/status", json={"status": "posted"}
    )).status_code == 404
    assert (await client.get(f"/corte/posts/{post_id}/image.jpg")).status_code == 404
    assert not repos["zion"].mock_calls


def test_corte_never_inherits_zion_instagram_credentials():
    settings = Settings(_env_file=None, instagram_user_id="zion-id",
                        instagram_access_token="zion-token", instagram_required=True)
    corte = client_settings(settings, "corte")
    assert corte.mongodb_database != settings.mongodb_database
    assert corte.instagram_username == "corte.visagismo"
    assert corte.instagram_user_id == ""
    assert not corte.instagram_access_token.get_secret_value()
    assert not corte.instagram_required
    assert corte.default_post_image_url is None


def test_same_database_is_rejected():
    with pytest.raises(ValueError, match="diferentes"):
        client_settings(Settings(_env_file=None, corte_mongodb_database="zion"), "corte")


async def test_worker_once_runs_both_even_when_one_fails(monkeypatch):
    from app.worker import run
    visited = []

    async def operation(settings, once):
        visited.append((settings.client_slug, settings.mongodb_database, once))
        if settings.client_slug == "zion":
            raise RuntimeError("failed")

    monkeypatch.setattr("app.worker.get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr("app.worker.run_client", operation)
    with pytest.raises(RuntimeError):
        await run(once=True)
    assert visited == [("zion", "zion", True), ("corte", "corte", True)]


async def test_corte_media_fallback_uses_real_permalink():
    from app.collectors import WordPressCollector
    url = "https://blog.cortevisagismo.com.br/2026/09/28/visagismo/"

    def handler(request):
        if request.url.path.endswith("/posts"):
            assert request.url.host == "blog.cortevisagismo.com.br"
            return httpx.Response(200, json=[{
                "id": 1, "slug": "visagismo", "link": url,
                "title": {"rendered": "Visagismo"},
                "content": {"rendered": "Visagismo masculino. " * 20},
                "_embedded": {"wp:featuredmedia": [{"code": "rest_forbidden"}]},
            }], headers={"X-WP-TotalPages": "1"})
        assert str(request.url) == url.rstrip("/")
        return httpx.Response(200, text=(
            '<meta property="og:image" '
            'content="https://blog.cortevisagismo.com.br/wp-content/uploads/corte.png">'
        ))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sources = [s async for s in WordPressCollector(
            client, client_settings(Settings(_env_file=None), "corte")
        ).collect()]
    assert len(sources) == 1
    assert sources[0].url == url.rstrip("/")
    assert sources[0].image_url.endswith("/corte.png")
