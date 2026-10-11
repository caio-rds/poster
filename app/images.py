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


def validate_image_url(url: str):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https" or parsed.hostname not in IMAGE_HOSTS
        or parsed.username or parsed.password or parsed.port not in (None, 443)
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
        transport = httpx.AsyncHTTPTransport(retries=2)
        async with httpx.AsyncClient(transport=transport, timeout=30) as client:
            # Validate each redirect before connecting, including its destination host.
            for _ in range(4):
                async with client.stream("GET", url) as response:
                    if response.is_redirect:
                        url = str(response.url.join(response.headers["location"]))
                        validate_image_url(url)
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
        raise HTTPException(502, "Não foi possível baixar a imagem do blog") from exc
