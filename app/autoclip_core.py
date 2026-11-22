"""
Breachcut - core (no UI).
Keeps Breachcut's own clip library (clips the built-in recorder saved, with the kills it spotted) and renders them as
TikTok/Shorts (vertical 1080x1920)
or YouTube widescreen (1920x1080) edits: haze grade, sharpened, glowing name intro, flash cuts.
"""
import hashlib
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


APP_NAME = "Breachcut"


def default_library():
    """Breachcut's own video library: Videos\\Breachcut (falls back next to the Desktop)."""
    home = os.path.expanduser("~")
    for d in (os.path.join(home, "Videos"), os.path.join(home, "OneDrive", "Videos")):
        if os.path.isdir(d):
            return os.path.join(d, APP_NAME)
    return os.path.join(default_desktop(), APP_NAME)

DEFAULTS = {
    "library_dir": default_library(),
    "clips_dir": os.path.join(default_library(), "Clips"),
    "output_dir": os.path.join(default_library(), "TikTok"),
    "youtube_dir": os.path.join(default_library(), "YouTube"),
    "recordings_dir": os.path.join(default_library(), "Recordings"),
    "auto_formats": ["vertical", "wide"],   # what auto-clip makes
    "creator": "",                # name shown in the intro
    "intro_image": "",            # custom intro PNG (optional)
    "voice_file": "",             # custom intro voice (optional)
    "onboarded": False,
    "min_kills": 3,               # auto-make videos for 3Ks, 4Ks and Aces
    "only_new_after": None,       # ISO date; clips older than this are ignored (set on first run)
    "auto_make": True,            # turn big plays into TikTok / YouTube videos by itself
    # kill detection + auto-clip (Breachcut watches the score feed on screen)
    "kill_detect": True,
    "kill_sensitivity": "Normal", # Low / Normal / High
    "auto_clip": True,            # save a clip by itself after a multi-kill
    "auto_clip_min": 2,           # kills in one play before a clip is saved
    "streak_gap": 25,             # kills this close together count as one play
    "auto_pre": 8,                # seconds saved before the first kill
    "auto_post": 3,               # seconds saved after the last kill
    "kill_sound": False,          # little tick when a kill is detected
    "scan_old": True,             # look for kills in recordings that don't have them yet
    "clip_name": "Breachcut {kind} {date} {time}",
    "show_hotkey_clips": True,
    "keep_auto_days": 0,          # 0 = keep forever
    # general
    "launch_on_startup": False,
    "start_minimized": False,
    "auto_update": True,
    # clips
    "clip_pre": 2.8,              # seconds kept before each kill
    "clip_post": 0.8,             # seconds kept after each kill
    # export
    "export_quality": "High",     # High / Balanced / Small file
    "export_fps": 60,
    "export_encoder": "Auto",     # Auto / GPU / CPU
    # recorder (used by the built-in recorder)
    "rec_replay_on": True,
    "rec_mode": "siege",          # siege = only while Siege is running, always, off
    "replay_seconds": 30,
    "rec_resolution": "Native",
    "rec_fps": 60,
    "rec_bitrate": 20,            # Mbps
    "rec_encoder": "Auto",
    "rec_codec": "H.264",         # H.264 / HEVC
    "rec_preset": "Balanced",     # Performance / Balanced / Quality
    "rec_rc": "CBR",              # CBR / VBR
    "rec_capture": "Auto",        # Auto (Desktop Duplication) / Compatibility (GDI)
    "rec_monitor": 0,
    "rec_cursor": False,
    "rec_audio_kbps": 160,
    # audio
    "rec_game_audio": True,
    "rec_game_vol": 100,
    "rec_mic": False,
    "mic_device": "Default",
    "mic_vol": 100,
    "separate_tracks": True,
    "mic_noise": False,           # noise suppression
    "mic_gate": False,            # noise gate (cuts background hum between words)
    "mic_mono": True,
    # hotkeys
    "hk_clip": "F10",
    "hk_record": "Shift+F10",
    "hk_clip_short": "None",      # save the last 15 seconds
    "hk_clip_long": "None",       # save the last 2 minutes
    "hk_buffer": "None",          # turn the replay buffer on / off
    "hk_autoclip": "None",        # turn auto-clip on / off
    "hotkey_sound": True,
    # storage
    "storage_limit_gb": 50,
    "auto_delete_days": 0,        # 0 = never
    "delete_when_full": True,
    # notifications
    "notify_made": True,
    "notify_clip": True,          # tell me when a clip is saved
    "notify_kill": False,         # tell me on every detected kill
    "notify_sound": True,
    "notify_update": True,
}

