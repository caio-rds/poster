import pytest
from pydantic import ValidationError

from app.models import Draft
from app.text import POST_TEXT_LIMIT, publication_text, remove_emojis, remove_hashtags


@pytest.mark.parametrize(
    "original,expected",
    [
        ("Legenda.\n\n#Zion #Inovação", "Legenda."),
        ("Tecnologia #Software para sua empresa.", "Tecnologia para sua empresa."),
        ("Programação C# e C++", "Programação C# e C++"),
        ("https://site.test/#secao", "https://site.test/#secao"),
        ("https://site.test/?x=#secao #Zion", "https://site.test/?x=#secao"),
        ("Título\r\n\r\n• Item um\r\n• Item dois\r\n\r\n#Zion", "Título\n\n• Item um\n• Item dois"),
    ],
)
def test_removes_hashtags_and_preserves_text_urls_and_lists(original, expected):
    assert remove_hashtags(original) == expected


@pytest.mark.parametrize(
    "original,expected",
    [
        ("\U0001f680 Software sob medida \U0001f4bb", "Software sob medida"),
        ("\U0001f469\u200d\U0001f4bb Tecnologia \U0001f1e7\U0001f1f7", "Tecnologia"),
        ("\U0001f44d\U0001f3fd Ação e inovação \u2764\ufe0f", "Ação e inovação"),
        ("1\ufe0f\u20e3 Primeiro item", "Primeiro item"),
        ("C# e C++: programação", "C# e C++: programação"),
        ("• Segurança\n• Desempenho", "• Segurança\n• Desempenho"),
        ("Software\U0001f680rápido", "Software rápido"),
    ],
)
def test_removes_emoji_sequences_without_damaging_text(original, expected):
    assert remove_emojis(original) == expected


def test_generated_draft_is_saved_without_emojis():
    draft = Draft(
        title="\U0001f680 Software",
        caption="Inovação \U0001f4bb para sua empresa.",
        topic="\U0001f1e7\U0001f1f7 Tecnologia",
        source_urls=["https://site.test/blog/artigo"],
    )
    assert draft.title == "Software"
    assert draft.caption == "Inovação para sua empresa."
    assert draft.topic == "Tecnologia"


@pytest.mark.parametrize("field", ["title", "caption", "topic"])
def test_emoji_only_required_field_cannot_become_empty_post(field):
    values = {
        "title": "Software",
        "caption": "Inovação para sua empresa.",
        "topic": "Tecnologia",
        "source_urls": ["https://site.test/blog/artigo"],
    }
    values[field] = "\U0001f680"
    with pytest.raises(ValidationError):
        Draft(**values)


def test_publication_text_includes_title_once_and_preserves_short_caption():
    text = publication_text("Software sob medida", "Soluções para seu negócio.")
    assert text == "Software sob medida\n\nSoluções para seu negócio."
    assert publication_text("Software sob medida", text) == text


def test_long_publication_text_keeps_complete_sentences_and_final_cta():
    title = "Seu site pode evoluir"
    sentence = "Documentação e código próprio dão autonomia à sua empresa."
    body = " ".join([sentence] * 25) + "\n\nConverse com a Zion."
    text = publication_text(title, body)
    assert len(text) <= POST_TEXT_LIMIT
    assert text.startswith(title + "\n\n" + sentence)
    assert text.endswith("\n\nConverse com a Zion.")
    assert publication_text(title, text) == text


def test_very_long_sentence_is_shortened_on_word_boundary():
    text = publication_text("Título", "palavra " * 500)
    assert len(text) <= POST_TEXT_LIMIT
    assert text.endswith("palavra...")
