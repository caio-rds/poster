import argparse
import asyncio
import contextlib
import logging
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.agent import EditorialAgent, embedding_text
from app.collectors import (
    USER_AGENT,
    InstagramCollector,
    WordPressCollector,
    blog_source_urls,
)
from app.config import Settings, get_settings
from app.database import create_client, ensure_indexes
from app.errors import failure_hint
from app.models import Draft, Source
from app.repository import PostRepository, digest

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, db, agent: EditorialAgent, settings: Settings):
        self.db = db
        self.repo = PostRepository(db)
        self.agent = agent
        self.settings = settings

    async def record(self, fingerprint, source, outcome, reason, post_id=None):
        await self.db.sources.update_one(
            {"fingerprint": fingerprint},
            {
                "$set": {
                    "url": source.url,
                    "kind": source.kind,
                    "outcome": outcome,
                    "reason": reason,
                    "post_id": str(post_id) if post_id else None,
                    "processed_at": datetime.now(UTC),
                }
            },
            upsert=True,
        )

    async def reindex(self):
        # Mantém todo o histórico comparável quando o modelo de embedding é alterado.
        model = self.settings.openai_embedding_model
        async for document in self.db.posts.find({"embedding_model": {"$ne": model}}):
            draft = Draft.model_validate(document)
            vector = await self.agent.embed(embedding_text(draft))
            await self.db.posts.update_one(
                {"_id": document["_id"]},
                {"$set": {"embedding": vector, "embedding_model": model}},
            )

    async def process(self, source: Source):
        if source.kind != "instagram" and (
            source.kind != "blog"
            or not source.image_url
            or source.image_url == self.settings.default_post_image_url
        ):
            logger.info("Fonte ignorada: não é artigo com imagem destacada (%s)", source.url)
            return
        if source.kind == "blog" and source.image_url:
            # Atualiza também posts antigos sem regenerar legendas ou alterar status/datas.
            await self.db.posts.update_many(
                {
                    "source_urls": {"$in": blog_source_urls(source.url)},
                    "image_url": {"$ne": source.image_url},
                },
                {"$set": {"image_url": source.image_url}},
            )
        fingerprint = digest(f"{source.url}\n{source.text}")
        if await self.db.sources.find_one({"fingerprint": fingerprint}):
            return
        source_key = (
            f"instagram:{source.external_id}" if source.kind == "instagram" else fingerprint
        )
        existing = await self.db.posts.find_one({"source_key": source_key})
        if existing:
            await self.record(fingerprint, source, "existing", "Já persistido", existing["_id"])
            return
        if source.kind == "instagram":
            # O histórico real publicado é referência de estilo e de deduplicação.
            post = Draft(
                title=source.title,
                caption=source.text,
                hashtags=[],
                topic=source.title,
                source_urls=[source.url],
            )
            vector = await self.agent.embed(embedding_text(post))
            status = "posted"
        else:
            source_vector = await self.agent.embed(f"{source.title}\n{source.text}")
            nearest = await self.repo.nearest(source_vector, self.settings.openai_embedding_model)
            generated = await self.agent.generate(source, nearest)
            if not generated.should_create:
                await self.record(fingerprint, source, "skipped", generated.reason)
                return
            post = generated.post
            vector = await self.agent.embed(embedding_text(post))
            nearest = await self.repo.nearest(vector, self.settings.openai_embedding_model)
            if nearest and nearest[0][0] >= self.settings.similarity_threshold:
                await self.record(fingerprint, source, "duplicate", "Similaridade acima do limiar")
                return
            if nearest:
                decision = await self.agent.duplicate(post, nearest)
                if decision.is_duplicate:
                    await self.record(
                        fingerprint, source, "duplicate", decision.reason, decision.similar_post_id
                    )
                    return
            status = "ready"
        now = datetime.now(UTC)
        document = {
            **post.model_dump(),
            "image_url": source.image_url,
            "status": status,
            "origin": source.kind,
            "created_at": now,
            "updated_at": now,
            "posted_at": source.published_at if status == "posted" else None,
            "content_hash": digest(post.caption),
            "source_key": source_key,
            "embedding": vector,
            "embedding_model": self.settings.openai_embedding_model,
            "generation_model": self.settings.openai_model if status == "ready" else None,
        }
        post_id = await self.repo.insert(document)
        await self.record(fingerprint, source, "created" if post_id else "duplicate", "", post_id)
        logger.info(
            "Fonte processada: %s, resultado=%s", source.url, status if post_id else "duplicate"
        )

    async def cycle(self, client):
        await self.reindex()
        failures = 0
        instagram_configured = bool(
            self.settings.instagram_user_id
            and self.settings.instagram_access_token.get_secret_value()
        )
        if instagram_configured or self.settings.instagram_required:
            try:
                async for source in InstagramCollector(client, self.settings).collect():
                    await self.process(source)
            except Exception as exc:
                # Não loga exceção HTTP de Instagram: pode conter credenciais.
                logger.warning("Coleta do Instagram indisponível (%s)", type(exc).__name__)
                if self.settings.instagram_required:
                    raise RuntimeError("Instagram obrigatório indisponível") from None
        async for source in WordPressCollector(client, self.settings).collect():
            try:
                await self.process(source)
            except Exception as exc:
                failures += 1
                logger.error(
                    "Falha ao processar %s: %s; tentará novamente",
                    source.url,
                    failure_hint(exc),
                )
        if failures:
            raise RuntimeError(f"{failures} fonte(s) não processada(s)")


