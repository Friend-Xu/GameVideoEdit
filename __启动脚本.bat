@echo off
rem Auto-relaunch in Windows Terminal if not already running there
if not defined WT_SESSION (
    wt.exe --title "Claude Code" -d "%CD%" cmd /k "%~f0"
    exit /b
)

chcp 65001 >nul
title Claude Code

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set LANG=zh_CN.UTF-8
set LC_ALL=zh_CN.UTF-8

rem ============================================================
rem ECC Plugin Pre-flight: check and enable without loading Claude Code
rem ============================================================
echo.
echo [Pre-flight] Checking ECC plugin status...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$settingsPath = Join-Path $env:USERPROFILE '.claude\settings.json';" ^
  "$installedPath = Join-Path $env:USERPROFILE '.claude\plugins\installed_plugins.json';" ^
  "$installed = Get-Content $installedPath -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json;" ^
  "$hasEcc = $installed -and $installed.plugins.PSObject.Properties.Name -contains 'everything-claude-code@everything-claude-code';" ^
  "if (-not $hasEcc) {" ^
  "  Write-Host '[Skip] ECC plugin not installed';" ^
  "  exit 0;" ^
  "};" ^
  "$settings = Get-Content $settingsPath -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json;" ^
  "if (-not $settings.PSObject.Properties['enabledPlugins']) {" ^
  "  $settings | Add-Member -NotePropertyName 'enabledPlugins' -NotePropertyValue @{} -Force;" ^
  "};" ^
  "$key = 'everything-claude-code@everything-claude-code';" ^
  "$isEnabled = $settings.enabledPlugins.$key -eq $true;" ^
  "if ($isEnabled) {" ^
  "  Write-Host '[OK] ECC plugin is enabled';" ^
  "} else {" ^
  "  Write-Host '[Action] ECC disabled, writing enable flag...';" ^
  "  $settings.enabledPlugins | Add-Member -NotePropertyName $key -NotePropertyValue $true -Force;" ^
  "  $settings | ConvertTo-Json -Depth 10 | Set-Content $settingsPath -Encoding UTF8;" ^
  "  Write-Host '[OK] ECC plugin enabled for next launch';" ^
  "}"

echo [Launch] Starting Claude Code...
echo.
claude --permission-mode auto --enable-auto-mode 
