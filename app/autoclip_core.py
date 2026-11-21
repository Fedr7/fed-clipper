"""
Fed Auto Clipper - core (no UI).
Finds R6 multi-kills in Medal's clip database and renders them in the Fed v5 style:
vertical 1080x1920, haze/purple grade, sharpened, glowing FED intro + voice, flash cuts, no music.
"""
import glob
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from datetime import datetime

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(APP_DIR, "assets")
STATE_FILE = os.path.join(APP_DIR, "processed.json")
SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")
NO_WINDOW = 0x08000000 if os.name == "nt" else 0   # hide ffmpeg console windows on Windows

KILL_EVENTS = ("Kill", "3K", "4K", "Ace")
RANK = {"Kill": 1, "3K": 3, "4K": 4, "Ace": 5}


def default_desktop():
    home = os.path.expanduser("~")
    for d in (os.path.join(home, "OneDrive", "Desktop"), os.path.join(home, "Desktop")):
        if os.path.isdir(d):
            return d
    return home


DEFAULTS = {
    "medal_dir": r"C:\Medal",
    "output_dir": os.path.join(default_desktop(), "Upload tiktok"),
    "min_kills": 3,               # 3 = 3Ks, 4Ks and Aces
    "modes": ["Ranked", "Unranked", "QuickMatch", "Quick Match", "Standard"],
    "only_new_after": None,       # ISO date; clips older than this are ignored (set on first run)
    "watch_minutes": 5,
}


def load_settings():
    s = dict(DEFAULTS)
    if os.path.exists(SETTINGS_FILE):
        try:
            s.update(json.load(open(SETTINGS_FILE, encoding="utf-8")))
        except Exception:
            pass
    return s


def save_settings(s):
    json.dump(s, open(SETTINGS_FILE, "w", encoding="utf-8"), indent=2)


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            return json.load(open(STATE_FILE, encoding="utf-8"))
        except Exception:
            pass
    return {"done": {}}


def save_state(st):
    tmp = STATE_FILE + ".tmp"
    json.dump(st, open(tmp, "w", encoding="utf-8"), indent=1)
    os.replace(tmp, STATE_FILE)


# ------------------------------------------------------------------ ffmpeg
def find_ffmpeg():
    for name in ("ffmpeg", "ffmpeg.exe"):
        p = shutil.which(name)
        if p:
            return p
    local = os.path.join(APP_DIR, "ffmpeg", "bin", "ffmpeg.exe")
    if os.path.exists(local):
        return local
    # winget puts Gyan.FFmpeg under LocalAppData\Microsoft\WinGet\Packages
    la = os.environ.get("LOCALAPPDATA", "")
    hits = glob.glob(os.path.join(la, "Microsoft", "WinGet", "Packages", "Gyan.FFmpeg*", "**", "ffmpeg.exe"), recursive=True)
    return hits[0] if hits else None


def find_tool(name):
    """ffplay/ffprobe next to ffmpeg, on PATH, or in the WinGet package."""
    ff = find_ffmpeg()
    exe = name + (".exe" if os.name == "nt" else "")
    if ff:
        p = os.path.join(os.path.dirname(ff), exe)
        if os.path.exists(p):
            return p
    w = shutil.which(name)
    if w:
        return w
    la = os.environ.get("LOCALAPPDATA", "")
    hits = glob.glob(os.path.join(la, "Microsoft", "WinGet", "Packages", "Gyan.FFmpeg*", "**", exe), recursive=True)
    return hits[0] if hits else None


