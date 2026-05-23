@echo off
echo Arret des instances precedentes...
taskkill /F /IM python.exe /T >nul 2>&1
taskkill /F /IM ffmpeg.exe /T >nul 2>&1
timeout /t 1 /nobreak >nul
echo Demarrage de TwitchOSS...
python "%~dp0server.py"
