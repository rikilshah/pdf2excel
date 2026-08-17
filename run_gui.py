import sys
from pathlib import Path

from pdf2excel.gui import main


def _packaged_self_test() -> int:
    """Internal packaging check used by the build; not part of the user interface."""
    from pdf2excel.extraction import extract_pdf
    result = extract_pdf(Path(sys.argv[2]), mode="ocr")
    return 0 if result.used_ocr and result.rows else 3


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--ocr-self-test":
        raise SystemExit(_packaged_self_test())
    main()
