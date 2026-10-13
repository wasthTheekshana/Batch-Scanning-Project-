@echo off
rem Start DOK Scanner against the separate DEMO data (run scripts\demo_setup.py first).
rem The app reads its data folder from LOCALAPPDATA, so pointing it at the demo
rem folder keeps real data untouched and lets the demo be reset at any time.
set "LOCALAPPDATA=%USERPROFILE%\DOKScannerDemo"
if not exist "%LOCALAPPDATA%\DOKScanner" (
  echo Demo data not found. Run:  python scripts\demo_setup.py
  exit /b 1
)
set "EXE=%~dp0..\dist\DOKScanner.exe"
if not exist "%EXE%" set "EXE=%~dp0..\DOKScanner.exe"
start "" /D "%LOCALAPPDATA%" "%EXE%"
