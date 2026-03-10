@echo off

set SRC=%SCRIPTROOT%\ChocoAgent
set DEST=C:\ProgramData\ChocoAgent

mkdir "%DEST%" 2>nul
copy /Y "%SRC%\DMCPatchAgent.exe" "%DEST%\DMCPatchAgent.exe"
copy /Y "%SRC%\nssm.exe" "%DEST%\nssm.exe"

sc query ChocoAgent >nul 2>&1
if %errorlevel% EQU 0 goto already_installed

"%DEST%\nssm.exe" install DMCPatchAgent "%DEST%\DMCPatchAgent.exe"
"%DEST%\nssm.exe" set DMCPatchAgent AppDirectory "%DEST%"
"%DEST%\nssm.exe" set DMCPatchAgent AppStdout "%DEST%\DMCPatchAgent.log"
"%DEST%\nssm.exe" set DMCPatchAgent AppStderr "%DEST%\DMCPatchAgent.log"
"%DEST%\nssm.exe" set DMCPatchAgent AppRestartDelay 10000
"%DEST%\nssm.exe" set DMCPatchAgent ObjectName "LocalSystem"
"%DEST%\nssm.exe" set DMCPatchAgent Start SERVICE_AUTO_START
"%DEST%\nssm.exe" set DMCPatchAgent Description "Chocolatey inventory and update agent"
"%DEST%\nssm.exe" start DMCPatchAgent
goto done

:already_installed
"%DEST%\nssm.exe" restart DMCPatchAgent
"%DEST%\nssm.exe" start DMCPatchAgent

:done