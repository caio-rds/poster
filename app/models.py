from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class PostStatus(StrEnum):
    ready = "ready"
    posted = "posted"


class Draft(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    caption: str = Field(min_length=1, max_length=2200)
    topic: str = Field(min_length=1, max_length=300)
    source_urls: list[str] = Field(min_length=1)


class Generation(BaseModel):
    should_create: bool
    reason: str
    post: Draft | None


class DuplicateDecision(BaseModel):
    is_duplicate: bool
    reason: str
    similar_post_id: str | None


class Source(BaseModel):
    url: str
    title: str
    text: str
    kind: str
    external_id: str | None = None
    published_at: datetime | None = None
    image_url: str | None = None


class PostOut(Draft):
    image_url: str | None = None
    # Compatibilidade com posts antigos; novos posts usam a imagem da fonte.
    image_brief: str | None = None
    id: str
    status: PostStatus
    created_at: datetime
    updated_at: datetime
    posted_at: datetime | None = None
    origin: str


class PostPage(BaseModel):
    items: list[PostOut]
    total: int
    offset: int
    limit: int


class StatusUpdate(BaseModel):
    status: Literal["posted"] = "posted"
