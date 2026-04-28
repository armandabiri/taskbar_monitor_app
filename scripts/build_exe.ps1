# Taskbar Monitor Executable Builder
# Compiles the Taskbar Monitor into a standalone .exe using PyInstaller.

Write-Host "--- Starting Taskbar Monitor Build Process ---" -ForegroundColor Cyan

$distPath = ".\dist_pyinstaller"
$workPath = ".\build_pyinstaller"

# 1. Activate virtualenv if present
if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    Write-Host "[1/5] Activating Virtual Environment..." -ForegroundColor Yellow
    . .\.venv\Scripts\Activate.ps1
}
else {
    Write-Host "[!] Virtual environment not found. Building with system Python..." -ForegroundColor Red
}

# 2. Ensure dependencies
Write-Host "[2/5] Verifying dependencies (editable app install + PyInstaller)..." -ForegroundColor Yellow
pip install -q -e . pyinstaller

# 3. Regenerate the .ico from the SVG so the EXE picks up the latest icon
Write-Host "[3/5] Building .ico from SVG..." -ForegroundColor Yellow
python scripts/svg_to_ico.py src/assets/taskbar-monitor.svg src/assets/taskbar-monitor.ico
if ($LASTEXITCODE -ne 0) {
    Write-Host "[!] Icon generation failed." -ForegroundColor Red
    exit 1
}

# 4. Clean previous isolated builds
if (Test-Path $distPath) { Remove-Item -Recurse -Force $distPath }
if (Test-Path $workPath) { Remove-Item -Recurse -Force $workPath }

# 5. Run PyInstaller using the spec (keeps icon + bundled assets in sync)
Write-Host "[4/5] Compiling to Standalone Executable..." -ForegroundColor Yellow
python -m PyInstaller --clean --noconfirm --distpath $distPath --workpath $workPath TaskbarMonitor.spec

if ($LASTEXITCODE -eq 0) {
    Write-Host "[5/5] Build Successful!" -ForegroundColor Green
    Write-Host "Executable located in: $(Get-Location)\dist_pyinstaller\TaskbarMonitor.exe" -ForegroundColor Cyan
}
else {
    Write-Host "[!] Build Failed. Check the error log above." -ForegroundColor Red
}
