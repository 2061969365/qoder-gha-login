@echo off
rem Fake default browser: record the URL Qoder hands to shell.openExternal.
if not exist "%~dp0urls.txt" type nul > "%~dp0urls.txt"
echo %~1>> "%~dp0urls.txt"
exit /b 0
