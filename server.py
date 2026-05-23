from flask import Flask, jsonify, send_file, send_from_directory
import subprocess, threading, os, atexit, shutil
from pathlib import Path

app = Flask(__name__)

STREAMLINK = "/home/ubuntu/twitchoss-env/bin/streamlink"
BASE_DIR   = Path(os.path.abspath(__file__)).parent
CHANNELS_FILE = BASE_DIR / "channels.txt"
HLS_DIR    = BASE_DIR / "hls"

_lock    = threading.Lock()
_sl_proc = None
_ff_proc = None


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
    """Thread: lit depuis src et écrit dans dst (pipe manuel entre deux processus)."""
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


def read_channels():
    try:
        return [l.strip() for l in CHANNELS_FILE.read_text(encoding="utf-8").splitlines()
                if l.strip() and not l.startswith("#")]
    except FileNotFoundError:
        return []


@app.route("/")
def index():
    return send_file(str(BASE_DIR / "index.html"))


@app.route("/channels")
def channels():
    return jsonify(read_channels())


@app.route("/channel-info")
def channel_info():
    import requests as req
    channels = read_channels()
    if not channels:
        return jsonify({})
    query = {
        "query": "query($logins:[String!]){users(logins:$logins){login profileImageURL(width:70) stream{id}}}",
        "variables": {"logins": channels},
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
        return jsonify({ch: {"live": False, "avatar": ""} for ch in channels})


@app.route("/hls/<path:filename>")
def hls_file(filename):
    resp = send_from_directory(str(HLS_DIR), filename)
    resp.headers["Cache-Control"] = "no-cache, no-store"
    return resp


@app.route("/start/<channel>")
def start(channel):
    global _sl_proc, _ff_proc

    _kill_all()
    _reset_hls()

    sl = subprocess.Popen(
        [STREAMLINK, "--stdout", f"twitch.tv/{channel}", "1080p60,720p60,best"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

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
        stderr=subprocess.DEVNULL,
    )

    with _lock:
        _sl_proc = sl
        _ff_proc = ff

    threading.Thread(target=_pipe, args=(sl.stdout, ff.stdin), daemon=True).start()

    return jsonify({"ok": True})


if __name__ == "__main__":
    HLS_DIR.mkdir(exist_ok=True)
    app.run(host="0.0.0.0", port=5000, threaded=True, use_reloader=False)
