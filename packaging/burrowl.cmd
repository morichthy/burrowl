@echo off
rem burrowl launcher: uses the bundled standalone Python, independent of the system environment.
setlocal
set "BURROWL_LAUNCHER=%~f0"
set "BURROWL_HOME=%~dp0"
set "PYTHONUTF8=1"
set "PYTHONNOUSERSITE=1"
rem Shared models live in <root>\models; the Python side derives it from BURROWL_HOME (through the current junction).
"%~dp0python\python.exe" -m burrowl.interfaces.cli %*
exit /b %ERRORLEVEL%
