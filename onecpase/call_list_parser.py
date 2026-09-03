"""OCR and parsing helpers for handwritten/scanned call-list documents.

Ported verbatim from the reference 1Cpace app's call_list_parser.py (Phase 2
of the 1Cpase rewrite) — the multi-variant image preprocessing, OCR-error
tolerant phone normalization, and fuzzy outcome mapping are all untouched.
The one addition is pointing pytesseract at the Tesseract binary explicitly
(TESSERACT_CMD env var, falling back to the common Windows install path),
since relying on PATH turned out to be unreliable across dev machines.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

_DEFAULT_WINDOWS_TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


def _configure_tesseract_path() -> None:
    """Point pytesseract at the Tesseract binary if it isn't already
    resolvable on PATH. Safe to call repeatedly; no-ops once configured."""
    try:
        import pytesseract
    except ImportError:
        return
    configured = os.environ.get("TESSERACT_CMD", "").strip()
    if configured:
        pytesseract.pytesseract.tesseract_cmd = configured
    elif os.name == "nt" and Path(_DEFAULT_WINDOWS_TESSERACT).exists():
        pytesseract.pytesseract.tesseract_cmd = _DEFAULT_WINDOWS_TESSERACT


SUPPORTED_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "tif", "tiff", "pdf"}


class CallListParserError(RuntimeError):
    """Raised when a call-list document cannot be safely processed."""


@dataclass(frozen=True)
class ParsedCallRow:
    client_name: str
    contact: str
    raw_response: str
    mapped_outcome: str
    appointment_date: str | None = None
    appointment_time: str | None = None
    confidence: float = 0.0


@dataclass(frozen=True)
class ExtractionResult:
    rows: list[ParsedCallRow]
    page_count: int
    message: str


_PHONE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:\+?\s*27|[0oOQq@])(?:[\s().\-/]*[0-9oOIl|Qq@]){8,12}(?![A-Za-z0-9])"
)
_HEADER_WORDS = {"name", "surname", "contact", "number", "response", "manager", "comments"}


def normalize_phone(value: str) -> str:
    """Normalize a South African phone number while tolerating common OCR errors."""
    translated = str(value or "").translate(
        str.maketrans({"O": "0", "o": "0", "Q": "0", "q": "0", "@": "0", "I": "1", "l": "1", "|": "1"})
    )
    digits = re.sub(r"\D", "", translated)
    if digits.startswith("00") and len(digits) == 11:
        digits = digits[1:]
    if digits.startswith("27") and len(digits) == 11:
        digits = "0" + digits[2:]
    elif len(digits) == 9 and digits[:1] in {"6", "7", "8"}:
        digits = "0" + digits
    return digits if 9 <= len(digits) <= 11 else ""


def map_response(response: str, *, today: date | None = None) -> tuple[str, str | None]:
    """Map handwritten response wording to the application's call outcomes."""
    reference_day = today or date.today()
    text = re.sub(r"\s+", " ", str(response or "")).strip().lower()
    compact = re.sub(r"[^a-z0-9]", "", text)

    appointment_date: str | None = None
    if re.search(r"\b(tomorrow|tmr|tmrw)\b", text):
        appointment_date = (reference_day + timedelta(days=1)).isoformat()
    elif re.search(r"\btoday\b", text):
        appointment_date = reference_day.isoformat()
    else:
        date_match = re.search(r"\b([0-3]?\d)[/\-.]([01]?\d)(?:[/\-.](\d{2,4}))?\b", text)
        if date_match:
            day, month = int(date_match.group(1)), int(date_match.group(2))
            year_text = date_match.group(3)
            year = reference_day.year if not year_text else int(year_text)
            if year < 100:
                year += 2000
            try:
                appointment_date = date(year, month, day).isoformat()
            except ValueError:
                appointment_date = None

    if any(term in text for term in ("wrong number", "does not exist", "invalid number")) or compact in {"dne", "wn"}:
        return "wrong_number", None
    if "not interested" in text or compact in {"ni", "notinterested"}:
        return "not_interested", None
    if any(term in text for term in ("appointment", "booked", "come in", "interview")) or re.search(r"\bapp\b", text):
        return "appointment_set", appointment_date
    if any(term in text for term in ("call back", "phone later", "try later", "call later")):
        return "call_back_later", appointment_date
    if any(term in text for term in ("voicemail", "voice mail", "left message")) or compact in {"vm", "vmail"}:
        return "voicemail", None
    if any(term in text for term in ("no answer", "not answering", "no response", "did not answer")) or compact in {
        "na", "nr", "np", "noanswer", "notanswering"
    }:
        return "no_answer", None
    return "other", appointment_date


def extract_appointment_time(response: str) -> str | None:
    """Extract a reviewable 24-hour appointment time from OCR text."""
    text = str(response or "").lower()
    match = re.search(r"\b([01]?\d|2[0-3])\s*(?::|h)\s*([0-5]\d)\b", text)
    if not match:
        return None
    return f"{int(match.group(1)):02d}:{int(match.group(2)):02d}"


