# TwitchOSS

Interface web pour regarder Twitch sans publicités, avec chat intégré. Déployable en local ou sur VPS.

![Stack](https://img.shields.io/badge/Python-Flask-blue) ![Stack](https://img.shields.io/badge/Streamlink-8.x-purple) ![Stack](https://img.shields.io/badge/ffmpeg-HLS-orange)

## Fonctionnalités

- Lecture des streams Twitch sans pubs via Streamlink
- **Multi-channel** : plusieurs utilisateurs peuvent regarder des chaînes différentes simultanément, chaque chaîne a son propre pipeline
- Interface reproduisant l'expérience Twitch (sidebar, vidéo, chat)
- Chat Twitch intégré (embed officiel) + bouton pour ouvrir en popup (compatibilité 7TV/BTTV/FFZ)
- **Sidebar** : statut live, viewers, jeu en cours, photo de profil, titre du stream dans l'info bar
- **Ajout de chaînes** depuis l'interface (champ `+` en bas de la sidebar)
- **URL avec hash** pour partager une chaîne (`/#squeezie`) — le stream se lance automatiquement
- **Clic molette** sur une chaîne → ouvre dans un nouvel onglet
- **Titre de l'onglet** mis à jour avec le nom de la chaîne
- **Volume persistant** en localStorage (survit aux rechargements)
- **Overlay pause** : fond semi-transparent + icône pause quand la vidéo est en pause
- **Bouton SYNC** : apparaît si le player décroche de +8s du live edge, un clic resynchronise
- Raccourcis clavier : `Espace` play/pause, `M` mute, `↑/↓` volume
- Favicon

## Stack technique

| Composant | Rôle |
|-----------|------|
| **Streamlink** | Extrait le flux Twitch (sans pubs, low latency, qualité max 60fps) |
| **ffmpeg** | Repackage le flux MPEG-TS en segments HLS sur disque |
| **Flask** | Serveur qui orchestre tout et sert l'interface |
| **hls.js** | Lecture des segments HLS dans le navigateur |
| **Twitch GQL API** | Statut live, viewers, jeu, titre, photos de profil |

## Architecture

```
Twitch CDN
    │
    ▼
Streamlink --twitch-low-latency --hls-live-edge 2 (stdout)
    │
    ▼ pipe
ffmpeg -fflags nobuffer+genpts+discardcorrupt -avoid_negative_ts make_zero
    │
    ▼
hls/<channel>/playlist.m3u8 + seg*.ts  (rolling 3 segments × ~2s)
    │
    ▼
Flask /hls/<channel>/<file>
    │
    ▼
hls.js (lowLatencyMode, liveSyncDurationCount=2, maxLiveSyncPlaybackRate=1.5)
```

**Pipelines simultanés** : chaque chaîne active a son propre dossier `hls/<channel>/` et ses propres processus streamlink+ffmpeg. Un thread de nettoyage kill les pipelines inactifs depuis 60s.

## Direct TV français (feeder maison) 📡

Pour regarder les chaînes TV françaises en direct (matchs, etc.) **sans compte ni pub plateforme**, et partager le direct avec des potes.

Les CDN officiels (TF1, france.tv…) et YouTube bloquent les **IP de datacenter** : un VPS se fait refuser ces flux quel que soit son pays. La parade : un **PC à la maison** (IP résidentielle, non bloquée) capte la chaîne via Streamlink et **pousse le flux au VPS en SRT** ; le VPS ne fait que rediffuser.

```
PC maison (France)              VPS (rediffuseur)              Potes
streamlink TF1 (sans compte)
  → ffmpeg → push SRT  ───────►  ffmpeg écoute SRT :9000
                                   → hls/ → Flask  ───────►  bouton "📡 direct maison"
```

**En bref :**
- Côté PC : `feeder.bat` (Windows) ou `feeder.sh` (Linux/Mac) — TF1 par défaut, ou passer une URL : `feeder.bat "https://www.france.tv/france-2/direct.html"`. Prérequis : `streamlink` + `ffmpeg` dans le PATH.
- Côté VPS : route `POST /start-feed` (récepteur SRT sur **UDP 9000**) — déjà déployée.
- Côté potes : ouvrir le site → bouton **« 📡 Regarder le direct maison »**.

Chaînes sans compte : TF1/TMC/TFX/LCI (plugin `tf1`), France 2/3/4/5 (plugin `pluzz`). Pas de M6/6play.

👉 **Détails, transport SRT, dépannage : voir [FEEDER.md](FEEDER.md).**

## Déploiement VPS (recommandé)

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

## Installation locale (Windows)

### Prérequis

Python 3.11+, [Streamlink](https://streamlink.github.io/) 8.x, [ffmpeg](https://ffmpeg.org/) dans le PATH.

```bash
pip install flask requests streamlink
```

Adapter le chemin dans `server.py` :

```python
STREAMLINK = r"C:\...\Scripts\streamlink.exe"
```

Lancer avec `start.bat`.

## Configuration des chaînes

Édite `channels.txt` — une chaîne par ligne, sans `twitch.tv/` :

```
# commentaire ignoré
squeezie
mistermv
zerator
```

Ou utilise le champ `+` en bas de la sidebar directement dans l'interface.

## Notes

- Qualité sélectionnée : `1080p60 > 720p60 > best`
- Les segments HLS sont recréés à chaque nouveau pipeline (`hls/<channel>/` réinitialisé)
- Le statut live/viewers/jeu/titre se rafraîchit toutes les 2 minutes
- Pour les emotes 7TV : ouvrir le chat en popup via le bouton ↗ (les iframes Twitch bloquent l'injection des extensions)
- Le désync son/image se corrige avec le bouton **SYNC** dans l'info bar

## Fichiers

```
twitchoss/
├── server.py           # Serveur Flask + pipelines streamlink/ffmpeg (Twitch, IPTV, feed SRT)
├── index.html          # Interface web complète
├── channels.txt        # Liste des chaînes à suivre
├── twitchoss.service   # Unit systemd pour déploiement VPS
├── start.bat           # Lanceur Windows
├── feeder.bat          # Feeder maison (Windows) — push SRT vers le VPS
├── feeder.sh           # Feeder maison (Linux/Mac)
├── FEEDER.md           # Doc du direct TV français via feeder maison
└── hls/                # Segments HLS temporaires par chaîne (généré au runtime)
    └── <channel>/
        ├── playlist.m3u8
        └── seg*.ts
```
