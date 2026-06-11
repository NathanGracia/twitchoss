@echo off
REM Feeder maison TwitchOSS - a lancer sur le PC Windows en France.
REM Capte une chaine via streamlink (sans compte, sans pub plateforme) et la pousse
REM en SRT vers le VPS, qui la rediffuse en HLS a tout le monde.
REM
REM Usage :  feeder.bat                                   (TF1 par defaut)
REM          feeder.bat "https://www.tf1.fr/tmc/direct"
REM
REM Prerequis : streamlink + ffmpeg dans le PATH.

set VPS=141.227.165.46
set PORT=9000
set URL=%1
if "%URL%"=="" set URL=https://www.tf1.fr/tf1/direct

echo Chaine : %URL%
echo Cible  : srt://%VPS%:%PORT%
echo.

:loop
REM 1) Demander au VPS de demarrer le recepteur SRT
curl -s -X POST "https://twitchoss.nathangracia.com/start-feed" >nul
timeout /t 2 >nul

REM 2) Capter le flux et le pousser en SRT
streamlink --stdout "%URL%" best | ffmpeg -hide_banner -i pipe:0 -c copy -f mpegts "srt://%VPS%:%PORT%?pkt_size=1316&latency=3000"

echo Flux interrompu - nouvelle tentative dans 5s (Ctrl+C pour arreter)...
timeout /t 5 >nul
goto loop
