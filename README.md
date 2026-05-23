# TwitchOSS

Interface locale pour regarder Twitch sans publicités, avec chat intégré.

![Stack](https://img.shields.io/badge/Python-Flask-blue) ![Stack](https://img.shields.io/badge/Streamlink-8.x-purple) ![Stack](https://img.shields.io/badge/ffmpeg-HLS-orange)

## Fonctionnalités

- Lecture des streams Twitch sans pubs via Streamlink
- Interface web reproduisant l'expérience Twitch (sidebar, vidéo, chat)
- Chat Twitch intégré (embed officiel) + bouton pour ouvrir en popup (compatibilité 7TV/BTTV/FFZ)
- Indicateurs de live en temps réel (point rouge) avec tri automatique des chaînes live en haut
- Photos de profil chargées depuis l'API Twitch
- Contrôles vidéo : play/pause, volume slider, mute
- Raccourcis clavier : `Espace` play/pause, `M` mute, `↑/↓` volume

## Stack technique

| Composant | Rôle |
|-----------|------|
| **Streamlink** | Extrait le flux Twitch (sans pubs, qualité max 60fps) |
| **ffmpeg** | Repackage le flux MPEG-TS en segments HLS sur disque |
| **Flask** | Serveur local qui orchestre tout et sert l'interface |
| **hls.js** | Lecture des segments HLS dans le navigateur |
| **Twitch GQL API** | Statut live + photos de profil des chaînes |

## Architecture

```
Twitch CDN
    │
    ▼
Streamlink (stdout) ──pipe──▶ ffmpeg ──▶ hls/playlist.m3u8
                                              hls/seg0.ts
                                              hls/seg1.ts ...
                                                  │
                                                  ▼
                                         Flask /hls/<file>
                                                  │
                                                  ▼
                                           hls.js (browser)
```

## Prérequis

- Python 3.11+
- [Streamlink](https://streamlink.github.io/) 8.x
- [ffmpeg](https://ffmpeg.org/) (dans le PATH)
- Firefox ou Chrome

## Installation

```bash
pip install flask requests
```

Modifie le chemin de Streamlink dans `server.py` si nécessaire :

```python
STREAMLINK = r"C:\...\Scripts\streamlink.exe"
```

## Utilisation

Double-clique sur **`start.bat`** (tue les instances précédentes et lance le serveur).

L'interface s'ouvre automatiquement sur `http://localhost:5000`.

## Configuration des chaînes

Édite `channels.txt` — une chaîne par ligne, sans `twitch.tv/` :

```
# commentaire ignoré
squeezie
mistermv
zerator
```

## Notes

- La qualité sélectionnée est `1080p60 > 720p60 > best` pour prioriser le 60fps
- Les segments HLS sont recréés à chaque changement de chaîne (dossier `hls/` réinitialisé)
- Le statut live se rafraîchit toutes les 2 minutes via l'API GQL de Twitch
- Pour les emotes 7TV : utiliser le bouton ↗ dans le header du chat pour ouvrir le chat en popup séparée (les iframes Twitch embed bloquent l'injection des extensions)

## Fichiers

```
twitchoss/
├── server.py       # Serveur Flask + pipeline streamlink/ffmpeg
├── index.html      # Interface web complète
├── channels.txt    # Liste des chaînes à suivre
├── start.bat       # Lanceur Windows
├── watch.ps1       # Ancien lanceur VLC (alternatif)
└── hls/            # Segments HLS temporaires (généré au runtime)
```
