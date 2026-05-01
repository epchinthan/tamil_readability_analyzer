@echo off
cd /d "%~dp0"
echo.
echo   Tamil Book Readability Analyzer
echo.
set NEED_SETUP=0
if not exist ".venv\Scripts\python.exe" set NEED_SETUP=1
if not exist ".install_complete" set NEED_SETUP=1
if "%NEED_SETUP%"=="0" (
  ".venv\Scripts\python.exe" -c "import importlib; mods=['flask','pdfminer','pdfplumber','openpyxl','werkzeug','snowballstemmer','reportlab','watchdog','pytesseract','pdf2image','PIL','docx']; missing=[m for m in mods if importlib.util.find_spec(m) is None]; raise SystemExit(1 if missing else 0)" >nul 2>nul
  if errorlevel 1 set NEED_SETUP=1
)

if "%NEED_SETUP%"=="1" (
  echo First run or setup update detected. Preparing everything now...
  python setup.py --no-prompt
  if errorlevel 1 pause & exit /b 1
  type nul > .install_complete
)

echo.
echo Starting server...
echo Open: http://localhost:5000
echo Press Ctrl+C to stop
echo.
".venv\Scripts\python.exe" app.py
pause
