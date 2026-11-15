param(
    [string]$Python = 'python',
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]*$')]
    [string]$Name = 'CHESSBOT'
)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
& (Join-Path $projectRoot 'download-model.ps1')
Push-Location -LiteralPath $projectRoot
try {
    & $Python -m PyInstaller --noconfirm --clean --onefile --windowed --name $Name `
        --additional-hooks-dir $projectRoot `
        --add-data ((Join-Path $projectRoot 'eng.traineddata') + ':.') `
        --add-data ((Join-Path $projectRoot 'THIRD_PARTY_LICENSES.zip') + ':.' ) `
        --add-data ((Join-Path $projectRoot 'LICENSE') + ':.') `
        --distpath (Join-Path $projectRoot 'dist') `
        --workpath (Join-Path $projectRoot 'build') `
        --specpath (Join-Path $projectRoot 'build') `
        (Join-Path $projectRoot 'trainer.py')
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
    Copy-Item -LiteralPath (Join-Path $projectRoot ('dist\' + $Name + '.exe')) `
        -Destination (Join-Path $projectRoot ($Name + '.exe')) -Force
    Write-Host ('Ready: ' + (Join-Path $projectRoot ($Name + '.exe')))
} finally {
    Pop-Location
}
