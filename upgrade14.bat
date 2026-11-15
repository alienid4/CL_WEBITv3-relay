@echo off
rem Upgrade the system host to the newest relay package. Script folder below; fix it if the folder moves.
set W=C:\Alien\cl_devtools\WEBIT3
powershell -NoProfile -File "%W%\upgrade14.ps1"
echo.
echo Exit code: %errorlevel%  (0 = OK)
pause
