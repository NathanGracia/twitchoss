from flask import Flask, jsonify, send_file, send_from_directory, request as freq
import subprocess, threading, os, atexit, shutil, time, urllib.parse, concurrent.futures, unicodedata, json
import requests
from pathlib import Path

app = Flask(__name__)

STREAMLINK = "/home/ubuntu/twitchoss-env/bin/streamlink"
BASE_DIR   = Path(os.path.abspath(__file__)).parent
CHANNELS_FILE = BASE_DIR / "channels.txt"
HLS_DIR    = BASE_DIR / "hls"

_lock    = threading.Lock()
_sl_proc = None
_ff_proc = None

_iptv_cache: dict      = {"channels": [], "ts": 0.0}
IPTV_BLOCKLIST_FILE   = BASE_DIR / "iptv_blocklist.json"
IPTV_QUALITY_TTL      = 6 * 3600  # ré-audit du débit des chaînes IPTV toutes les 6h
_iptv_quality_cache: dict = {"bad_ids": set(), "ts": 0.0, "refreshing": False}

try:
    _blocklist_data = json.loads(IPTV_BLOCKLIST_FILE.read_text(encoding="utf-8"))
    _iptv_quality_cache["bad_ids"] = set(_blocklist_data.get("bad_ids", []))
    _iptv_quality_cache["ts"]      = _blocklist_data.get("ts", 0.0)
except Exception:
    pass
_streams_cache: dict   = {"streams": None, "ts": 0.0}
_channels_cache: dict  = {"channels": None, "ts": 0.0}
IPTV_CACHE_TTL     = 3600
STREAMS_CACHE_TTL  = 7200
CHANNELS_CACHE_TTL = 7200

# User-Agent navigateur partagé pour toutes les requêtes IPTV (test de débit + ffmpeg).
# Certains relais bloquent les UA "VLC"/ffmpeg par défaut.
IPTV_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# Port d'écoute SRT pour le feeder maison (cf. /start-feed). SRT = UDP.
SRT_PORT = 9000


def _flat(s: str) -> str:
    """Normalise un nom : minuscules, sans accents, sans non-alphanumériques."""
    s = unicodedata.normalize('NFD', s.lower())
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return ''.join(c for c in s if c.isalnum())


def _clean(s: str) -> str:
    """Retire les qualificateurs entre parenthèses : 'France 2 (1080p)' → 'France 2'."""
    if '(' in s:
        s = s[:s.index('(')]
    return s.strip()


def _name_score(query: str, candidate: str) -> float:
    q, c = _flat(_clean(query)), _flat(_clean(candidate))
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    shorter, longer = (q, c) if len(q) <= len(c) else (c, q)
    if shorter in longer:
        return len(shorter) / len(longer)
    return 0.0


# ── Debug logging ──────────────────────────────────────────────────────────────

def log(tag, msg):
    print(f"[{time.strftime('%H:%M:%S')}][{tag}] {msg}", flush=True)


def _drain_stderr(pipe, tag):
    """Thread: affiche stderr d'un sous-processus ligne par ligne."""
    try:
        for raw in pipe:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                log(tag, line)
    except Exception:
        pass


# ── Processus ─────────────────────────────────────────────────────────────────

def _kill_all():
    global _sl_proc, _ff_proc
    with _lock:
        for p in (_ff_proc, _sl_proc):
            if p:
                try: p.kill()
                except: pass
        _sl_proc = _ff_proc = None

atexit.register(_kill_all)


def _reset_hls():
    try:
        shutil.rmtree(HLS_DIR)
    except Exception:
        pass
    HLS_DIR.mkdir(exist_ok=True)


def _pipe(src, dst):
    try:
        while True:
            chunk = src.read1(65536)
            if not chunk:
                break
            dst.write(chunk)
            dst.flush()
    except Exception:
        pass
    finally:
        try: dst.close()
        except: pass


# ── Helpers ───────────────────────────────────────────────────────────────────

def read_channels():
    try:
        return [l.strip() for l in CHANNELS_FILE.read_text(encoding="utf-8").splitlines()
                if l.strip() and not l.startswith("#")]
    except FileNotFoundError:
        return []


# ── Routes statiques ──────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_file(str(BASE_DIR / "index.html"))


