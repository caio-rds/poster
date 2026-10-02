from pymongo import AsyncMongoClient

from app.config import Settings


def create_client(settings: Settings) -> AsyncMongoClient:
    return AsyncMongoClient(settings.mongodb_uri, tz_aware=True, serverSelectionTimeoutMS=10000)


async def ensure_indexes(db):
    await db.posts.create_index("content_hash", unique=True)
    await db.posts.create_index("source_key", unique=True)
    await db.posts.create_index([("status", 1), ("created_at", 1), ("_id", 1)])
    await db.posts.create_index("embedding_model")
    await db.sources.create_index("fingerprint", unique=True)
