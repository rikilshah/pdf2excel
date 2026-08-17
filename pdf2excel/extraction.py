from __future__ import annotations

import re
import os
import sys
from collections import Counter
from pathlib import Path

from .models import ExtractedTable, ExtractionResult


class PasswordRequiredError(RuntimeError):
    pass


class InvalidPasswordError(RuntimeError):
    pass


def _check_password(pdf_path: Path, password: str | None) -> None:
    try:
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)
        if reader.is_encrypted:
            if password is None:
                raise PasswordRequiredError("This PDF is password protected.")
            if reader.decrypt(password) == 0:
                raise InvalidPasswordError("The PDF password is incorrect.")
    except (PasswordRequiredError, InvalidPasswordError):
        raise
    except Exception as exc:
        # Parsing continues in pdfplumber, which provides the authoritative error
        # for malformed but non-encrypted documents.
        if "password" in str(exc).lower():
            raise PasswordRequiredError("This PDF is password protected.") from exc


def _clean(value: object) -> str:
    if value is None:
        return ""
    return re.sub(r"[ \t]+", " ", str(value).replace("\r", " ").replace("\n", " ")).strip()


def _unique_headers(raw: list[object], width: int) -> list[str]:
    headers, counts = [], Counter()
    for i in range(width):
        base = _clean(raw[i] if i < len(raw) else "") or f"Column {i + 1}"
        counts[base] += 1
        headers.append(base if counts[base] == 1 else f"{base} ({counts[base]})")
    return headers


def _looks_like_header(row: list[str], headers: list[str]) -> bool:
    if len(row) != len(headers):
        return False
    norm = lambda x: re.sub(r"[^a-z0-9]", "", x.lower())
    return sum(norm(a) == norm(b) and bool(norm(a)) for a, b in zip(row, headers)) >= max(1, len(headers) // 2)


def _extract_with_pdfplumber(pdf_path: Path, strategy: str = "auto", password: str | None = None) -> tuple[list[ExtractedTable], list[str], int]:
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("pdfplumber is required. Install the project dependencies first.") from exc
    tables: list[ExtractedTable] = []
    warnings: list[str] = []
    text_pages = 0
    line_settings = {"vertical_strategy": "lines", "horizontal_strategy": "lines", "snap_tolerance": 4, "join_tolerance": 4}
    text_settings = {"vertical_strategy": "text", "horizontal_strategy": "text", "snap_tolerance": 4, "join_tolerance": 4}
    settings_candidates = {"auto": [{}, text_settings], "lines": [line_settings], "text": [text_settings]}.get(strategy)
    if settings_candidates is None:
        raise ValueError(f"Unknown extraction strategy: {strategy}")
    try:
        with pdfplumber.open(pdf_path, password=password) as pdf:
            for page_no, page in enumerate(pdf.pages, 1):
                if (page.extract_text() or "").strip():
                    text_pages += 1
                found = []
                for settings in settings_candidates:
                    found = page.extract_tables(settings) or []
                    if found:
                        break
                if not found:
                    warnings.append(f"Page {page_no}: no table detected.")
                    continue
                for table_no, raw in enumerate(found, 1):
                    raw = [list(r or []) for r in raw if r and any(_clean(c) for c in r)]
                    if len(raw) < 2:
                        warnings.append(f"Page {page_no}, table {table_no}: fewer than two nonblank rows.")
                        continue
                    width = max(map(len, raw))
                    headers = _unique_headers(raw[0], width)
                    rows = []
                    for r in raw[1:]:
                        cleaned = [_clean(r[i] if i < len(r) else "") for i in range(width)]
                        if _looks_like_header(cleaned, headers):
                            continue
                        rows.append(cleaned)
                    tables.append(ExtractedTable(page_no, table_no, headers, rows))
    except Exception as exc:
        raise RuntimeError(f"Unable to extract tables from the PDF: {exc}") from exc
    return tables, warnings, text_pages


def _extract_ocr(pdf_path: Path, password: str | None = None) -> tuple[list[ExtractedTable], list[str]]:
    try:
        import pytesseract
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise RuntimeError("PDF appears scanned; OCR support is not installed. Install with: pip install -e .[ocr]") from exc
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    candidates = [
        bundle_root / "tesseract" / "tesseract.exe",
        Path(sys.executable).parent / "tesseract" / "tesseract.exe",
        Path(__file__).resolve().parents[1] / "vendor" / "tesseract" / "tesseract.exe",
    ]
    bundled = next((p for p in candidates if p.is_file()), None)
    if bundled:
        pytesseract.pytesseract.tesseract_cmd = str(bundled)
        tessdata = bundled.parent / "tessdata"
        if tessdata.is_dir():
            os.environ["TESSDATA_PREFIX"] = str(tessdata)
    try:
        pytesseract.get_tesseract_version()
    except Exception as exc:
        raise RuntimeError("OCR requires Tesseract. Install it or place a portable 'tesseract' folder beside the application executable.") from exc
    warnings = ["PDF appears to be scanned. OCR extraction was used; verify all extracted data carefully."]
    tables: list[ExtractedTable] = []
    document = pdfium.PdfDocument(str(pdf_path), password=password)
    for page_no, page in enumerate(document, 1):
        image = page.render(scale=300 / 72).to_pil()
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT, config="--psm 6")
        lines: dict[tuple[int, int, int], list[tuple[int, str]]] = {}
        for i, text in enumerate(data["text"]):
            text = _clean(text)
            if text and int(data["conf"][i]) >= 25:
                key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
                lines.setdefault(key, []).append((int(data["left"][i]), text))
        parsed = [[word for _, word in sorted(words)] for words in lines.values() if words]
        if len(parsed) >= 2:
            width = max(map(len, parsed))
            headers = _unique_headers(parsed[0], width)
            rows = [[*(r + [""] * width)][:width] for r in parsed[1:]]
            tables.append(ExtractedTable(page_no, 1, headers, rows, method="ocr", warnings=["OCR word-position table inference used."]))
        else:
            warnings.append(f"Page {page_no}: OCR found no usable rows.")
        image.close()
    return tables, warnings