QUALITY_CRF = {"High": 16, "Balanced": 20, "Small file": 24}
PAD = {"pre": 2.8, "post": 0.8}


def apply_settings(s, enc=None):
    """Push settings that the render code reads into this module and the encoder."""
    try:
        PAD["pre"] = max(0.5, min(8.0, float(s.get("clip_pre", 2.8))))
        PAD["post"] = max(0.2, min(6.0, float(s.get("clip_post", 0.8))))
    except (TypeError, ValueError):
        pass
    if enc is not None:
        enc.quality = QUALITY_CRF.get(s.get("export_quality"), 16)
        enc.fps = 30 if str(s.get("export_fps")) == "30" else 60
        enc.force_cpu = s.get("export_encoder") == "CPU"


def load_settings():
    s = dict(DEFAULTS)
    existed = os.path.exists(SETTINGS_FILE)
    if existed:
        try:
            s.update(json.load(open(SETTINGS_FILE, encoding="utf-8")))
        except Exception:
            pass
    return migrate_settings(s, existed)


def migrate_settings(s, existed):
    """Older Fed Clipper installs: keep the FED logo/voice as this user's brand."""
    if existed and not s.get("creator") and not s.get("onboarded"):
        logo, voice = os.path.join(ASSETS, "fed_logo_glow.png"), os.path.join(ASSETS, "fed_voice.mp3")
        s["creator"] = "Fed"
        if os.path.exists(logo):
            s["intro_image"] = logo
        if os.path.exists(voice):
            s["voice_file"] = voice
        if os.path.basename(s.get("output_dir", "")) == "Upload tiktok":
            s["youtube_dir"] = os.path.join(os.path.dirname(s["output_dir"]), "Upload youtube")
        s["onboarded"] = True
        save_settings(s)
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
        self.quality = None
        self.fps = 60
        self.force_cpu = False

    def _run(self, args, capture=False):
        r = subprocess.run(args, capture_output=True, text=True, creationflags=NO_WINDOW)
        if r.returncode != 0 and not capture:
            raise RuntimeError(r.stderr.strip()[-600:])
        return (r.stdout or "") + (r.stderr or "") if capture else r

    def venc(self, quality=16):
        if self.quality is not None:
            quality = self.quality + (quality - 16)
        if self.nvenc and not self.force_cpu:
            return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(quality + 3), "-b:v", "0"]
        return ["-c:v", "libx264", "-preset", "fast", "-crf", str(quality)]

    def audio_count(self, path):
        r = subprocess.run([self.ffprobe, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                            "-of", "csv=p=0", path], capture_output=True, text=True, creationflags=NO_WINDOW)
        return len([l for l in r.stdout.splitlines() if l.strip()])

    def run(self, args):
        self._run([self.ff, "-nostdin", "-v", "error", "-y"] + args)


# ------------------------------------------------------------------ Breachcut's own clip library
META_DIR = os.path.join(APP_DIR, "clipmeta")
STAMP = re.compile(r"(\d{4})-(\d{2})-(\d{2})[ _](\d{2})[-.](\d{2})[-.](\d{2})")


def _meta_file(path):
    return os.path.join(META_DIR, re.sub(r"[^\w.() -]", "_", os.path.basename(path)) + ".json")


def read_meta(path):
    try:
        return json.load(open(_meta_file(path), encoding="utf-8"))
    except Exception:
        return None


