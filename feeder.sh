#!/usr/bin/env bash
# Feeder maison TwitchOSS — à lancer sur le PC en France (IP résidentielle).
# Capte une chaîne via streamlink (sans compte, sans pub plateforme) et la pousse
# en SRT vers le VPS, qui la rediffuse en HLS à tout le monde.
#
# Usage :   ./feeder.sh                       # TF1 par défaut
#           ./feeder.sh "https://www.tf1.fr/tmc/direct"
#           ./feeder.sh "https://www.france.tv/france-2/direct.html"
#
# Prérequis : streamlink + ffmpeg installés et dans le PATH.

VPS="141.227.165.46"          # IP du VPS (port SRT 9000 en UDP)
PORT="9000"
URL="${1:-https://www.tf1.fr/tf1/direct}"
QUALITY="best"

echo "Chaîne : $URL"
echo "Cible  : srt://$VPS:$PORT"
echo

while true; do
  # 1) Dire au VPS de démarrer le récepteur SRT
  curl -s -X POST "https://twitchoss.nathangracia.com/start-feed" >/dev/null
  sleep 2

  # 2) Capter le flux et le pousser en SRT (latence 3s pour absorber la gigue réseau)
  streamlink --stdout "$URL" "$QUALITY" \
    | ffmpeg -hide_banner -i pipe:0 -c copy \
        -f mpegts "srt://$VPS:$PORT?pkt_size=1316&latency=3000"

  echo "Flux interrompu — nouvelle tentative dans 5s (Ctrl+C pour arrêter)…"
  sleep 5
done
