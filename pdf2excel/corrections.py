from __future__ import annotations

import csv
import re


class CorrectionError(ValueError):
    pass


def _find(columns: list[str], name: str) -> str:
    matches = [c for c in columns if c.casefold() == name.strip().casefold()]
    if not matches:
        raise CorrectionError(f"Column not found: {name}")
    return matches[0]


def apply_instruction(columns: list[str], rows: list[dict[str, str]], instruction: str) -> tuple[list[str], list[dict[str, str]], str]:
    """Apply one auditable correction command; row numbers are one-based data rows."""
    text = instruction.strip()
    rename = re.fullmatch(r"rename\s+(.+?)\s+to\s+(.+)", text, re.I)
    delete = re.fullmatch(r"(?:delete|remove)\s+row\s+(\d+)", text, re.I)
    header = re.fullmatch(r"use\s+row\s+(\d+)\s+as\s+headers?", text, re.I)
    split = re.fullmatch(r"split\s+(.+?)\s+by\s+(.+?)\s+into\s+(.+)", text, re.I)
    merge = re.fullmatch(r"merge\s+(.+?)\s+into\s+(.+?)(?:\s+with\s+(.+))?", text, re.I)
    if rename:
        old = _find(columns, rename.group(1)); new = rename.group(2).strip()
        if not new or new in columns:
            raise CorrectionError("The new header is blank or already exists.")
        new_columns = [new if c == old else c for c in columns]
        return new_columns, [{(new if k == old else k): v for k, v in r.items()} for r in rows], f"Renamed '{old}' to '{new}'."
    if delete:
        index = int(delete.group(1)) - 1
        if index < 0 or index >= len(rows):
            raise CorrectionError("Row number is outside the data range.")
        return columns[:], rows[:index] + rows[index + 1:], f"Removed data row {index + 1}."
    if header:
        index = int(header.group(1)) - 1
        if index < 0 or index >= len(rows):
            raise CorrectionError("Row number is outside the data range.")
        proposed = [rows[index].get(c, "").strip() or f"Column {i + 1}" for i, c in enumerate(columns)]
        if len(set(proposed)) != len(proposed):
            raise CorrectionError("Selected row would create duplicate headers.")
        converted = [dict(zip(proposed, [r.get(c, "") for c in columns])) for r in rows[index + 1:]]
        return proposed, converted, f"Used data row {index + 1} as headers. Earlier rows were removed."
    if split:
        source = _find(columns, split.group(1)); delimiter = split.group(2).strip().strip("'\"")
        names = next(csv.reader([split.group(3)], skipinitialspace=True))
        names = [n.strip() for n in names if n.strip()]
        if not delimiter or len(names) < 2 or any(n in columns for n in names):
            raise CorrectionError("Provide a delimiter and at least two unique new column names.")
        at = columns.index(source); new_columns = columns[:at] + names + columns[at + 1:]
        new_rows = []
        for row in rows:
            parts = row.get(source, "").split(delimiter, len(names) - 1)
            parts += [""] * (len(names) - len(parts))
            new_row = {c: row.get(c, "") for c in columns if c != source}
            new_row.update(dict(zip(names, parts)))
            new_rows.append({c: new_row.get(c, "") for c in new_columns})
        return new_columns, new_rows, f"Split '{source}' into {', '.join(names)}."
    if merge:
        source_names = [x.strip() for x in merge.group(1).split("+")]
        sources = [_find(columns, x) for x in source_names]
        target = merge.group(2).strip()
        separator = merge.group(3).strip().strip("'\"") if merge.group(3) else " "
        if target in columns and target not in sources:
            raise CorrectionError("Target column already exists.")
        at = min(columns.index(s) for s in sources)
        new_columns = [c for c in columns if c not in sources]
        new_columns.insert(at, target)
        new_rows = []
        for row in rows:
            result = {c: row.get(c, "") for c in columns if c not in sources}
            result[target] = separator.join(row.get(s, "") for s in sources).strip()
            new_rows.append({c: result.get(c, "") for c in new_columns})
        return new_columns, new_rows, f"Merged {', '.join(sources)} into '{target}'."
    raise CorrectionError("Unsupported instruction. Try: rename A to B; remove row 3; use row 2 as headers; split Name by , into First,Last; merge First + Last into Name.")
