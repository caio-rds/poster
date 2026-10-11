import re

EMOJI_PATTERN = re.compile(
    r"[#*0-9]\ufe0f?\u20e3|["
    r"\U0001f000-\U0001faff"
    r"\u2600-\u27bf"
    r"\u00a9\u00ae\u203c\u2049\u2122\u2139"
    r"\u2194-\u2199\u21a9\u21aa\u231a\u231b\u2328\u23cf"
    r"\u23e9-\u23f3\u23f8-\u23fa\u24c2\u25aa\u25ab\u25b6\u25c0\u25fb-\u25fe"
    r"\u2934\u2935\u2b05-\u2b07\u2b1b\u2b1c\u2b50\u2b55\u3030\u303d\u3297\u3299"
    r"]"
)
EMOJI_COMPONENTS = re.compile(r"[\u200d\ufe0e\ufe0f\u20e3\U000e0020-\U000e007f]")


def remove_emojis(text: str) -> str:
    """Limpa emojis e componentes de sequências, preservando acentos e listas."""
    cleaned = EMOJI_COMPONENTS.sub("", EMOJI_PATTERN.sub(" ", text))
    if cleaned == text:
        return text
    lines = [re.sub(r"[ \t]{2,}", " ", line).strip() for line in cleaned.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def remove_hashtags(text: str) -> str:
    """Remove hashtags sem apagar fragmentos de URL ou nomes como C#."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    pattern = re.compile(r"(?:https?://|www\.)\S+|(?<![\w/#])#\w+")
    lines = []
    for line in text.split("\n"):
        cleaned = pattern.sub(lambda match: "" if match[0].startswith("#") else match[0], line)
        if cleaned != line:
            cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
        lines.append(cleaned)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
