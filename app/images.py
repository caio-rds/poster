import logging
from io import BytesIO
from urllib.parse import urljoin, urlsplit

from curl_cffi.requests import AsyncSession, RequestsError
from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool

MAX_IMAGE_BYTES = 20 * 1024 * 1024
IMAGE_HOSTS = {
    "cms.zionsoftwarehouse.com.br", "zionsoftwarehouse.com.br",
    "www.zionsoftwarehouse.com.br",
}
logger = logging.getLogger(__name__)


def validate_image_url(url: str):
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(502, "Origem de imagem inválida") from exc
    if (
        parsed.scheme != "https" or port not in (None, 443)
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


async def download_jpeg(url: str) -> bytes:
    validate_image_url(url)
    try:
        # The CMS accepts browser TLS/HTTP fingerprints but resets httpx connections.
        async with AsyncSession(impersonate="chrome", timeout=30) as client:
            # Validate each redirect before connecting, including its destination host.
            for _ in range(4):
                async with client.stream("GET", url, allow_redirects=False) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        url = urljoin(url, response.headers["location"])
                        validate_image_url(url)
                        continue
                    response.raise_for_status()
                    content = bytearray()
                    async for chunk in response.aiter_content():
                        content.extend(chunk)
                        if len(content) > MAX_IMAGE_BYTES:
                            raise HTTPException(502, "Imagem muito grande")
                    return await run_in_threadpool(jpeg_bytes, bytes(content))
        raise HTTPException(502, "Redirecionamentos demais na imagem")
    except (RequestsError, KeyError) as exc:
        logger.warning("Falha ao baixar imagem %s: %s %r", url, type(exc).__name__, exc)
        raise HTTPException(502, "Não foi possível baixar a imagem do blog") from exc