@app.route("/channels")
def channels():
    return jsonify(read_channels())


@app.route("/channels/add", methods=["POST"])
def add_channel():
    data = freq.get_json(silent=True) or {}
    name = (data.get("channel") or "").strip().lower()
    if not name or not all(c.isalnum() or c == "_" for c in name):
        return jsonify({"error": "Nom de chaîne invalide"}), 400

    chs = read_channels()
    if name not in [c.lower() for c in chs]:
        with CHANNELS_FILE.open("a", encoding="utf-8") as f:
            f.write(("\n" if chs else "") + name + "\n")

    return jsonify({"ok": True, "channels": read_channels()})


@app.route("/channel-info")
def channel_info():
    import requests as req
    chs = read_channels()
    if not chs:
        return jsonify({})
    query = {
        "query": "query($logins:[String!]){users(logins:$logins){login profileImageURL(width:70) stream{id title viewersCount game{name}}}}",
        "variables": {"logins": chs},
    }
    try:
        r = req.post(
            "https://gql.twitch.tv/gql",
            json=query,
            headers={"Client-ID": "kimne78kx3ncx6brgo4mv6wki5h1ko"},
            timeout=5,
        )
        users = r.json()["data"]["users"]
        return jsonify({
            u["login"].lower(): {
                "live":    u["stream"] is not None,
                "avatar":  u.get("profileImageURL") or "",
                "title":   (u["stream"] or {}).get("title") or "",
                "viewers": (u["stream"] or {}).get("viewersCount") or 0,
                "game":    ((u["stream"] or {}).get("game") or {}).get("name") or "",
            }
            for u in users
            if u is not None
        })
    except Exception:
        return jsonify({ch: {"live": False, "avatar": "", "title": "", "viewers": 0, "game": ""} for ch in chs})


@app.route("/hls/<path:filename>")
def hls_file(filename):
    resp = send_from_directory(str(HLS_DIR), filename)
    resp.headers["Cache-Control"] = "no-cache, no-store"
    return resp


# ── Twitch (streamlink → ffmpeg → HLS local) ──────────────────────────────────

