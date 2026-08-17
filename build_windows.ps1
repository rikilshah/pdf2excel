$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot

python -m pip install -e ".[gui,ocr,build]"

$tesseractDir = "$projectRoot\vendor\tesseract"
$tesseractExe = "$tesseractDir\tesseract.exe"
if (-not (Test-Path -LiteralPath $tesseractExe)) {
    $version = "5.5.3.20260724"
    $installer = "$projectRoot\tesseract-ocr-w64-setup-$version.exe"
    $url = "https://github.com/tesseract-ocr/tesseract/releases/download/5.5.3/tesseract-ocr-w64-setup-$version.exe"
    $expectedSha256 = "bee9e3434bd94fd65387d9be28cd467a41f61b1275383b55b0f59a1331270ae4"
    Write-Host "Downloading official Tesseract OCR $version..."
    Invoke-WebRequest -Uri $url -OutFile $installer
    $actualSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $installer).Hash.ToLowerInvariant()
    if ($actualSha256 -ne $expectedSha256) {
        throw "Tesseract installer checksum mismatch. Expected $expectedSha256, received $actualSha256."
    }
    $install = Start-Process -FilePath $installer -ArgumentList "/S", "/D=$tesseractDir" -WindowStyle Hidden -Wait -PassThru
    if ($install.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $tesseractExe)) {
        throw "Portable Tesseract installation failed with exit code $($install.ExitCode)."
    }
}
python -m PyInstaller --noconfirm --clean pdf2excel_gui.spec

Write-Host ""
Write-Host "Build complete: $projectRoot\dist\PDF2ExcelMapper.exe"
