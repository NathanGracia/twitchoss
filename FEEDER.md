# Feeder maison (direct TF1 / France.tv via SRT)

Permet de regarder les chaînes TV françaises en direct (matchs, etc.) sur TwitchOSS,
**sans compte ni pub plateforme**, et de partager le direct avec des potes.

## Pourquoi ce montage

Les CDN officiels (TF1, france.tv, ORF, ZDF, ServusTV…) et même YouTube bloquent les
**IP de datacenter**. Un VPS (OVH, etc.) se fait donc refuser tous ces flux, peu importe
le pays où il est hébergé — ce n'est pas un blocage géographique mais un blocage de
réputation d'IP. Les relais IPTV publics (iptv-org) qui restent accessibles sont morts
ou trop lents pour du direct HD.

La solution : **ne pas capter le flux depuis le VPS**. Un PC à la maison, sur une
connexion résidentielle (non bloquée), capte la chaîne et pousse le flux au VPS. Le VPS
ne fait que **rediffuser** — il ne contacte jamais la source, donc son IP bloquée
n'intervient plus.

## Architecture

```
PC maison (France, IP résidentielle)        VPS (datacenter, bloqué)          Potes
────────────────────────────────────        ────────────────────────          ─────
streamlink <chaîne> best  (sans compte)
   │ flux brut, sans pub pre-roll plateforme
   ▼
ffmpeg -c copy -f mpegts
   │ push SRT (latency=3000) ───internet───►  ffmpeg écoute srt://0.0.0.0:9000
                                                  │ remux -c copy
                                                  ▼
                                              hls/playlist.m3u8 + seg*.ts
                                                  │
                                                  ▼
                                              Flask /hls/...  ──────────────►  hls.js
                                                                              (bouton
                                                                          "📡 direct maison")
```

- **Transport** : SRT sur **UDP 9000** (ffmpeg côté VPS compilé avec `libsrt`). SRT
  encaisse la gigue réseau bien mieux que du TCP brut.
- **Latence SRT** : le feeder pousse avec `latency=3000`, le VPS écoute avec
  `latency=6000` ; SRT négocie le **max des deux côtés**, donc 6 s effectives
  (montées de 3 s à 6 s pour absorber les freezes/artefacts côté viewers).
- **Latence totale** : ~15-20 s entre le PC et l'affichage chez les potes
  (latence SRT + buffer HLS). Normal.

## Côté VPS

Déjà en place dans `server.py` :

- Constante `SRT_PORT = 9000`.
- Route `POST /start-feed` : lance `ffmpeg -i srt://0.0.0.0:9000?mode=listener&latency=6000
  -c copy -f hls …`. ffmpeg bloque jusqu'à ce que le feeder se connecte, puis écrit les
  segments dans `hls/`.

Côté UI (`index.html`) : bouton **« 📡 Regarder le direct maison »** + fonction
`watchFeed()` qui charge simplement `/hls/playlist.m3u8` **sans relancer ffmpeg** (un
pote qui clique ne coupe donc pas le flux du host).

Le feeder appelle lui-même `/start-feed` avant de pousser : le host n'a qu'**une seule
commande** à lancer.

## Côté PC maison

Prérequis : `streamlink` et `ffmpeg` installés et dans le PATH.

```bash
streamlink --version
ffmpeg -version
```

Windows : `winget install streamlink.streamlink` et `winget install Gyan.FFmpeg`.

### Lancer le feeder

**Windows :**
```bat
feeder.bat
feeder.bat "https://www.tf1.fr/tmc/direct"
```

**Linux / Mac :**
```bash
./feeder.sh
./feeder.sh "https://www.france.tv/france-2/direct.html"
```

Par défaut : TF1 (`https://www.tf1.fr/tf1/direct`). Le script :
1. envoie `POST /start-feed` au VPS (démarre le récepteur SRT) ;
2. lance `streamlink --stdout <url> best | ffmpeg -i pipe:0 -c copy -f mpegts srt://<VPS>:9000?pkt_size=1316&latency=3000` ;
3. se reconnecte automatiquement si le flux coupe.

Laisser la fenêtre ouverte pendant toute la diffusion.

### Chaînes supportées par streamlink (sans compte)

| Plugin | Chaînes | Exemple d'URL |
|--------|---------|---------------|
| `tf1`   | TF1, TMC, TFX, TF1 Séries Films | `https://www.tf1.fr/tf1/direct` |
| `tf1` (lci) | LCI | `https://www.tf1.fr/lci/direct` |
| `pluzz` | France 2/3/4/5, franceinfo (france.tv) | `https://www.france.tv/france-2/direct.html` |

> **Pas de plugin M6/6play** dans streamlink. Pour M6 il faudrait une autre source.
> Le plugin `tf1` se connecte en anonyme (login uniquement si `--tf1-email` + `--tf1-password`
> sont fournis), donc **aucun compte requis**.

## Regarder

Ouvrir `https://twitchoss.nathangracia.com` → cliquer **📡 Regarder le direct maison**.

## Dépannage

- **streamlink réclame un compte / erreur d'auth** : vérifier l'URL (forme `/direct` pour
  TF1, `/direct.html` pour france.tv). Tester d'abord `streamlink <url> best` seul.
- **Le VPS ne reçoit rien** : vérifier que l'**UDP 9000** est joignable depuis le PC
  (ufw inactif sur le VPS, mais contrôler un éventuel firewall réseau OVH). Tester côté VPS :
  `ss -ulpn | grep 9000` doit montrer ffmpeg en écoute après un `/start-feed`.
- **« Pas de direct maison en ce moment »** : le feeder n'est pas (encore) connecté, ou
  ffmpeg n'a pas fini de remplir le premier segment (~10 s). Relancer le feeder.
- **Saccades** : la connexion montante du PC maison est insuffisante pour le débit de la
  chaîne. Augmenter `latency` côté feeder **au-delà de 6000** (SRT prend le max des deux
  côtés, donc en dessous ça ne change rien) ou choisir une qualité plus basse.