@app.route("/start/<channel>")
def start(channel):
    global _sl_proc, _ff_proc
    log("TWITCH", f"start {channel}")
    _kill_all()
    _reset_hls()

    sl = subprocess.Popen(
        [
            STREAMLINK, "--stdout",
            "--twitch-low-latency",
            f"twitch.tv/{channel}", "1080p60,720p60,best",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    threading.Thread(target=_drain_stderr, args=(sl.stderr, "streamlink"), daemon=True).start()

    ff = subprocess.Popen(
        [
            "ffmpeg", "-y",
            "-i", "pipe:0",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-force_key_frames", "expr:gte(t,n_forced*1)",
            "-sc_threshold", "0",
            "-c:a", "copy",
            "-f", "hls",
            "-hls_time", "1",
            "-hls_list_size", "6",
            "-hls_flags", "delete_segments",
            "-hls_allow_cache", "0",
            "-hls_segment_filename", str(HLS_DIR / "seg%d.ts"),
            str(HLS_DIR / "playlist.m3u8"),
        ],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    threading.Thread(target=_drain_stderr, args=(ff.stderr, "ffmpeg-twitch"), daemon=True).start()

    with _lock:
        _sl_proc = sl
        _ff_proc = ff

    threading.Thread(target=_pipe, args=(sl.stdout, ff.stdin), daemon=True).start()
    return jsonify({"ok": True})


# ── Emotes 7TV (chat maison) ──────────────────────────────────────────────────
# Le chat est rendu côté client (WebSocket IRC direct vers Twitch), mais la
# résolution de l'ID Twitch + le fetch des sets d'emotes 7TV se fait ici pour
# éviter le CORS et pouvoir mettre en cache.
GQL_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"  # client-id public du site twitch.tv
_emote_cache = {}   # channel -> (expiry_ts, {name: url})
_EMOTE_CACHE_TTL = 600

def _7tv_url(emote_id):
    return f"https://cdn.7tv.app/emote/{emote_id}/2x.webp"

@app.route("/chat-emotes/<channel>")
def chat_emotes(channel):
    now = time.time()
    cached = _emote_cache.get(channel)
    if cached and cached[0] > now:
        return jsonify(cached[1])

    emotes = {}
    try:
        r = requests.get("https://7tv.io/v3/emote-sets/global", timeout=5)
        for e in r.json().get("emotes", []):
            emotes[e["name"]] = _7tv_url(e["id"])
    except Exception as e:
        log("7TV", f"global set error: {e}")

    try:
        gql = requests.post(
            "https://gql.twitch.tv/gql",
            json={"query": f'query{{user(login:"{channel}"){{id}}}}'},
            headers={"Client-Id": GQL_CLIENT_ID},
            timeout=5,
        ).json()
        uid = gql["data"]["user"]["id"]
        r = requests.get(f"https://7tv.io/v3/users/twitch/{uid}", timeout=5)
        es = r.json().get("emote_set") or {}
        for e in es.get("emotes", []):
            emotes[e["name"]] = _7tv_url(e["id"])
    except Exception as e:
        log("7TV", f"channel set error for {channel}: {e}")

    _emote_cache[channel] = (now + _EMOTE_CACHE_TTL, emotes)
    return jsonify(emotes)


# ── Feed maison (SRT) ───────────────────────────────────────────────────────────
# Un PC en France (IP résidentielle) capte une chaîne via streamlink et pousse le
# flux ici en SRT. Le VPS ne fait que remuxer en HLS et rediffuser : il ne contacte
# aucune source, donc son IP datacenter (bloquée par TF1/france.tv/etc.) n'intervient
# jamais. Voir feeder.sh / feeder.bat pour la commande à lancer côté PC.

@app.route("/start-feed", methods=["GET", "POST"])
def start_feed():
    global _sl_proc, _ff_proc
    log("FEED", f"écoute SRT sur :{SRT_PORT}, attente du feeder maison…")
    _kill_all()
    _reset_hls()

    ff = subprocess.Popen(
        [
            "ffmpeg", "-y",
            # Écoute SRT : ffmpeg bloque ici jusqu'à ce que le feeder se connecte.
            "-fflags", "+genpts",
            "-i", f"srt://0.0.0.0:{SRT_PORT}?mode=listener&latency=6000",
            "-c", "copy",
            "-f", "hls",
            "-hls_time", "4",
            "-hls_list_size", "6",
            "-hls_flags", "delete_segments",
            "-hls_allow_cache", "0",
            "-hls_segment_filename", str(HLS_DIR / "seg%d.ts"),
            str(HLS_DIR / "playlist.m3u8"),
        ],
        stderr=subprocess.PIPE,
    )
    threading.Thread(target=_drain_stderr, args=(ff.stderr, "ffmpeg-feed"), daemon=True).start()

    with _lock:
        _sl_proc = None
        _ff_proc = ff

    return jsonify({"ok": True, "playlist": "/hls/playlist.m3u8", "mode": "feed", "srt_port": SRT_PORT})


# ── IPTV channels list ─────────────────────────────────────────────────────────

def _parse_m3u_attr(line, attr):
    try:
        return line.split(f'{attr}="')[1].split('"')[0]
    except IndexError:
        return ""


M3U_SOURCES = [
    ("FR", "https://iptv-org.github.io/iptv/countries/fr.m3u"),
    ("DE", "https://iptv-org.github.io/iptv/countries/de.m3u"),
    ("AT", "https://iptv-org.github.io/iptv/countries/at.m3u"),
]


def _parse_m3u(text: str, groups: dict):
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("#EXTINF"):
            name   = line.split(",", 1)[-1].strip() if "," in line else "Unknown"
            logo   = _parse_m3u_attr(line, "tvg-logo")
            tvg_id = _parse_m3u_attr(line, "tvg-id") or name
            i += 1
            while i < len(lines) and not lines[i].strip():
                i += 1
            if i < len(lines) and lines[i].strip() and not lines[i].startswith("#"):
                url = lines[i].strip()
                if url.lower().endswith(".mpd"):
                    i += 1
                    continue
                if tvg_id not in groups:
                    groups[tvg_id] = {"name": name, "logo": logo, "tvg_id": tvg_id, "sources": []}
                if url not in groups[tvg_id]["sources"]:
                    groups[tvg_id]["sources"].append(url)
        i += 1


@app.route("/iptv-channels")
def iptv_channels():
    import requests as req
    now = time.time()
    if now - _iptv_cache["ts"] >= IPTV_CACHE_TTL or not _iptv_cache["channels"]:
        groups: dict[str, dict] = {}
        for country, url in M3U_SOURCES:
            try:
                r = req.get(url, timeout=15)
                r.raise_for_status()
                _parse_m3u(r.text, groups)
                log("IPTV-LIST", f"{country}: {len(groups)} chaînes total après merge")
            except Exception as e:
                log("IPTV-LIST", f"ERREUR {country}: {e}")
        chs = list(groups.values())
        _iptv_cache["channels"] = chs
        _iptv_cache["ts"] = now
        log("IPTV-LIST", f"Total: {len(chs)} chaînes (FR+DE+AT)")

    chs = _iptv_cache["channels"]
    if now - _iptv_quality_cache["ts"] > IPTV_QUALITY_TTL:
        _refresh_iptv_quality(chs)

    bad = _iptv_quality_cache["bad_ids"]
    return jsonify([c for c in chs if c["tvg_id"] not in bad])


# ── IPTV start ────────────────────────────────────────────────────────────────

def _speed_test_url(url):
    """Retourne (url, dl_kbps, video_kbps) en téléchargeant le premier segment."""
    import requests as req
    try:
        if not url.startswith(("http://", "https://")):
            return (url, 0, 0)
        hdrs = {"User-Agent": IPTV_UA}
        # Si c'est un m3u8, récupérer le 1er segment
        seg_url = url
        extinf  = 0.0
        if url.split("?")[0].lower().endswith(".m3u8"):
            r0 = req.get(url, timeout=6, headers=hdrs)
            if r0.status_code != 200:
                return (url, 0, 0)
            for line in r0.text.splitlines():
                s = line.strip()
                if s.startswith("#EXTINF"):
                    try: extinf = float(s.split(":")[1].split(",")[0])
                    except: pass
                if s and not s.startswith("#"):
                    seg_url = urllib.parse.urljoin(url, s)
                    break
        t0 = time.time()
        r = req.get(seg_url, stream=True, timeout=8, headers=hdrs)
        if r.status_code != 200:
            return (url, 0, 0)
        cl        = int(r.headers.get("content-length", 0))
        downloaded = 0
        for chunk in r.iter_content(32768):
            downloaded += len(chunk)
            if downloaded >= 200_000:
                break
        r.close()
        dt       = max(time.time() - t0, 0.001)
        dl_kbps  = downloaded * 8 / (dt * 1000)
        vid_kbps = (cl * 8 / (extinf * 1000)) if cl and extinf > 0 else 0
        return (url, dl_kbps, vid_kbps)
    except Exception as e:
        return (url, 0, 0)


def _refresh_iptv_quality(chs):
    """Teste le débit de toutes les chaînes IPTV en arrière-plan et met à jour
    le blocklist des chaînes mortes / trop lentes pour être regardables."""
    if _iptv_quality_cache["refreshing"]:
        return
    _iptv_quality_cache["refreshing"] = True

    def _run():
        bad = set()
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
                futs = {ex.submit(_speed_test_url, ch["sources"][0]): ch for ch in chs}
                for f in concurrent.futures.as_completed(futs, timeout=300):
                    ch = futs[f]
                    try:
                        _, dl_kbps, vid_kbps = f.result()
                    except Exception:
                        dl_kbps, vid_kbps = 0, 0
                    ratio = (dl_kbps / vid_kbps) if vid_kbps else None
                    is_bad = (ratio is not None and ratio < 0.85) or (ratio is None and dl_kbps < 500)
                    if is_bad:
                        bad.add(ch["tvg_id"])
            _iptv_quality_cache["bad_ids"] = bad
            _iptv_quality_cache["ts"] = time.time()
            try:
                IPTV_BLOCKLIST_FILE.write_text(
                    json.dumps({"bad_ids": sorted(bad), "ts": _iptv_quality_cache["ts"]}, ensure_ascii=False),
                    encoding="utf-8",
                )
            except Exception:
                pass
            log("IPTV-QUALITY", f"{len(bad)}/{len(chs)} chaînes filtrées (débit insuffisant)")
        finally:
            _iptv_quality_cache["refreshing"] = False

    threading.Thread(target=_run, daemon=True).start()


def _best_source(sources):
    """Teste toutes les sources en parallèle, retourne la plus rapide."""
    if len(sources) == 1:
        return sources[0], 0, 0
    log("SPEED-TEST", f"test de {len(sources)} sources en parallele...")
    best_url, best_dl, best_vid = sources[0], 0, 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(sources)) as ex:
        futs = {ex.submit(_speed_test_url, u): u for u in sources}
        for f in concurrent.futures.as_completed(futs, timeout=12):
            try:
                url, dl_kbps, vid_kbps = f.result()
                ratio = dl_kbps / max(vid_kbps, 1) if vid_kbps else 0
                log("SPEED-TEST", f"  {url[-50:]}  dl={dl_kbps:.0f} vid={vid_kbps:.0f} ratio={ratio:.2f}")
                if dl_kbps > best_dl:
                    best_dl, best_url, best_vid = dl_kbps, url, vid_kbps
            except Exception as e:
                log("SPEED-TEST", f"  erreur: {e}")
    log("SPEED-TEST", f"meilleure: {best_url[-60:]}  dl={best_dl:.0f} kbps")
    return best_url, best_dl, best_vid


@app.route("/start-iptv", methods=["GET", "POST"])
def start_iptv():
    global _ff_proc, _sl_proc

    # Accepte GET (url=) ou POST JSON (sources=[...])
    if freq.is_json:
        sources = freq.get_json().get("sources", [])
        if not sources:
            return jsonify({"ok": False, "error": "no sources"})
    else:
        u = freq.args.get("url", "").strip()
        sources = [u] if u else []

    valid = [u for u in sources if u.startswith(("http://", "https://", "rtmp://", "rtmps://"))]
    if not valid:
        return jsonify({"ok": False, "error": "invalid url"})

    # Choisir la meilleure source
    stream_url, dl_kbps, vid_kbps = _best_source(valid)
    ratio   = dl_kbps / max(vid_kbps, 1) if vid_kbps else None
    parsed  = urllib.parse.urlparse(stream_url)
    is_hls  = parsed.path.lower().endswith(".m3u8")

    log("IPTV-START", f"url={stream_url}  is_hls={is_hls}  dl={dl_kbps:.0f} vid={vid_kbps:.0f}")

    # Tout passe par ffmpeg (HLS et RTMP) — un proxy Flask en temps réel causait des
    # rebufferings (double-hop par segment). ffmpeg pré-télécharge et écrit les
    # segments sur disque ; le browser les lit localement.
    _kill_all()
    _reset_hls()
    log("IPTV-START", f"-> mode FFMPEG (is_hls={is_hls})")

    ff = subprocess.Popen(
        [
            "ffmpeg", "-y",
            # User-Agent navigateur : beaucoup de relais IPTV (ex: France 2 sur
            # 69.64.57.208) rejettent l'UA par défaut de ffmpeg et renvoient une
            # playlist vide -> boucle "End of file" sans jamais écrire de segment.
            "-user_agent", IPTV_UA,
            "-reconnect", "1",
            "-reconnect_at_eof", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
            # Corrige les timestamps incohérents de certains flux (évite les
            # "Invalid timestamps" et le décalage son/image).
            "-fflags", "+genpts+igndts",
            "-i", stream_url,
            "-c", "copy",
            "-f", "hls",
            "-hls_time", "4",
            "-hls_list_size", "6",
            "-hls_flags", "delete_segments",
            "-hls_allow_cache", "0",
            "-hls_segment_filename", str(HLS_DIR / "seg%d.ts"),
            str(HLS_DIR / "playlist.m3u8"),
        ],
        stderr=subprocess.PIPE,
    )
    threading.Thread(target=_drain_stderr, args=(ff.stderr, "ffmpeg-iptv"), daemon=True).start()

    with _lock:
        _sl_proc = None
        _ff_proc = ff

    return jsonify({
        "ok": True, "playlist": "/hls/playlist.m3u8", "mode": "ffmpeg",
        "dl_kbps": round(dl_kbps), "vid_kbps": round(vid_kbps),
        "ratio": round(ratio, 2) if ratio else None,
    })


# ── Debug endpoint ────────────────────────────────────────────────────────────

@app.route("/find-sources")
def find_sources():
    import requests as req
    tvg_id = freq.args.get("tvg_id", "").strip()
    name   = freq.args.get("name", "").strip()
    if not tvg_id and not name:
        return jsonify({"error": "tvg_id ou name requis"}), 400

    now = time.time()
    if _streams_cache["streams"] is None or now - _streams_cache["ts"] > STREAMS_CACHE_TTL:
        log("FIND-SOURCES", "telechargement streams.json...")
        try:
            r = req.get("https://iptv-org.github.io/api/streams.json", timeout=25)
            r.raise_for_status()
            _streams_cache["streams"] = r.json()
            _streams_cache["ts"] = now
            log("FIND-SOURCES", f"{len(_streams_cache['streams'])} streams en cache")
        except Exception as e:
            log("FIND-SOURCES", f"ERREUR streams.json: {e}")
            return jsonify({"error": str(e), "sources": []}), 502

    streams = _streams_cache["streams"]

    def _fetch_sources(tid):
        return [s["url"] for s in streams
                if s.get("channel") == tid
                and s.get("url", "").startswith("http")
                and s.get("status") != "offline"]

    # 1) Recherche par tvg_id exact
    if tvg_id:
        sources = _fetch_sources(tvg_id)
        if sources:
            log("FIND-SOURCES", f"tvg_id={tvg_id!r} -> {len(sources)} sources")
            return jsonify({"tvg_id": tvg_id, "sources": sources, "count": len(sources), "method": "tvg_id"})
        log("FIND-SOURCES", f"tvg_id={tvg_id!r} -> 0 résultat, fallback par nom")

    # 2) Fallback : recherche par nom dans channels.json
    query = name or tvg_id
    if _channels_cache["channels"] is None or now - _channels_cache["ts"] > CHANNELS_CACHE_TTL:
        log("FIND-SOURCES", "telechargement channels.json...")
        try:
            r = req.get("https://iptv-org.github.io/api/channels.json", timeout=20)
            r.raise_for_status()
            _channels_cache["channels"] = r.json()
            _channels_cache["ts"] = now
            log("FIND-SOURCES", f"{len(_channels_cache['channels'])} channels en cache")
        except Exception as e:
            log("FIND-SOURCES", f"ERREUR channels.json: {e}")
            return jsonify({"tvg_id": tvg_id, "sources": [], "count": 0, "method": "tvg_id"})

    best_id, best_score = None, 0.0
    for ch in _channels_cache["channels"]:
        score = _name_score(query, ch.get("name", ""))
        if score > best_score:
            best_score, best_id = score, ch["id"]

    if best_id and best_score >= 0.85:
        sources = _fetch_sources(best_id)
        log("FIND-SOURCES", f"nom={query!r} -> {best_id!r} (score={best_score:.2f}) -> {len(sources)} sources")
        return jsonify({"tvg_id": best_id, "sources": sources, "count": len(sources),
                        "method": "name", "score": round(best_score, 2)})

    log("FIND-SOURCES", f"nom={query!r} -> aucune correspondance (meilleur score={best_score:.2f})")
    return jsonify({"tvg_id": tvg_id, "sources": [], "count": 0, "method": "none"})


@app.route("/debug-info")
def debug_info():
    with _lock:
        sl_alive = _sl_proc is not None and _sl_proc.poll() is None
        ff_alive = _ff_proc is not None and _ff_proc.poll() is None
        ff_code  = _ff_proc.poll() if _ff_proc else None
    hls_files = sorted(HLS_DIR.glob("*")) if HLS_DIR.exists() else []
    return jsonify({
        "streamlink_alive": sl_alive,
        "ffmpeg_alive":     ff_alive,
        "ffmpeg_exit_code": ff_code,
        "hls_files":        [f.name for f in hls_files],
    })


if __name__ == "__main__":
    HLS_DIR.mkdir(exist_ok=True)
    app.run(host="0.0.0.0", port=5000, threaded=True, use_reloader=False)
