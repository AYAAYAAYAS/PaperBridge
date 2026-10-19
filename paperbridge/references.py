"""Find citations in an imported PDF and resolve public publisher pages."""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass

import pymupdf

from paperbridge.network import urlopen

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s<>\]\[\"']+", re.I)
NUMBER_RE = re.compile(r"^\s*\[(\d{1,4})\]\s*")
HEADING_RE = re.compile(r"^\s*(references?|bibliography|参考文献)\s*$", re.I)
ARXIV_RE = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|arxiv:\s*)(\d{4}\.\d{4,5})(?:v\d+)?", re.I)
AUTHOR_YEAR_RE = re.compile(r"^[A-Z][\w'-]+,\s.{2,100}?\b(?:19|20)\d{2}[a-z]?\s*\.", re.I)
FOOTER_RE = re.compile(r"(?:\b\d{1,3}\s*Preprint\b|^This preprint research paper|^Electronic copy available at:|^https?://ssrn\.com/abstract=)", re.I)


@dataclass(frozen=True)
class Reference:
    citation: str
    page: int
    number: str = ""
    doi: str = ""
    arxiv: str = ""

    @property
    def direct_url(self) -> str:
        if self.doi:
            return "https://doi.org/" + urllib.parse.quote(self.doi, safe="/")
        if self.arxiv:
            return "https://arxiv.org/abs/" + self.arxiv
        return ""


def _doi(text: str) -> str:
    match = DOI_RE.search(text)
    return match.group().rstrip(".,;:)]}") if match else ""


def extract_references(document: pymupdf.Document) -> list[Reference]:
    """Extract numbered entries and DOI-terminated entries near the end."""

    start = None
    first = max(0, len(document) // 2)
    for page_index in range(first, len(document)):
        for line in document[page_index].get_text(sort=True).splitlines():
            if HEADING_RE.fullmatch(line.strip()):
                start = page_index
                break
        if start is not None:
            break
    if start is None:
        start = max(0, len(document) - 5)

    references: list[Reference] = []
    pending: list[str] = []
    pending_page = start + 1
    pending_number = ""
    has_heading = any(HEADING_RE.fullmatch(line.strip()) for line in document[start].get_text(sort=True).splitlines())
    heading_seen = False

    def append_pending():
        nonlocal pending, pending_number
        citation = " ".join(pending).strip()
        if len(citation) >= 30 and (heading_seen or _doi(citation) or pending_number):
            arxiv = ARXIV_RE.search(citation)
            references.append(Reference(citation, pending_page, pending_number,
                                        _doi(citation), arxiv.group(1) if arxiv else ""))
        pending = []
        pending_number = ""

    for page_index in range(start, len(document)):
        for raw_line in document[page_index].get_text(sort=True).splitlines():
            line = " ".join(raw_line.split())
            if not line:
                continue
            if HEADING_RE.fullmatch(line):
                heading_seen = True
                continue
            if not has_heading and NUMBER_RE.match(line):
                heading_seen = True
            if not heading_seen:
                continue
            if FOOTER_RE.search(line):
                if not pending_number:
                    pending = []
                continue
            number = NUMBER_RE.match(line)
            if number:
                append_pending()
                pending_page = page_index + 1
                pending_number = number.group(1)
            elif not pending_number and pending and AUTHOR_YEAR_RE.match(line):
                append_pending()
                pending_page = page_index + 1
            elif not pending:
                pending_page = page_index + 1
            # On unnumbered bibliographies, a DOI normally closes an entry.
            pending.append(line)
            if not pending_number and (_doi(line) or ARXIV_RE.search(line)):
                append_pending()
    append_pending()
    # A whole page may contain one block of references; numbers still split it.
    seen = set()
    unique = []
    for item in references:
        marker = item.doi or item.arxiv or item.citation[:120]
        if marker not in seen:
            seen.add(marker)
            unique.append(item)
    return unique


def resolve_reference(reference: Reference, timeout: int = 8) -> str:
    """Prefer a public full-text page, then fall back to the publisher DOI."""

    doi = reference.doi
    if not doi and reference.arxiv:
        return reference.direct_url
    if not doi:
        query = urllib.parse.urlencode({"query.bibliographic": reference.citation[:500],
                                        "rows": 3, "select": "DOI,title,score"})
        request = urllib.request.Request(
            "https://api.crossref.org/works?" + query,
            headers={"User-Agent": "PaperBridge/1.0 (reference lookup)",
                     "Accept": "application/json"},
        )
        with urlopen(request, timeout=timeout) as response:
            items = json.load(response).get("message", {}).get("items", [])
        citation_words = set(re.findall(r"[a-z]{3,}", reference.citation.lower()))
        for item in items:
            title = (item.get("title") or [""])[0]
            title_words = set(re.findall(r"[a-z]{3,}", title.lower()))
            # Bibliographic matches must share most title words to avoid opening a wrong paper.
            if len(title_words) >= 4 and len(title_words & citation_words) / len(title_words) >= 0.7:
                doi = item.get("DOI", "")
                if doi:
                    break
    if not doi:
        return ""
    doi_url = "https://doi.org/" + urllib.parse.quote(doi, safe="/")
    try:
        request = urllib.request.Request(
            "https://api.openalex.org/works/" + doi_url + "?select=best_oa_location",
            headers={"User-Agent": "PaperBridge/1.0 (open-access reference lookup)",
                     "Accept": "application/json"},
        )
        with urlopen(request, timeout=timeout) as response:
            location = json.load(response).get("best_oa_location") or {}
        for candidate in (location.get("landing_page_url"), location.get("pdf_url")):
            if candidate and urllib.parse.urlsplit(candidate).scheme in ("https", "http"):
                return candidate
    except (OSError, ValueError, TypeError):
        pass
    return doi_url
