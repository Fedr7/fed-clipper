"""Breachcut UI kit: theme, rounded buttons, glowing backdrops, rounded thumbnails, avatars.

Tk has no rounded corners or transparency, so anything that needs to look modern is drawn with
Pillow (anti-aliased at 3x, then scaled down) and shown on a Canvas.
"""
import math
import os
import tkinter as tk
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageTk

import autoclip_core as core

# ---------------------------------------------------------------- theme
BG = "#0b0910"
SIDE = "#07060b"
PANEL = "#110e17"
CARD = "#17131f"
CARD_HI = "#211b2c"
LINE = "#2a2336"
TEXT = "#f4f1fb"
MUTED = "#9a92ad"
DIM = "#5e576c"
ACCENT = "#ff4d6d"
ACCENT_HI = "#ff6f8a"
ACCENT_DK = "#c9304f"
VIOLET = "#7b5cff"
GOOD = "#5ee0a0"
WARN = "#ffb454"
DISCORD = "#5865f2"
DISCORD_HI = "#6d78f5"
BADGE = {"ACE": "#ff4d6d", "4K": "#ffa94d", "3K": "#ffd43b", "2K": "#7aa2ff"}
FONT = "Segoe UI"
FONT_SEMI = "Segoe UI Semibold"
FONT_BOLD = "Segoe UI Black"


def rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _pil_font(size, bold=True):
    for f in (("seguisb.ttf" if bold else "segoeui.ttf"), "arialbd.ttf" if bold else "arial.ttf",
              core.FONT_FILE, "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


# ---------------------------------------------------------------- shapes
def rounded_rect(w, h, r, fill, outline=None, width=0, scale=3):
    """Anti-aliased rounded rectangle image (RGBA)."""
    W, H = max(1, w * scale), max(1, h * scale)
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    f = fill if isinstance(fill, tuple) else rgb(fill) + (255,)
    o = None if outline is None else (outline if isinstance(outline, tuple) else rgb(outline) + (255,))
    d.rounded_rectangle([0, 0, W - 1, H - 1], radius=r * scale, fill=f, outline=o, width=width * scale)
    return im.resize((max(1, w), max(1, h)), Image.LANCZOS)


def round_corners(im, r, bg=BG):
    """Give an image rounded corners over a solid background colour."""
    im = im.convert("RGBA")
    w, h = im.size
    mask = Image.new("L", (w * 3, h * 3), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w * 3 - 1, h * 3 - 1], radius=r * 3, fill=255)
    mask = mask.resize((w, h), Image.LANCZOS)
    base = Image.new("RGBA", (w, h), rgb(bg) + (255,))
    base.paste(im, (0, 0), mask)
    return base


def circle(im, size, ring=None, bg=SIDE):
    """Round avatar with optional coloured ring."""
    im = im.convert("RGBA").resize((size * 3, size * 3), Image.LANCZOS)
    mask = Image.new("L", (size * 3, size * 3), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size * 3 - 1, size * 3 - 1], fill=255)
    out = Image.new("RGBA", (size * 3, size * 3), rgb(bg) + (255,))
    if ring:
        ImageDraw.Draw(out).ellipse([0, 0, size * 3 - 1, size * 3 - 1], fill=rgb(ring) + (255,))
        inner = int(size * 3 * 0.88)
        off = (size * 3 - inner) // 2
        im = im.resize((inner, inner), Image.LANCZOS)
        mask = Image.new("L", (inner, inner), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, inner - 1, inner - 1], fill=255)
        out.paste(im, (off, off), mask)
    else:
        out.paste(im, (0, 0), mask)
    return out.resize((size, size), Image.LANCZOS)


def letter_avatar(name, size):
    """Fallback avatar: first letter on a gradient."""
    im = Image.new("RGB", (size, size))
    a, b = rgb(ACCENT), rgb(VIOLET)
    px = im.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * size)
            px[x, y] = tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))
    d = ImageDraw.Draw(im)
    ch = (name or "?").strip()[:1].upper() or "?"
    d.text((size / 2, size / 2), ch, font=_pil_font(int(size * 0.5)), fill=(255, 255, 255), anchor="mm")
    return im


