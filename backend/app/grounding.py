"""Locating quoted text in a document.

Used for two things: finding the page of a quote the LLM returned, and
rejecting quotes that do not exist in the source (hallucination control).
"""

import re

from rapidfuzz import fuzz

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> str:
    return _NON_ALNUM.sub(" ", text.lower()).strip()


def score_in(quote: str, haystack: str) -> float:
    """0 to 100: how well `quote` appears inside `haystack`."""
    q, h = normalize(quote), normalize(haystack)
    if len(q) < 4 or not h:
        return 0.0
    if q in h:
        return 100.0
    return float(fuzz.partial_ratio(q, h))


def locate(quote: str, pages: dict[int, str], threshold: float) -> tuple[int, float] | None:
    """Best page for the quote, or None if no page reaches the threshold."""
    best: tuple[int, float] | None = None
    for number, text in pages.items():
        s = score_in(quote, text)
        if s >= threshold and (best is None or s > best[1]):
            best = (number, s)
    return best


def page_mentioning(term: str, pages: dict[int, str]) -> int | None:
    n = normalize(term)
    if not n:
        return None
    for number, text in pages.items():
        if n in normalize(text):
            return number
    return None