def probe_duration(path):
    fp = find_tool("ffprobe")
    if not fp:
        return 0.0
    r = subprocess.run([fp, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True, creationflags=NO_WINDOW)
    try:
        return float(r.stdout.strip().splitlines()[0])
    except Exception:
        return 0.0


def audio_streams(path):
    fp = find_tool("ffprobe")
    if not fp:
        return 0
    r = subprocess.run([fp, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0",
                        path], capture_output=True, text=True, creationflags=NO_WINDOW)
    return len([l for l in r.stdout.splitlines() if l.strip()])


class Encoder:
    def __init__(self, ffmpeg):
        self.ff = ffmpeg
        self.ffprobe = os.path.join(os.path.dirname(ffmpeg), "ffprobe" + (".exe" if ffmpeg.endswith(".exe") else ""))
        if not os.path.exists(self.ffprobe):
            self.ffprobe = find_tool("ffprobe") or self.ffprobe
        out = self._run([self.ff, "-hide_banner", "-encoders"], capture=True)
        filt = self._run([self.ff, "-hide_banner", "-filters"], capture=True)
        self.has_cas = " cas " in filt
        self.nvenc = False
        if "h264_nvenc" in out:   # try the NVIDIA encoder once; fall back to CPU if it fails
            r = subprocess.run([self.ff, "-v", "error", "-f", "lavfi", "-i", "color=s=256x256:d=0.2", "-c:v", "h264_nvenc",
                                "-f", "null", "-"], capture_output=True, creationflags=NO_WINDOW)
            self.nvenc = r.returncode == 0

    def _run(self, args, capture=False):
        r = subprocess.run(args, capture_output=True, text=True, creationflags=NO_WINDOW)
        if r.returncode != 0 and not capture:
            raise RuntimeError(r.stderr.strip()[-600:])
        return (r.stdout or "") + (r.stderr or "") if capture else r

    def venc(self, quality=16):
        if self.nvenc:
            return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(quality + 3), "-b:v", "0"]
        return ["-c:v", "libx264", "-preset", "fast", "-crf", str(quality)]

    def audio_count(self, path):
        r = subprocess.run([self.ffprobe, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                            "-of", "csv=p=0", path], capture_output=True, text=True, creationflags=NO_WINDOW)
        return len([l for l in r.stdout.splitlines() if l.strip()])

    def run(self, args):
        self._run([self.ff, "-nostdin", "-v", "error", "-y"] + args)


# ------------------------------------------------------------------ Medal database
def read_clips(medal_dir):
    """Return R6 clips with kill events, newest first. Copies the DB so Medal can keep using it."""
    dbs = sorted(glob.glob(os.path.join(medal_dir, "Databases", "medal-*.db")), key=os.path.getmtime, reverse=True)
    if not dbs:
        raise RuntimeError(f"No Medal database found in {os.path.join(medal_dir, 'Databases')}")
    tmp = os.path.join(tempfile.gettempdir(), "fed_autoclip_medal.db")
    shutil.copy2(dbs[0], tmp)
    con = sqlite3.connect(tmp)
    rows = con.execute("select video_path, metadata, created_at from contents "
                       "where video_path like '%RainbowSix%'").fetchall()
    con.close()
    clips = []
    for path, meta, created in rows:
        if not isinstance(meta, (bytes, bytearray)):
            meta = (meta or "").encode("utf-8", "ignore")
        ev = sorted(set((float(t), n.decode("utf-8", "ignore")) for t, n in
                        re.findall(rb"time.([0-9]+(?:\.[0-9]+)?)Gname.(.{1,40}?)Gdata", meta, re.S)))
        m = re.search(rb"Mode.([A-Za-z ]{3,20}?)G", meta)
        mode = m.group(1).decode() if m else "?"
        title = re.findall(rb"((?:Ace|\dK|Kill)[^\x00-\x1f]{0,40}?- [A-Z][a-z]+(?: - [A-Z][a-z]+)?)", meta)
        kills = []
        for t, n in ev:
            if n in KILL_EVENTS and (not kills or t - kills[-1] > 0.15):
                kills.append(t)
        top = max([RANK[n] for t, n in ev if n in RANK] or [0])
        d = re.search(rb"clipDuration.([0-9]+(?:\.[0-9]+)?)", meta)
        dur = float(d.group(1)) if d else None
        maps = re.findall(rb"- ([A-Z][A-Za-z ]{2,20})(?:\x97|$)", meta)
        clips.append({"dur": dur, "path": path, "id": os.path.splitext(re.split(r"[\\/]", path)[-1])[0], "created": created,
                      "mode": mode, "kills": kills, "top": top,
                      "title": title[0].decode("utf-8", "ignore") if title else ""})
    clips.sort(key=lambda c: c["id"], reverse=True)
    return clips


