"""Structure-aware chunking.

Reference documents are organised as monographs: a bold heading, followed by
labelled fields ("Standard adult dose:", "Contraindications:" ...). The unit of
retrieval is one field of one monograph, because that is the unit a prescribing
parameter lives in and the finest level a citation can point to
(section > monograph > field, plus page). Free paragraphs, bullets and tables
outside monographs become their own chunks. Oversized paragraphs are split on
sentence boundaries with a small overlap.
"""

import re
from dataclasses import dataclass

from app.ingest.pdf import Block, PageContent

SECTION_RE = re.compile(r"^(?:[IVXLC]+|\d+)\.\s+\S")
TERMINAL = (".", "!", "?", ":", ";", ")")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(])")


@dataclass
class ChunkDraft:
    ordinal: int
    kind: str
    page_start: int
    page_end: int
    section_title: str | None
    subsection_title: str | None
    monograph: str | None
    monograph_key: str | None
    field_label: str | None
    text: str


def monograph_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())[:120]


def embedding_input(c) -> str:
    """Text that is embedded: the chunk body prefixed with its place in the document."""
    context = " > ".join(p for p in [c.section_title, c.monograph, c.field_label or c.subsection_title] if p)
    return f"{context}\n{c.text}" if context else c.text


def _is_heading(b: Block) -> bool:
    return (
        b.kind == "text"
        and b.fully_bold
        and b.bold_prefix is None
        and len(b.text.split()) <= 12
        and len(b.text) <= 120
    )


def _is_field(b: Block) -> bool:
    return b.kind == "text" and b.bold_prefix is not None and len(b.bold_prefix.split()) <= 6


def _split_long(text: str, max_words: int, overlap: int) -> list[str]:
    if len(text.split()) <= max_words:
        return [text]
    sentences = SENTENCE_RE.split(text)
    out: list[str] = []
    cur: list[str] = []
    count = 0
    for s in sentences:
        n = len(s.split())
        if cur and count + n > max_words:
            out.append(" ".join(cur))
            tail: list[str] = []
            words = 0
            for prev in reversed(cur):
                words += len(prev.split())
                tail.insert(0, prev)
                if words >= overlap:
                    break
            cur, count = tail, sum(len(t.split()) for t in tail)
        cur.append(s)
        count += n
    if cur:
        out.append(" ".join(cur))
    return out


def _split_table(md: str, max_words: int) -> list[str]:
    lines = md.split("\n")
    if len(md.split()) <= max_words or len(lines) <= 3:
        return [md]
    header, rows = lines[:2], lines[2:]
    out, cur, count = [], [], 0
    for r in rows:
        n = len(r.split())
        if cur and count + n > max_words:
            out.append("\n".join(header + cur))
            cur, count = [], 0
        cur.append(r)
        count += n
    if cur:
        out.append("\n".join(header + cur))
    return out


def chunk_pages(
    pages: list[PageContent],
    *,
    detect_monographs: bool,
    max_words: int = 220,
    overlap: int = 30,
) -> list[ChunkDraft]:
    blocks = [b for p in pages for b in p.blocks]
    drafts: list[ChunkDraft] = []
    section = sub = mono = None
    last_field: str | None = None

    def emit(kind: str, text: str, page: int, field_label: str | None = None, page_end: int | None = None):
        pieces = (
            _split_table(text, max_words) if kind == "table" else _split_long(text, max_words, overlap)
        )
        for piece in pieces:
            drafts.append(
                ChunkDraft(
                    ordinal=len(drafts),
                    kind=kind,
                    page_start=page,
                    page_end=page_end or page,
                    section_title=section,
                    subsection_title=sub,
                    monograph=mono,
                    monograph_key=monograph_key(mono) if mono else None,
                    field_label=field_label,
                    text=piece,
                )
            )

    i = 0
    while i < len(blocks):
        b = blocks[i]
        nxt = blocks[i + 1] if i + 1 < len(blocks) else None
        if b.kind == "table":
            emit("table", b.text, b.page)
        elif _is_heading(b):
            if SECTION_RE.match(b.text):
                section, sub, mono, last_field = b.text, None, None, None
            elif detect_monographs and nxt is not None and _is_field(nxt):
                mono, sub, last_field = b.text, None, None
            else:
                sub, mono, last_field = b.text, None, None
        elif _is_field(b):
            label = b.bold_prefix.rstrip(":").strip()
            body = b.text[len(b.bold_prefix):].strip()
            last_field = label
            emit("field", f"{label}: {body}", b.page, field_label=label)
        else:
            continued = (
                drafts
                and b.page > drafts[-1].page_end
                and drafts[-1].kind in ("field", "paragraph")
                and not drafts[-1].text.rstrip().endswith(TERMINAL)
                and b.text[:1].islower()
            )
            if continued:
                drafts[-1].text += " " + b.text
                drafts[-1].page_end = b.page
            elif len(b.text) >= 3:
                emit("paragraph", b.text, b.page, field_label=last_field if mono else None)
        i += 1
    return drafts