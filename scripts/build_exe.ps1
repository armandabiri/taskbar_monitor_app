# Taskbar Monitor Executable Builder
# This script compiles the Taskbar Monitor into a single, standalone .exe using PyInstaller.

Write-Host "--- Starting Taskbar Monitor Build Process ---" -ForegroundColor Cyan

# 1. Ensure Virtual Environment is active
if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    Write-Host "[1/4] Activating Virtual Environment..." -ForegroundColor Yellow
    . .\.venv\Scripts\Activate.ps1
}
else {
    Write-Host "[!] Virtual environment not found. Building with system Python..." -ForegroundColor Red
}

# 2. Ensure PyInstaller and dependencies are installed
Write-Host "[2/4] Verifying dependencies (PyInstaller, psutil, PyQt6)..." -ForegroundColor Yellow
pip install -q pyinstaller psutil PyQt6

# 3. Clean previous builds
if (Test-Path ".\dist") { Remove-Item -Recurse -Force ".\dist" }
if (Test-Path ".\build") { Remove-Item -Recurse -Force ".\build" }

# 4. Run PyInstaller
Write-Host "[3/4] Compiling to Standalone Executable..." -ForegroundColor Yellow
# Flags explained:
# --onefile: Bundle everything into a single .exe
# --noconsole: Prevents a command prompt window from popping up on launch
# --clean: Clean PyInstaller cache before building
# --name: Specific name for the output file
pyinstaller --onefile --noconsole --clean --name "TaskbarMonitor" "src/main.py"

if ($LASTEXITCODE -eq 0) {
    Write-Host "[4/4] Build Successful!" -ForegroundColor Green
    Write-Host "Executable located in: $(Get-Location)\dist\TaskbarMonitor.exe" -ForegroundColor Cyan
}
else {
    Write-Host "[!] Build Failed. Check the error log above." -ForegroundColor Red
}