def write_meta(path, meta):
    os.makedirs(META_DIR, exist_ok=True)
    old = read_meta(path) or {}
    old.update(meta)
    tmp = _meta_file(path) + ".tmp"
    json.dump(old, open(tmp, "w", encoding="utf-8"), indent=1)
    os.replace(tmp, _meta_file(path))


def clip_filename(s, kind):
    now = datetime.now()
    tpl = s.get("clip_name") or DEFAULTS["clip_name"]
    try:
        name = tpl.format(kind=kind, date=now.strftime("%Y-%m-%d"), time=now.strftime("%H-%M-%S"), game="Siege")
    except (KeyError, IndexError, ValueError):
        name = f"Breachcut {kind} {now:%Y-%m-%d %H-%M-%S}"
    if not STAMP.search(name):
        name += f" {now:%Y-%m-%d %H-%M-%S}"
    return NAME_BAD.sub("", name).strip() or f"Breachcut {kind} {now:%Y-%m-%d %H-%M-%S}"


def best_run(kills, gap=25.0):
    """Most kills in one play (kills no more than `gap` seconds apart)."""
    best = run = 0
    last = None
    for k in sorted(kills):
        run = run + 1 if last is not None and k - last <= gap else 1
        best = max(best, run)
        last = k
    return best


def clip_from_file(path, s=None, kind=None):
    """Turn a saved video into a clip entry the app understands."""
    s = s or {}
    meta = read_meta(path) or {}
    kills = [float(k) for k in meta.get("kills") or []]
    try:
        st = os.stat(path)
    except OSError:
        return None
    kind = meta.get("kind") or kind or "clip"
    m = STAMP.search(os.path.basename(path))
    when = datetime(*map(int, m.groups())) if m else datetime.fromtimestamp(st.st_mtime)
    top = min(5, best_run(kills, float(s.get("streak_gap", 25) or 25)))
    dur = meta.get("dur") or None
    return {"dur": dur, "path": path, "id": os.path.splitext(os.path.basename(path))[0], "created": when.isoformat(),
            "when": when, "mode": "Recording" if kind == "recording" else "Siege", "kind": kind, "kills": kills,
            "top": top if top >= 3 else (1 if kills else 0), "nkills": len(kills), "scanned": "kills" in meta,
            "size": st.st_size, "title": ""}


def read_clips(s):
    """Every clip and recording in Breachcut's library, newest first."""
    out, seen = [], set()
    for key, kind in (("clips_dir", "clip"), ("recordings_dir", "recording")):
        folder = s.get(key) or ""
        if not os.path.isdir(folder):
            continue
        for n in os.listdir(folder):
            p = os.path.join(folder, n)
            if not n.lower().endswith(VIDEO_EXT) or n.endswith(".part.mp4") or not os.path.isfile(p):
                continue
            k = os.path.normcase(os.path.abspath(p))
            if k in seen:
                continue
            seen.add(k)
            c = clip_from_file(p, s, kind)
            if c:
                out.append(c)
    out.sort(key=lambda c: c["when"], reverse=True)
    return out


def clip_time(c):
    if c.get("when"):
        return c["when"]
    m = STAMP.search(c["id"])
    if m:
        return datetime(*map(int, m.groups()))
    s = re.search(r"(\d{14})", c["id"])
    return datetime.strptime(s.group(1), "%Y%m%d%H%M%S") if s else datetime.min


def pick_new_highlights(clips, settings, state, kinds=("auto",)):
    """Clips worth a video of their own: enough kills in one play, not made yet. By default only auto-clips
    (the hotkey clip and the auto-clip of the same play would otherwise both get made)."""
    after = settings.get("only_new_after")
    after_dt = datetime.fromisoformat(after) if after else None
    chosen = []
    for c in sorted(clips, key=clip_time):
        if kinds and c.get("kind") not in kinds:
            continue
        if c["top"] < settings["min_kills"] or len(c["kills"]) < 2:
            continue
        if after_dt and clip_time(c) < after_dt:
            continue
        if c["id"] in state["done"]:
            continue
        chosen.append(c)
    return chosen