def extract_pdf(pdf_path: Path, allow_ocr: bool = True, mode: str = "auto", password: str | None = None) -> ExtractionResult:
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    _check_password(pdf_path, password)
    if mode == "ocr":
        tables, warnings = _extract_ocr(pdf_path, password)
        if not tables:
            raise RuntimeError("Unable to extract tables from the PDF using OCR.")
        text_pages, used_ocr = 0, True
    else:
        tables, warnings, text_pages = _extract_with_pdfplumber(pdf_path, mode, password)
        used_ocr = False
    if not tables and text_pages == 0 and allow_ocr:
        tables, ocr_warnings = _extract_ocr(pdf_path, password)
        warnings.extend(ocr_warnings)
        used_ocr = True
    if not tables:
        detail = " PDF appears image-based; OCR is required." if text_pages == 0 else " The table structure may be unsupported."
        raise RuntimeError("Unable to extract tables from the PDF." + detail)

    # Use the most frequent header structure as canonical. Incompatible tables are retained via positional names and warned.
    signatures = Counter(tuple(t.headers) for t in tables)
    canonical = list(signatures.most_common(1)[0][0])
    all_columns = list(canonical)
    rows: list[dict[str, str]] = []
    for table in tables:
        if len(table.headers) != len(canonical):
            warnings.append(f"Page {table.page}, table {table.table}: {len(table.headers)} columns; expected {len(canonical)}.")
        for header in table.headers:
            if header not in all_columns:
                all_columns.append(header)
        for values in table.rows:
            rows.append({h: (values[i] if i < len(values) else "") for i, h in enumerate(table.headers)})
    return ExtractionResult(all_columns, rows, tables, warnings, used_ocr)
