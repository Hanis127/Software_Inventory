@echo off
mkdir "C:\ProgramData\ChocoAgent" 2>nul
copy /Y "\\your-server\NETLOGON\ChocoAgent\agent.exe" "C:\ProgramData\ChocoAgent\agent.exe"
copy /Y "\\your-server\NETLOGON\ChocoAgent\nssm.exe" "C:\ProgramData\ChocoAgent\nssm.exe"

sc query ChocoAgent >nul 2>&1
if %errorlevel% == 0 goto already_installed

C:\ProgramData\ChocoAgent\nssm.exe install ChocoAgent "C:\ProgramData\ChocoAgent\agent.exe"
C:\ProgramData\ChocoAgent\nssm.exe set ChocoAgent AppDirectory "C:\ProgramData\ChocoAgent"
C:\ProgramData\ChocoAgent\nssm.exe set ChocoAgent AppStdout "C:\ProgramData\ChocoAgent\agent.log"
C:\ProgramData\ChocoAgent\nssm.exe set ChocoAgent AppStderr "C:\ProgramData\ChocoAgent\agent.log"
C:\ProgramData\ChocoAgent\nssm.exe set ChocoAgent AppRestartDelay 10000
C:\ProgramData\ChocoAgent\nssm.exe set ChocoAgent ObjectName "LocalSystem"
C:\ProgramData\ChocoAgent\nssm.exe set ChocoAgent Start SERVICE_AUTO_START
C:\ProgramData\ChocoAgent\nssm.exe set ChocoAgent Description "Chocolatey inventory and update agent"
C:\ProgramData\ChocoAgent\nssm.exe start ChocoAgent
goto done

:already_installed
C:\ProgramData\ChocoAgent\nssm.exe restart ChocoAgent

:done