def parse_ocr_lines(
    lines: Iterable[str | tuple[str, float]], *, today: date | None = None
) -> list[ParsedCallRow]:
    """Parse OCR lines that resemble ``name | phone | response`` rows."""
    rows: list[ParsedCallRow] = []
    seen: set[tuple[str, str]] = set()

    for value in lines:
        if isinstance(value, tuple):
            line, confidence = value
        else:
            line, confidence = value, 0.0
        line = re.sub(r"\s+", " ", str(line or "")).strip(" |\t-_")
        if not line:
            continue
        matches = list(_PHONE_RE.finditer(line))
        if not matches:
            continue
        match = matches[0]
        contact = normalize_phone(match.group(0))
        if not contact:
            continue

        name = re.sub(r"^[#\d\s.()\-]+", "", line[: match.start()]).strip(" |:;,-_")
        response = line[match.end() :].strip(" |:;,-_")
        name = re.sub(r"\b(?:name\s*(?:and|&)\s*surname|contact(?:\s+number)?)\b", "", name, flags=re.I).strip()
        alpha_words = re.findall(r"[A-Za-z][A-Za-z'\-]*", name)
        if alpha_words and set(word.lower() for word in alpha_words) <= _HEADER_WORDS:
            name = ""
        if len(name) > 120:
            name = name[:120].rstrip()
        if len(response) > 300:
            response = response[:300].rstrip()

        outcome, appointment_date = map_response(response, today=today)
        appointment_time = extract_appointment_time(response) if outcome == "appointment_set" else None
        key = (contact, name.lower())
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            ParsedCallRow(
                client_name=name,
                contact=contact,
                raw_response=response,
                mapped_outcome=outcome,
                appointment_date=appointment_date,
                appointment_time=appointment_time,
                confidence=max(0.0, min(float(confidence), 100.0)),
            )
        )
    return rows


def _load_pages(path: Path):
    extension = path.suffix.lower().lstrip(".")
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:  # pragma: no cover - deployment dependency check
        raise CallListParserError("Pillow is not installed on the server.") from exc

    if extension == "pdf":
        try:
            import pypdfium2 as pdfium
        except ImportError as exc:  # pragma: no cover - deployment dependency check
            raise CallListParserError("PDF image support is not installed on the server.") from exc
        try:
            document = pdfium.PdfDocument(str(path))
            return [document[index].render(scale=2.5).to_pil().convert("RGB") for index in range(min(len(document), 5))]
        except Exception as exc:
            raise CallListParserError("The PDF could not be rendered. Try uploading a clear JPG or PNG photo.") from exc

    try:
        image = Image.open(path)
        pages = []
        for index in range(min(getattr(image, "n_frames", 1), 5)):
            image.seek(index)
            pages.append(ImageOps.exif_transpose(image.copy()).convert("RGB"))
        return pages
    except Exception as exc:
        raise CallListParserError("The image could not be opened. Use JPG, PNG, WebP, TIFF, or PDF.") from exc


def _image_variants(image):
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps

    width, height = image.size
    scale = min(3.0, max(1.0, 2200 / max(width, 1)))
    resized = image.resize((int(width * scale), int(height * scale)), Image.Resampling.LANCZOS)
    gray = ImageOps.autocontrast(ImageOps.grayscale(resized), cutoff=1)
    sharp = ImageEnhance.Sharpness(gray).enhance(1.8).filter(ImageFilter.MedianFilter(size=3))
    threshold = sharp.point(lambda pixel: 255 if pixel > 165 else 0)
    return [sharp, threshold, resized]


def _ocr_lines(image, config: str) -> list[tuple[str, float]]:
    _configure_tesseract_path()
    import pytesseract
    from pytesseract import Output

    data = pytesseract.image_to_data(image, config=config, output_type=Output.DICT)
    grouped: dict[tuple[int, int, int, int], list[tuple[str, float]]] = {}
    for index, token in enumerate(data.get("text", [])):
        token = str(token or "").strip()
        if not token:
            continue
        try:
            confidence = float(data["conf"][index])
        except (KeyError, TypeError, ValueError):
            confidence = 0.0
        key = (
            int(data.get("page_num", [0])[index]),
            int(data.get("block_num", [0])[index]),
            int(data.get("par_num", [0])[index]),
            int(data.get("line_num", [0])[index]),
        )
        grouped.setdefault(key, []).append((token, confidence))
    lines = []
    for words in grouped.values():
        text = " ".join(word for word, _ in words)
        usable = [score for _, score in words if score >= 0]
        confidence = sum(usable) / len(usable) if usable else 0.0
        lines.append((text, confidence))
    return lines


def extract_call_list(path: str | Path) -> ExtractionResult:
    """OCR an uploaded call-list image/PDF and return editable candidate rows."""
    document_path = Path(path)
    extension = document_path.suffix.lower().lstrip(".")
    if extension not in SUPPORTED_EXTENSIONS:
        raise CallListParserError("Unsupported call-list file type.")
    _configure_tesseract_path()
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
    except Exception as exc:  # pragma: no cover - depends on host binary
        raise CallListParserError("The OCR engine is not available on the server.") from exc

    pages = _load_pages(document_path)
    all_rows: list[ParsedCallRow] = []
    for page in pages:
        candidates: list[list[ParsedCallRow]] = []
        for variant, page_mode in zip(_image_variants(page), (6, 6, 4)):
            lines = _ocr_lines(variant, f"--oem 3 --psm {page_mode}")
            candidates.append(parse_ocr_lines(lines))
        best = max(
            candidates,
            key=lambda rows: (len(rows), sum(row.confidence for row in rows)),
            default=[],
        )
        all_rows.extend(best)

    deduplicated: list[ParsedCallRow] = []
    seen: set[tuple[str, str]] = set()
    for row in all_rows:
        key = (row.contact, row.client_name.lower())
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(row)

    if deduplicated:
        message = f"OCR found {len(deduplicated)} possible call-list row(s). Review every row before importing."
    else:
        message = "No reliable rows were detected. Try a straighter, closer photo or use manual call entry."
    return ExtractionResult(rows=deduplicated, page_count=len(pages), message=message)
