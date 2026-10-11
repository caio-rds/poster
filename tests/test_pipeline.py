from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.collectors import blog_source_urls
from app.config import Settings
from app.models import Draft, DuplicateDecision, Generation, Source
from app.repository import PostRepository, cosine, digest
from app.worker import Pipeline, WorkerLease


@pytest.fixture
def pipeline():
    db = SimpleNamespace(sources=AsyncMock(), posts=AsyncMock())
    db.sources.find_one.return_value = None
    db.posts.find_one.return_value = None
    agent = AsyncMock()
    agent.embed.return_value = [1.0, 0.0]
    agent.generate.return_value = Generation(
        should_create=True,
        reason="Recorte novo",
        post=Draft(
            title="Sites rápidos",
            caption="Seu site precisa acompanhar seu negócio. #Zion",
            hashtags=["#Zion"],
            image_brief="Arte sobre velocidade",
            topic="Performance web",
            source_urls=["https://zionsoftwarehouse.com.br/blog/performance"],
        ),
    )
    agent.duplicate.return_value = DuplicateDecision(
        is_duplicate=False, reason="Ideia diferente", similar_post_id=None
    )
    instance = Pipeline(db, agent, Settings(_env_file=None))
    instance.repo = AsyncMock()
    instance.repo.nearest.return_value = []
    instance.repo.insert.return_value = ObjectId()
    return instance


@pytest.fixture
def source():
    return Source(
        url="https://zionsoftwarehouse.com.br/blog/performance",
        title="Performance",
        text="Conteúdo original sobre performance web.",
        kind="blog",
        image_url="https://cms.example/featured.jpg",
    )


async def test_saves_complete_ready_post(pipeline, source):
    await pipeline.process(source)
    document = pipeline.repo.insert.call_args.args[0]
    assert document["status"] == "ready"
    assert document["embedding"] == [1, 0]
    assert document["posted_at"] is None
    assert document["source_urls"] == [source.url]
    assert document["content_hash"] == digest(document["caption"])


async def test_processed_source_does_not_call_openai(pipeline, source):
    pipeline.db.sources.find_one.return_value = {"outcome": "created"}
    await pipeline.process(source)
    pipeline.agent.embed.assert_not_awaited()
    pipeline.repo.insert.assert_not_awaited()


async def test_blog_image_and_brief_are_persisted(pipeline, source):
    source.image_url = "https://cms.example/featured.jpg"
    await pipeline.process(source)
    document = pipeline.repo.insert.call_args.args[0]
    assert document["image_url"] == source.image_url
    assert document["image_brief"] == "Arte sobre velocidade"
    assert document["status"] == "ready"
    assert document["posted_at"] is None


async def test_processed_blog_updates_image_only_without_regenerating(pipeline, source):
    source.image_url = "https://cms.example/featured.jpg"
    pipeline.db.sources.find_one.return_value = {"outcome": "created"}
    await pipeline.process(source)
    pipeline.db.posts.update_many.assert_awaited_once_with(
        {
            "source_urls": {"$in": blog_source_urls(source.url)},
            "image_url": {"$ne": source.image_url},
        },
        {"$set": {"image_url": source.image_url}},
    )
    pipeline.agent.generate.assert_not_awaited()
    pipeline.agent.embed.assert_not_awaited()
    pipeline.repo.insert.assert_not_awaited()


async def test_missing_wordpress_image_does_not_erase_existing_image(pipeline, source):
    source.image_url = None
    pipeline.db.sources.find_one.return_value = {"outcome": "created"}
    await pipeline.process(source)
    pipeline.db.posts.update_many.assert_not_awaited()


@pytest.mark.parametrize("kind,image", [
    ("blog", None),
    ("blog", "https://zionsoftwarehouse.com.br/og-image.webp"),
    ("website", "https://cms.example/featured.jpg"),
])
async def test_generation_requires_blog_with_real_image(pipeline, source, kind, image):
    source.kind = kind
    source.image_url = image
    await pipeline.process(source)
    pipeline.repo.insert.assert_not_awaited()
    pipeline.agent.generate.assert_not_awaited()
    pipeline.agent.embed.assert_not_awaited()
    pipeline.db.sources.update_one.assert_not_awaited()


async def test_recovers_insert_before_source_record(pipeline, source):
    pipeline.db.posts.find_one.return_value = {"_id": ObjectId()}
    await pipeline.process(source)
    pipeline.agent.generate.assert_not_awaited()
    pipeline.db.sources.update_one.assert_awaited_once()


async def test_semantic_threshold_rejects(pipeline, source):
    pipeline.repo.nearest.return_value = [(0.95, {"_id": ObjectId()})]
    await pipeline.process(source)
    pipeline.repo.insert.assert_not_awaited()
    pipeline.agent.duplicate.assert_not_awaited()
    record = pipeline.db.sources.update_one.call_args.args[1]["$set"]
    assert record["outcome"] == "duplicate"


