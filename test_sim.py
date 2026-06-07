# -*- coding: utf-8 -*-
"""
Simulation navigateur : teste tous les endpoints comme HLS.js + le browser le feraient.
"""
import sys, io, requests, time, re

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE    = "http://localhost:5000"
TIMEOUT = 15
PASS, FAIL = [], []

def ok(name):
    PASS.append(name)
    print(f"  [OK]   {name}")

def fail(name, reason):
    FAIL.append(name)
    print(f"  [FAIL] {name}: {reason}")

def check(name, fn):
    try:
        fn()
        ok(name)
    except AssertionError as e:
        fail(name, str(e) or "assertion failed")
    except Exception as e:
        fail(name, f"{type(e).__name__}: {e}")

# ── 1. Page principale ───────────────────────────────────────────────────────
print("\n[1] Page principale")

def t_index_loads():
    r = requests.get(f"{BASE}/", timeout=TIMEOUT)
    assert r.status_code == 200, f"HTTP {r.status_code}"
    assert "TwitchOSS" in r.text
check("GET / -> 200 + titre TwitchOSS", t_index_loads)

def t_html_tabs():
    r = requests.get(f"{BASE}/").text
    assert 'id="tab-twitch"'   in r, "tab-twitch absent"
    assert 'id="tab-iptv"'     in r, "tab-iptv absent"
    assert 'id="search-input"' in r, "search-input absent"
    assert 'id="twitch-list"'  in r, "twitch-list absent"
    assert 'id="iptv-list"'    in r, "iptv-list absent"
    assert 'id="debug-panel"'  in r, "debug-panel absent"
    assert 'id="no-chat"'      in r, "no-chat absent"
check("Structure HTML (onglets, search, debug)", t_html_tabs)

def t_js_functions():
    r = requests.get(f"{BASE}/").text
    fns = [
        "switchTab", "onSearch", "renderTwitchList", "renderIptvList",
        "toggleFavTwitch", "toggleFavIptv", "selectChannel", "selectIptvChannel",
        "startHls", "waitForPlaylist", "dbgCopy", "dbgFetchM3u8", "dbgToggle",
        "setChatMode", "loadIptvChannels",
    ]
    for fn in fns:
        assert fn in r, f"fonction JS '{fn}' absente"
check("Toutes les fonctions JS presentes", t_js_functions)

def t_js_dbg_selectable():
    r = requests.get(f"{BASE}/").text
    assert "pointer-events: auto" in r, "debug panel non-selectable (pointer-events)"
    assert "user-select: text"    in r, "debug panel non-selectable (user-select)"
    assert "dbgCopy"              in r, "bouton copy absent"
check("Debug panel selectionnable + bouton copy", t_js_dbg_selectable)

# ── 2. Endpoints API ─────────────────────────────────────────────────────────
print("\n[2] Endpoints API")

def t_channels():
    r = requests.get(f"{BASE}/channels", timeout=TIMEOUT)
    assert r.status_code == 200
    d = r.json()
    assert isinstance(d, list), "pas une liste"
    print(f"         -> {len(d)} chaines Twitch: {d[:3]}")
check("GET /channels -> liste", t_channels)

def t_channel_info():
    r = requests.get(f"{BASE}/channel-info", timeout=TIMEOUT)
    assert r.status_code == 200
    d = r.json()
    assert isinstance(d, dict), "pas un dict"
    print(f"         -> {len(d)} entrees retournees")
check("GET /channel-info -> dict", t_channel_info)

def t_iptv_channels():
    r = requests.get(f"{BASE}/iptv-channels", timeout=30)
    assert r.status_code == 200
    d = r.json()
    assert isinstance(d, list) and len(d) > 0, "liste vide"
    ch = d[0]
    assert "name" in ch,    "champ 'name' manquant"
    assert "logo" in ch,    "champ 'logo' manquant"
    # Nouvelle structure : sources[] au lieu de url
    has_sources = "sources" in ch and isinstance(ch["sources"], list)
    has_url     = "url" in ch  # ancienne structure (serveur pas encore redemarré)
    assert has_sources or has_url, "ni 'sources' ni 'url' present"
    multi = sum(1 for c in d if len(c.get("sources", [])) > 1)
    ex_src = ch.get("sources", [ch.get("url", "?")])
    print(f"         -> {len(d)} chaines IPTV ({multi} multi-sources), ex: {ch['name']} [{len(ex_src)} source(s)]")
