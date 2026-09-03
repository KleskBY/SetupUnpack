@echo off
setlocal
pushd "%~dp0"
if exist "%~dp0portable\SetupUnpack\SetupUnpack.exe" (
    start "" "%~dp0portable\SetupUnpack\SetupUnpack.exe"
) else (
    py -3 -m setupunpack gui
)
popd
