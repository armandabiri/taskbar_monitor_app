@echo off
echo [Intelag] Installing recommended VS Code extensions...
powershell -ExecutionPolicy Bypass -Command "Get-Content '%~dp0extensions.json' | ConvertFrom-Json | Select-Object -ExpandProperty recommendations | ForEach-Object { code --install-extension $_ --force }"
echo [Intelag] Extension installation complete.
pause
