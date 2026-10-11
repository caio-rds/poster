import hashlib
import math
import re
from datetime import UTC, datetime

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.config import get_settings
from app.models import PostOut
from app.text import publication_text


def digest(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", text).strip().lower().encode()).hexdigest()


def cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    denominator = math.sqrt(sum(x * x for x in left) * sum(x * x for x in right))
    return sum(a * b for a, b in zip(left, right, strict=True)) / denominator if denominator else 0


def public_post(document: dict, image_base_url: str | None = None) -> PostOut:
    image_base_url = get_settings().public_api_url or image_base_url
    source_url = document.get("image_url") or get_settings().default_post_image_url
    return PostOut.model_validate(
        {
            **document,
            "id": str(document["_id"]),
            "caption": publication_text(document["title"], document["caption"]),
            "image_source_url": source_url,
            "image_url": (
                f"{image_base_url.rstrip('/')}/posts/{document['_id']}/image.jpg"
                if image_base_url and source_url else source_url
            ),
        }
    )


class PostRepository:
    def __init__(self, db):
        self.db = db

    async def image_post(self, post_id: ObjectId):
        return await self.db.posts.find_one(
            {"_id": post_id, "archived": {"$ne": True}}, {"image_url": 1}
        )

    async def next_post(self):
        now = datetime.now(UTC)
        return await self.db.posts.find_one_and_update(
            {"status": "ready", "archived": {"$ne": True}},
            {"$set": {"status": "posted", "posted_at": now, "updated_at": now}},
            sort=[("created_at", 1), ("_id", 1)],
            return_document=ReturnDocument.AFTER,
        )

    async def list_posts(self, status, offset, limit):
        query = {"archived": {"$ne": True}}
        if status:
            query["status"] = status
        total = await self.db.posts.count_documents(query)
        items = await (
            self.db.posts.find(query)
            .sort([("created_at", -1), ("_id", -1)])
            .skip(offset)
            .limit(limit)
            .to_list(length=limit)
        )
        return items, total

    async def mark_posted(self, post_id: ObjectId):
        now = datetime.now(UTC)
        document = await self.db.posts.find_one_and_update(
            {"_id": post_id, "status": "ready", "archived": {"$ne": True}},
            {"$set": {"status": "posted", "posted_at": now, "updated_at": now}},
            return_document=ReturnDocument.AFTER,
        )
        return document or await self.db.posts.find_one(
            {"_id": post_id, "archived": {"$ne": True}}
        )

    async def nearest(self, vector, model, limit=5):
        # Examina todos os posts, inclusive os publicados, sem depender de MongoDB Atlas.
        best = []
        cursor = self.db.posts.find(
            {
                "embedding_model": model,
                "$or": [{"archived": {"$ne": True}}, {"status": "posted"}],
            },
            {"embedding": 1, "title": 1, "caption": 1, "topic": 1},
        )
        async for item in cursor:
            best.append((cosine(vector, item["embedding"]), item))
            best.sort(key=lambda pair: pair[0], reverse=True)
            del best[limit:]
        return best

    async def insert(self, document):
        try:
            return (await self.db.posts.insert_one(document)).inserted_id
        except DuplicateKeyError:
            return None