def clip_time(c):
    s = re.search(r"(\d{14})", c["id"])
    return datetime.strptime(s.group(1), "%Y%m%d%H%M%S") if s else datetime.min


def pick_new_highlights(clips, settings, state):
    """Clips worth a solo video: enough kills, allowed mode, not done, not a duplicate save of the same play."""
    after = settings.get("only_new_after")
    after_dt = datetime.fromisoformat(after) if after else None
    chosen = []
    for c in sorted(clips, key=clip_time):
        if c["mode"] not in settings["modes"] or c["top"] < settings["min_kills"] or len(c["kills"]) < 2:
            continue
        if after_dt and clip_time(c) < after_dt:
            continue
        if c["id"] in state["done"]:
            continue
        # Medal often saves the same play twice a few seconds apart: keep only the first
        if any(abs((clip_time(c) - clip_time(o)).total_seconds()) < 150 and o["top"] == c["top"]
               for o in chosen) or any(abs((clip_time(c) - datetime.fromisoformat(v["clip_time"])).total_seconds()) < 150
                                       and v.get("top") == c["top"] for v in state["done"].values() if v.get("clip_time")):
            state["done"][c["id"]] = {"skipped": "duplicate", "clip_time": clip_time(c).isoformat(), "top": c["top"]}
            continue
        chosen.append(c)
    return chosen


def fastest_bursts(clips, settings, max_gap=3.0):
    """Every multi-kill burst (kills <= max_gap apart), fastest first, duplicates removed."""
    out = []
    for c in clips:
        if c["mode"] not in settings["modes"]:
            continue
        k = c["kills"]
        i = 0
        while i < len(k):
            j = i
            while j + 1 < len(k) and k[j + 1] - k[j] <= max_gap:
                j += 1
            if j > i:
                out.append({"clip": c, "a": k[i], "b": k[j], "n": j - i + 1, "span": k[j] - k[i]})
            i = j + 1
    out.sort(key=lambda r: (-r["n"], r["span"] / (r["n"] - 1)))
    keep = []
    for r in out:
        if any(abs((clip_time(r["clip"]) - clip_time(k["clip"])).total_seconds()) < 150 for k in keep):
            continue
        keep.append(r)
    return keep


# ------------------------------------------------------------------ rendering
NAME_BAD = re.compile(r'[<>:"/\\|?*]')


DEFAULT_STYLE = {
    "intro": True,          # glowing FED title at the start
    "voice": True,          # "Fed" voiceover
    "transition": "Flash",  # Flash / Fade to black / None (between cuts)
    "trans_len": 0.06,      # seconds
    "fade_in": 0.0,         # fade from black at the very start (s)
    "fade_out": 0.0,        # fade to black at the end (s)
    "brightness": 0,        # -15 .. +25
    "glow": 16,             # 0 .. 40
    "sharpen": True,
    "game_vol": 100,        # %
    "music": "",            # optional music file
    "music_vol": 60,        # %
}
EDITS_FILE = os.path.join(APP_DIR, "edits.json")


def load_edits():
    try:
        return json.load(open(EDITS_FILE, encoding="utf-8"))
    except Exception:
        return {}


def save_edits(e):
    tmp = EDITS_FILE + ".tmp"
    json.dump(e, open(tmp, "w", encoding="utf-8"), indent=1)
    os.replace(tmp, EDITS_FILE)


def _ev(n):
    return max(2, int(round(n / 2)) * 2)


def _pulse(cuts, d, amt):
    if not cuts or d <= 0:
        return None
    return "+".join(f"{amt}*max(0,1-abs(t-{c:.3f})/{d:.3f})" for c in cuts)


