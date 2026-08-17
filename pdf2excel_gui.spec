# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = collect_submodules("pdfplumber") + collect_submodules("pypdfium2")
datas = collect_data_files("pdfplumber") + collect_data_files("pypdfium2")
portable_tesseract = Path("vendor/tesseract")
if portable_tesseract.is_dir():
    datas.extend([
        (str(portable_tesseract / "tesseract.exe"), "tesseract"),
        (str(portable_tesseract / "*.dll"), "tesseract"),
        (str(portable_tesseract / "tessdata" / "eng.traineddata"), "tesseract/tessdata"),
        (str(portable_tesseract / "tessdata" / "osd.traineddata"), "tesseract/tessdata"),
        (str(portable_tesseract / "tessdata" / "pdf.ttf"), "tesseract/tessdata"),
        (str(portable_tesseract / "tessdata" / "configs"), "tesseract/tessdata/configs"),
        (str(portable_tesseract / "tessdata" / "tessconfigs"), "tesseract/tessdata/tessconfigs"),
    ])
    license_file = portable_tesseract / "TESSERACT_LICENSE.txt"
    if license_file.is_file():
        datas.append((str(license_file), "tesseract"))

a = Analysis(
    ["run_gui.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pandas", "scipy", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PDF2ExcelMapper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
