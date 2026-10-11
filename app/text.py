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
POST_TEXT_LIMIT = 700


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


def publication_text(title: str, caption: str) -> str:
    """Título e legenda em um campo curto, preservando frases e CTA quando couber."""
    title = remove_hashtags(remove_emojis(title)).strip()
    body = remove_hashtags(remove_emojis(caption)).strip()
    if body == title:
        body = ""
    elif body.startswith(title + "\n"):
        body = body[len(title):].strip()
    if not body:
        return title[:POST_TEXT_LIMIT]
    budget = POST_TEXT_LIMIT - len(title) - 2
    if len(body) <= budget:
        return f"{title}\n\n{body}"

    # Guarda a chamada final, quando há um parágrafo separado e ela cabe no limite.
    paragraphs = body.split("\n\n")
    cta = paragraphs[-1] if len(paragraphs) > 1 else ""
    if len(cta) > 220:
        cta = ""
    main = "\n\n".join(paragraphs[:-1]) if cta else body
    main_budget = budget - len(cta) - (2 if cta else 0)
    sentences = re.split(r"(?<=[.!?])\s+", main)
    selected = []
    for sentence in sentences:
        candidate = " ".join([*selected, sentence])
        if len(candidate) > main_budget:
            break
        selected.append(sentence)
    summary = " ".join(selected)
    if not summary:
        fragment = main[:max(0, main_budget - 3)]
        summary = fragment.rsplit(" ", 1)[0].rstrip(" ,;:") + "..."
    short_body = "\n\n".join(part for part in (summary, cta) if part)
    return f"{title}\n\n{short_body}"
