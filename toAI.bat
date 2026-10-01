@echo off
REM ============================================================
REM  toAI.bat - one-click package for the collaborating AI (Codex)
REM
REM  Double-click this file, or run "toAI.bat" from a command prompt.
REM
REM  NOTE: this launcher is deliberately ASCII-only.
REM  cmd.exe reads .bat files in the OEM codepage, so non-ASCII text
REM  here gets mangled and full-width punctuation is even parsed as
REM  command separators (hit this on 2026-08-27).
REM  All human-facing output comes from the Python script instead.
REM
REM  Plain text on purpose: nothing is downloaded, decoded or executed
REM  from outside. This is a financial-sector environment - the script
REM  has to be readable end to end by whoever audits it.
REM ============================================================

cd /d "%~dp0"

set PY=python
if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe

"%PY%" .project\make_codex_pack.py %*
set RC=%errorlevel%

echo.
pause
exit /b %RC%
