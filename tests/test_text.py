import pytest

from app.text import remove_hashtags


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
