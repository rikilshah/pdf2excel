# PDF2Excel Mapper

PDF2Excel Mapper is an offline Windows desktop application for extracting tabular data from text PDFs and scanned documents. It combines automatic detection with a visual workflow where the user can mark the exact table area and column boundaries before OCR.

## Main features

- Modern native Windows interface with responsive background extraction.
- Integrated PDF viewer with page navigation.
- Loading a PDF only opens the preview; OCR starts only after the user chooses **Run OCR** or **Run OCR on selection**.
- Real page-by-page progress, processing phase messages, and an OCR activity log.
- PDF zoom from 50% to 200% and clockwise/counter-clockwise rotation.
- Automatic extraction for text PDFs, borderless tables, and ruled tables.
- Bundled offline Tesseract OCR for image-only PDFs.
- Visual table selection: drag the area, place column dividers, then OCR only that geometry.
- Apply the same column layout to the current page or every PDF page.
- Editable cells and editable column headers.
- Validation warnings without silently changing business or financial values.
- Direct XLSX, UTF-8 CSV, TXT, and TSV export.
- Interactive mapping to column headers imported from an Excel template.
- Secure password prompt and retry for encrypted PDFs; passwords are never persisted.

## Visual table and column selection

1. Open a PDF. The document appears in the **PDF viewer & column selection** tab.
2. Use the sidebar **Run OCR** button for automatic extraction, or continue with visual selection for precise scanned tables. OCR never starts merely because a file was opened.
3. Navigate to a representative page containing the table header or clear transaction rows. Zoom or rotate the page when needed.
4. Click **1. Draw table area**, then drag a rectangle around only the table.
5. Click **2. Add column divider** and click each vertical boundary between columns. Red guides show the resulting columns. The rectangle edges are automatically used as the outer boundaries.
6. Leave **Apply column layout to all pages** enabled when the same columns repeat throughout the statement. The application keeps the selected column positions while detecting each page's table height.
7. Click **3. Run OCR on selection**. OCR words are assigned by their geometric position, so blank debit or credit cells remain blank instead of shifting adjacent values.
8. Follow actual page progress and phase messages in the sidebar, then review the result in **Extracted data**.

For bank statements, draw the area from the table header/top border through the last visible transaction and place guides at Date, Narration, Reference, Value Date, Withdrawal, Deposit, and Closing Balance boundaries.

## Editing extracted data

- Double-click any data cell to edit its value.
- Double-click a column header to rename it. Column names must be nonblank and unique.
- Select cells from one or more rows and choose **Delete selected rows** to remove those rows after confirmation.
- Use correction commands for repeatable structural fixes:
  - `rename Qty to Quantity`
  - `remove row 3`
  - `use row 2 as headers`
  - `split Name by , into First,Last`
  - `merge First + Last into Name`

## Export and Excel mapping

Choose **Export detected data** to preserve the reviewed structure and export XLSX, CSV, TXT, or TSV.

Choose **Map to Excel template** to import the first worksheet's header row, select one or more detected columns for each Excel target column, review duplicate-use warnings, and export the mapped result. Multiple source selections are joined with a space; unselected targets remain blank.

XLSX output includes typed dates and safe numeric values where parsing is unambiguous, text-preserved identifiers, filters, frozen headers, and capped autofit widths.

## Install from source

Python 3.10 or newer is required.

```powershell
python -m pip install -e ".[gui]"
python -m pdf2excel.gui
```

Command-line usage remains available:

```powershell
pdf2excel "C:\path\input.pdf"
pdf2excel input.pdf --columns "Invoice No,Invoice Date,Customer Name,Total Amount"
pdf2excel input.pdf --schema schema.json --output result.xlsx
```

Example schema:

```json
{
  "columns": ["Invoice No", "Invoice Date", "Customer Name", "Total Amount"]
}
```

The CLI asks the user to confirm every mapping. Enter `0` to leave a target blank or comma-separated source numbers to combine columns.

## Extraction and integrity behavior

- Text PDFs use `pdfplumber` with ruled and text-boundary strategies.
- Scanned PDFs use PDFium rendering and offline Tesseract OCR.
- Automatic ruled-table OCR detects long vertical rules and groups narrative continuation lines under dated transaction rows.
- User-guided OCR uses explicit selected boundaries for reliable blank-cell preservation.
- Repeated headers are removed where detected.
- Duplicate rows are reported but preserved.
- Ambiguous strings remain strings; the program never invents or silently corrects missing values.
- Invoice numbers, GSTINs, HSN codes, account references, and similar identifiers remain text.

## Password-protected PDFs

Encrypted PDFs trigger a masked password dialog. Incorrect passwords can be retried. Passwords remain in process memory only for the active document and are reset when another PDF is opened.

## Build the self-contained Windows executable

```powershell
.\build_windows.ps1
```

The build script installs dependencies, downloads the pinned official Tesseract 5.5.3 package when needed, verifies its published SHA-256, and bundles Python, Qt, PDFium, Tesseract, English OCR data, and application code into `dist\PDF2ExcelMapper.exe`.

The released executable requires no separate Python, Poppler, or OCR installation.

## Tests

```powershell
python -m pytest
```

Tests cover mapping, correction commands, exports, real PDF table extraction, encrypted PDFs, bundled OCR, user-guided region OCR, and header renaming. See [CHANGELOG.md](CHANGELOG.md) for release history.
