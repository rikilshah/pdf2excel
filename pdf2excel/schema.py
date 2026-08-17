from __future__ import annotations

import json
from pathlib import Path

DEFAULT_COLUMNS = [
    "Invoice No", "Invoice Date", "Customer Name", "GSTIN", "HSN",
    "Description", "Quantity", "Taxable Value", "CGST", "SGST", "IGST",
    "Total Amount",
]


def load_schema(path: Path | None, inline: str | None) -> list[str]:
    if path:
        data = json.loads(path.read_text(encoding="utf-8"))
        columns = data.get("columns") if isinstance(data, dict) else data
    elif inline:
        columns = [part.strip() for part in inline.split(",")]
    else:
        columns = DEFAULT_COLUMNS
    if not isinstance(columns, list) or not columns or not all(isinstance(x, str) and x.strip() for x in columns):
        raise ValueError("Excel schema must be a non-empty JSON list (or {\"columns\": [...]}) of names.")
    cleaned = [x.strip() for x in columns]
    if len(set(cleaned)) != len(cleaned):
        raise ValueError("Excel schema contains duplicate column names.")
    return cleaned

