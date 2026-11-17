"""Text helpers."""
import re


def slugify(title):
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower())
    return slug.strip("-")


def wrap_words(text, width):
    lines = []
    line = ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = line + " " + word if line else word
    if line:
        lines.append(line)
    return lines
