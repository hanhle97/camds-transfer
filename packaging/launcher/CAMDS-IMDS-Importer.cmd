@echo off
rem The only thing a user is given. It updates this machine from the share and
rem starts the application; everything it does is in launcher.ps1 beside it.
rem
rem PowerShell is called with its policy bypassed for this one file, because a
rem managed machine refuses to run scripts otherwise - and this is a file the
rem user was handed, not one they downloaded.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher.ps1" %*
if errorlevel 2 pause
