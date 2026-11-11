$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$modelDir = $projectRoot
$modelPath = Join-Path $modelDir 'eng.traineddata'
$expected = '7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2'
$url = 'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/87416418657359cb625c412a48b6e1d6d41c29bd/eng.traineddata'
if ((Test-Path -LiteralPath $modelPath) -and
    ((Get-FileHash -LiteralPath $modelPath -Algorithm SHA256).Hash -eq $expected)) {
    Write-Host 'OCR model is ready.'
    return
}
New-Item -ItemType Directory -Path $modelDir -Force | Out-Null
$downloadPath = Join-Path $modelDir ('eng.' + [guid]::NewGuid().ToString('N') + '.download')
try {
    Invoke-WebRequest -Uri $url -OutFile $downloadPath -UseBasicParsing
    if ((Get-FileHash -LiteralPath $downloadPath -Algorithm SHA256).Hash -ne $expected) {
        throw 'OCR model checksum mismatch; the existing model has not been replaced.'
    }
    Move-Item -LiteralPath $downloadPath -Destination $modelPath -Force
    Write-Host 'OCR model downloaded and verified.'
} finally {
    if (Test-Path -LiteralPath $downloadPath) {
        Remove-Item -LiteralPath $downloadPath
    }
}
