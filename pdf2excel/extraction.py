from __future__ import annotations

import re
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Callable

from .models import ExtractedTable, ExtractionResult

ProgressCallback = Callable[[int, str], None]


def _report(progress: ProgressCallback | None, percent: int, message: str) -> None:
    if progress:
        progress(max(0, min(100, percent)), message)


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


def _extract_with_pdfplumber(pdf_path: Path, strategy: str = "auto", password: str | None = None,
                             progress: ProgressCallback | None = None) -> tuple[list[ExtractedTable], list[str], int]:
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
            total_pages = max(1, len(pdf.pages))
            for page_no, page in enumerate(pdf.pages, 1):
                _report(progress, 10 + round(65 * (page_no - 1) / total_pages),
                        f"Inspecting page {page_no} of {total_pages} for table geometry")
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


def _extract_ocr(pdf_path: Path, password: str | None = None,
                 progress: ProgressCallback | None = None) -> tuple[list[ExtractedTable], list[str]]:
    try:
        import pytesseract
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise RuntimeError("PDF appears scanned; OCR support is not installed. Install with: pip install -e .[ocr]") from exc
    _report(progress, 5, "Starting bundled Tesseract OCR engine")
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
    document = pdfium.PdfDocument(str(pdf_path), password=password)
    _report(progress, 10, f"Opened image document with {len(document)} page(s)")
    first_image = document[0].render(scale=300 / 72).to_pil()
    rules = _detect_vertical_rules(first_image)
    if len(rules) >= 3:
        _report(progress, 14, f"Detected {len(rules) - 1} ruled columns; using geometric OCR")
        tables, warnings = _extract_ruled_ocr(document, first_image, rules, pytesseract, progress)
        if tables and sum(len(t.rows) for t in tables):
            warnings.insert(0, "PDF appears to be scanned. Ruled-table OCR was used; verify all extracted data carefully.")
            return tables, warnings
    warnings = ["PDF appears to be scanned. OCR extraction was used; verify all extracted data carefully."]
    tables: list[ExtractedTable] = []
    for page_no, page in enumerate(document, 1):
        _report(progress, 15 + round(70 * (page_no - 1) / max(1, len(document))),
                f"Recognizing page {page_no} of {len(document)}")
        image = first_image if page_no == 1 else page.render(scale=300 / 72).to_pil()
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


def _configure_tesseract(pytesseract) -> None:
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    candidates = [
        bundle_root / "tesseract" / "tesseract.exe",
        Path(sys.executable).parent / "tesseract" / "tesseract.exe",
        Path(__file__).resolve().parents[1] / "vendor" / "tesseract" / "tesseract.exe",
    ]
    bundled = next((path for path in candidates if path.is_file()), None)
    if bundled:
        pytesseract.pytesseract.tesseract_cmd = str(bundled)
        tessdata = bundled.parent / "tessdata"
        if tessdata.is_dir():
            os.environ["TESSDATA_PREFIX"] = str(tessdata)
    try:
        pytesseract.get_tesseract_version()
    except Exception as exc:
        raise RuntimeError("OCR requires Tesseract, but the bundled runtime could not be started.") from exc


def _clusters(points: list[int], gap: int = 4) -> list[int]:
    groups: list[list[int]] = []
    for point in points:
        if not groups or point - groups[-1][-1] > gap:
            groups.append([point])
        else:
            groups[-1].append(point)
    return [round(sum(group) / len(group)) for group in groups]


def _longest_true_run(values) -> int:
    best = current = 0
    for value in values:
        current = current + 1 if value else 0
        best = max(best, current)
    return best


def _detect_vertical_rules(image) -> list[int]:
    """Find long vertical table rules on the first statement page."""
    import numpy as np

    gray = np.asarray(image.convert("L"))
    dark = gray < 190
    height, width = dark.shape
    top, bottom = int(height * 0.20), int(height * 0.90)
    minimum = int(height * 0.12)
    # Search a narrow horizontal band per x so anti-aliased rules are not missed.
    candidates = [x for x in range(width)
                  if _longest_true_run(dark[top:bottom, max(0, x - 2):min(width, x + 3)].any(axis=1)) >= minimum]
    rules = _clusters(candidates)
    # Ignore short box decorations; a transaction table spans most of the page.
    if len(rules) >= 3:
        span_groups: list[list[int]] = []
        for point in rules:
            if not span_groups:
                span_groups.append([point])
            elif point - span_groups[-1][-1] < width * 0.38:
                span_groups[-1].append(point)
            else:
                span_groups.append([point])
        rules = max(span_groups, key=lambda group: (len(group), group[-1] - group[0]))
        if rules and rules[-1] < width * 0.96:
            rules.append(width - 1)
    return rules


