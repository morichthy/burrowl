@echo off
rem Install or switch burrowl versions.
rem   setup.cmd               install the version in this folder and make it current
rem   setup.cmd -Use 0.1.0    switch to an installed version (rollback)
rem   setup.cmd -List         list installed versions
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
exit /b %ERRORLEVEL%
