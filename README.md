# TwitchOSS

Interface web pour regarder Twitch sans publicités (avec chat intégré), les chaînes TV via IPTV, et un « direct maison » poussé depuis un PC en France. Déployé sur VPS derrière Nginx.

![Stack](https://img.shields.io/badge/Python-Flask-blue) ![Stack](https://img.shields.io/badge/Streamlink-purple) ![Stack](https://img.shields.io/badge/ffmpeg-HLS-orange)

## Fonctionnalités

- **Twitch sans pubs** : streamlink extrait le flux, ffmpeg le repackage en HLS local
- **Chaînes TV (IPTV)** : sources publiques iptv-org (FR/DE/AT), avec speed-test automatique des sources multiples et recherche d'alternatives dans la base globale si le débit est insuffisant
- **📡 Direct maison** : un PC en France pousse une chaîne TV en SRT au VPS, qui la rediffuse à tout le monde (voir [FEEDER.md](FEEDER.md))
- **Sidebar** : onglets Twitch / Chaînes TV, recherche, favoris épinglés (localStorage), statut live + avatars (API GQL Twitch)
- **Chat Twitch** intégré (embed officiel) + bouton popout (compatibilité 7TV/BTTV/FFZ)
- **Panneau debug** (touche `D`) : mode, buffer, segments, stalls, m3u8 brut, copie en un clic
- Raccourcis clavier : `Espace` play/pause, `M` mute, `↑/↓` volume, `F` plein écran, `D` debug

> ⚠️ **Un seul flux actif à la fois** : le serveur n'a qu'un pipeline ffmpeg. Lancer une chaîne coupe celle en cours pour tout le monde. C'est pensé pour un petit groupe qui regarde la même chose (le bouton « direct maison », lui, ne redémarre rien : il rejoint le flux en cours).

## Stack technique

| Composant | Rôle |
|-----------|------|
| **Streamlink** | Extrait le flux Twitch (sans pubs) — aussi utilisé côté feeder maison |
| **ffmpeg** | Repackage tout (Twitch, IPTV, SRT) en segments HLS sur disque |
| **Flask** | Orchestration + sert l'interface et les segments |
| **hls.js** | Lecture des segments HLS dans le navigateur |
| **Twitch GQL API** | Statut live + photos de profil de la sidebar |

## Architecture

Trois sources possibles, un seul pipeline de sortie :

```
Twitch:   streamlink --stdout twitch.tv/<ch> ──pipe──► ffmpeg -c copy
IPTV:     ffmpeg -user_agent <UA navigateur> -i <meilleure source>   ─┐
Feeder:   PC maison ──SRT (UDP 9000)──► ffmpeg en écoute             ─┤
                                                                      ▼
                                              hls/playlist.m3u8 + seg*.ts
                                              (rolling 6 segments)
                                                                      ▼
                                              Flask /hls/* ──► hls.js
```

Points importants (appris à la dure) :

- **Tout passe par ffmpeg → disque.** Un proxy HTTP temps réel (re-télécharger chaque segment à la demande) causait des rebufferings ; ffmpeg pré-télécharge en continu et le navigateur lit en local.
- **User-Agent navigateur obligatoire** pour l'IPTV : certains relais renvoient une playlist vide à l'UA par défaut de ffmpeg.
- **Les CDN officiels (TF1, france.tv…) bloquent les IP datacenter**, d'où le feeder maison — détails dans [FEEDER.md](FEEDER.md).

## Endpoints

| Route | Rôle |
|-------|------|
| `GET /` | Interface web |
| `GET /channels` | Liste des chaînes Twitch (`channels.txt`) |
| `GET /channel-info` | Statut live + avatars (GQL) |
| `GET /start/<channel>` | Lance le pipeline Twitch |
| `GET /iptv-channels` | Chaînes IPTV iptv-org FR/DE/AT (cache 1 h) |
| `GET\|POST /start-iptv` | Lance le pipeline IPTV (`?url=` ou JSON `{sources: [...]}` → speed-test) |
| `GET /find-sources` | Cherche d'autres sources d'une chaîne dans la base globale iptv-org |
| `GET\|POST /start-feed` | Met ffmpeg en écoute SRT (UDP 9000) pour le feeder maison |
| `GET /hls/<file>` | Playlist + segments HLS |
| `GET /debug-info` | État des processus + fichiers HLS |

## Déploiement VPS

### Prérequis

```bash
sudo apt install ffmpeg nginx certbot python3-certbot-nginx python3-venv
```

### Installation

```bash
git clone https://github.com/NathanGracia/twitchoss /home/ubuntu/twitchoss
python3 -m venv /home/ubuntu/twitchoss-env
/home/ubuntu/twitchoss-env/bin/pip install streamlink flask requests
```

### Service systemd

```bash
sudo cp twitchoss.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now twitchoss
```

### Nginx + HTTPS

```nginx
server {
    listen 80;
    server_name twitchoss.ton-domaine.com;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_buffering off;
        proxy_cache off;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/twitchoss /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d twitchoss.ton-domaine.com
```

> Le chat Twitch embed requiert HTTPS + un domaine (pas une IP brute).
> Pour le feeder maison, l'**UDP 9000** doit être joignable depuis l'extérieur.

## Installation locale (Windows)

Python 3.11+, [Streamlink](https://streamlink.github.io/) et [ffmpeg](https://ffmpeg.org/) dans le PATH.

```bash
pip install flask requests streamlink
```

Adapter le chemin dans `server.py` :

```python
STREAMLINK = r"C:\...\Scripts\streamlink.exe"
```

Lancer avec `start.bat`.

Alternative sans serveur : `watch.bat` / `watch.ps1` ouvrent directement une chaîne de `channels.txt` dans VLC via streamlink (menu interactif, chat en popup optionnel).

## Configuration des chaînes Twitch

Édite `channels.txt` — une chaîne par ligne, sans `twitch.tv/` :

```
# commentaire ignoré
squeezie
mistermv
zerator
```

## Tests

`test_sim.py` simule un navigateur (hls.js) contre un serveur qui tourne :

```bash
python test_sim.py     # cible http://localhost:5000
```

> Attention : il lance de vrais pipelines IPTV, donc il coupe un éventuel flux en cours et laisse un ffmpeg actif à la fin.

## Fichiers

```
twitchoss/
├── server.py           # Serveur Flask + pipelines ffmpeg (Twitch, IPTV, feed SRT)
├── index.html          # Interface web complète (CSS + JS inclus)
├── channels.txt        # Chaînes Twitch de la sidebar
├── twitchoss.service   # Unit systemd pour le VPS
├── feeder.sh / .bat    # Feeder maison — push SRT vers le VPS (voir FEEDER.md)
├── FEEDER.md           # Doc du direct TV via feeder maison
├── start.bat           # Lance le serveur en local (Windows)
├── watch.bat / .ps1    # Lecture directe streamlink → VLC (Windows, sans serveur)
├── test_sim.py         # Tests end-to-end contre un serveur lancé
├── CONTEXT.md          # Note de passation entre machines (dernière modif + action requise)
└── hls/                # Segments HLS (généré au runtime, ignoré par git)
    ├── playlist.m3u8
    └── seg*.ts
```
