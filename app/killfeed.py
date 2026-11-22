"""Breachcut kill detection - spots YOUR kills by watching Siege's score feed on screen.

Every time you get a kill, Siege shows a "+100 / Kill" card on the right side of the screen with a light-blue bar
next to it. Breachcut looks at a small strip of the screen 4 times a second and counts those cards. When the count
goes up, that's a new kill. It only looks at pixels on screen (like any recorder) - it never reads the game.

Works at any resolution (everything is scaled to 1080p first) and with different HUD layouts, because it searches
for the blue bar instead of expecting it in one exact spot.
"""
import os
import subprocess

try:
    import numpy as np
except ImportError:          # installed on first use (see ensure_numpy)
    np = None

import autoclip_core as core

FPS = 4                      # frames looked at per second
LAG = 0.65                   # the card shows up ~half a second after the kill (+1 frame to confirm it)
STRIP_W, STRIP_H, STRIP_X, STRIP_Y = 360, 560, 420, 300   # strip at 1080p: x = width-420 .. width-60, y = 300..860
FRAME_BYTES = STRIP_W * STRIP_H * 3
HOLD = 1.5                   # seconds a card must be gone before it counts as gone

SENSITIVITY = {           # (min white px in the "Kill" word, min white px in "+100")
    "Low": (40, 110),
    "Normal": (25, 60),
    "High": (15, 35),
}


def ensure_numpy():
    """numpy does the pixel maths. Older installs don't have it, so add it once."""
    global np
    if np is not None:
        return True
    import sys
    py = sys.executable
    if py.lower().endswith("pythonw.exe") and os.path.exists(py[:-11] + "python.exe"):
        py = py[:-11] + "python.exe"
    try:
        subprocess.run([py, "-m", "pip", "install", "--user", "--quiet", "--disable-pip-version-check", "numpy"],
                       capture_output=True, timeout=600, creationflags=core.NO_WINDOW)
        import importlib
        import site
        import sys as _sys
        user = site.getusersitepackages()
        if user and user not in _sys.path:
            _sys.path.append(user)
        importlib.invalidate_caches()
        np = importlib.import_module("numpy")
        return True
    except Exception:
        return False


def ffmpeg_filter(fps=FPS):
    """The FFmpeg filter that turns the game picture into the small strip we look at."""
    return (f"fps={fps},scale=-2:1080:flags=bilinear,"
            f"crop={STRIP_W}:{STRIP_H}:iw-{STRIP_X}:{STRIP_Y},format=rgb24")


def count_kill_cards(a, sens="Normal"):
    """How many "+100 Kill" cards are on screen in this strip (an HxWx3 int array)."""
    need_word, need_top = SENSITIVITY.get(sens, SENSITIVITY["Normal"])
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    # the bar is a thin light-blue stripe: blue here, but not 6 px to the left or right of it
    blue = (b > 150) & (b - r > 45) & (g > 110) & (g < 240) & (r < 150)
    W = blue.shape[1]
    stripe = np.zeros_like(blue)
    stripe[:, 6:W - 6] = blue[:, 6:W - 6] & ~blue[:, 12:] & ~blue[:, :W - 12]
    cols = stripe.sum(0)
    if cols.max() < 20:
        return 0
    white = (r > 185) & (g > 185) & (b > 185)
    n = 0
    cand = [x for x in range(120, W - 6) if cols[x] >= 20 and cols[x] >= cols[x - 1] and cols[x] >= cols[x + 1]]
    seen = []
    for bx in sorted(cand, key=lambda x: -cols[x]):
        if any(abs(bx - s_) < 12 for s_ in seen):
            continue
        seen.append(bx)
        col = stripe[:, max(bx - 2, 0):bx + 3].any(1)
        y, H = 0, len(col)
        while y < H:
            if not col[y]:
                y += 1
                continue
            s = y
            while y < H and col[y]:
                y += 1
            h = y - s
            if not 30 <= h <= 72:
                continue
            word = white[s + int(h * .56):s + int(h * .92)]
            near = word[:, max(bx - int(h * .9), 0):bx - 3].sum()
            far = word[:, max(bx - int(h * 2.9), 0):max(bx - int(h * .95), 0)].sum()
            top = white[s + int(h * .05):s + int(h * .48), max(bx - int(h * 1.5), 0):bx - 3].sum()
            if near >= need_word and far <= 6 + need_word // 5 and top >= need_top:
                n += 1
    return n


class KillFeed:
    """Feed it strip frames in order; it returns the kill times (seconds on the video's clock)."""

    def __init__(self, fps=FPS, sens="Normal", start=0.0):
        self.fps, self.sens, self.start = fps, sens, start
        self.frame = 0
        self.last_raw = 0
        self.held = 0                # cards we believe are on screen
        self.low = 0                 # frames in a row with fewer cards than that

    def push(self, raw):
        t = self.start + self.frame / self.fps
        self.frame += 1
        if np is None:
            return []
        try:
            a = np.frombuffer(raw, np.uint8).reshape(STRIP_H, STRIP_W, 3).astype(np.int16)
            c = count_kill_cards(a, self.sens)
        except Exception:
            return []
        # a card has to be there for 2 frames in a row (ignores one-frame flukes), and a card can blink out for a
        # moment on a bright background, so cards only count as gone after HOLD seconds without them
        # (once a card is already showing we're sure the feed is there, so one frame is enough for the next one)
        c, self.last_raw = (c if self.held else min(c, self.last_raw)), c
        out = []
        if c > self.held:
            out = [round(max(0.0, t - LAG), 2)] * (c - self.held)
            self.held, self.low = c, 0
        elif c < self.held:
            self.low += 1
            if self.low >= int(self.fps * HOLD):
                self.held, self.low = c, 0
        else:
            self.low = 0
        return out

    @property
    def now(self):
        return self.start + self.frame / self.fps


def scan_file(path, sens="Normal", progress=None, stop=None):
    """Find the kills in a saved video. Returns kill times in seconds."""
    ff = core.find_ffmpeg()
    if not ff or not ensure_numpy():
        return []
    dur = core.probe_duration(path) or 0
    p = subprocess.Popen([ff, "-v", "error", "-i", path, "-an", "-vf", ffmpeg_filter(), "-f", "rawvideo", "-"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=core.NO_WINDOW)
    kf = KillFeed(sens=sens)
    kills = []
    try:
        while True:
            if stop and stop():
                break
            raw = p.stdout.read(FRAME_BYTES)
            if len(raw) < FRAME_BYTES:
                break
            kills += kf.push(raw)
            if progress and dur and kf.frame % 40 == 0:
                progress(min(1.0, kf.now / dur))
    finally:
        try:
            p.kill()
        except Exception:
            pass
    return kills


def streaks(kills, gap=30.0):
    """Group kills that happen close together (one play / one round)."""
    out = []
    for k in sorted(kills):
        if out and k - out[-1][-1] <= gap:
            out[-1].append(k)
        else:
            out.append([k])
    return out
