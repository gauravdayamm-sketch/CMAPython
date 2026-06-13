@echo off
rem ============================================================
rem  CMA Analyser — one-click credit memo
rem  Put this borrower's files in workspace\cma, workspace\probe42,
rem  workspace\notes (optional), then double-click this file.
rem  The finished memo lands in workspace\output.
rem ============================================================
cd /d "%~dp0"
echo.
"%~dp0venv\Scripts\python.exe" "%~dp0cma.py" autorun
echo.
echo ------------------------------------------------------------
echo  Finished. Your memo is in:  workspace\output
echo ------------------------------------------------------------
pause
