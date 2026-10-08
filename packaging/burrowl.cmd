@echo off
rem burrowl launcher: uses the bundled standalone Python, independent of the system environment.
setlocal
set "BURROWL_LAUNCHER=%~f0"
set "BURROWL_HOME=%~dp0"
set "PYTHONUTF8=1"
set "PYTHONNOUSERSITE=1"
rem Shared models live in <root>\models (installed by setup.ps1) unless BURROWL_MODELS_DIR is set.
if not defined BURROWL_MODELS_DIR if exist "%~dp0..\..\models\multilingual-minilm" set "BURROWL_MODELS_DIR=%~dp0..\..\models"
"%~dp0python\python.exe" -m burrowl.interfaces.cli %*
exit /b %ERRORLEVEL%