def fastest_bursts(clips, settings, max_gap=3.0):
    """Every multi-kill burst (kills <= max_gap apart), fastest first, duplicates removed."""
    out = []
    for c in clips:
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


FONT_FILE = os.path.join(ASSETS, "Anton-Regular.ttf")
if not os.path.exists(FONT_FILE):   # older installs: use a built-in Windows font
    for _f in (r"C:\Windows\Fonts\impact.ttf", r"C:\Windows\Fonts\arialbd.ttf"):
        if os.path.exists(_f):
            FONT_FILE = _f
            break
INTRO_DIR = os.path.join(APP_DIR, "intro")


def make_name_intro(text):
    """Render the creator's name as a glowing intro title (1080x700 PNG, cached)."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    text = (text or "").strip().upper()[:16]
    if not text:
        return None
    os.makedirs(INTRO_DIR, exist_ok=True)
    out = os.path.join(INTRO_DIR, hashlib.md5(text.encode("utf-8")).hexdigest()[:10] + ".png")
    if os.path.exists(out):
        return out
    W, H = 1080, 700
    size = 330
    font = ImageFont.truetype(FONT_FILE, size)
    d0 = ImageDraw.Draw(Image.new("L", (1, 1)))
    while size > 90 and d0.textlength(text, font=font) > 900:
        size -= 10
        font = ImageFont.truetype(FONT_FILE, size)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((W // 2, H // 2), text, font=font, fill=(255, 255, 255, 255), anchor="mm")
    out_im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for r, a in ((46, 0.9), (18, 1.0), (6, 1.0)):
        g = layer.filter(ImageFilter.GaussianBlur(r))
        g.putalpha(g.getchannel("A").point(lambda v, a=a: min(255, int(v * a))))
        out_im = Image.alpha_composite(out_im, g)
    out_im = Image.alpha_composite(out_im, layer)
    out_im.save(out)
    return out


def brand_assets(settings=None):
    """(intro_png or None, voice_file or None) for this user."""
    s = settings or {}

    def resolve(p):   # file moved with the app (e.g. Fed Clipper -> Breachcut folder)? look in our assets
        if p and not os.path.exists(p) and os.path.exists(os.path.join(ASSETS, os.path.basename(p))):
            return os.path.join(ASSETS, os.path.basename(p))
        return p
    img = resolve(s.get("intro_image") or "")
    if not (img and os.path.exists(img)):
        img = make_name_intro(s.get("creator", "")) if s.get("creator") else None
    voice = resolve(s.get("voice_file") or "")
    if not (voice and os.path.exists(voice)):
        voice = None
    return img, voice


def _ev(n):
    return max(2, int(round(n / 2)) * 2)


def _pulse(cuts, d, amt):
    if not cuts or d <= 0:
        return None
    return "+".join(f"{amt}*max(0,1-abs(t-{c:.3f})/{d:.3f})" for c in cuts)


def render(enc, segments, out_path, log=print, style=None, preview=False, fmt="vertical", brand=None):
    """segments: list of (video_path, start, end). Writes a finished edit.
    fmt: "vertical" (TikTok/Shorts 1080x1920) or "wide" (YouTube 1920x1080).
    style: overrides for DEFAULT_STYLE. preview=True makes a fast half-size draft.
    brand: (intro_png, voice_file) - either may be None."""
    if brand is None:
        brand = (os.path.join(ASSETS, "fed_logo_glow.png"), os.path.join(ASSETS, "fed_voice.mp3"))
    intro_png, voice_file = brand
    intro_png = intro_png if intro_png and os.path.exists(intro_png) else None
    voice_file = voice_file if voice_file and os.path.exists(voice_file) else None
    wide = fmt == "wide"
    st = dict(DEFAULT_STYLE)
    st.update({k: v for k, v in (style or {}).items() if k in DEFAULT_STYLE})
    k = 0.5 if preview else 1.0
    if wide:
        W, H = _ev(1920 * k), _ev(1080 * k)
        logo_k, LY = 0.75 * k, None
    else:
        W, H, FH, FY, LY = _ev(1080 * k), _ev(1920 * k), _ev(1654 * k), _ev(266 * k), _ev(600 * k)
        logo_k = k
    fps = 30 if preview else getattr(enc, "fps", 60)
    venc = (["-c:v", "h264_nvenc", "-preset", "p1", "-cq", "30"] if enc.nvenc and not getattr(enc, "force_cpu", False) else
            ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "27"]) if preview else None
    work = tempfile.mkdtemp(prefix="fedclip_")
    sharpen = ("cas=0.7," if enc.has_cas else "") + "unsharp=5:5:0.9:3:3:0.4," if st["sharpen"] else ""
    glow = float(st["glow"]) / 100.0
    glow_chain = (f"split[f1][f2];[f2]gblur=sigma={14 * k:.1f}[gl];[f1][gl]blend=all_mode=screen:all_opacity={glow:.3f},"
                  if glow > 0 else "")
    grade = ("colorbalance=rs=0.05:gs=-0.07:bs=0.14:rm=0.05:gm=-0.06:bm=0.10:rh=0.03:gh=-0.02:bh=0.05,"
             "eq=contrast=1.1:saturation=1.1:gamma=0.96")
    if wide:
        fc = (f"[0:v]hqdn3d=1.5:1.5:3:3,scale={W}:{H}:flags=lanczos,"
              f"{sharpen}eq=contrast=1.08:saturation=1.22,format=gbrp,{glow_chain}{grade},format=yuv420p,fps={fps}[v]")
    else:
        fc = (f"[0:v]split[a][b];[a]scale=-2:{H},crop={W}:{H},boxblur={max(2, int(22 * k))}:2,eq=brightness=-0.1:saturation=1.1,"
              "colorbalance=rs=0.05:gs=-0.07:bs=0.14,format=gbrp[bg];"
              f"[b]hqdn3d=1.5:1.5:3:3,crop=trunc(ih*0.6528/2)*2:ih:(iw-ow)/2:0,scale={W}:{FH}:flags=spline,"
              f"{sharpen}eq=contrast=1.08:saturation=1.22,format=gbrp,{glow_chain}{grade}[fg];"
              f"[bg][fg]overlay=0:{FY},format=yuv420p,fps={fps}[v]")
    cuts, t = [], 0.0
    lst = os.path.join(work, "list.txt")
    with open(lst, "w", encoding="utf-8") as L:
        for i, (src, s, e) in enumerate(segments):
            s = max(0.0, float(s))
            e = float(e)
            if e - s < 0.2:
                continue
            part = os.path.join(work, f"p{i:02d}.mp4")
            na = enc.audio_count(src)
            amap = "0:a:1" if na >= 2 else ("0:a:0" if na == 1 else "1:a:0")   # Medal track 2 = game audio only
            silent = [] if na else ["-f", "lavfi", "-t", f"{e - s:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
            log(f"  cutting part {i + 1}/{len(segments)}")
            enc.run(["-ss", f"{s:.3f}", "-t", f"{e - s:.3f}", "-i", src] + silent + ["-filter_complex", fc, "-map", "[v]",
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
    intro = bool(st["intro"]) and intro_png is not None
    if intro:
        vf.append("eq=brightness=-0.2:saturation=0.8:enable='between(t,0.3,1.95)'")
    vchain = "[0:v]" + ",".join(vf) + "[d];"
    if intro:
        logo_i = nxt
        nxt += 1
        inputs += ["-loop", "1", "-t", "2.1", "-framerate", str(fps), "-i", intro_png]
        ypos = "(H-h)/2" if LY is None else str(LY)
        vchain += (f"[{logo_i}:v]format=rgba,scale=1080*{logo_k}:700*{logo_k},fade=t=in:st=0.3:d=0.15:alpha=1,"
                   f"fade=t=out:st=1.6:d=0.35:alpha=1[tt];"
                   f"[d][tt]overlay=(W-w)/2:{ypos}:enable='between(t,0.3,2.0)':format=auto,")
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
    use_voice = bool(st["voice"]) and voice_file is not None
    duck = intro or use_voice
    achain = f"[0:a]volume='{gv:.2f}*if(lt(t,2.0),{0.45 if duck else 1},1)':eval=frame[g];"
    mix = ["[g]"]
    if use_voice:
        vi = nxt
        nxt += 1
        inputs += ["-i", voice_file]
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


def clip_segments(c, pre=None, post=None, merge=1.5):
    pre = PAD["pre"] if pre is None else pre
    post = PAD["post"] if post is None else post
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


def video_entry(path, fmt, clip_id=None, title=None, seconds=None, kind="highlight"):
    return {"path": path, "fmt": fmt, "clip_id": clip_id, "title": title or os.path.splitext(os.path.basename(path))[0],
            "seconds": round(seconds or 0, 1), "kind": kind, "made_at": datetime.now().isoformat(timespec="seconds")}


def add_video(state, entry):
    vids = [v for v in state.setdefault("videos", []) if v.get("path") != entry["path"]]
    vids.append(entry)
    state["videos"] = vids
    save_state(state)


def list_videos(state, fmt=None):
    """Made videos that still exist, newest first (includes ones made by older versions)."""
    vids = list(state.get("videos", []))
    known = {v.get("path") for v in vids}
    for cid, d in (state.get("done") or {}).items():
        for key, f in (("made", "vertical"), ("wide", "wide")):
            p = d.get(key)
            if p and p not in known:
                vids.append({"path": p, "fmt": f, "clip_id": cid, "title": os.path.splitext(os.path.basename(p))[0],
                             "seconds": d.get("seconds", 0), "kind": "highlight", "made_at": d.get("at", "")})
                known.add(p)
    vids = [v for v in vids if v.get("path") and os.path.exists(v["path"]) and (fmt is None or v.get("fmt") == fmt)]
    vids.sort(key=lambda v: v.get("made_at") or "", reverse=True)
    return vids


def remove_video(state, path):
    state["videos"] = [v for v in state.get("videos", []) if v.get("path") != path]
    for d in (state.get("done") or {}).values():
        for key in ("made", "wide"):
            if d.get(key) == path:
                d.pop(key, None)
    save_state(state)


def label(c):
    names = {5: "Ace", 4: "4K", 3: "3K"}
    kind = names.get(c["top"]) or (f"{len(c['kills'])}K" if len(c["kills"]) > 1 else "Clip")
    bits = [p.strip() for p in c["title"].split("-")[1:]] if c["title"] else []
    when = clip_time(c).strftime("%b %d %H%M")
    return NAME_BAD.sub("", " - ".join(["R6 " + kind] + bits + [when]))


def make_highlight(enc, c, out_dir, state, log=print, segments=None, style=None, fmt="vertical", brand=None):
    os.makedirs(out_dir, exist_ok=True)
    suffix = " (YouTube)" if fmt == "wide" else ""
    out = os.path.join(out_dir, f"{label(c)}{suffix}.mp4")
    log(f"Making {os.path.basename(out)}")
    if segments is None:
        ed = load_edits().get(c["id"]) or {}
        segments = [(c["path"], a, b) for a, b in ed["segments"]] if ed.get("segments") else clip_segments(c)
        style = ed.get("style") or style
    length = render(enc, segments, out, log, style=style, fmt=fmt, brand=brand)
    d = state["done"].setdefault(c["id"], {})
    d.update({"made" if fmt != "wide" else "wide": out, "at": datetime.now().isoformat(timespec="seconds"),
              "clip_time": clip_time(c).isoformat(), "top": c["top"], "seconds": round(length, 1)})
    add_video(state, video_entry(out, fmt, c["id"], label(c), length))
    return out


def make_aim_short(enc, clips, settings, out_dir, target_s=60, pre=1.6, post=1.7, log=print, style=None,
                   fmt="vertical", brand=None, state=None):
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
    suffix = " (YouTube)" if fmt == "wide" else ""
    out = os.path.join(out_dir, f"R6 Aim Montage - {datetime.now():%b %d %H%M}{suffix}.mp4")
    log(f"Making aim montage from {len(segs)} multi-kills")
    length = render(enc, segs, out, log, style=style if style is not None else settings.get("style"), fmt=fmt,
                    brand=brand if brand is not None else brand_assets(settings))
    if state is not None:
        add_video(state, video_entry(out, fmt, None, os.path.splitext(os.path.basename(out))[0], length, kind="montage"))
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


def video_thumb(enc, v):
    """Thumbnail for a made video: 180x320 (vertical) or 320x180 (wide), cached."""
    os.makedirs(THUMBS, exist_ok=True)
    key = hashlib.md5((v["path"] + str(os.path.getmtime(v["path"]) if os.path.exists(v["path"]) else "")).encode()).hexdigest()[:12]
    out = os.path.join(THUMBS, f"v_{key}.jpg")
    if os.path.exists(out):
        return out
    size = "320:180" if v.get("fmt") == "wide" else "180:320"
    t = max(2.4, float(v.get("seconds") or 6) * 0.45)
    try:
        enc.run(["-ss", f"{t:.2f}", "-i", v["path"], "-frames:v", "1",
                 "-vf", f"scale={size}:force_original_aspect_ratio=increase,crop={size}", "-q:v", "4", out])
    except Exception:
        return None
    return out if os.path.exists(out) else None


def parts(c):
    """('ACE', 'Ash', 'Oregon') style info for the gallery card."""
    n = len(c["kills"])
    if c.get("kind") == "recording" or n > 5:
        kind = f"{n} KILLS" if n > 1 else ("1 KILL" if n else "")
    else:
        kind = {5: "ACE", 4: "4K", 3: "3K"}.get(c["top"]) or (f"{n}K" if n > 1 else ("1 KILL" if n else ""))
    bits = [p.strip() for p in c["title"].split("-")[1:]] if c["title"] else []
    mp = next((b for b in bits if b.lower() in MAPS), "")
    op = next((b for b in bits if b != mp), "")
    return kind, op, mp


MAPS = {m.lower() for m in (
    "Bank", "Border", "Chalet", "Clubhouse", "Club", "Coastline", "Consulate", "Emerald Plains", "Favela", "Fortress",
    "Hereford", "House", "Kafe", "Kanal", "Lair", "Nighthaven", "Nighthaven Labs", "Oregon", "Outback", "Plane",
    "Skyscraper", "Stadium", "Theme Park", "Tower", "Villa", "Yacht", "Calypso", "Close Quarter")}


# ------------------------------------------------------------------ storage (Breachcut's own library)
VIDEO_EXT = (".mp4", ".mov", ".mkv")


def library_folders(s):
    """(label, path) for every folder Breachcut stores videos in."""
    return [("Clips", s.get("clips_dir") or ""), ("Recordings", s.get("recordings_dir") or ""),
            ("TikTok / Shorts", s.get("output_dir") or ""), ("YouTube", s.get("youtube_dir") or "")]


def _videos_in(folder):
    out = []
    if folder and os.path.isdir(folder):
        for n in os.listdir(folder):
            p = os.path.join(folder, n)
            if n.lower().endswith(VIDEO_EXT) and os.path.isfile(p) and not n.endswith(".part.mp4"):
                try:
                    st = os.stat(p)
                    out.append((p, st.st_size, st.st_mtime))
                except OSError:
                    pass
    return out


def storage_usage(s):
    """{'folders': [(label, path, bytes, count)], 'total': bytes, 'thumbs': bytes, 'free': bytes, 'disk': bytes}"""
    rows, total = [], 0
    seen = set()
    for lab, path in library_folders(s):
        key = os.path.normcase(os.path.abspath(path)) if path else ""
        vids = [] if key in seen else _videos_in(path)
        seen.add(key)
        b = sum(v[1] for v in vids)
        rows.append((lab, path, b, len(vids)))
        total += b
    th = 0
    if os.path.isdir(THUMBS):
        for n in os.listdir(THUMBS):
            try:
                th += os.path.getsize(os.path.join(THUMBS, n))
            except OSError:
                pass
    free = disk = 0
    probe = next((p for _, p in library_folders(s) if p and os.path.isdir(p)), None) or s.get("library_dir") or APP_DIR
    while probe and not os.path.isdir(probe):
        probe = os.path.dirname(probe)
    try:
        du = shutil.disk_usage(probe or APP_DIR)
        free, disk = du.free, du.total
    except OSError:
        pass
    return {"folders": rows, "total": total, "thumbs": th, "free": free, "disk": disk}


def enforce_storage(s, state=None, log=print):
    """Delete Breachcut-made videos older than the auto-delete age, then the oldest ones while over the size limit.
    Only touches video files directly inside Breachcut's own output folders. Returns (files, bytes) removed."""
    vids, seen = [], set()
    for _, path in library_folders(s):
        key = os.path.normcase(os.path.abspath(path)) if path else ""
        if key and key not in seen:
            seen.add(key)
            vids += _videos_in(path)
    vids.sort(key=lambda v: v[2])          # oldest first
    gone, freed = [], 0
    days = int(s.get("auto_delete_days") or 0)
    if days > 0:
        cutoff = time.time() - days * 86400
        for v in vids:
            if v[2] < cutoff:
                gone.append(v)
    limit = float(s.get("storage_limit_gb") or 0) * 1024 ** 3
    if s.get("delete_when_full", True) and limit > 0:
        left = [v for v in vids if v not in gone]
        total = sum(v[1] for v in left)
        for v in left:
            if total <= limit:
                break
            gone.append(v)
            total -= v[1]
    for p, size, _ in gone:
        try:
            os.remove(p)
            freed += size
            if state is not None:
                remove_video(state, p)
        except OSError:
            pass
    if gone:
        log(f"Storage: removed {len(gone)} old video(s), freed {fmt_bytes(freed)}")
    return len(gone), freed


