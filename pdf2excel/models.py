from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ExtractedTable:
    page: int
    table: int
    headers: list[str]
    rows: list[list[str]]
    method: str = "pdfplumber"
    warnings: list[str] = field(default_factory=list)


@dataclass
class ExtractionResult:
    columns: list[str]
    rows: list[dict[str, str]]
    tables: list[ExtractedTable]
    warnings: list[str] = field(default_factory=list)
    used_ocr: bool = False


@dataclass
class MappingSpec:
    # Each output column may concatenate zero, one, or several source columns.
    mappings: dict[str, list[str]]
    separator: str = " "

    def mapped_count(self) -> int:
        return sum(bool(v) for v in self.mappings.values())


@dataclass
class ValidationIssue:
    level: str
    message: str
    row: int | None = None


@dataclass
class ConversionSummary:
    pdf: Path
    excel: Path
    records_extracted: int
    records_exported: int
    warnings: list[str]
    mapped_columns: int


CellValue = str | int | float | Any | None