def render(enc, segments, out_path, log=print, style=None, preview=False):
    """segments: list of (video_path, start, end). Writes a finished Fed video.
    style: overrides for DEFAULT_STYLE. preview=True makes a fast half-size draft."""
    st = dict(DEFAULT_STYLE)
    st.update({k: v for k, v in (style or {}).items() if k in DEFAULT_STYLE})
    k = 0.5 if preview else 1.0
    W, H, FH, FY, LY = _ev(1080 * k), _ev(1920 * k), _ev(1654 * k), _ev(266 * k), _ev(600 * k)
    fps = 30 if preview else 60
    venc = (["-c:v", "h264_nvenc", "-preset", "p1", "-cq", "30"] if enc.nvenc else
            ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "27"]) if preview else None
    work = tempfile.mkdtemp(prefix="fedclip_")
    sharpen = ("cas=0.7," if enc.has_cas else "") + "unsharp=5:5:0.9:3:3:0.4," if st["sharpen"] else ""
    glow = float(st["glow"]) / 100.0
    glow_chain = (f"split[f1][f2];[f2]gblur=sigma={14 * k:.1f}[gl];[f1][gl]blend=all_mode=screen:all_opacity={glow:.3f},"
                  if glow > 0 else "")
    fc = (f"[0:v]split[a][b];[a]scale=-2:{H},crop={W}:{H},boxblur={max(2, int(22 * k))}:2,eq=brightness=-0.1:saturation=1.1,"
          "colorbalance=rs=0.05:gs=-0.07:bs=0.14,format=gbrp[bg];"
          f"[b]hqdn3d=1.5:1.5:3:3,crop=trunc(ih*0.6528/2)*2:ih:(iw-ow)/2:0,scale={W}:{FH}:flags=spline,"
          f"{sharpen}eq=contrast=1.08:saturation=1.22,format=gbrp,{glow_chain}"
          "colorbalance=rs=0.05:gs=-0.07:bs=0.14:rm=0.05:gm=-0.06:bm=0.10:rh=0.03:gh=-0.02:bh=0.05,"
          f"eq=contrast=1.1:saturation=1.1:gamma=0.96[fg];[bg][fg]overlay=0:{FY},format=yuv420p,fps={fps}[v]")
    cuts, t = [], 0.0
    lst = os.path.join(work, "list.txt")
    with open(lst, "w", encoding="utf-8") as L:
        for i, (src, s, e) in enumerate(segments):
            s = max(0.0, float(s))
            e = float(e)
            if e - s < 0.2:
                continue
            part = os.path.join(work, f"p{i:02d}.mp4")
            amap = "0:a:1" if enc.audio_count(src) >= 2 else "0:a:0"   # Medal track 2 = game audio only
            log(f"  cutting part {i + 1}/{len(segments)}")
            enc.run(["-ss", f"{s:.3f}", "-t", f"{e - s:.3f}", "-i", src, "-filter_complex", fc, "-map", "[v]",
                     "-map", amap] + (venc or enc.venc(15)) + ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                                                              "-ar", "48000", "-ac", "2", part])
            L.write(f"file '{part.replace(os.sep, '/')}'\n")
            t += e - s
            cuts.append(t)
    if not cuts:
        raise RuntimeError("Nothing to render - every part is empty.")
    joined = os.path.join(work, "join.mp4")
    enc.run(["-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", joined])
    T = t

    inputs = ["-i", joined]
    nxt = 1
    vf = ["vignette=PI/5"]
    tr = st["transition"]
    tl = max(0.02, float(st["trans_len"]))
    if tr == "Flash":
        p = _pulse(cuts[:-1], tl, 0.3)
        if p:
            vf.append(f"eq=brightness='{p}':eval=frame")
    elif tr == "Fade to black":
        p = _pulse(cuts[:-1], tl, 1.0)
        if p:
            vf.append(f"eq=brightness='-min(1,{p})':saturation='1-min(1,{p})':contrast='1-min(1,{p})':eval=frame")
    intro = bool(st["intro"])
    if intro:
        vf.append("eq=brightness=-0.2:saturation=0.8:enable='between(t,0.3,1.95)'")
    vchain = "[0:v]" + ",".join(vf) + "[d];"
    if intro:
        logo_i = nxt
        nxt += 1
        inputs += ["-loop", "1", "-t", "2.1", "-framerate", str(fps), "-i", os.path.join(ASSETS, "fed_logo_glow.png")]
        vchain += (f"[{logo_i}:v]format=rgba,scale=iw*{k}:-1,fade=t=in:st=0.3:d=0.15:alpha=1,fade=t=out:st=1.6:d=0.35:alpha=1[tt];"
                   f"[d][tt]overlay=0:{LY}:enable='between(t,0.3,2.0)':format=auto,")
    else:
        vchain += "[d]"
    bright = 0.035 + float(st["brightness"]) / 100.0
    vchain += f"eq=gamma=1.2:brightness={bright:.3f}:contrast=1.03:saturation=1.05"
    fi, fo = float(st["fade_in"]), min(float(st["fade_out"]), T / 2)
    if fi > 0:
        vchain += f",fade=t=in:st=0:d={fi:.2f}"
    if fo > 0:
        vchain += f",fade=t=out:st={T - fo:.3f}:d={fo:.2f}"
    vchain += ",format=yuv420p[v]"

    gv = float(st["game_vol"]) / 100.0
    duck = intro or st["voice"]
    achain = f"[0:a]volume='{gv:.2f}*if(lt(t,2.0),{0.45 if duck else 1},1)':eval=frame[g];"
    mix = ["[g]"]
    if st["voice"]:
        vi = nxt
        nxt += 1
        inputs += ["-i", os.path.join(ASSETS, "fed_voice.mp3")]
        achain += f"[{vi}:a]aresample=48000,volume=3.0,adelay=300|300[vo];"
        mix.append("[vo]")
    music = st.get("music") or ""
    if music and os.path.exists(music):
        mi = nxt
        nxt += 1
        inputs += ["-stream_loop", "-1", "-i", music]
        achain += f"[{mi}:a]aresample=48000,volume={float(st['music_vol']) / 100.0:.2f},atrim=0:{T:.3f}[mu];"
        mix.append("[mu]")
    if len(mix) > 1:
        achain += f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0,"
    else:
        achain += "[g]"
    achain += "loudnorm=I=-14:TP=-1.5:LRA=11"
    if fi > 0:
        achain += f",afade=t=in:st=0:d={fi:.2f}"
    if fo > 0:
        achain += f",afade=t=out:st={T - fo:.3f}:d={fo:.2f}"
    achain += "[a]"

    log("  adding finishing touches" if not intro else "  adding FED intro + finishing")
    tmp_out = out_path + ".part.mp4"
    enc.run(inputs + ["-filter_complex", vchain + ";" + achain, "-map", "[v]", "-map", "[a]", "-t", f"{T:.3f}"]
            + (venc or enc.venc(17)) + ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k" if preview else "256k",
                                        "-ar", "48000", "-movflags", "+faststart", tmp_out])
    os.replace(tmp_out, out_path)
    shutil.rmtree(work, ignore_errors=True)
    return T