# ---------------------------------------------------------------- backdrops
@lru_cache(maxsize=24)
def backdrop(w, h, glow=1.0, hexes=True, base=BG, spots=(("accent", 0.12, 0.05, 0.30), ("violet", 0.92, 0.0, 0.26))):
    """Dark gradient with soft pink/violet glows and a faint hex pattern (cached)."""
    w, h = max(1, int(w)), max(1, int(h))
    small = (max(1, w // 4), max(1, h // 4))
    im = Image.new("RGB", small, rgb(base))
    glow_l = Image.new("RGB", small, (0, 0, 0))
    gd = ImageDraw.Draw(glow_l)
    for name, fx, fy, fr in spots:
        col = rgb(ACCENT if name == "accent" else VIOLET)
        cx, cy, r = fx * small[0], fy * small[1], fr * max(small)
        gd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=tuple(int(c * 0.30 * glow) for c in col))
    glow_l = glow_l.filter(ImageFilter.GaussianBlur(max(small) * 0.12))
    im = ImageChops.add(im, glow_l)
    im = im.resize((w, h), Image.BICUBIC)
    if hexes:
        pat = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        pd = ImageDraw.Draw(pat)
        r = 26
        dx, dy = r * 1.732, r * 1.5
        for row in range(int(h / dy) + 2):
            for col in range(int(w / dx) + 2):
                cx = col * dx + (dx / 2 if row % 2 else 0)
                cy = row * dy
                pts = [(cx + r * math.cos(math.radians(60 * k - 30)), cy + r * math.sin(math.radians(60 * k - 30)))
                       for k in range(6)]
                pd.polygon(pts, outline=(255, 255, 255, 9))
        # fade the pattern out towards the bottom
        fade = Image.linear_gradient("L").resize((w, h)).point(lambda v: 255 - v)
        pat.putalpha(Image.composite(pat.getchannel("A"), Image.new("L", (w, h), 0), fade))
        im = Image.alpha_composite(im.convert("RGBA"), pat).convert("RGB")
    return im


def photo(im):
    return ImageTk.PhotoImage(im)


# ---------------------------------------------------------------- widgets
class RoundButton(tk.Canvas):
    """Pill button drawn with Pillow. kind: accent | normal | ghost | danger | discord | chip | chip_on"""
    STYLES = {
        "accent": (ACCENT, ACCENT_HI, "#ffffff"),
        "normal": (CARD_HI, "#2c2439", TEXT),
        "ghost": (None, CARD_HI, MUTED),
        "danger": ("#2a1820", "#3a1f2a", "#ff8a9e"),
        "discord": (DISCORD, DISCORD_HI, "#ffffff"),
        "chip": (CARD, CARD_HI, TEXT),
        "chip_on": (ACCENT, ACCENT_HI, "#ffffff"),
    }

    def __init__(self, master, text="", command=None, kind="normal", small=False, width=None, height=None,
                 bg=None, radius=None, font=None):
        self.bgc = bg or _bg_of(master)
        self.kind = kind
        self.small = small
        self.h = height or (30 if small else 40)
        self.font = font or (FONT_SEMI, 9 if small else 10)
        self.fixed_w = width
        self.radius = radius if radius is not None else self.h // 2
        self.text = text
        self.command = command
        self.enabled = True
        self.hover = False
        w = width or self._text_w(text)
        super().__init__(master, width=w, height=self.h, bg=self.bgc, highlightthickness=0, bd=0, cursor="hand2")
        self.w = w
        self._imgs = {}
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<ButtonRelease-1>", self._click)
        self.bind("<Configure>", self._resized)
        self._draw()

    def _text_w(self, text):
        f = _pil_font(13 if self.small else 14)
        tw = ImageDraw.Draw(Image.new("L", (1, 1))).textlength(text, font=f)
        return int(tw + (28 if self.small else 40))

    def _resized(self, e):
        if e.width > 4 and e.width != self.w:
            self.w = e.width
            self._imgs = {}
            self._draw()

    def _img(self, hover):
        key = (self.w, self.h, self.kind, hover, self.enabled)
        if key not in self._imgs:
            fill, hov, _ = self.STYLES[self.kind]
            col = hov if hover and self.enabled else fill
            if col is None:
                col = self.bgc
            im = rounded_rect(self.w, self.h, self.radius, col)
            if not self.enabled:
                im.putalpha(im.getchannel("A").point(lambda v: int(v * 0.45)))
            base = Image.new("RGBA", im.size, rgb(self.bgc) + (255,))
            self._imgs[key] = ImageTk.PhotoImage(Image.alpha_composite(base, im))
        return self._imgs[key]

    def _draw(self):
        self.delete("all")
        self.create_image(0, 0, image=self._img(self.hover), anchor="nw")
        fg = self.STYLES[self.kind][2] if self.enabled else DIM
        self.create_text(self.w // 2, self.h // 2, text=self.text, fill=fg, font=self.font)

    def _set_hover(self, on):
        self.hover = on
        self._draw()

    def _click(self, e):
        if self.enabled and self.command and 0 <= e.x <= self.w and 0 <= e.y <= self.h:
            self.command()

    def invoke(self):
        if self.enabled and self.command:
            return self.command()

    def config(self, **kw):
        changed = False
        if "text" in kw:
            self.text = kw.pop("text")
            if not self.fixed_w:
                nw = self._text_w(self.text)
                if nw != self.w:
                    self.w = nw
                    tk.Canvas.config(self, width=nw)
            changed = True
        if "state" in kw:
            self.enabled = kw.pop("state") != "disabled"
            changed = True
        if "kind" in kw:
            self.kind = kw.pop("kind")
            changed = True
        if "command" in kw:
            self.command = kw.pop("command")
        if kw:
            tk.Canvas.config(self, **kw)
        if changed:
            self._imgs = {}
            self._draw()

    configure = config

    def cget(self, k):
        if k == "text":
            return self.text
        return tk.Canvas.cget(self, k)


def _bg_of(w):
    try:
        return w.cget("bg")
    except Exception:
        return BG


class Toggle(tk.Canvas):
    """iOS-style switch."""

    def __init__(self, master, variable, command=None, bg=None):
        self.bgc = bg or _bg_of(master)
        super().__init__(master, width=44, height=24, bg=self.bgc, highlightthickness=0, bd=0, cursor="hand2")
        self.var = variable
        self.command = command
        self._imgs = {}
        self.bind("<ButtonRelease-1>", lambda e: self.flip())
        self._draw()

    def flip(self):
        self.var.set(not self.var.get())
        self._draw()
        if self.command:
            self.command()

    def _draw(self):
        on = bool(self.var.get())
        if on not in self._imgs:
            im = Image.new("RGBA", (44, 24), rgb(self.bgc) + (255,))
            im.alpha_composite(rounded_rect(44, 24, 12, ACCENT if on else "#3a3346"))
            k = rounded_rect(18, 18, 9, "#ffffff")
            im.alpha_composite(k, (23 if on else 3, 3))
            self._imgs[on] = ImageTk.PhotoImage(im)
        self.delete("all")
        self.create_image(0, 0, image=self._imgs[on], anchor="nw")


class CheckBox(tk.Canvas):
    """Rounded checkbox with a label (label can contain clickable link callbacks)."""

    def __init__(self, master, variable, text="", bg=None, command=None, fg=MUTED, wrap=340):
        self.bgc = bg or _bg_of(master)
        super().__init__(master, height=22, bg=self.bgc, highlightthickness=0, bd=0, cursor="hand2")
        self.var = variable
        self.command = command
        self._imgs = {}
        self.text = text
        self.fg = fg
        self.bind("<ButtonRelease-1>", lambda e: self.flip() if e.x < 26 else None)
        self._draw()

    def flip(self):
        self.var.set(not self.var.get())
        self._draw()
        if self.command:
            self.command()

    def _draw(self):
        on = bool(self.var.get())
        if on not in self._imgs:
            im = Image.new("RGBA", (18, 18), rgb(self.bgc) + (255,))
            im.alpha_composite(rounded_rect(18, 18, 5, ACCENT if on else self.bgc, outline=None if on else "#5a516a",
                                            width=0 if on else 2))
            if on:
                d = ImageDraw.Draw(im)
                d.line([(4, 9), (8, 13), (14, 5)], fill=(255, 255, 255, 255), width=2)
            self._imgs[on] = ImageTk.PhotoImage(im)
        self.delete("all")
        self.create_image(0, 2, image=self._imgs[on], anchor="nw")
        self.create_text(28, 11, text=self.text, fill=self.fg, font=(FONT, 9), anchor="w")


def center(win, w, h):
    try:
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    except Exception:
        sw, sh = 1920, 1080
    win.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")
