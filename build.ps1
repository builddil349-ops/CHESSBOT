param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
& (Join-Path $projectRoot 'download-model.ps1')
Push-Location -LiteralPath $projectRoot
try {
    & $Python -m PyInstaller --noconfirm --clean --onefile --windowed --name CHESSBOT `
        --additional-hooks-dir $projectRoot `
        --add-data ((Join-Path $projectRoot 'eng.traineddata') + ':.') `
        --add-data ((Join-Path $projectRoot 'THIRD_PARTY_LICENSES.zip') + ':.' ) `
        --add-data ((Join-Path $projectRoot 'LICENSE') + ':.') `
        --distpath (Join-Path $projectRoot 'dist') `
        --workpath (Join-Path $projectRoot 'build') `
        --specpath (Join-Path $projectRoot 'build') `
        (Join-Path $projectRoot 'trainer.py')
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
    Copy-Item -LiteralPath (Join-Path $projectRoot 'dist\CHESSBOT.exe') `
        -Destination (Join-Path $projectRoot 'CHESSBOT.exe') -Force
    Write-Host ('Ready: ' + (Join-Path $projectRoot 'CHESSBOT.exe'))
} finally {
    Pop-Location
}
