param(
    [switch]$SkipTests,
    [string]$LicenseDirectory
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$packagingPython = Join-Path $projectRoot ".venv-packaging\Scripts\python.exe"
$pythonExe = $packagingPython
$buildOutput = Join-Path $projectRoot "dist\FourAIConsult"
$buildWork = Join-Path $projectRoot "build\pyinstaller"
$specOutput = Join-Path $projectRoot "build\pyinstaller-spec"
$testTemp = Join-Path $projectRoot (".test-temp-build-" + $PID)
if (-not $LicenseDirectory) { $LicenseDirectory = Join-Path $projectRoot "licenses" }
if (-not (Test-Path -LiteralPath (Join-Path $LicenseDirectory "qt-6.11.2\sources.json"))) {
    throw "Missing reviewed Qt notice snapshot. Run tools.collect_licenses licenses --qt-docs and review before packaging."
}

if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw "Missing clean release environment: $packagingPython"
}

$originalPath = $env:PATH
$testEnvKeys = @("RUN_QT_WEBENGINE_TESTS", "QT_QPA_PLATFORM", "QTWEBENGINE_CHROMIUM_FLAGS", "PYTHONIOENCODING")
$savedTestEnv = @{}
foreach ($key in $testEnvKeys) { $savedTestEnv[$key] = [Environment]::GetEnvironmentVariable($key, "Process") }
Push-Location $projectRoot
try {
    $version = (& $pythonExe -c "from four_ai_consult import __version__; print(__version__)").Trim()
    if ($LASTEXITCODE -ne 0 -or $version -notmatch '^\d+\.\d+\.\d+$') { throw "Invalid release version" }
    $portableArchive = Join-Path $projectRoot "dist\FourAIConsult-$version-portable.zip"
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

    if (Test-Path -LiteralPath $buildOutput) {
        $resolvedOutput = (Resolve-Path -LiteralPath $buildOutput).Path
        $expectedOutput = Join-Path $projectRoot "dist\FourAIConsult"
        if ($resolvedOutput -ne $expectedOutput) {
            throw "Refusing to clean unexpected build directory: $resolvedOutput"
        }
        Remove-Item -LiteralPath $resolvedOutput -Recurse -Force
    }

    & $pythonExe -m PyInstaller `
        --noconfirm `
        --clean `
        --additional-hooks-dir (Join-Path $PSScriptRoot "hooks") `
        --windowed `
        --name FourAIConsult `
        --icon (Join-Path $projectRoot "resources\four-ai-consult.ico") `
        --add-data ((Join-Path $projectRoot "resources") + ";resources") `
        --distpath (Join-Path $projectRoot "dist") `
        --workpath $buildWork `
        --specpath $specOutput `
        --collect-submodules keyring.backends `
        launcher.pyw
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }

    $pollutedIcu = Get-ChildItem -LiteralPath $buildOutput -Recurse -File | Where-Object {
        $_.Name -eq "icuuc.dll" -or $_.Name -like "icudt*.dll"
    }
    if ($pollutedIcu) {
        Write-Warning "Removing incompatible ICU files injected by the host runtime"
        $pollutedIcu | Remove-Item -Force
    }
    $remainingIcu = Get-ChildItem -LiteralPath $buildOutput -Recurse -File | Where-Object {
        $_.Name -eq "icuuc.dll" -or $_.Name -like "icudt*.dll"
    }
    if ($remainingIcu) {
        $names = ($remainingIcu.FullName -join ", ")
        throw "Could not clean external ICU files from the release: $names"
    }
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "开始使用.html") -Destination $buildOutput
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "内测说明.txt") -Destination $buildOutput
    foreach ($notice in @("LICENSE", "THIRD_PARTY_NOTICES.md", "PRIVACY.md", "SECURITY.md")) {
        Copy-Item -LiteralPath (Join-Path $projectRoot $notice) -Destination $buildOutput
    }
    Copy-Item -LiteralPath $LicenseDirectory -Destination (Join-Path $buildOutput "licenses") -Recurse
    & $pythonExe -m tools.release_audit $buildOutput --prepare
    if ($LASTEXITCODE -ne 0) { throw "Release directory audit failed" }
    if (Test-Path -LiteralPath $portableArchive) {
        Remove-Item -LiteralPath $portableArchive -Force
    }
    Compress-Archive -LiteralPath $buildOutput -DestinationPath $portableArchive -CompressionLevel Optimal
    & $pythonExe -m tools.release_audit $portableArchive
    if ($LASTEXITCODE -ne 0) { throw "Portable archive audit failed" }
    Write-Host "Portable build created at: $buildOutput"
    Write-Host "Shareable archive created at: $portableArchive"
}
finally {
    $env:PATH = $originalPath
    foreach ($key in $testEnvKeys) { [Environment]::SetEnvironmentVariable($key, $savedTestEnv[$key], "Process") }
    Pop-Location
}
