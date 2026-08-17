from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from .export import export_xlsx
from .extraction import InvalidPasswordError, PasswordRequiredError, extract_pdf
from .mapping import MappingStore, apply_mapping, interactive_mapping
from .schema import load_schema
from .validation import validate


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Extract PDF tables and interactively map them to Excel columns.")
    p.add_argument("pdf", type=Path, help="Input PDF path")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--schema", type=Path, help="JSON list or {columns: [...]} file")
    group.add_argument("--columns", help="Comma-separated desired Excel columns")
    p.add_argument("--output", type=Path, help="Output .xlsx (default: <pdf>_converted.xlsx)")
    p.add_argument("--mapping-file", type=Path, default=Path(".pdf2excel_mappings.json"))
    p.add_argument("--no-ocr", action="store_true", help="Do not try OCR for scanned PDFs")
    return p


def _preview(rows: list[dict[str, str]], limit: int = 10) -> None:
    print("\nFINAL DATA PREVIEW")
    if not rows:
        print("(no rows)")
        return
    columns = list(rows[0])
    widths = {c: min(24, max(len(c), *(len(str(r[c])) for r in rows[:limit]))) for c in columns}
    print(" | ".join(c[:widths[c]].ljust(widths[c]) for c in columns))
    print("-+-".join("-" * widths[c] for c in columns))
    for row in rows[:limit]:
        print(" | ".join(str(row[c])[:widths[c]].ljust(widths[c]) for c in columns))


def run(args: argparse.Namespace) -> int:
    columns = load_schema(args.schema, args.columns)
    output = args.output or args.pdf.with_name(f"{args.pdf.stem}_converted.xlsx")
    if output.suffix.lower() != ".xlsx":
        raise ValueError("Output filename must end in .xlsx")
    print(f"Extracting tables from: {args.pdf}")
    password = None
    while True:
        try:
            extraction = extract_pdf(args.pdf, allow_ocr=not args.no_ocr, password=password)
            break
        except PasswordRequiredError:
            password = getpass.getpass("PDF password: ")
        except InvalidPasswordError:
            password = getpass.getpass("Incorrect password. Try again: ")
    print(f"Detected {len(extraction.tables)} table(s), {len(extraction.rows)} data row(s).")
    store = MappingStore(args.mapping_file)
    previous = store.load(extraction.columns, columns)
    if previous:
        reuse = input("A previous mapping for this PDF structure was found. Reuse it? [Y/n]: ").strip().lower()
        if reuse in {"", "y", "yes"}:
            initial = previous
        else:
            initial = None
    else:
        initial = None
    while True:
        spec = interactive_mapping(columns, extraction.columns, initial)
        mapped = apply_mapping(extraction.rows, spec)
        _preview(mapped)
        issues = validate(extraction, mapped)
        if issues:
            print("\nVALIDATION WARNINGS")
            for issue in issues:
                print(f"{issue.level}: {issue.message}")
        action = input("\nExport this mapping to Excel? [Y] Yes / [N] No / [M] Modify: ").strip().lower()
        if action in {"y", "yes"}:
            break
        if action in {"m", "modify"}:
            initial = spec
            continue
        print("Export cancelled; no Excel file was created.")
        return 1
    store.save(extraction.columns, columns, spec)
    exported = export_xlsx(mapped, columns, output)
    print("\nCONVERSION COMPLETE")
    print(f"PDF: {args.pdf}")
    print(f"Records extracted: {len(extraction.rows):,}")
    print(f"Records exported: {exported:,}")
    print(f"Excel: {output}")
    print(f"Warnings: {len(issues)}")
    print(f"Mapping: {spec.mapped_count()} Excel columns mapped successfully.")
    return 0


def main() -> None:
    try:
        raise SystemExit(run(_parser().parse_args()))
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
