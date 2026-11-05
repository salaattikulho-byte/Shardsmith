@echo off
setlocal
title Building Shardsmith.exe
cd /d "%~dp0"
set "LOG=%~dp0build_log.txt"
set "WORK=%TEMP%\shardsmith_build"
echo Shardsmith build log - %DATE% %TIME% > "%LOG%"

rem ---- find Python ("py" launcher first, then "python")
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
    echo Python was not found. >> "%LOG%"
    echo Python was not found. It is only needed on YOUR pc to build the exe once.
    goto failed
)
%PY% --version >> "%LOG%" 2>&1

rem ---- clean leftovers from earlier attempts
if exist dist rmdir /s /q dist
if exist "%WORK%" rmdir /s /q "%WORK%"

echo [1/3] Installing PyInstaller...
%PY% -m pip install --upgrade pyinstaller >> "%LOG%" 2>&1
if errorlevel 1 goto failed

echo [2/3] Building Shardsmith.exe (takes a minute)...
echo ===== one-file build ===== >> "%LOG%"
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed --name Shardsmith --icon "%~dp0shardsmith.ico" --workpath "%WORK%" --specpath "%WORK%" --distpath "%~dp0dist" shardsmith.py >> "%LOG%" 2>&1
if not errorlevel 1 goto onefile_ok

echo    One-file build was blocked, trying the folder version instead...
echo ===== folder build ===== >> "%LOG%"
if exist dist rmdir /s /q dist
%PY% -m PyInstaller --noconfirm --clean --onedir --windowed --name Shardsmith --icon "%~dp0shardsmith.ico" --workpath "%WORK%" --specpath "%WORK%" --distpath "%~dp0dist" shardsmith.py >> "%LOG%" 2>&1
if errorlevel 1 goto failed

echo [3/3] Packing...
if exist "Shardsmith share" rmdir /s /q "Shardsmith share"
mkdir "Shardsmith share"
xcopy /e /i /y /q "dist\Shardsmith" "Shardsmith share\Shardsmith" >> "%LOG%" 2>&1
copy /y "Shardsmith - read me.txt" "Shardsmith share\" >nul 2>nul
goto done

:onefile_ok
echo [3/3] Packing...
if exist "Shardsmith share" rmdir /s /q "Shardsmith share"
mkdir "Shardsmith share"
move /y "dist\Shardsmith.exe" "Shardsmith share\Shardsmith.exe" >> "%LOG%" 2>&1
copy /y "Shardsmith - read me.txt" "Shardsmith share\" >nul 2>nul

:done
if exist dist rmdir /s /q dist
echo BUILD OK >> "%LOG%"
echo.
echo Done! Your shareable copy is in the folder "Shardsmith share".
echo Zip that folder and send it to your friends.
explorer "Shardsmith share"
pause
exit /b 0

:failed
echo BUILD FAILED >> "%LOG%"
echo.
echo Something went wrong. Last lines of build_log.txt:
echo ------------------------------------------------------------
powershell -NoProfile -Command "Get-Content -LiteralPath '%LOG%' -Tail 20"
echo ------------------------------------------------------------
echo The full log is saved as build_log.txt in this folder.
pause
exit /b 1