def clear_thumbs():
    n = 0
    if os.path.isdir(THUMBS):
        for f in os.listdir(THUMBS):
            try:
                os.remove(os.path.join(THUMBS, f))
                n += 1
            except OSError:
                pass
    return n


def move_library(s, new_root, state=None, log=print):
    """Move every Breachcut video into new_root\\Clips, \\TikTok, \\YouTube, \\Recordings and point settings there."""
    targets = {"output_dir": "TikTok", "youtube_dir": "YouTube", "recordings_dir": "Recordings", "clips_dir": "Clips"}
    moved = 0
    remap = {}
    for key, sub in targets.items():
        src = s.get(key) or ""
        dst = os.path.join(new_root, sub)
        os.makedirs(dst, exist_ok=True)
        if src and os.path.isdir(src) and os.path.normcase(os.path.abspath(src)) != os.path.normcase(os.path.abspath(dst)):
            for p, _, _ in _videos_in(src):
                name = os.path.basename(p)
                target = os.path.join(dst, name)
                i = 1
                while os.path.exists(target):
                    b, e = os.path.splitext(name)
                    target = os.path.join(dst, f"{b} ({i}){e}")
                    i += 1
                try:
                    shutil.move(p, target)
                    remap[p] = target
                    moved += 1
                except OSError as ex:
                    log(f"Couldn't move {name}: {ex}")
        s[key] = dst
    s["library_dir"] = new_root
    if state is not None and remap:
        for v in state.get("videos", []):
            if v.get("path") in remap:
                v["path"] = remap[v["path"]]
        for d in (state.get("done") or {}).values():
            for k in ("made", "wide"):
                if d.get(k) in remap:
                    d[k] = remap[d[k]]
        save_state(state)
    save_settings(s)
    return moved


def fmt_bytes(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024


def audio_devices():
    """Microphone names Windows exposes to FFmpeg (DirectShow). Empty list off Windows / on failure."""
    ff = find_ffmpeg()
    if not ff or os.name != "nt":
        return []
    r = subprocess.run([ff, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                       capture_output=True, text=True, creationflags=NO_WINDOW)
    out = []
    for line in (r.stderr or "").splitlines():
        m = re.search(r'"([^"]+)"\s*\(audio\)', line)
        if m and m.group(1) not in out:
            out.append(m.group(1))
    return out
