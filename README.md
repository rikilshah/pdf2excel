# PDF2Excel Mapper

A modern Windows desktop application and command-line tool that extracts PDF tables, lets users correct the detected grid, maps data to imported Excel headers, and exports XLSX, CSV, or text.

## Install

Python 3.10 or newer is required.

```powershell
python -m pip install -e .
```

Install and launch the desktop application:

```powershell
python -m pip install -e ".[gui,ocr]"
pdf2excel-gui
```

The desktop interface provides:

- Responsive split-pane layout and background extraction so the application remains responsive.
- Auto, ruled-table, borderless-table, and forced-OCR extraction modes. OCR is bundled in the Windows release and works offline by default.
- An editable data grid, highlighted blank cells, row deletion, and correction commands.
- Direct XLSX, UTF-8 CSV, or tab-delimited text export in the detected structure.
- Import of an existing `.xlsx` header row followed by reviewed, multi-source column mapping.
- Validation warnings before export without silently changing business data.

Supported correction commands include `rename Qty to Quantity`, `remove row 3`, `use row 2 as headers`, `split Name by , into First,Last`, and `merge First + Last into Name`. Cell values can also be edited directly.

For scanned/image PDFs, also install the OCR extra plus system copies of [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) and [Poppler](https://poppler.freedesktop.org/):

```powershell
python -m pip install -e ".[ocr]"
```

## Run

Use the built-in invoice schema:

```powershell
pdf2excel "C:\path\input.pdf"
```

Supply columns directly:

```powershell
pdf2excel input.pdf --columns "Invoice No,Invoice Date,Customer Name,Total Amount"
```

Or use `schema.json`:

```json
{
  "columns": ["Invoice No", "Invoice Date", "Customer Name", "Total Amount"]
}
```

```powershell
pdf2excel input.pdf --schema schema.json --output result.xlsx
```

The mapper shows numbered PDF columns. Press Enter to accept a suggestion, enter `0` to leave an Excel column blank, or enter comma-separated numbers such as `2,3` to combine PDF columns. Duplicate source mappings require confirmation. A saved `.pdf2excel_mappings.json` mapping is offered on later runs with the same source structure and Excel schema.

## Extraction and integrity behavior

- Uses `pdfplumber` across all pages and retries with text-based table boundaries.
- Removes repeated header rows and preserves blank cells without shifting adjacent values.
- Warns about missing tables, inconsistent column counts, duplicate rows, suspicious numbers, invalid dates, and blank invoice identifiers.
- Keeps identifiers such as invoice numbers, GSTINs, HSNs, and codes as Excel text.
- Converts dates and numeric financial fields only when parsing is unambiguous; otherwise it preserves the original string and warns.
- Never deletes duplicate rows or invents missing data.
- Falls back to OCR only when no table and no PDF text are found. OCR table inference is intentionally conservative and always warns for manual verification.

The default output is `<original_filename>_converted.xlsx`, with filters, frozen headers, safe data types, and capped autofit widths.

## Test

```powershell
python -m pytest
```

## Build the self-contained Windows executable

Run on Windows:

```powershell
.\build_windows.ps1
```

The executable is written to `dist\PDF2ExcelMapper.exe`. Python, Qt, PDFium, Tesseract OCR 5.5.3, English OCR data, and Python packages are bundled; neither Python, Poppler, nor a separate OCR installation is required. Password-protected PDFs prompt securely for a password and retry without saving it.