def _table_vertical_span(image, rules: list[int]) -> tuple[int, int] | None:
    import numpy as np

    gray = np.asarray(image.convert("L"))
    dark = gray < 205
    height, width = dark.shape
    valid = []
    for y in range(int(height * 0.18), int(height * 0.92)):
        hits = sum(bool(dark[y, max(0, x - 2):min(width, x + 3)].any()) for x in rules)
        valid.append(hits >= max(3, round(len(rules) * 0.65)))
    runs: list[tuple[int, int]] = []
    start = None
    offset = int(height * 0.18)
    for index, value in enumerate(valid + [False]):
        if value and start is None:
            start = index
        elif not value and start is not None:
            if index - start >= 25:
                runs.append((start + offset, index - 1 + offset))
            start = None
    return max(runs, key=lambda run: run[1] - run[0]) if runs else None


def _extract_ruled_ocr(document, first_image, rules: list[int], pytesseract,
                       progress: ProgressCallback | None = None) -> tuple[list[ExtractedTable], list[str]]:
    tables: list[ExtractedTable] = []
    warnings: list[str] = []
    canonical_headers: list[str] | None = None
    for page_no, page in enumerate(document, 1):
        _report(progress, 15 + round(70 * (page_no - 1) / max(1, len(document))),
                f"OCR page {page_no} of {len(document)}: locating rows in {len(rules) - 1} columns")
        image = first_image if page_no == 1 else page.render(scale=300 / 72).to_pil()
        # Scale the first-page rules if a page has a slightly different bitmap width.
        scaled_rules = [round(x * image.width / first_image.width) for x in rules]
        span = _table_vertical_span(image, scaled_rules)
        if not span:
            warnings.append(f"Page {page_no}: ruled table boundaries could not be isolated.")
            if page_no != 1:
                image.close()
            continue
        top, bottom = span
        headers, rows = _ocr_rows_by_rules(image, scaled_rules, (top, bottom), pytesseract, canonical_headers)
        if headers and headers != [f"Column {i + 1}" for i in range(len(scaled_rules) - 1)]:
            canonical_headers = headers
        if rows:
            tables.append(ExtractedTable(page_no, 1, headers, rows, method="ocr-ruled"))
        else:
            warnings.append(f"Page {page_no}: ruled-table OCR found no dated transaction rows.")
        if page_no != 1:
            image.close()
    first_image.close()
    if canonical_headers:
        for table in tables:
            if len(table.headers) == len(canonical_headers):
                table.headers = canonical_headers[:]
    return tables, warnings


def _ocr_rows_by_rules(image, rules: list[int], span: tuple[int, int], pytesseract,
                       canonical_headers: list[str] | None = None) -> tuple[list[str], list[list[str]]]:
        date_pattern = re.compile(r"^\d{2}[/.-]\d{2}[/.-]\d{2,4}$")
        top, bottom = span
        crop = image.crop((rules[0], top, rules[-1], bottom))
        data = pytesseract.image_to_data(crop, output_type=pytesseract.Output.DICT, config="--psm 6")
        lines: dict[tuple[int, int, int], list[tuple[int, int, str]]] = {}
        for i, raw_text in enumerate(data["text"]):
            text = _clean(raw_text)
            if not text or int(float(data["conf"][i])) < 20:
                continue
            center_x = rules[0] + int(data["left"][i]) + int(data["width"][i]) // 2
            center_y = top + int(data["top"][i]) + int(data["height"][i]) // 2
            column = next((j for j in range(len(rules) - 1) if rules[j] <= center_x < rules[j + 1]), None)
            if column is not None:
                key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
                lines.setdefault(key, []).append((center_y, column, text))
        line_cells: list[tuple[int, list[str]]] = []
        for words in lines.values():
            cells = [""] * (len(rules) - 1)
            for _, column, text in sorted(words, key=lambda item: (item[1], item[0])):
                cells[column] = (cells[column] + " " + text).strip()
            line_cells.append((round(sum(w[0] for w in words) / len(words)), cells))
        line_cells.sort(key=lambda item: item[0])
        header_index = next((i for i, (_, cells) in enumerate(line_cells)
                             if sum(any(term in cell.lower() for term in ("date", "narration", "withdraw", "deposit", "closing")) for cell in cells) >= 2), None)
        if header_index is not None:
            detected = line_cells[header_index][1]
            headers = _unique_headers(detected, len(detected))
            line_cells = line_cells[header_index + 1:]
        else:
            headers = canonical_headers or [f"Column {i + 1}" for i in range(len(rules) - 1)]
        rows: list[list[str]] = []
        for _, cells in line_cells:
            first = cells[0].replace(" ", "")
            if date_pattern.match(first):
                cells[0] = first
                rows.append(cells)
            elif rows:
                for column, value in enumerate(cells):
                    if value:
                        rows[-1][column] = (rows[-1][column] + " " + value).strip()
        crop.close()
        return headers, rows


