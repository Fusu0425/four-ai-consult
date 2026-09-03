param(
    [switch]$SkipTests,
    [string]$LicenseDirectory
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonExe = Join-Path $projectRoot ".venv-packaging\Scripts\python.exe"
$output = Join-Path $projectRoot "dist\onefile"
$work = Join-Path $projectRoot "build\pyinstaller-onefile"
$spec = Join-Path $PSScriptRoot "FourAIConsult-onefile.spec"
$testTemp = Join-Path $projectRoot (".test-temp-onefile-" + $PID)
if (-not $LicenseDirectory) { $LicenseDirectory = Join-Path $projectRoot "licenses" }

if (-not (Test-Path -LiteralPath $pythonExe)) { throw "Missing release Python: $pythonExe" }
if (-not (Test-Path -LiteralPath (Join-Path $LicenseDirectory "qt-6.11.2\sources.json"))) {
    throw "Missing reviewed Qt notice snapshot"
}

$originalPath = $env:PATH
$testEnvKeys = @("RUN_QT_WEBENGINE_TESTS", "QT_QPA_PLATFORM", "QTWEBENGINE_CHROMIUM_FLAGS", "PYTHONIOENCODING")
$savedTestEnv = @{}
foreach ($key in $testEnvKeys) { $savedTestEnv[$key] = [Environment]::GetEnvironmentVariable($key, "Process") }
Push-Location $projectRoot
try {
    $version = (& $pythonExe -c "from four_ai_consult import __version__; print(__version__)").Trim()
    if ($LASTEXITCODE -ne 0 -or $version -notmatch '^\d+\.\d+\.\d+$') { throw "Invalid version" }
    $pythonBase = (& $pythonExe -c "import sys; print(sys.base_prefix)").Trim()
    if ($LASTEXITCODE -ne 0) { throw "Could not resolve the release Python base" }
    $env:PATH = @(
        (Join-Path $env:SystemRoot "System32"),
        $env:SystemRoot,
        (Join-Path $env:SystemRoot "System32\Wbem"),
        $pythonBase,
        (Split-Path $pythonExe)
    ) -join ";"
    if (-not $SkipTests) {
        $env:PYTHONIOENCODING = "utf-8"
        $env:RUN_QT_WEBENGINE_TESTS = "1"
        $env:QT_QPA_PLATFORM = "offscreen"
        $env:QTWEBENGINE_CHROMIUM_FLAGS = "--disable-gpu --no-sandbox"
        & $pythonExe -m ruff check main.py launcher.pyw four_ai_consult tools tests packaging
        if ($LASTEXITCODE -ne 0) { throw "Ruff failed" }
        & $pythonExe -m pytest -q -p no:cacheprovider --basetemp $testTemp
        if ($LASTEXITCODE -ne 0) { throw "Tests failed" }
    }
    if (Test-Path -LiteralPath $output) { Remove-Item -LiteralPath $output -Recurse -Force }
    $env:FOUR_AI_LICENSE_DIRECTORY = (Resolve-Path -LiteralPath $LicenseDirectory).Path
    & $pythonExe -m PyInstaller `
        --noconfirm --clean --distpath $output --workpath $work $spec
    if ($LASTEXITCODE -ne 0) { throw "One-file build failed" }
    $artifact = Join-Path $output "FourAIConsult-$version-onefile.exe"
    if (-not (Test-Path -LiteralPath $artifact)) { throw "One-file artifact missing" }
    $archiveListing = (& $pythonExe -m PyInstaller.utils.cliutils.archive_viewer -l $artifact) -join "`n"
    if ($LASTEXITCODE -ne 0) { throw "Could not inspect one-file archive" }
    foreach ($required in @("QtWebEngineProcess.exe", "THIRD_PARTY_NOTICES.md", "licenses\\qt-6.11.2\\sources.json")) {
        if ($archiveListing -notmatch [regex]::Escape($required)) { throw "Missing required packaged file: $required" }
    }
    if ($archiveListing -match "(?im)'(?:[^']*\\)?(?:icuuc\.dll|icudt[^']*\.dll)'$") {
        throw "One-file archive contains incompatible ICU files from the host environment"
    }
    $hash = (Get-FileHash -LiteralPath $artifact -Algorithm SHA256).Hash
    ($hash + "  " + (Split-Path $artifact -Leaf) + "`n") | Set-Content -LiteralPath ($artifact + ".sha256") -Encoding utf8NoBOM
    Write-Host "One-file build: $artifact"
    Write-Host "SHA256: $hash"
}
finally {
    Remove-Item Env:FOUR_AI_LICENSE_DIRECTORY -ErrorAction SilentlyContinue
    $env:PATH = $originalPath
    foreach ($key in $testEnvKeys) { [Environment]::SetEnvironmentVariable($key, $savedTestEnv[$key], "Process") }
    Pop-Location
}
