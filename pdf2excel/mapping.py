from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from rapidfuzz import fuzz

from .models import MappingSpec

ABBREVIATIONS = {"no": "number", "num": "number", "qty": "quantity", "amt": "amount", "gst": "gstin", "party": "customer"}


def normalized(name: str) -> str:
    words = re.findall(r"[a-z0-9]+", name.lower())
    return " ".join(ABBREVIATIONS.get(w, w) for w in words)


def suggest_mapping(excel_columns: list[str], pdf_columns: list[str]) -> dict[str, list[str]]:
    suggestions: dict[str, list[str]] = {}
    for target in excel_columns:
        tn = normalized(target)
        ranked = sorted(((fuzz.token_set_ratio(tn, normalized(src)), src) for src in pdf_columns), reverse=True)
        score, source = ranked[0] if ranked else (0, "")
        suggestions[target] = [source] if score >= 45 else []
    return suggestions


def structure_key(columns: list[str]) -> str:
    return "|".join(normalized(c) for c in columns)


class MappingStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self, pdf_columns: list[str], excel_columns: list[str]) -> MappingSpec | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            item = data.get(structure_key(pdf_columns))
            if not item or item.get("excel_columns") != excel_columns:
                return None
            mappings = {k: [s for s in v if s in pdf_columns] for k, v in item["mappings"].items()}
            return MappingSpec(mappings, item.get("separator", " "))
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, pdf_columns: list[str], excel_columns: list[str], spec: MappingSpec) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        except (OSError, ValueError):
            data = {}
        data[structure_key(pdf_columns)] = {"excel_columns": excel_columns, "mappings": spec.mappings, "separator": spec.separator}
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _yes(prompt: str, default: bool = False) -> bool:
    suffix = " [Y/n]: " if default else " [y/N]: "
    answer = input(prompt + suffix).strip().lower()
    return default if not answer else answer in {"y", "yes"}


def interactive_mapping(excel_columns: list[str], pdf_columns: list[str], initial: MappingSpec | None = None) -> MappingSpec:
    suggestions = initial.mappings if initial else suggest_mapping(excel_columns, pdf_columns)
    print("\nPDF COLUMNS DETECTED")
    for i, col in enumerate(pdf_columns, 1):
        print(f"[{i}] {col}")
    print("\nMAPPING REVIEW (comma-separated numbers combine columns; 0 leaves blank)")
    used: dict[str, list[str]] = defaultdict(list)
    mappings: dict[str, list[str]] = {}
    for target in excel_columns:
        suggested = [pdf_columns.index(x) + 1 for x in suggestions.get(target, []) if x in pdf_columns]
        default = ",".join(map(str, suggested)) or "0"
        while True:
            raw = input(f"{target} [{default}]: ").strip() or default
            try:
                nums = [] if raw == "0" else [int(x.strip()) for x in raw.split(",")]
                if any(n < 1 or n > len(pdf_columns) for n in nums) or len(nums) != len(set(nums)):
                    raise ValueError
            except ValueError:
                print(f"Enter 0 or unique column numbers from 1 to {len(pdf_columns)}.")
                continue
            sources = [pdf_columns[n - 1] for n in nums]
            duplicates = [(s, used[s]) for s in sources if used[s]]
            if duplicates:
                detail = "; ".join(f"'{s}' already maps to {', '.join(t)}" for s, t in duplicates)
                if not _yes(f"WARNING: {detail}. Use again?"):
                    continue
            if not sources and not _yes(f"No PDF column mapped to '{target}'. Keep blank?", default=True):
                continue
            mappings[target] = sources
            for source in sources:
                used[source].append(target)
            break
    return MappingSpec(mappings)


def apply_mapping(rows: list[dict[str, str]], spec: MappingSpec) -> list[dict[str, str]]:
    return [{target: spec.separator.join(row.get(src, "") for src in sources).strip() if sources else ""
             for target, sources in spec.mappings.items()} for row in rows]