class WorkerLease:
    """Uma única escrita editorial por vez, com renovação e cancelamento ao perder lease."""

    def __init__(self, db):
        self.collection = db.worker_locks
        self.owner = str(uuid.uuid4())

    async def acquire(self):
        now = datetime.now(UTC)
        try:
            doc = await self.collection.find_one_and_update(
                {"_id": "editorial", "expires_at": {"$lte": now}},
                {"$set": {"owner": self.owner, "expires_at": now + timedelta(seconds=120)}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
            return bool(doc and doc["owner"] == self.owner)
        except DuplicateKeyError:
            return False

    async def heartbeat(self):
        while True:
            await asyncio.sleep(20)
            now = datetime.now(UTC)
            result = await self.collection.update_one(
                {"_id": "editorial", "owner": self.owner, "expires_at": {"$gt": now}},
                {"$set": {"expires_at": now + timedelta(seconds=120)}},
            )
            if not result.modified_count:
                raise RuntimeError("Lease do worker perdido")

    async def run(self, operation):
        if not await self.acquire():
            logger.info("Outro worker está processando; ciclo ignorado")
            return
        job = asyncio.create_task(operation())
        heartbeat = asyncio.create_task(self.heartbeat())
        try:
            done, _ = await asyncio.wait({job, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in (job, heartbeat):
                task.cancel()
            for task in (job, heartbeat):
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            await self.collection.delete_one({"_id": "editorial", "owner": self.owner})


async def run(once=False):
    settings = get_settings()
    agent = EditorialAgent(settings)
    mongo = create_client(settings)
    try:
        db = mongo[settings.mongodb_database]
        pipeline = Pipeline(db, agent, settings)
        lease = WorkerLease(db)
        transport = httpx.AsyncHTTPTransport(retries=3)
        async with httpx.AsyncClient(
            timeout=settings.request_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            transport=transport,
        ) as client:
            while True:
                delay = settings.worker_interval_seconds
                try:
                    await ensure_indexes(db)
                    await lease.run(lambda: pipeline.cycle(client))
                except Exception as exc:
                    logger.error("Ciclo falhou: %s", failure_hint(exc))
                    if once:
                        raise
                    delay = settings.worker_retry_seconds
                if once:
                    return
                await asyncio.sleep(delay)
    finally:
        await agent.client.close()
        await mongo.close()


def main(default_once=False):
    parser = argparse.ArgumentParser(description="Worker editorial Zion")
    parser.add_argument("--once", action="store_true", help="Executa somente um ciclo")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    try:
        asyncio.run(run(once=args.once or default_once))
    except Exception as exc:
        logger.error("Worker encerrado: %s", failure_hint(exc))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
