"""Stages 1-2 - ingest and document analysis.

Decides, from the file itself, whether the text layer is usable or whether the
pages have to be read as images. The threshold is in rules.yaml, and the
reasoning is surfaced to the UI rather than buried.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field

import pdfplumber
import pymupdf

from ..config import Rules
from ..rules.codes import Finding

PDF_MAGIC = b"%PDF-"


class IngestError(Exception):
    """The file cannot be processed at all. Carries the reason code to show."""

    def __init__(self, code: str, **evidence) -> None:
        super().__init__(code)
        self.code = code
        self.evidence = evidence


@dataclass
class DocumentInfo:
    sha256: str
    size_bytes: int
    page_count: int
    text: str
    chars_per_page: float
    is_scanned: bool
    page_images: list[bytes] = field(default_factory=list)

    @property
    def path_label(self) -> str:
        return "scanned (vision)" if self.is_scanned else "text layer"


def ingest(data: bytes, filename: str = "") -> tuple[str, int]:
    """Stage 1 - accept the file, or refuse it with a clear reason."""
    if not data:
        raise IngestError("FILE_EMPTY")
    if not data.startswith(PDF_MAGIC):
        detected = _sniff(data, filename)
        raise IngestError("FILE_NOT_PDF", detected=detected)
    return hashlib.sha256(data).hexdigest(), len(data)


def _sniff(data: bytes, filename: str) -> str:
    signatures = {
        b"\x89PNG": "PNG image", b"\xff\xd8\xff": "JPEG image",
        b"PK\x03\x04": "ZIP / Office document", b"GIF8": "GIF image",
        b"{": "JSON or text", b"<": "HTML or XML",
    }
    for magic, label in signatures.items():
        if data.startswith(magic):
            return label
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return f".{suffix} file" if suffix else "unrecognised file type"


def analyse(data: bytes, rules: Rules, render_dpi: int = 160) -> tuple[DocumentInfo, list[Finding]]:
    """Stage 2 - open the PDF and decide which extraction path it needs."""
    sha256, size = ingest(data)

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise IngestError("FILE_CORRUPT", detail=str(exc)[:200]) from exc

    try:
        if doc.needs_pass:
            raise IngestError("FILE_ENCRYPTED")
        page_count = doc.page_count
        if page_count == 0:
            raise IngestError("FILE_CORRUPT", detail="the PDF contains no pages")

        try:
            with pdfplumber.open(io.BytesIO(data)) as plumbed:
                pages = [(p.extract_text() or "").strip() for p in plumbed.pages]
        except Exception as exc:
            raise IngestError("FILE_CORRUPT", detail=str(exc)[:200]) from exc

        text = "\n\n".join(pages).strip()
        chars_per_page = len(text) / page_count if page_count else 0.0
        threshold = rules.integer("extraction.scanned_page_char_threshold")
        is_scanned = chars_per_page < threshold

        images: list[bytes] = []
        if is_scanned:
            # Only render when we actually need pixels - rendering every page
            # of every text PDF would waste most of a run's time budget.
            for page in doc:
                images.append(page.get_pixmap(dpi=render_dpi).tobytes("png"))

        info = DocumentInfo(
            sha256=sha256, size_bytes=size, page_count=page_count, text=text,
            chars_per_page=round(chars_per_page, 1), is_scanned=is_scanned,
            page_images=images,
        )
    finally:
        doc.close()

    findings: list[Finding] = []
    if is_scanned:
        findings.append(Finding("SCANNED_DOCUMENT", "document_analysis", {
            "chars": len(text), "pages": page_count,
            "threshold": threshold, "chars_per_page": info.chars_per_page,
        }))
    return info, findings
