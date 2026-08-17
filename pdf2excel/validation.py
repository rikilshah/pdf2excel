from __future__ import annotations

import re
from collections import Counter
from datetime import datetime

from .models import ExtractionResult, ValidationIssue

NUMERIC_HINTS = ("quantity", "qty", "amount", "value", "cgst", "sgst", "igst", "tax", "total")
DATE_HINTS = ("date",)
MANDATORY_HINTS = ("invoice no", "invoice number")


def _number(value: str) -> bool:
    return bool(re.fullmatch(r"[-+]?\s*(?:\d{1,3}(?:,\d{3})*|\d+)(?:\.\d+)?", value.strip()))


def _date(value: str) -> bool:
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%m/%d/%Y"):
        try:
            datetime.strptime(value.strip(), fmt)
            return True
        except ValueError:
            pass
    return False


def validate(extraction: ExtractionResult, mapped: list[dict[str, str]]) -> list[ValidationIssue]:
    issues = [ValidationIssue("WARNING", w) for w in extraction.warnings]
    serialized = [tuple(r.values()) for r in mapped]
    duplicate_count = sum(n - 1 for n in Counter(serialized).values() if n > 1)
    if duplicate_count:
        issues.append(ValidationIssue("WARNING", f"{duplicate_count} duplicate row(s) detected; they will be preserved."))
    for col in (mapped[0].keys() if mapped else []):
        key = col.lower()
        values = [(i, r[col]) for i, r in enumerate(mapped, 2) if r[col].strip()]
        if any(h in key for h in NUMERIC_HINTS):
            bad = [(i, v) for i, v in values if not _number(v)]
            if bad:
                issues.append(ValidationIssue("WARNING", f"{len(bad)} non-numeric value(s) in '{col}' (first at Excel row {bad[0][0]})."))
        if any(h in key for h in DATE_HINTS):
            bad = [(i, v) for i, v in values if not _date(v)]
            if bad:
                issues.append(ValidationIssue("WARNING", f"{len(bad)} invalid/unknown date value(s) in '{col}' (first at Excel row {bad[0][0]})."))
        if key in MANDATORY_HINTS:
            blanks = sum(not r[col].strip() for r in mapped)
            if blanks:
                issues.append(ValidationIssue("WARNING", f"{blanks} blank value(s) in mandatory column '{col}'."))
    if not mapped:
        issues.append(ValidationIssue("ERROR", "No data rows were extracted."))
    return issues

