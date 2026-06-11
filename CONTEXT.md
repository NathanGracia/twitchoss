# TwitchOSS — contexte agent VPS

## Ce que c'est

Interface web pour regarder Twitch sans pubs + chaînes TV françaises en direct, déployée sur un VPS OVH (`141.227.165.46`). Stack : Python/Flask + Streamlink + ffmpeg.

- URL publique : `https://twitchoss.nathangracia.com`
- Repo : `https://github.com/NathanGracia/twitchoss`
- Service systemd : `twitchoss.service`
- Dossier sur le VPS : probablement `/home/ubuntu/twitchoss` ou à vérifier avec `systemctl status twitchoss`

## Architecture

```
PC maison (IP résidentielle)       VPS (datacenter, bloqué par TF1/france.tv)
─────────────────────────          ──────────────────────────────────────────
feeder.bat / feeder.sh             server.py (Flask, port 5000, derrière nginx)
streamlink → ffmpeg                ffmpeg écoute SRT :9000 → écrit hls/
  │ push SRT UDP 9000 ──────────►  Flask sert /hls/playlist.m3u8
```

## Fichiers clés

- `server.py` — backend Flask, toute la logique (Twitch, IPTV, feeder SRT)
- `index.html` — UI
- `channels.txt` — liste des chaînes Twitch suivies
- `twitchoss.service` — unit systemd
- `feeder.bat` / `feeder.sh` — lancés sur le PC maison (**ignorés par git**, contiennent des credentials)

## Routes importantes

| Route | Rôle |
|-------|------|
| `GET /start/<channel>` | Lance un stream Twitch |
| `POST /start-feed` | Démarre le récepteur SRT (feeder maison) |
| `POST /start-iptv` | Lance une chaîne IPTV via ffmpeg |
| `GET /hls/<file>` | Sert les segments HLS |
| `GET /debug-info` | État en temps réel (ffmpeg vivant, fichiers HLS) |

## Ports

- `5000` — Flask (interne, nginx proxy)
- `9000 UDP` — SRT receiver pour le feeder maison

## Paramètres SRT actuels

- Latence : `6000 ms` (sender + receiver, augmentée pour réduire les artefacts)
- pkt_size : `1316`

## Commandes utiles sur le VPS

```bash
# Voir les logs en temps réel
journalctl -u twitchoss -f

# Redémarrer après un git pull
git pull && sudo systemctl restart twitchoss

# État du service
sudo systemctl status twitchoss

# Vérifier que ffmpeg écoute SRT
ss -ulpn | grep 9000

# Debug via l'API
curl https://twitchoss.nathangracia.com/debug-info
```

## Déploiement

```bash
git pull
sudo systemctl restart twitchoss
```

## Notes importantes

- Le VPS a une IP datacenter bloquée par TF1, france.tv, etc. → le feeder maison contourne ça.
- `feeder.bat`/`feeder.sh` sont dans `.gitignore` (credentials TF1 en clair).
- ffmpeg doit être compilé avec `libsrt` sur le VPS.
- Quand le feeder coupe, ffmpeg reste en listener SRT et attend une nouvelle connexion — pas de `#EXT-X-ENDLIST` dans la playlist tant que ffmpeg tourne.
