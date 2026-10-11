from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bson import ObjectId
from pydantic import ValidationError

from app.agent import EditorialAgent, format_caption, generation_format
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
    "caption",
    [
        "Texto #Zion",
        "Texto sem hashtags",
        "Texto #Zion #Sites #Software",
    ],
)
async def test_generation_removes_hashtags(agent, caption):
    source = Source(url="https://site/", title="Site", text="Dado", kind="blog")
    agent.client.responses.parse.return_value = SimpleNamespace(
        output_parsed=Generation(
            should_create=True,
            reason="Recorte",
            post=Draft(
                title="Post",
                caption=caption,
                topic="Software",
                image_brief="Arte",
                source_urls=[source.url],
            ),
        )
    )
    result = await agent.generate(source, [])
    assert "#" not in result.post.caption
    assert "hashtags" not in result.post.model_dump()


@pytest.mark.parametrize("separator", [" ", "\n\n"])
async def test_complete_generated_caption_is_preserved(agent, separator):
    source = Source(url="https://site/", title="Site", text="Dado", kind="blog")
    post = Draft(
        title="Post",
        caption="Texto final pronto.\n\nFale com a Zion." + separator + "#Zion #Sites #Software",
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
    assert result.post.caption == (
        "Texto final pronto.\n\nFale com a Zion."
    )
    request = agent.client.responses.parse.call_args.kwargs
    assert request["store"] is False
    assert request["input"][0]["role"] == "system"
    assert "o texto final a publicar" in request["input"][0]["content"]


def test_caption_removes_trailing_hashtags():
    assert format_caption("Fale com a Zion. #Zion #Sites #Software") == "Fale com a Zion."


def test_caption_breaks_dense_text_without_changing_words():
    body = " ".join([
        "Você consegue evoluir seu site sem depender do fornecedor?",
        "O código próprio permite que sua empresa tenha controle sobre o projeto.",
        "A documentação explica as tecnologias e as integrações utilizadas no sistema.",
        "Com essas informações, sua equipe pode planejar melhorias conforme o negócio cresce.",
        "Converse com a Zion para entender como desenvolver um sistema sob medida.",
    ])
    formatted = format_caption(body + " #Zion #Sites #Software")
    blocks = formatted.split("\n\n")
    assert len(blocks) >= 2
    assert " ".join(blocks) == body


def test_caption_preserves_lists_and_normalizes_line_endings():
    caption = (
        "Seu site sob controle.\r\n\r\n• Código próprio\r\n• Documentação"
        "\r\n\r\nFale com a Zion.\r\n\r\n#Zion #Sites #Software"
    )
    assert format_caption(caption) == (
        "Seu site sob controle.\n\n• Código próprio\n• Documentação\n\nFale com a Zion."
    )


async def test_formatted_generation_respects_caption_limit(agent):
    source = Source(url="https://site/", title="Site", text="Dado", kind="blog")
    caption = ("A" * 298 + ". ") * 7 + "B" * 99
    agent.client.responses.parse.return_value = SimpleNamespace(
        output_parsed=Generation(
            should_create=True,
            reason="Recorte",
            post=Draft(
                title="Post", caption=caption, hashtags=["#Zion", "#Sites", "#Software"],
                topic="Software", image_brief="Arte", source_urls=[source.url],
            ),
        )
    )
    with pytest.raises(ValidationError):
        await agent.generate(source, [])


async def test_generation_schema_requires_exact_translated_source(agent):
    url = "https://zionsoftwarehouse.com.br/es/blog/geo-ecommerce-ia-respostas"
    agent.client.responses.parse.return_value = SimpleNamespace(output_parsed=None)
    with pytest.raises(ValueError, match="post válido"):
        await agent.generate(Source(url=url, title="Artículo", text="Contenido", kind="blog"), [])
    schema = agent.client.responses.parse.call_args.kwargs["text_format"].model_json_schema()
    urls = schema["$defs"]["SourceDraft"]["properties"]["source_urls"]
    assert urls["items"]["enum"] == [url]
    assert urls["minItems"] == urls["maxItems"] == 1
    assert "hashtags" not in schema["$defs"]["SourceDraft"]["properties"]
    assert schema["$defs"]["SourceDraft"]["properties"]["title"]["maxLength"] == 100
    assert schema["$defs"]["SourceDraft"]["properties"]["caption"]["maxLength"] == 500


def test_generation_schema_does_not_reuse_another_source_url():
    first = generation_format("https://site/first").model_json_schema()
    second = generation_format("https://site/second").model_json_schema()
    assert first["$defs"]["SourceDraft"]["properties"]["source_urls"]["items"]["enum"] == [
        "https://site/first"
    ]
    assert second["$defs"]["SourceDraft"]["properties"]["source_urls"]["items"]["enum"] == [
        "https://site/second"
    ]


async def test_generation_does_not_request_or_require_image_brief(agent):
    source = Source(url="https://site/", title="Site", text="Dado", kind="blog")
    agent.client.responses.parse.return_value = SimpleNamespace(
        output_parsed=Generation(
            should_create=True,
            reason="Novo recorte",
            post=Draft(
                title="Post",
                caption="Texto final. #Zion #Sites #Software",
                hashtags=["#Zion", "#Sites", "#Software"],
                topic="Software",
                source_urls=[source.url],
            ),
        )
    )
    result = await agent.generate(source, [])
    assert result.post is not None
    request = agent.client.responses.parse.call_args.kwargs
    properties = request["text_format"].model_json_schema()["$defs"]["SourceDraft"]["properties"]
    assert "image_brief" not in properties
    assert "image_url" not in properties
    assert "image_brief" not in request["input"][0]["content"]