def clip_segments(c, pre=2.8, post=0.8, merge=1.5):
    if not c["kills"]:
        end = c.get("dur") or 30.0
        return [(c["path"], max(0.0, end - 15.0), end - 0.2)]
    wins = []
    for k in c["kills"]:
        s, e = k - pre, k + post
        if wins and s - wins[-1][1] <= merge:
            wins[-1][1] = e
        else:
            wins.append([s, e])
    wins[0][0] = max(0.0, wins[0][0])
    wins[-1][1] += 0.4
    return [(c["path"], s, e) for s, e in wins]


def label(c):
    names = {5: "Ace", 4: "4K", 3: "3K"}
    kind = names.get(c["top"]) or (f"{len(c['kills'])}K" if len(c["kills"]) > 1 else "Clip")
    bits = [p.strip() for p in c["title"].split("-")[1:]] if c["title"] else []
    when = clip_time(c).strftime("%b %d %H%M")
    return NAME_BAD.sub("", " - ".join(["R6 " + kind] + bits + [when]))


def make_highlight(enc, c, out_dir, state, log=print, segments=None, style=None):
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{label(c)} (Fed).mp4")
    log(f"Making {os.path.basename(out)}")
    if segments is None:
        ed = load_edits().get(c["id"]) or {}
        segments = [(c["path"], a, b) for a, b in ed["segments"]] if ed.get("segments") else clip_segments(c)
        style = ed.get("style") or style
    length = render(enc, segments, out, log, style=style)
    state["done"][c["id"]] = {"made": out, "at": datetime.now().isoformat(timespec="seconds"),
                              "clip_time": clip_time(c).isoformat(), "top": c["top"], "seconds": round(length, 1)}
    save_state(state)
    return out


