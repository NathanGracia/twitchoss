# Contexte dernière modif

Refacto / ménage (11 juin 2026, fait sur le VPS, déjà déployé là-bas) :

- **Code mort supprimé** : routes `/iptv-proxy/*` de `server.py` + branches `mode === 'proxy'`
  d'`index.html` (tout passe par ffmpeg → HLS local depuis le fix rebuffering, le proxy ne
  servait plus jamais). `_iptv_proxy_url`, imports inutilisés et champ `proxy_url` de
  `/debug-info` enlevés aussi.
- **Petit fix front** : si `/start-iptv` répond `ok: false`, on affiche l'erreur au lieu de
  lancer `startHls(undefined)`.
- **test_sim.py** : sections proxy réécrites pour le mode ffmpeg (playlist HLS locale,
  segment servi du disque, vérif que la playlist avance). 17/17 passent.
- **README.md** : réécrit pour coller au code réel (l'ancien décrivait une version
  multi-channel avec bouton SYNC, ajout de chaînes dans l'UI, etc. qui n'existent pas ici).
  Ajout d'un tableau des endpoints et de la limite « un seul flux actif à la fois ».
- **FEEDER.md** : latences SRT à jour (VPS = 6000, négociation au max des deux côtés).

## Action requise (PC)

```bash
git pull
```

C'est tout — rien à changer côté feeder (il peut rester à `latency=3000`, le VPS impose 6000).
