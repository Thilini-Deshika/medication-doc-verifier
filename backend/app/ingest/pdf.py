"""Document normalisation and text extraction.

Every upload is normalised to PDF (DOCX is converted with LibreOffice) so that
page numbers are real, the same viewer renders every document, and citations
can point at a page. Extraction keeps paragraph blocks together with their
bold formatting, which the chunker uses to detect headings and labelled fields.
"""

import io
import logging
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

logger = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF-"


class DocumentError(Exception):
    """The document is unreadable or unsupported. Safe to show to the user."""


@dataclass
class Block:
    page: int
    text: str
    kind: str = "text"  # text | table
    bold_prefix: str | None = None
    fully_bold: bool = False
    y0: float = 0.0


@dataclass
class PageContent:
    number: int
    blocks: list[Block] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(b.text for b in self.blocks)


def detect_format(data: bytes) -> str:
    """Return 'pdf' or 'docx' from file content, or raise DocumentError."""
    if data[:5] == PDF_MAGIC:
        return "pdf"
    if data[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                if "word/document.xml" in zf.namelist():
                    return "docx"
        except zipfile.BadZipFile:
            pass
    raise DocumentError("Unsupported or corrupt file. Upload a PDF or DOCX document.")


def docx_to_pdf(data: bytes, timeout: int = 120) -> bytes:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise DocumentError("DOCX conversion is unavailable: LibreOffice is not installed.")
    with tempfile.TemporaryDirectory(prefix="conv_") as tmp:
        src = Path(tmp) / "input.docx"
        src.write_bytes(data)
        profile = Path(tmp) / "profile"
        cmd = [
            soffice,
            f"-env:UserInstallation=file://{profile}",
            "--headless",
            "--norestore",
            "--convert-to",
            "pdf",
            "--outdir",
            tmp,
            str(src),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise DocumentError("DOCX conversion timed out.") from exc
        except subprocess.CalledProcessError as exc:
            logger.error("docx conversion failed", extra={"fields": {"stderr": exc.stderr[-500:].decode(errors="replace")}})
            raise DocumentError("DOCX conversion failed.") from exc
        out = Path(tmp) / "input.pdf"
        if not out.exists():
            raise DocumentError("DOCX conversion produced no output.")
        return out.read_bytes()


def to_pdf(data: bytes) -> bytes:
    return data if detect_format(data) == "pdf" else docx_to_pdf(data)


def _is_bold(span: dict) -> bool:
    return bool(span["flags"] & 16) or "bold" in span["font"].lower()


def _block_from_dict(page_no: int, raw: dict) -> Block | None:
    parts: list[str] = []
    prefix: list[str] = []
    prefix_open = True
    all_bold = True
    seen = False
    for line in raw["lines"]:
        line_text = ""
        for span in line["spans"]:
            t = span["text"]
            if not t.strip():
                line_text += t
                continue
            seen = True
            bold = _is_bold(span)
            all_bold = all_bold and bold
            if prefix_open and bold:
                prefix.append(t)
            else:
                prefix_open = False
            line_text += t
        parts.append(line_text.strip())
    text = " ".join(p for p in parts if p).strip()
    if not text or not seen:
        return None
    bp = "".join(prefix).strip()
    bold_prefix = bp if bp.endswith(":") and len(text) > len(bp) else None
    return Block(page=page_no, text=text, bold_prefix=bold_prefix, fully_bold=all_bold, y0=raw["bbox"][1])


def _table_markdown(rows: list[list[str | None]]) -> str:
    clean = [[(c or "").replace("\n", " ").strip() for c in row] for row in rows]
    clean = [r for r in clean if any(r)]
    if not clean:
        return ""
    width = max(len(r) for r in clean)
    clean = [r + [""] * (width - len(r)) for r in clean]
    lines = ["| " + " | ".join(clean[0]) + " |", "|" + "|".join(["---"] * width) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in clean[1:]]
    return "\n".join(lines)


def _table_rows(page: pymupdf.Page, table) -> list[list[str | None]]:
    # Clipping the page text to each cell keeps word spacing intact, which
    # Table.extract() loses on narrow, wrapped cells.
    rows: list[list[str | None]] = []
    for row in table.rows:
        cells: list[str | None] = []
        for cell in row.cells:
            if cell is None:
                cells.append("")
                continue
            rect = pymupdf.Rect(cell) + (1, 1, -1, -1)
            cells.append(" ".join(page.get_text("text", clip=rect).split()))
        rows.append(cells)
    return rows


def extract_pages(pdf: bytes) -> list[PageContent]:
    try:
        doc = pymupdf.open(stream=pdf, filetype="pdf")
    except Exception as exc:
        raise DocumentError("The PDF could not be opened.") from exc
    pages: list[PageContent] = []
    with doc:
        if doc.needs_pass:
            raise DocumentError("Password protected PDFs are not supported.")
        for index, page in enumerate(doc, start=1):
            blocks: list[Block] = []
            table_boxes: list[pymupdf.Rect] = []
            try:
                finder = page.find_tables()
                for table in finder.tables:
                    md = _table_markdown(_table_rows(page, table))
                    if md:
                        table_boxes.append(pymupdf.Rect(table.bbox))
                        blocks.append(Block(page=index, text=md, kind="table", y0=table.bbox[1]))
            except Exception:
                logger.warning("table detection failed", extra={"fields": {"page": index}})
            for raw in page.get_text("dict")["blocks"]:
                if raw.get("type") != 0:
                    continue
                center = pymupdf.Point((raw["bbox"][0] + raw["bbox"][2]) / 2, (raw["bbox"][1] + raw["bbox"][3]) / 2)
                if any(box.contains(center) for box in table_boxes):
                    continue
                block = _block_from_dict(index, raw)
                if block:
                    blocks.append(block)
            blocks.sort(key=lambda b: b.y0)
            pages.append(PageContent(number=index, blocks=blocks))
    if not any(p.text.strip() for p in pages):
        raise DocumentError("No extractable text found. Scanned documents are not supported.")
    return pages