def make_aim_short(enc, clips, settings, out_dir, target_s=60, pre=1.6, post=1.7, log=print, style=None):
    bursts = fastest_bursts(clips, settings)
    if not bursts:
        raise RuntimeError("No multi-kills found to build a short from.")
    picked, total = [], 2.0
    for r in bursts:
        ln = r["b"] - r["a"] + pre + post
        if total + ln > target_s and picked:
            break
        picked.append(r)
        total += ln
    # build up: quick doubles first, biggest multi-kills last
    picked.sort(key=lambda r: (r["n"], -r["span"]))
    segs = [(r["clip"]["path"], max(0, r["a"] - pre), r["b"] + post) for r in picked]
    segs[-1] = (segs[-1][0], segs[-1][1], segs[-1][2] + 0.5)
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"R6 Aim Short - {datetime.now():%b %d %H%M} (Fed).mp4")
    log(f"Making aim short from {len(segs)} multi-kills")
    render(enc, segs, out, log, style=style if style is not None else settings.get("style"))
    return out


# ------------------------------------------------------------------ gallery helpers
THUMBS = os.path.join(APP_DIR, "thumbs")


def thumb_path(c):
    return os.path.join(THUMBS, c["id"] + ".jpg")


def make_thumb(enc, c):
    """Grab a 16:9 frame at the clip's last kill (or 60% in) for the gallery."""
    out = thumb_path(c)
    if os.path.exists(out):
        return out
    if not os.path.exists(c["path"]):
        return None
    os.makedirs(THUMBS, exist_ok=True)
    t = c["kills"][-1] if c["kills"] else (c.get("dur") or 30) * 0.6
    try:
        enc.run(["-ss", f"{max(0.0, t):.2f}", "-i", c["path"], "-frames:v", "1",
                 "-vf", "scale=320:180:force_original_aspect_ratio=increase,crop=320:180", "-q:v", "4", out])
    except Exception:
        return None
    return out if os.path.exists(out) else None


def parts(c):
    """('ACE', 'Ash', 'Oregon') style info for the gallery card."""
    kind = {5: "ACE", 4: "4K", 3: "3K"}.get(c["top"]) or (f"{len(c['kills'])}K" if len(c["kills"]) > 1
                                                          else ("1 KILL" if c["kills"] else ""))
    bits = [p.strip() for p in c["title"].split("-")[1:]] if c["title"] else []
    mp = next((b for b in bits if b.lower() in MAPS), "")
    op = next((b for b in bits if b != mp), "")
    return kind, op, mp


MAPS = {m.lower() for m in (
    "Bank", "Border", "Chalet", "Clubhouse", "Club", "Coastline", "Consulate", "Emerald Plains", "Favela", "Fortress",
    "Hereford", "House", "Kafe", "Kanal", "Lair", "Nighthaven", "Nighthaven Labs", "Oregon", "Outback", "Plane",
    "Skyscraper", "Stadium", "Theme Park", "Tower", "Villa", "Yacht", "Calypso", "Close Quarter")}