check("GET /iptv-channels -> liste non-vide", t_iptv_channels)

def t_debug_info():
    r = requests.get(f"{BASE}/debug-info", timeout=TIMEOUT)
    assert r.status_code == 200
    d = r.json()
    for k in ["streamlink_alive", "ffmpeg_alive", "ffmpeg_exit_code", "proxy_url", "hls_files"]:
        assert k in d, f"champ '{k}' manquant"
    print(f"         -> {d}")
check("GET /debug-info -> etat serveur", t_debug_info)

# ── 3. Securite ──────────────────────────────────────────────────────────────
print("\n[3] Securite / validation")

def t_invalid_url():
    for bad in ["file:///etc/passwd", "javascript:alert(1)", "ftp://evil", ""]:
        r = requests.get(f"{BASE}/start-iptv?url={requests.utils.quote(bad)}", timeout=TIMEOUT)
        assert r.status_code == 200
        assert r.json().get("ok") == False, f"URL '{bad}' non rejetee"
check("URLs invalides rejetees par /start-iptv", t_invalid_url)

def t_seg_bad_url():
    for bad in ["file:///etc/passwd", "ftp://x"]:
        r = requests.get(f"{BASE}/iptv-proxy/seg?url={requests.utils.quote(bad)}", timeout=TIMEOUT)
        assert r.status_code in (400, 404), f"URL '{bad}' -> HTTP {r.status_code} (attendu 400/404)"
check("URLs invalides rejetees par /iptv-proxy/seg", t_seg_bad_url)

# ── 4. Simulation flux IPTV (mode proxy) ─────────────────────────────────────
print("\n[4] Simulation HLS.js - flux IPTV proxy")

TEST_URL = "http://69.64.57.208/france2/mono.m3u8"

def t_start_iptv_get():
    r = requests.get(f"{BASE}/start-iptv?url={requests.utils.quote(TEST_URL)}", timeout=TIMEOUT)
    assert r.status_code == 200
    d = r.json()
    assert d.get("ok")       == True,                        f"ok=False"
    assert d.get("mode")     == "proxy",                     f"mode={d.get('mode')} (attendu proxy)"
    assert d.get("playlist") == "/iptv-proxy/playlist.m3u8", f"playlist={d.get('playlist')}"
    print(f"         -> GET: mode={d['mode']} dl={d.get('dl_kbps')} vid={d.get('vid_kbps')} ratio={d.get('ratio')}")
check("start-iptv GET -> mode proxy pour .m3u8", t_start_iptv_get)

def t_start_iptv_post_multi():
    # POST avec plusieurs sources (dont une invalide pour tester le filtrage)
    payload = {"sources": [TEST_URL, "http://invalid.invalid/test.m3u8"]}
    r = requests.post(f"{BASE}/start-iptv", json=payload, timeout=30)
    assert r.status_code == 200
    d = r.json()
    assert d.get("ok")   == True,    f"ok=False"
    assert d.get("mode") == "proxy", f"mode={d.get('mode')}"
    assert "dl_kbps"  in d,          "dl_kbps absent de la reponse"
    assert "vid_kbps" in d,          "vid_kbps absent"
    assert "ratio"    in d,          "ratio absent"
    r_val = d.get("ratio")
    if r_val and r_val < 0.85:
        print(f"         -> ratio={r_val} (serveur lent - avertissement UI attendu)")
    elif r_val:
        print(f"         -> ratio={r_val} (debit ok)")
    print(f"         -> POST multi-sources: dl={d['dl_kbps']} vid={d['vid_kbps']} ratio={d['ratio']}")
check("start-iptv POST multi-sources -> speed-test + meilleure source", t_start_iptv_post_multi)

def t_start_iptv_post_all_invalid():
    payload = {"sources": ["file:///etc/passwd", "ftp://x"]}
    r = requests.post(f"{BASE}/start-iptv", json=payload, timeout=TIMEOUT)
    assert r.status_code == 200
    assert r.json().get("ok") == False, "sources invalides non rejetees"
check("start-iptv POST toutes invalides -> ok=False", t_start_iptv_post_all_invalid)

raw_m3u8 = None
seg_urls = []