async def test_agent_rejects_paraphrase_below_threshold(pipeline, source):
    similar_id = str(ObjectId())
    pipeline.repo.nearest.return_value = [(0.7, {"_id": ObjectId(similar_id)})]
    pipeline.agent.duplicate.return_value = DuplicateDecision(
        is_duplicate=True, reason="Mesmo recorte", similar_post_id=similar_id
    )
    await pipeline.process(source)
    pipeline.repo.insert.assert_not_awaited()
    pipeline.agent.duplicate.assert_awaited_once()


async def test_openai_failure_leaves_source_retryable(pipeline, source):
    pipeline.agent.generate.side_effect = RuntimeError("OpenAI indisponível")
    with pytest.raises(RuntimeError):
        await pipeline.process(source)
    pipeline.db.sources.update_one.assert_not_awaited()


async def test_instagram_import_is_posted(pipeline):
    source = Source(
        url="https://www.instagram.com/p/example/",
        title="Post existente",
        text="Legenda original",
        kind="instagram",
        external_id="123",
        published_at=datetime.now(UTC),
    )
    await pipeline.process(source)
    document = pipeline.repo.insert.call_args.args[0]
    assert document["status"] == "posted"
    assert document["source_key"] == "instagram:123"
    assert document["posted_at"] == source.published_at
    pipeline.agent.generate.assert_not_awaited()


async def test_unique_constraint_rejects_exact_duplicate():
    db = SimpleNamespace(posts=AsyncMock())
    db.posts.insert_one.side_effect = DuplicateKeyError("duplicate")
    assert await PostRepository(db).insert({}) is None


async def test_mark_posted_is_idempotent():
    db = SimpleNamespace(posts=AsyncMock())
    original = {"_id": ObjectId(), "status": "posted", "posted_at": datetime.now(UTC)}
    db.posts.find_one_and_update.return_value = None
    db.posts.find_one.return_value = original
    assert await PostRepository(db).mark_posted(original["_id"]) == original
    assert db.posts.find_one_and_update.call_args.args[0]["status"] == "ready"


async def test_nearest_considers_entire_matching_model_history():
    async def cursor():
        for index in range(50):
            yield {"_id": ObjectId(), "embedding": [1, index / 50], "title": str(index)}

    db = SimpleNamespace(posts=Mock())
    db.posts.find.return_value = cursor()
    nearest = await PostRepository(db).nearest([1, 0], "model", limit=5)
    assert len(nearest) == 5
    assert nearest[0][1]["title"] == "0"
    assert db.posts.find.call_args.args[0] == {
        "embedding_model": "model",
        "$or": [{"archived": {"$ne": True}}, {"status": "posted"}],
    }


async def test_archived_posts_are_excluded_from_api_operations():
    db = SimpleNamespace(posts=Mock())
    db.posts.find_one_and_update = AsyncMock(return_value=None)
    db.posts.find_one = AsyncMock(return_value=None)
    db.posts.count_documents = AsyncMock(return_value=0)
    cursor = Mock()
    cursor.sort.return_value = cursor
    cursor.skip.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(return_value=[])
    db.posts.find.return_value = cursor
    repo = PostRepository(db)
    assert await repo.list_posts("ready", 0, 20) == ([], 0)
    db.posts.find.assert_called_once_with({"archived": {"$ne": True}, "status": "ready"})
    assert await repo.next_post() is None
    assert db.posts.find_one_and_update.call_args.args[0]["archived"] == {"$ne": True}
    assert await repo.mark_posted(ObjectId()) is None
    assert db.posts.find_one.call_args.args[0]["archived"] == {"$ne": True}


async def test_worker_cycle_fetches_only_cms_for_new_posts(pipeline):
    import httpx

    pipeline.reindex = AsyncMock()
    pipeline.process = AsyncMock()
    pipeline.settings.instagram_required = False
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.host == "cms.zionsoftwarehouse.com.br"
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await pipeline.cycle(client)
    assert len(requests) == 1
    pipeline.process.assert_not_awaited()


async def test_worker_does_not_run_when_another_has_lease():
    db = SimpleNamespace(worker_locks=AsyncMock())
    db.worker_locks.find_one_and_update.side_effect = DuplicateKeyError("locked")
    operation = AsyncMock()
    await WorkerLease(db).run(operation)
    operation.assert_not_awaited()


async def test_worker_cancels_job_on_lease_loss():
    import asyncio

    db = SimpleNamespace(worker_locks=AsyncMock())
    lease = WorkerLease(db)
    lease.acquire = AsyncMock(return_value=True)
    lease.heartbeat = AsyncMock(side_effect=RuntimeError("lost"))
    cancelled = asyncio.Event()

    async def operation():
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    with pytest.raises(RuntimeError, match="lost"):
        await lease.run(operation)
    assert cancelled.is_set()
    db.worker_locks.delete_one.assert_awaited_once_with({"_id": "editorial", "owner": lease.owner})


def test_hash_and_cosine():
    assert digest(" Olá   Mundo ") == digest("olá mundo")
    assert cosine([1, 0], [1, 0]) == 1
    assert cosine([1, 0], [0, 1]) == 0
    assert cosine([0, 0], [1, 0]) == 0
    assert cosine([1], [1, 0]) == 0
