import re


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
