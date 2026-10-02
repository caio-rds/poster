from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bson import ObjectId

from app.agent import EditorialAgent, generation_format
from app.config import Settings
from app.models import Draft, DuplicateDecision, Generation, Source


@pytest.fixture
def agent():
    instance = EditorialAgent(Settings(_env_file=None, openai_api_key="fake-key"))
    instance.client = SimpleNamespace(
        responses=SimpleNamespace(parse=AsyncMock()),
        embeddings=SimpleNamespace(create=AsyncMock()),
    )
    return instance


async def test_generation_refusal_is_retryable(agent):
    agent.client.responses.parse.return_value = SimpleNamespace(output_parsed=None)
    with pytest.raises(ValueError, match="post válido"):
        await agent.generate(
            Source(url="https://site/", title="Site", text="Dado", kind="blog"), []
        )


async def test_generation_rejects_invented_source(agent):
    agent.client.responses.parse.return_value = SimpleNamespace(
        output_parsed=Generation(
            should_create=True,
            reason="Novo",
            post=Draft(
                title="Post",
                caption="Legenda",
                hashtags=[],
                topic="Software",
                image_brief="Arte",
                source_urls=["https://invented.example/"],
            ),
        )
    )
    with pytest.raises(ValueError, match="fonte não fornecida"):
        await agent.generate(
            Source(url="https://site/", title="Site", text="Dado", kind="blog"), []
        )


async def test_duplicate_rejects_unknown_id(agent):
    agent.client.responses.parse.return_value = SimpleNamespace(
        output_parsed=DuplicateDecision(
            is_duplicate=True,
            reason="Igual",
            similar_post_id=str(ObjectId()),
        )
    )
    post = Draft(
        title="Post",
        caption="Legenda",
        hashtags=[],
        topic="Software",
        image_brief="Arte",
        source_urls=["https://site/"],
    )
    nearest = [(0.7, {"_id": ObjectId(), "title": "Outro", "caption": "Legenda", "topic": "SW"})]
    with pytest.raises(ValueError, match="ID de duplicata desconhecido"):
        await agent.duplicate(post, nearest)


def test_worker_requires_openai_key():
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        EditorialAgent(Settings(_env_file=None))


@pytest.mark.parametrize(
    "hashtags,caption",
    [
        (["#Zion"], "Texto #Zion"),
        (["#Zion", "#Sites", "#Software"], "Texto sem hashtags"),
        (["Zion", "#Sites", "#Software"], "Texto Zion #Sites #Software"),
        (["#Zion", "#Zion", "#Software"], "Texto #Zion #Software"),
    ],
)
async def test_generated_hashtags_are_validated(agent, hashtags, caption):
    source = Source(url="https://site/", title="Site", text="Dado", kind="blog")
    agent.client.responses.parse.return_value = SimpleNamespace(
        output_parsed=Generation(
            should_create=True,
            reason="Recorte",
            post=Draft(
                title="Post",
                caption=caption,
                hashtags=hashtags,
                topic="Software",
                image_brief="Arte",
                source_urls=[source.url],
            ),
        )
    )
    with pytest.raises(ValueError, match="[Hh]ashtags"):
        await agent.generate(source, [])


async def test_complete_generated_caption_is_preserved(agent):
    source = Source(url="https://site/", title="Site", text="Dado", kind="blog")
    post = Draft(
        title="Post",
        caption="Texto final pronto. Fale com a Zion. #Zion #Sites #Software",
        hashtags=["#Zion", "#Sites", "#Software"],
        topic="Software",
        image_brief="Arte",
        source_urls=[source.url],
    )
    agent.client.responses.parse.return_value = SimpleNamespace(
        output_parsed=Generation(
            should_create=True,
            reason="Recorte",
            post=post,
        )
    )
    result = await agent.generate(source, [])
    assert result.post.caption == post.caption
    request = agent.client.responses.parse.call_args.kwargs
    assert request["store"] is False
    assert request["input"][0]["role"] == "system"
    assert "o texto final a publicar" in request["input"][0]["content"]


async def test_generation_schema_requires_exact_translated_source(agent):
    url = "https://zionsoftwarehouse.com.br/es/blog/geo-ecommerce-ia-respostas"
    agent.client.responses.parse.return_value = SimpleNamespace(output_parsed=None)
    with pytest.raises(ValueError, match="post válido"):
        await agent.generate(Source(url=url, title="Artículo", text="Contenido", kind="blog"), [])
    schema = agent.client.responses.parse.call_args.kwargs["text_format"].model_json_schema()
    urls = schema["$defs"]["SourceDraft"]["properties"]["source_urls"]
    assert urls["items"]["enum"] == [url]
    assert urls["minItems"] == urls["maxItems"] == 1
    hashtags = schema["$defs"]["SourceDraft"]["properties"]["hashtags"]
    assert hashtags["minItems"] == 3
    assert hashtags["maxItems"] == 6


def test_generation_schema_does_not_reuse_another_source_url():
    first = generation_format("https://site/first").model_json_schema()
    second = generation_format("https://site/second").model_json_schema()
    assert first["$defs"]["SourceDraft"]["properties"]["source_urls"]["items"]["enum"] == [
        "https://site/first"
    ]
    assert second["$defs"]["SourceDraft"]["properties"]["source_urls"]["items"]["enum"] == [
        "https://site/second"
    ]
