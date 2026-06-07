from flask import Flask, jsonify, send_file, send_from_directory, request as freq, make_response, Response
import subprocess, threading, os, atexit, shutil, time, urllib.parse, traceback, concurrent.futures
from pathlib import Path

app = Flask(__name__)

STREAMLINK = "/home/ubuntu/twitchoss-env/bin/streamlink"
BASE_DIR   = Path(os.path.abspath(__file__)).parent
CHANNELS_FILE = BASE_DIR / "channels.txt"
HLS_DIR    = BASE_DIR / "hls"

_lock    = threading.Lock()
_sl_proc = None
_ff_proc = None

_iptv_cache: dict   = {"channels": [], "ts": 0.0}
_streams_cache: dict = {"streams": None, "ts": 0.0}
IPTV_CACHE_TTL    = 3600
STREAMS_CACHE_TTL = 7200
_iptv_proxy_url: str | None = None


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


@app.route("/channel-info")
def channel_info():
    import requests as req
    chs = read_channels()
    if not chs:
        return jsonify({})
    query = {
        "query": "query($logins:[String!]){users(logins:$logins){login profileImageURL(width:70) stream{id}}}",
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
                "live":   u["stream"] is not None,
                "avatar": u.get("profileImageURL") or "",
            }
            for u in users
        })
    except Exception:
        return jsonify({ch: {"live": False, "avatar": ""} for ch in chs})


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
        [STREAMLINK, "--stdout", f"twitch.tv/{channel}", "1080p60,720p60,best"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    threading.Thread(target=_drain_stderr, args=(sl.stderr, "streamlink"), daemon=True).start()

    ff = subprocess.Popen(
        [
            "ffmpeg", "-y",
            "-i", "pipe:0",
            "-c", "copy",
            "-f", "hls",
            "-hls_time", "2",
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


# ── IPTV channels list ─────────────────────────────────────────────────────────

def _parse_m3u_attr(line, attr):
    try:
        return line.split(f'{attr}="')[1].split('"')[0]
    except IndexError:
        return ""


@app.route("/iptv-channels")
def iptv_channels():
    import requests as req
    now = time.time()
    if now - _iptv_cache["ts"] < IPTV_CACHE_TTL and _iptv_cache["channels"]:
        return jsonify(_iptv_cache["channels"])
    try:
        r = req.get("https://iptv-org.github.io/iptv/countries/fr.m3u", timeout=15)
        r.raise_for_status()
        lines = r.text.splitlines()
        # Groupe par tvg-id (ou nom si tvg-id absent)
        groups: dict[str, dict] = {}
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
                    if tvg_id not in groups:
                        groups[tvg_id] = {"name": name, "logo": logo, "tvg_id": tvg_id, "sources": []}
                    groups[tvg_id]["sources"].append(url)
            i += 1
        chs = list(groups.values())
        _iptv_cache["channels"] = chs
        _iptv_cache["ts"] = now
        multi = sum(1 for c in chs if len(c["sources"]) > 1)
        log("IPTV-LIST", f"{len(chs)} chaines ({multi} avec plusieurs sources)")
        return jsonify(chs)
    except Exception as e:
        log("IPTV-LIST", f"ERREUR: {e}")
        return jsonify(_iptv_cache["channels"])


# ── IPTV start ────────────────────────────────────────────────────────────────

def _speed_test_url(url):
    """Retourne (url, dl_kbps, video_kbps) en téléchargeant le premier segment."""
    import requests as req
    try:
        if not url.startswith(("http://", "https://")):
            return (url, 0, 0)
        hdrs = {"User-Agent": "VLC/3.0.0"}
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
    global _ff_proc, _sl_proc, _iptv_proxy_url

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

    if is_hls:
        _kill_all()
        _iptv_proxy_url = stream_url
        log("IPTV-START", "-> mode PROXY")
        return jsonify({
            "ok": True, "playlist": "/iptv-proxy/playlist.m3u8", "mode": "proxy",
            "dl_kbps": round(dl_kbps), "vid_kbps": round(vid_kbps),
            "ratio": round(ratio, 2) if ratio else None,
        })

    # Non-HLS (RTMP…) → ffmpeg
    _iptv_proxy_url = None
    _kill_all()
    _reset_hls()
    log("IPTV-START", "-> mode FFMPEG")

    ff = subprocess.Popen(
        [
            "ffmpeg", "-y",
            "-reconnect", "1",
            "-reconnect_at_eof", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
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


# ── IPTV proxy ────────────────────────────────────────────────────────────────

@app.route("/iptv-proxy/playlist.m3u8")
def iptv_proxy_playlist():
    import requests as req
    t0 = time.time()
    url = freq.args.get("url", _iptv_proxy_url or "").strip()
    if not url:
        return "No stream", 404

    log("PROXY-M3U8", f"fetch {url}")
    try:
        r = req.get(url, timeout=10, headers={"User-Agent": "VLC/3.0.0"})
        r.raise_for_status()
        dt = time.time() - t0
        log("PROXY-M3U8", f"OK {r.status_code} en {dt*1000:.0f}ms  {len(r.text)} chars")

        lines = []
        seg_count = 0
        for line in r.text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                abs_url = urllib.parse.urljoin(url, stripped)
                if abs_url.split("?")[0].lower().endswith(".m3u8"):
                    line = f"/iptv-proxy/playlist.m3u8?url={urllib.parse.quote(abs_url, safe='')}"
                else:
                    line = f"/iptv-proxy/seg?url={urllib.parse.quote(abs_url, safe='')}"
                    seg_count += 1
            lines.append(line)

        log("PROXY-M3U8", f"{seg_count} segments réécrits")
        resp = make_response("\n".join(lines))
        resp.headers["Content-Type"] = "application/x-mpegURL"
        resp.headers["Cache-Control"] = "no-cache, no-store"
        return resp

    except Exception as e:
        log("PROXY-M3U8", f"ERREUR: {e}")
        traceback.print_exc()
        return str(e), 502


@app.route("/iptv-proxy/seg")
def iptv_proxy_seg():
    import requests as req
    t0 = time.time()
    seg_url = freq.args.get("url", "").strip()
    if not seg_url.startswith(("http://", "https://")):
        return "Bad URL", 400

    short = seg_url.split("/")[-1][:40]
    log("PROXY-SEG", f"fetch {short}")
    try:
        r = req.get(seg_url, stream=True, timeout=30, headers={"User-Agent": "VLC/3.0.0"})
        r.raise_for_status()
        ct      = r.headers.get("content-type", "video/MP2T")
        cl      = r.headers.get("content-length")
        log("PROXY-SEG", f"→ connexion OK {cl or '?'}B prévus, streaming…")

        def generate():
            total = 0
            for chunk in r.iter_content(32768):
                total += len(chunk)
                yield chunk
            dt = time.time() - t0
            log("PROXY-SEG", f"OK {total//1024}KB en {dt*1000:.0f}ms ({total*8//max(1,int(dt*1000))} kbps)")

        headers = {"Content-Type": ct, "Cache-Control": "no-cache"}
        if cl:
            headers["Content-Length"] = cl
        return Response(generate(), headers=headers)
    except Exception as e:
        log("PROXY-SEG", f"ERREUR: {e}")
        return "Error", 502


# ── Debug endpoint ────────────────────────────────────────────────────────────

@app.route("/find-sources")
def find_sources():
    import requests as req
    tvg_id = freq.args.get("tvg_id", "").strip()
    if not tvg_id:
        return jsonify({"error": "tvg_id requis"}), 400

    now = time.time()
    if _streams_cache["streams"] is None or now - _streams_cache["ts"] > STREAMS_CACHE_TTL:
        log("FIND-SOURCES", "telechargement streams.json (base globale iptv-org)...")
        try:
            r = req.get("https://iptv-org.github.io/api/streams.json", timeout=25)
            r.raise_for_status()
            _streams_cache["streams"] = r.json()
            _streams_cache["ts"] = now
            log("FIND-SOURCES", f"{len(_streams_cache['streams'])} streams en cache")
        except Exception as e:
            log("FIND-SOURCES", f"ERREUR: {e}")
            return jsonify({"error": str(e), "sources": []}), 502

    streams = _streams_cache["streams"]
    sources = [
        s["url"] for s in streams
        if s.get("channel") == tvg_id
        and s.get("url", "").startswith("http")
        and s.get("status") != "offline"
    ]
    log("FIND-SOURCES", f"tvg_id={tvg_id!r} -> {len(sources)} sources trouvees")
    return jsonify({"tvg_id": tvg_id, "sources": sources, "count": len(sources)})


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
        "proxy_url":        _iptv_proxy_url,
        "hls_files":        [f.name for f in hls_files],
    })


if __name__ == "__main__":
    HLS_DIR.mkdir(exist_ok=True)
    app.run(host="0.0.0.0", port=5000, threaded=True, use_reloader=False)
