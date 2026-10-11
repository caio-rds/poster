from io import BytesIO
from unittest.mock import AsyncMock

import httpx
import pytest
from bson import ObjectId
from fastapi import HTTPException
from PIL import Image

from app.images import download_jpeg, jpeg_bytes, validate_image_url
from app.main import app, repository


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
