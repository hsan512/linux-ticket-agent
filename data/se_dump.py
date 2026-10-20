"""Helpers for parsing Stack Exchange dump XML (Posts.xml, Users.xml)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from html.parser import HTMLParser
from pathlib import Path

# Fabricated rows ("watermark v1") use fixed IDs >= 1_000_000_000; real U&L IDs are far below.
# https://github.com/LunarWatcher/se-data-dump-transformer/blob/master/docs/meta/Known%20watermarks.md
WATERMARK_MIN_ID = 1_000_000_000

QUESTION = "1"
ANSWER = "2"

_BLOCK_TAGS = {"p", "pre", "div", "li", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "hr"}


def iter_rows(path: Path) -> Iterator[dict[str, str]]:
    """Stream <row> attributes from a dump XML file without loading it into memory."""
    for _, elem in ET.iterparse(path, events=("end",)):
        if elem.tag == "row":
            yield dict(elem.attrib)
        elem.clear()


def is_watermark(row: dict[str, str]) -> bool:
    return int(row["Id"]) >= WATERMARK_MIN_ID


def parse_tags(raw: str | None) -> list[str]:
    """Tags appear as '<a><b>' in older dumps and '|a|b|' in newer ones."""
    if not raw:
        return []
    if raw.startswith("<"):
        return re.findall(r"<([^>]+)>", raw)
    return [t for t in raw.split("|") if t]


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self.parts.append("\n")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS:
            self.parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(html: str) -> str:
    """Convert post HTML to plain text, keeping code block content and line breaks."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