def t_proxy_playlist():
    global raw_m3u8, seg_urls
    r = requests.get(f"{BASE}/iptv-proxy/playlist.m3u8", timeout=TIMEOUT)
    assert r.status_code == 200, f"HTTP {r.status_code}"
    ct = r.headers.get("Content-Type", "")
    assert "mpegurl" in ct.lower() or "m3u" in ct.lower(), f"Content-Type inattendu: {ct}"
    raw_m3u8 = r.text
    seg_urls = [l.strip() for l in raw_m3u8.splitlines()
                if l.strip() and not l.startswith("#")]
    assert len(seg_urls) > 0, "aucun segment dans la playlist"
    for u in seg_urls:
        assert u.startswith("/iptv-proxy/seg?url="), f"URL non reecrite: {u}"
    durations = re.findall(r"#EXTINF:([\d.]+)", raw_m3u8)
    print(f"         -> {len(seg_urls)} segments, durees EXTINF: {durations}")
    print(f"\n         --- M3U8 brut ---")
    for line in raw_m3u8.splitlines():
        print(f"         {line}")
    print(f"         --- fin M3U8 ---\n")
check("GET /iptv-proxy/playlist.m3u8 -> m3u8 avec URLs reecrites", t_proxy_playlist)

def t_proxy_seg_streaming():
    if not seg_urls:
        raise AssertionError("aucun segment disponible (playlist vide)")
    seg = seg_urls[0]
    t0 = time.time()
    r = requests.get(f"{BASE}{seg}", stream=True, timeout=20)
    assert r.status_code == 200, f"HTTP {r.status_code}"
    first_chunk = next(r.iter_content(4096), None)
    dt_first = time.time() - t0
    assert first_chunk and len(first_chunk) > 0, "premier chunk vide"
    cl = r.headers.get("Content-Length", "?")
    ct = r.headers.get("Content-Type", "?")
    print(f"         -> premiers 4KB recus en {dt_first*1000:.0f}ms")
    print(f"         -> Content-Length={cl}  Content-Type={ct}")
    assert dt_first < 8.0, f"premier chunk trop lent: {dt_first:.1f}s"
    r.close()
check("Premier chunk segment recu rapidement (streaming)", t_proxy_seg_streaming)

def t_seg_speed():
    if not seg_urls:
        raise AssertionError("aucun segment disponible")
    seg = seg_urls[0]
    t0 = time.time()
    r = requests.get(f"{BASE}{seg}", timeout=60)
    dt = time.time() - t0
    size_kb = len(r.content) // 1024
    speed_kbps = size_kb * 8 / max(dt, 0.001)
    print(f"         -> {size_kb} KB en {dt:.1f}s = {speed_kbps:.0f} kbps")
    # Recuperer les durees pour calculer le bitrate source
    durations = re.findall(r"#EXTINF:([\d.]+)", raw_m3u8 or "")
    if durations:
        seg_duration = float(durations[0])
        video_kbps = size_kb * 8 / max(seg_duration, 1)
        ratio = speed_kbps / max(video_kbps, 1)
        print(f"         -> duree segment: {seg_duration}s  bitrate video: {video_kbps:.0f} kbps")
        print(f"         -> ratio dl/bitrate: {ratio:.2f}x ({'OK: buffer peut se remplir' if ratio >= 1 else 'PROBLEME: trop lent pour la lecture'})")
        assert ratio > 0, "impossible de calculer"
check("Debit segment vs bitrate video", t_seg_speed)

# ── 5. Robustesse ────────────────────────────────────────────────────────────
print("\n[5] Robustesse")

def t_double_start():
    requests.get(f"{BASE}/start-iptv?url={requests.utils.quote(TEST_URL)}", timeout=TIMEOUT)
    time.sleep(0.2)
    r = requests.get(f"{BASE}/start-iptv?url={requests.utils.quote(TEST_URL)}", timeout=TIMEOUT)
    assert r.status_code == 200 and r.json().get("ok")
check("Double start-iptv sans crash", t_double_start)

def t_server_alive():
    r = requests.get(f"{BASE}/", timeout=5)
    assert r.status_code == 200
check("Serveur toujours vivant apres les tests", t_server_alive)

# ── Bilan ────────────────────────────────────────────────────────────────────
print(f"\n{'='*52}")
print(f"  [OK]   {len(PASS)} passes  |  [FAIL] {len(FAIL)} echoues")
if FAIL:
    print(f"\n  Echoues:")
    for f in FAIL:
        print(f"    - {f}")
print()
sys.exit(0 if not FAIL else 1)
