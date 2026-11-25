from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from bson import ObjectId
from fastapi import HTTPException

from app.config import Settings, client_settings
from app.corte import editorial_posts, queue_corte_posts
from app.repository import PostRepository
from app.worker import run_client


def test_editorial_catalog_links_each_caption_to_a_unique_drive_photo():
    posts = editorial_posts()
    assert posts
    assert len({post.file_id for post in posts}) == len(posts)
    assert len({post.caption for post in posts}) == len(posts)
    assert all("Balneário Camboriú" in post.caption for post in posts)
    assert all(post.document()["image_brief"] is None for post in posts)


async def test_corte_queues_only_unmarked_images_and_retries_unavailable_photos(monkeypatch):
    marked, available, unavailable = editorial_posts()[:3]
    monkeypatch.setattr("app.corte.editorial_posts", lambda: [marked, available, unavailable])
    db = SimpleNamespace(
        posts=SimpleNamespace(
            distinct=AsyncMock(side_effect=[[marked.file_id], [marked.file_id, available.file_id]]),
            insert_one=AsyncMock(return_value=SimpleNamespace(inserted_id=ObjectId())),
        ),
        post_images=SimpleNamespace(
            find_one=AsyncMock(return_value=None),
            update_one=AsyncMock(),
        ),
    )
    download = AsyncMock(side_effect=[b"jpeg", HTTPException(502, "Drive"), b"jpeg"])
    monkeypatch.setattr("app.corte.download_corte_image", download)

    with pytest.raises(RuntimeError, match="1 imagem"):
        await queue_corte_posts(db)
    assert db.posts.insert_one.await_count == 1
    assert db.post_images.update_one.await_count == 1
    document = db.posts.insert_one.call_args.args[0]
    assert document["drive_image_id"] == available.file_id
    assert document["image_url"] == available.image_url
    assert document["image_status"] == "marcado"
    assert document["image_marked_at"] == document["created_at"]
    assert document["status"] == "ready"

    await queue_corte_posts(db)
    assert [call.args[0] for call in download.await_args_list] == [
        available.file_id, unavailable.file_id, unavailable.file_id,
    ]
    assert db.posts.insert_one.call_args.args[0]["drive_image_id"] == unavailable.file_id


async def test_corte_worker_runs_without_initializing_the_zion_agent(monkeypatch):
    db = Mock()
    client = MagicMock()
    client.__getitem__.return_value = db
    client.close = AsyncMock()
    agent = Mock()
    queue = AsyncMock()
    indexes = AsyncMock()

    async def run_lease(operation):
        await operation()

    monkeypatch.setattr("app.worker.create_client", lambda settings: client)
    monkeypatch.setattr("app.worker.EditorialAgent", agent)
    monkeypatch.setattr("app.worker.queue_corte_posts", queue)
    monkeypatch.setattr("app.worker.ensure_indexes", indexes)
    monkeypatch.setattr("app.worker.WorkerLease", lambda db: SimpleNamespace(run=run_lease))
    await run_client(client_settings(Settings(_env_file=None), "corte"), once=True)
    agent.assert_not_called()
    indexes.assert_awaited_once_with(db, corte=True)
    queue.assert_awaited_once_with(db)


async def test_corte_preview_and_claim_only_use_the_drive_queue():
    collection = SimpleNamespace(find_one=AsyncMock(), find_one_and_update=AsyncMock())
    db = SimpleNamespace(posts=collection)
    corte = PostRepository(db, corte=True)
    await corte.next_post(mark_posted=False)
    await corte.next_post()
    query = collection.find_one.call_args.args[0]
    assert query == collection.find_one_and_update.call_args.args[0]
    assert query["status"] == "ready"
    assert query["$or"] == [
        {"status": "posted"}, {"origin": "drive", "image_status": "marcado"},
    ]
    await PostRepository(db).next_post(mark_posted=False)
    assert collection.find_one.call_args.args[0] == {
        "status": "ready", "archived": {"$ne": True},
    }
