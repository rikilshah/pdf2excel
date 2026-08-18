# Debug report: OCR transaction rows merged

- **Symptom:** Adjacent statement transactions were concatenated into one extracted row, including two dates, amounts, and balances.
- **Root cause:** Tesseract returned the first-column date of the second transaction with trailing punctuation (for example, `28/07/25.`). `_ocr_rows_by_rules` required an exact date match, rejected that line as a new transaction, and appended it to the preceding row as continuation text.
- **Fix:** Strip only leading/trailing OCR punctuation from the first-column date candidate before applying the strict date pattern. Internal date separators remain unchanged.
- **Evidence:** The supplied HDFC statement increased from 117 to 122 correctly separated rows. The affected pairs on 28/07/25, 13–15/05/25, and 22/05/25 now have separate amounts and closing balances.
- **Regression test:** `tests/test_core.py::test_ruled_ocr_treats_date_with_trailing_ocr_punctuation_as_new_row`
- **Related:** Ruled-table OCR intentionally appends non-date physical lines to the preceding transaction to retain wrapped narration. Correct date-boundary normalization is therefore essential.
- **Status:** DONE
