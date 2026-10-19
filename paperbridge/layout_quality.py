"""Detect visibly misplaced formula fragments in translated PDF pages."""
from __future__ import annotations

import html
import re

import pymupdf


def _has_cjk(text: str) -> bool:
    return any("\u3400" <= char <= "\u9fff" for char in text)


def overlapping_formula_fragments(page: pymupdf.Page) -> int:
    """Count non-Chinese glyph groups drawn on top of translated Chinese text."""

    words = page.get_text("words")
    chinese = [(pymupdf.Rect(word[:4]), word[4]) for word in words if _has_cjk(word[4])]
    fragments = [(pymupdf.Rect(word[:4]), word[4]) for word in words
                 if word[4].strip() and not _has_cjk(word[4])]
    score = 0
    for area, _ in chinese:
        for fragment, text in fragments:
            if fragment.y1 < area.y0 or fragment.y0 > area.y1:
                continue
            if len(text) > 24:
                continue
            overlap = area & fragment
            if overlap.is_empty:
                continue
            smaller = min(area.get_area(), fragment.get_area())
            if smaller > 0 and overlap.get_area() >= 2 and overlap.get_area() / smaller >= 0.25:
                score += 1
    return score


def readable_page_html(page: pymupdf.Page) -> str:
    """Provide a text reading view while formulas remain visible in the source pane."""

    lines = []
    for block in page.get_text("dict", sort=True)["blocks"]:
        if "lines" not in block:
            continue
        for line in block["lines"]:
            text = "".join(span["text"] for span in line["spans"]).strip()
            if _has_cjk(text) and len(re.sub(r"\s", "", text)) >= 3:
                lines.append(html.escape(text))
    intro = "<p style='color:#667c70'>此页检测到行内公式错位。这里仅显示易读译文，公式请对照左侧原文；可点击上方按钮查看原 PDF 排版。</p>"
    body = "".join(f"<p style='line-height:1.8;margin:0 0 12px'>{line}</p>" for line in lines)
    return "<div style='font-family:Microsoft YaHei UI;font-size:14px;margin:24px'>" + intro + body + "</div>"
