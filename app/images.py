import logging
from io import BytesIO
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool

MAX_IMAGE_BYTES = 20 * 1024 * 1024
IMAGE_HOSTS = {
    "cms.zionsoftwarehouse.com.br", "zionsoftwarehouse.com.br",
    "www.zionsoftwarehouse.com.br",
}
logger = logging.getLogger(__name__)


def validate_image_url(url: str, *, allow_cms_http=False):
    parsed = urlsplit(url)
    cms_http = (
        allow_cms_http and parsed.scheme == "http"
        and parsed.hostname == "cms.zionsoftwarehouse.com.br"
        and parsed.path.startswith("/wp-content/uploads/")
        and parsed.port in (None, 80)
    )
    if (
        (not cms_http and (parsed.scheme != "https" or parsed.port not in (None, 443)))
        or parsed.hostname not in IMAGE_HOSTS or parsed.username or parsed.password
    ):
        raise HTTPException(502, "Origem de imagem não permitida")


def jpeg_bytes(content: bytes) -> bytes:
    try:
        with Image.open(BytesIO(content)) as original:
            if original.width * original.height > 40_000_000:
                raise ValueError("Imagem muito grande")
            image = ImageOps.exif_transpose(original).convert("RGBA")
            image.thumbnail((1440, 1440))
            background = Image.new("RGB", image.size, "white")
            background.paste(image, mask=image.getchannel("A"))
            output = BytesIO()
            background.save(output, "JPEG", quality=90, optimize=True)
            return output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(502, "Arquivo de imagem inválido") from exc


async def fetch_jpeg(url: str, *, allow_cms_http=False) -> bytes:
    validate_image_url(url, allow_cms_http=allow_cms_http)
    try:
        transport = httpx.AsyncHTTPTransport(retries=2)
        async with httpx.AsyncClient(transport=transport, timeout=30) as client:
            # Validate each redirect before connecting, including its destination host.
            for _ in range(4):
                async with client.stream("GET", url) as response:
                    if response.is_redirect:
                        url = str(response.url.join(response.headers["location"]))
                        validate_image_url(url, allow_cms_http=allow_cms_http)
                        continue
                    response.raise_for_status()
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_IMAGE_BYTES:
                            raise HTTPException(502, "Imagem muito grande")
                    return await run_in_threadpool(jpeg_bytes, bytes(content))
        raise HTTPException(502, "Redirecionamentos demais na imagem")
    except (httpx.HTTPError, KeyError) as exc:
        logger.warning("Falha ao baixar imagem %s: %s %r", url, type(exc).__name__, exc)
        raise HTTPException(502, "Não foi possível baixar a imagem do blog") from exc


async def download_jpeg(url: str) -> bytes:
    validate_image_url(url)
    try:
        return await fetch_jpeg(url)
    except HTTPException as exc:
        parsed = urlsplit(url)
        if (
            not isinstance(exc.__cause__, httpx.TransportError)
            or parsed.hostname != "cms.zionsoftwarehouse.com.br"
            or not parsed.path.startswith("/wp-content/uploads/")
        ):
            raise
        # Public blog assets only; never send credentials over the HTTP fallback.
        return await fetch_jpeg(parsed._replace(scheme="http", netloc=parsed.hostname).geturl(),
                                allow_cms_http=True)