def extract_selected_ocr(pdf_path: Path, box: tuple[float, float, float, float],
                         boundaries: list[float], page_index: int = 0, all_pages: bool = False,
                         password: str | None = None, progress: ProgressCallback | None = None,
                         rotation: int = 0) -> ExtractionResult:
    """OCR only a user-selected normalized page rectangle using explicit column boundaries."""
    _report(progress, 2, "Validating selected table geometry")
    _check_password(pdf_path, password)
    if len(boundaries) < 2 or any(not 0 <= value <= 1 for value in boundaries):
        raise ValueError("At least two valid column boundaries are required.")
    left, top, right, bottom = box
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        raise ValueError("The selected table area is invalid.")
    try:
        import pytesseract
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise RuntimeError("OCR support is not installed.") from exc
    # Configure bundled Tesseract through the same validated path as automatic OCR.
    _configure_tesseract(pytesseract)
    document = pdfium.PdfDocument(str(pdf_path), password=password)
    indexes = range(len(document)) if all_pages else [page_index]
    selected_indexes = list(indexes)
    tables: list[ExtractedTable] = []
    warnings = ["User-guided region OCR was used; verify extracted values carefully."]
    canonical_headers: list[str] | None = None
    for position, index in enumerate(selected_indexes, 1):
        _report(progress, 10 + round(75 * (position - 1) / max(1, len(selected_indexes))),
                f"OCR page {position} of {len(selected_indexes)} inside selected columns")
        if index < 0 or index >= len(document):
            raise ValueError("Selected page is outside the PDF page range.")
        image = document[index].render(scale=300 / 72).to_pil()
        if rotation % 360:
            image = image.rotate(-(rotation % 360), expand=True)
        x_rules = sorted(set(round(value * image.width) for value in boundaries))
        selected_span = (round(top * image.height), round(bottom * image.height))
        # When applying guides across pages, retain user-defined columns but let
        # the visible vertical rules determine each page's table height.
        y_span = (_table_vertical_span(image, x_rules) or selected_span) if all_pages else selected_span
        headers, rows = _ocr_rows_by_rules(image, x_rules, y_span, pytesseract, canonical_headers)
        if canonical_headers is None or not all(h.startswith("Column ") for h in headers):
            canonical_headers = headers
        if rows:
            tables.append(ExtractedTable(index + 1, 1, headers, rows, method="ocr-user-guided"))
        else:
            warnings.append(f"Page {index + 1}: no dated rows found inside the selected area.")
        image.close()
    if not tables:
        raise RuntimeError("No transaction rows were recognized inside the selected area. Adjust the box or column guides and try again.")
    if canonical_headers:
        for table in tables:
            if len(table.headers) == len(canonical_headers):
                table.headers = canonical_headers[:]
    _report(progress, 92, f"Assembled {sum(len(t.rows) for t in tables)} row(s) from selected area")
    return _result_from_tables(tables, warnings, used_ocr=True)


def extract_pdf(pdf_path: Path, allow_ocr: bool = True, mode: str = "auto", password: str | None = None,
                progress: ProgressCallback | None = None) -> ExtractionResult:
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    _report(progress, 1, "Validating PDF and password")
    _check_password(pdf_path, password)
    if mode == "ocr":
        tables, warnings = _extract_ocr(pdf_path, password, progress)
        if not tables:
            raise RuntimeError("Unable to extract tables from the PDF using OCR.")
        text_pages, used_ocr = 0, True
    else:
        inspection_progress = progress
        if mode == "auto" and progress is not None:
            inspection_progress = lambda percent, message: _report(progress, 3 + round(percent * 0.28), message)
        tables, warnings, text_pages = _extract_with_pdfplumber(pdf_path, mode, password, inspection_progress)
        used_ocr = False
    if not tables and text_pages == 0 and allow_ocr:
        _report(progress, 28, "No text table found; switching to OCR")
        ocr_progress = progress
        if mode == "auto" and progress is not None:
            ocr_progress = lambda percent, message: _report(progress, 28 + round(percent * 0.64), message)
        tables, ocr_warnings = _extract_ocr(pdf_path, password, ocr_progress)
        warnings.extend(ocr_warnings)
        used_ocr = True
    if not tables:
        detail = " PDF appears image-based; OCR is required." if text_pages == 0 else " The table structure may be unsupported."
        raise RuntimeError("Unable to extract tables from the PDF." + detail)

    _report(progress, 92, "Normalizing columns and validating extracted rows")
    result = _result_from_tables(tables, warnings, used_ocr)
    _report(progress, 100, f"Complete: {len(result.rows)} row(s), {len(result.columns)} column(s)")
    return result


def _result_from_tables(tables: list[ExtractedTable], warnings: list[str], used_ocr: bool) -> ExtractionResult:
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
