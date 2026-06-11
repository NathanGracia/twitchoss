# Contexte dernière modif

Latence SRT augmentée de 3000ms à 6000ms dans `server.py` (route `/start-feed`) pour réduire les freezes et artefacts vidéo côté viewers.

## Action requise

```bash
git pull
sudo systemctl restart twitchoss
```

C'est tout.
