from io import BytesIO
from unittest.mock import AsyncMock

import httpx
import pytest
from bson import ObjectId
from fastapi import HTTPException
from PIL import Image

from app.images import download_jpeg, jpeg_bytes, validate_image_url
from app.main import app, repository
from app.repository import PostRepository, digest


def webp():
    output = BytesIO()
    Image.new("RGBA", (1600, 900), (255, 0, 0, 128)).save(output, "WEBP")
    return output.getvalue()


def test_webp_becomes_valid_jpeg():
    with Image.open(BytesIO(jpeg_bytes(webp()))) as image:
        assert image.format == "JPEG"
        assert image.mode == "RGB"
        assert image.size == (1440, 810)


def test_invalid_image_returns_502():
    with pytest.raises(HTTPException) as error:
        jpeg_bytes(b"<html>blocked</html>")
    assert error.value.status_code == 502


@pytest.mark.parametrize("url", [
    "http://cms.zionsoftwarehouse.com.br/a.webp", "https://localhost/a",
    "https://cms.zionsoftwarehouse.com.br.evil.test/a",
    "https://user:pass@cms.zionsoftwarehouse.com.br/a",
    "https://cms.zionsoftwarehouse.com.br:8000/a",
])
def test_rejects_untrusted_images(url):
    with pytest.raises(HTTPException):
        validate_image_url(url)


async def test_downloader_converts_blog_image(monkeypatch):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=webp()))
    monkeypatch.setattr("app.images.httpx.AsyncHTTPTransport", lambda **kwargs: transport)
    content = await download_jpeg("https://cms.zionsoftwarehouse.com.br/a.webp")
    assert content.startswith(b"\xff\xd8")


async def test_cms_tls_failure_falls_back_to_public_http_asset(monkeypatch):
    requests = []

    def handler(request):
        requests.append(str(request.url))
        if request.url.scheme == "https":
            raise httpx.ConnectError("TLS connection reset", request=request)
        assert "authorization" not in request.headers
        return httpx.Response(200, content=webp())

    monkeypatch.setattr(
        "app.images.httpx.AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(handler)
    )
    content = await download_jpeg(
        "https://cms.zionsoftwarehouse.com.br/wp-content/uploads/image.webp"
    )
    assert content.startswith(b"\xff\xd8")
    assert requests == [
        "https://cms.zionsoftwarehouse.com.br/wp-content/uploads/image.webp",
        "http://cms.zionsoftwarehouse.com.br/wp-content/uploads/image.webp",
    ]


async def test_non_asset_tls_failure_never_downgrades(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ConnectError("TLS connection reset", request=request)

    monkeypatch.setattr(
        "app.images.httpx.AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(handler)
    )
    with pytest.raises(HTTPException):
        await download_jpeg("https://cms.zionsoftwarehouse.com.br/wp-json/wp/v2/posts")
    assert len(requests) == 1


async def test_public_image_serves_persistent_cache_without_cms(monkeypatch):
    repo = AsyncMock()
    repo.image_post.return_value = {"image_url": "https://cms.zionsoftwarehouse.com.br/a.webp"}
    content = jpeg_bytes(webp())
    repo.cached_image.return_value = content
    downloader = AsyncMock()
    monkeypatch.setattr("app.main.download_jpeg", downloader)
    app.dependency_overrides[repository] = lambda: repo
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            response = await client.get(f"/posts/{ObjectId()}/image.jpg")
            assert response.status_code == 200
            assert response.content == content
            downloader.assert_not_awaited()
            repo.cache_image.assert_not_awaited()
            repo.next_post.assert_not_awaited()
    finally:
        app.dependency_overrides.clear()


async def test_image_cache_is_persisted_separately_from_posts():
    db = AsyncMock()
    repo = PostRepository(db)
    url = "https://cms.zionsoftwarehouse.com.br/wp-content/uploads/image.webp"
    content = jpeg_bytes(webp())
    db.post_images.find_one.return_value = None
    assert await repo.cached_image(url) is None
    await repo.cache_image(url, content)
    args, kwargs = db.post_images.update_one.call_args
    assert args[0] == {"_id": digest(url)}
    assert args[1]["$set"]["jpeg"] == content
    assert args[1]["$set"]["source_url"] == url
    assert kwargs == {"upsert": True}
    db.post_images.find_one.return_value = {"jpeg": content}
    assert await repo.cached_image(url) == content
    db.posts.update_one.assert_not_awaited()


async def test_redirect_to_private_host_is_not_requested(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://localhost/private"})

    monkeypatch.setattr(
        "app.images.httpx.AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(handler)
    )
    with pytest.raises(HTTPException):
        await download_jpeg("https://cms.zionsoftwarehouse.com.br/a.webp")
    assert len(requests) == 1


async def test_public_image_download_does_not_consume_post(monkeypatch):
    repo = AsyncMock()
    post_id = ObjectId()
    repo.image_post.return_value = {
        "image_url": "https://cms.zionsoftwarehouse.com.br/a.webp"
    }
    repo.cached_image.return_value = None
    app.dependency_overrides[repository] = lambda: repo
    monkeypatch.setattr("app.main.download_jpeg", AsyncMock(return_value=jpeg_bytes(webp())))
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            response = await client.get(f"/posts/{post_id}/image.jpg")
            assert response.status_code == 200
            assert response.headers["content-type"] == "image/jpeg"
            assert response.content.startswith(b"\xff\xd8")
            assert (await client.head(f"/posts/{post_id}/image.jpg")).content == b""
            repo.image_post.assert_awaited_with(post_id)
            repo.next_post.assert_not_awaited()
            repo.mark_posted.assert_not_awaited()
            repo.image_post.return_value = None
            assert (await client.get(f"/posts/{post_id}/image.jpg")).status_code == 404
            assert (await client.get("/posts/invalid/image.jpg")).status_code == 422
    finally:
        app.dependency_overrides.clear()
