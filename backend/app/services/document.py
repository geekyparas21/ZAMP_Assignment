"""Stages 1-2 - ingest and document analysis.

Three kinds of input reach this module, and it decides between them from the
file's own bytes rather than from its name:

  * a PDF with a usable text layer  -> send the text to the model;
  * a PDF with no text layer        -> render the pages and send images;
  * an image (PNG / JPG / WEBP)     -> there is no text layer to check, so it
                                       goes straight to the vision path.

Images are the common real case: an invoice photographed on a phone or
screenshotted out of an email. Two things are done to one before it is sent -
EXIF orientation is applied, because a phone photo is usually stored rotated
with a tag saying so, and the long edge is capped, because beyond about
2000 px nothing more becomes legible and every extra pixel is free-tier
tokens spent for nothing. Both the original and the processed dimensions are
recorded so the screen can show what was actually sent.

The thresholds are in rules.yaml, and the reasoning is surfaced to the UI
rather than buried.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
from typing import Any

import pdfplumber
import pymupdf
from PIL import Image, ImageOps, UnidentifiedImageError

from ..config import Rules, get_rules
from ..rules.codes import Finding

PDF_MAGIC = b"%PDF-"

#: Accepted image types, by magic bytes. WEBP needs a second check at offset 8
#: because its container is a generic RIFF header.
IMAGE_MAGIC: tuple[tuple[bytes, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "PNG", "image/png"),
    (b"\xff\xd8\xff", "JPG", "image/jpeg"),
)

#: What other signatures are, so a refusal can name what was actually sent
#: instead of saying "unsupported".
OTHER_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"GIF8", "GIF image"),
    (b"PK\x03\x04", "ZIP / Office document"),
    (b"BM", "BMP image"),
    (b"II*\x00", "TIFF image"),
    (b"MM\x00*", "TIFF image"),
    (b"\x00\x00\x01\x00", "Windows icon"),
    (b"%!PS", "PostScript file"),
    (b"{", "JSON or text file"),
    (b"<", "HTML or XML file"),
)


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
    #: pdf_text | pdf_scanned | image - what the badge in the UI shows.
    kind: str = "pdf_text"
    #: The media type the original bytes are served back with.
    media_type: str = "application/pdf"
    #: The media type of everything in page_images.
    image_media_type: str = "image/png"
    #: Present only for an image upload: format and dimensions before and
    #: after orientation and downscaling.
    image: dict[str, Any] | None = None

    @property
    def path_label(self) -> str:
        if self.kind == "image":
            return "image (vision)"
        return "scanned (vision)" if self.is_scanned else "text layer"

    @property
    def kind_label(self) -> str:
        """The short badge: PDF, Scanned PDF or Image."""
        return {"image": "Image", "pdf_scanned": "Scanned PDF"}.get(
            self.kind, "PDF")


# ---------------------------------------------------------------------------
# Stage 1 - ingest
# ---------------------------------------------------------------------------
def accepted_formats(rules: Rules | None = None) -> list[str]:
    return (rules or get_rules()).strings("ingest.accepted_formats")


def accepted_label(rules: Rules | None = None) -> str:
    """'PDF, PNG, JPG, JPEG or WEBP' - for every message that lists them."""
    formats = accepted_formats(rules)
    return ", ".join(formats[:-1]) + f" or {formats[-1]}" if len(formats) > 1 \
        else formats[0]


def max_file_bytes(rules: Rules | None = None) -> int:
    return int((rules or get_rules()).number("ingest.max_file_mb") * 1024 * 1024)


def detect(data: bytes, filename: str = "") -> tuple[str, str, str]:
    """What this file really is, from its content: (kind, label, media type).

    kind is 'pdf' or 'image'. An unsupported type raises, naming what was
    detected, because "that is a GIF" is far more useful to whoever has to fix
    it than "upload failed".
    """
    if data.startswith(PDF_MAGIC):
        return "pdf", "PDF", "application/pdf"

    for magic, label, media_type in IMAGE_MAGIC:
        if data.startswith(magic):
            return "image", label, media_type

    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image", "WEBP", "image/webp"

    raise IngestError("FILE_TYPE_UNSUPPORTED", detected=_sniff(data, filename),
                      accepted=accepted_label())


def _sniff(data: bytes, filename: str) -> str:
    """Name an unsupported file as precisely as its bytes allow.

    The extension is only consulted when the content says nothing at all - a
    .txt renamed to .png must be reported as the text file it is.
    """
    for magic, label in OTHER_MAGIC:
        if data.startswith(magic):
            return label
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix:
        return f"file with an unrecognised format (named .{suffix})"
    return "file of an unrecognised format"


def ingest(data: bytes, filename: str = "",
           rules: Rules | None = None) -> tuple[str, int, str]:
    """Stage 1 - accept the file, or refuse it with a clear reason.

    Returns (sha256, size, kind). The hash is of the original bytes, so
    duplicate detection behaves identically for a PDF and for an image.
    """
    rules = rules or get_rules()
    if not data:
        raise IngestError("FILE_EMPTY")

    limit = max_file_bytes(rules)
    if len(data) > limit:
        raise IngestError("FILE_TOO_LARGE",
                          size_mb=f"{len(data) / (1024 * 1024):.1f}",
                          limit_mb=rules.get("ingest.max_file_mb"))

    kind, _, _ = detect(data, filename)
    return hashlib.sha256(data).hexdigest(), len(data), kind


# ---------------------------------------------------------------------------
# Stage 2 - document analysis
# ---------------------------------------------------------------------------
def analyse(data: bytes, rules: Rules, filename: str = "",
            render_dpi: int = 160) -> tuple[DocumentInfo, list[Finding]]:
    """Stage 2 - work out which extraction path this document needs."""
    sha256, size, kind = ingest(data, filename, rules)
    if kind == "image":
        return _analyse_image(data, sha256, size, rules, filename)
    return _analyse_pdf(data, sha256, size, rules, render_dpi)


def _analyse_pdf(data: bytes, sha256: str, size: int, rules: Rules,
                 render_dpi: int) -> tuple[DocumentInfo, list[Finding]]:
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
            kind="pdf_scanned" if is_scanned else "pdf_text",
            media_type="application/pdf", image_media_type="image/png",
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


def _analyse_image(data: bytes, sha256: str, size: int, rules: Rules,
                   filename: str) -> tuple[DocumentInfo, list[Finding]]:
    """An image has no text layer to test, so it goes straight to vision.

    Orientation is applied first: a phone photo is normally stored in the
    sensor's orientation with an EXIF tag describing the rotation, and a model
    sent the raw pixels would be reading a sideways invoice.
    """
    _, label, media_type = detect(data, filename)
    max_edge = rules.integer("ingest.image.max_long_edge_px")
    quality = rules.integer("ingest.image.jpeg_quality")
    floor = rules.integer("ingest.image.low_quality_short_edge_px")

    try:
        with Image.open(io.BytesIO(data)) as opened:
            opened.load()
            stored_size = opened.size
            # exif_transpose returns a new, upright image; the tag it acted on
            # is reported so the screen can say the rotation was corrected.
            orientation = (opened.getexif() or {}).get(0x0112, 1)
            upright = ImageOps.exif_transpose(opened)
            original_size = upright.size
            if upright.mode != "RGB":
                upright = upright.convert("RGB")

            longest = max(original_size)
            scale = min(1.0, max_edge / longest) if longest else 1.0
            if scale < 1.0:
                target = (max(1, round(original_size[0] * scale)),
                          max(1, round(original_size[1] * scale)))
                upright = upright.resize(target, Image.LANCZOS)
            processed_size = upright.size

            buffer = io.BytesIO()
            upright.save(buffer, format="JPEG", quality=quality, optimize=True)
            payload = buffer.getvalue()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise IngestError("FILE_IMAGE_UNREADABLE", detail=str(exc)[:200]) from exc

    image_detail = {
        "format": label,
        "original_width": original_size[0],
        "original_height": original_size[1],
        "processed_width": processed_size[0],
        "processed_height": processed_size[1],
        "exif_orientation": int(orientation or 1),
        "exif_rotation_applied": stored_size != original_size or int(orientation or 1) > 1,
        "downscaled": processed_size != original_size,
        "max_long_edge_px": max_edge,
        "sent_bytes": len(payload),
        "sent_as": "image/jpeg",
    }

    info = DocumentInfo(
        sha256=sha256, size_bytes=size, page_count=1, text="",
        chars_per_page=0.0,
        # There is no text layer in an image by definition, so the vision path
        # is not a judgement call here - it is the only path.
        is_scanned=True,
        page_images=[payload], kind="image", media_type=media_type,
        image_media_type="image/jpeg", image=image_detail,
    )

    findings = [Finding("IMAGE_UPLOAD", "document_analysis", {
        "format": label, "width": original_size[0], "height": original_size[1],
    })]
    if min(original_size) < floor:
        findings.append(Finding("LOW_IMAGE_QUALITY", "document_analysis", {
            "width": original_size[0], "height": original_size[1],
            "threshold": floor,
        }))
    return info, findings
