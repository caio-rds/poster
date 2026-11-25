import asyncio
import logging
import re
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, Field, TypeAdapter

from app.errors import failure_hint
from app.images import download_jpeg
from app.models import Draft
from app.repository import PostRepository, digest

logger = logging.getLogger(__name__)
DRIVE_HOST = "drive.usercontent.google.com"
DRIVE_ID_PATTERN = r"^[A-Za-z0-9_-]+$"
BATCH_SIZE = 4


class EditorialPost(BaseModel):
    file_id: str = Field(pattern=DRIVE_ID_PATTERN)
    file_name: str
    title: str = Field(min_length=1, max_length=100)
    caption: str = Field(min_length=1, max_length=500)

    @property
    def image_url(self) -> str:
        return drive_image_url(self.file_id)

    def document(self) -> dict:
        draft = Draft(
            title=self.title,
            caption=self.caption,
            topic=self.title,
            source_urls=[f"https://drive.google.com/file/d/{self.file_id}/view"],
        )
        now = datetime.now(UTC)
        return {
            **draft.model_dump(),
            "origin": "drive",
            "image_url": self.image_url,
            "drive_image_id": self.file_id,
            "image_status": "marcado",
            "image_marked_at": now,
            "status": "ready",
            "created_at": now,
            "updated_at": now,
            "posted_at": None,
            "content_hash": digest(draft.caption),
            "source_key": f"drive:{self.file_id}",
        }


@lru_cache
def editorial_posts() -> list[EditorialPost]:
    path = Path(__file__).with_name("corte_posts.json")
    return TypeAdapter(list[EditorialPost]).validate_json(path.read_bytes())


def drive_image_url(file_id: str) -> str:
    return f"https://{DRIVE_HOST}/download?id={file_id}&export=download&confirm=t"


async def download_corte_image(file_id: str) -> bytes:
    if not re.fullmatch(DRIVE_ID_PATTERN, file_id):
        raise HTTPException(502, "ID de imagem do Drive inválido")
    return await download_jpeg(drive_image_url(file_id), hosts={DRIVE_HOST})


async def prepare_image(repo: PostRepository, post: EditorialPost) -> None:
    if await repo.cached_image(post.image_url) is None:
        content = await download_corte_image(post.file_id)
        await repo.cache_image(post.image_url, content)


async def queue_corte_posts(db) -> None:
    repo = PostRepository(db, corte=True)
    posts = editorial_posts()
    marked_ids = await db.posts.distinct(
        "drive_image_id", {"drive_image_id": {"$in": [post.file_id for post in posts]}}
    )
    marked = set(marked_ids)
    pending = [post for post in posts if post.file_id not in marked]
    failures = created = 0
    for offset in range(0, len(pending), BATCH_SIZE):
        batch = pending[offset : offset + BATCH_SIZE]
        results = await asyncio.gather(
            *(prepare_image(repo, post) for post in batch),
            return_exceptions=True,
        )
        for post, result in zip(batch, results, strict=True):
            if isinstance(result, Exception):
                failures += 1
                logger.warning(
                    "Imagem Côrte %s indisponível: %s", post.file_name, failure_hint(result)
                )
                continue
            if isinstance(result, BaseException):
                raise result
            if await repo.insert(post.document()) is not None:
                created += 1
    if created:
        logger.info("Côrte Balneário Camboriú: %d publicações preparadas", created)
    if failures:
        raise RuntimeError(f"{failures} imagem(ns) da Côrte não puderam ser preparadas")
