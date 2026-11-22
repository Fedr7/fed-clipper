"""
Breachcut - turn your Rainbow Six Siege Medal clips into TikToks, Shorts and YouTube edits.

Pages:  Home · Clips (everything Medal saved) · TikTok · YouTube (Shorts + Widescreen) · Auto-clip · Settings
"""
import os
import queue
import sys
import subprocess
import threading
import time
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import filedialog, messagebox, simpledialog

from PIL import Image, ImageDraw, ImageTk

import auth
import autoclip_core as core
import login
import settings_page
import updater
import ui_kit as ui
from ui_kit import (ACCENT, ACCENT_HI, BADGE, BG, CARD, CARD_HI, DIM, FONT, FONT_BOLD, FONT_SEMI, GOOD, LINE,
                    MUTED, PANEL, SIDE, TEXT, VIOLET, WARN, RoundButton, Toggle)
from editor import Editor
from player import Player, fmt as fmt_time

PAGE = 45


def startfile(path):
    try:
        os.startfile(path)  # Windows
    except AttributeError:
        subprocess.Popen(["xdg-open", path])
    except OSError as e:
        messagebox.showerror(core.APP_NAME, str(e))


def show_in_folder(path):
    if os.name == "nt":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        startfile(os.path.dirname(path))


def button(parent, text, cmd, kind="normal", small=False, width=None):
    return RoundButton(parent, text, cmd, kind=kind, small=small, width=width)


def badge_image(text, color, h=20, font_px=11):
    """Small rounded badge as an image (tk has no rounded rectangles)."""
    from PIL import ImageFont
    try:
        f = ImageFont.truetype("seguisb.ttf", font_px)
    except OSError:
        try:
            f = ImageFont.truetype(core.FONT_FILE, font_px)
        except OSError:
            f = ImageFont.load_default()
    d = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    w = int(d.textlength(text, font=f)) + 14
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dd = ImageDraw.Draw(im)
    dd.rounded_rectangle([0, 0, w - 1, h - 1], radius=h // 2, fill=color)
    dark = color in ("#ffd43b", "#ffa94d", "#5ee0a0")
    dd.text((w // 2, h // 2), text, font=f, fill=(20, 14, 26) if dark else (255, 255, 255), anchor="mm")
    return im


def compose_thumb(path, size, badges_tl=(), badge_br=None, marks_bl=()):
    """Thumbnail with Medal-style overlay badges."""
    w, h = size
    try:
        im = Image.open(path).convert("RGB")
        im = im.resize((w, h), Image.LANCZOS) if im.size != (w, h) else im
    except Exception:
        im = Image.new("RGB", (w, h), (10, 8, 14))
    im = im.convert("RGBA")
    # soft gradient at the bottom so badges read well
    grad = Image.new("L", (1, h))
    for y in range(h):
        grad.putpixel((0, y), int(max(0, (y - h * 0.55) / (h * 0.45)) * 150))
    shade = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    shade.putalpha(grad.resize((w, h)))
    im = Image.alpha_composite(im, shade)
    x = 8
    for text, color in badges_tl:
        b = badge_image(text, color)
        im.alpha_composite(b, (x, 8))
        x += b.width + 5
    if badge_br:
        b = badge_image(badge_br, "#000000")
        b.putalpha(b.getchannel("A").point(lambda v: int(v * 0.75)))
        im.alpha_composite(b, (w - b.width - 8, h - b.height - 8))
    x = 8
    for text, color in marks_bl:
        b = badge_image(text, color, h=18, font_px=10)
        im.alpha_composite(b, (x, h - b.height - 8))
        x += b.width + 4
    return ui.round_corners(im.convert("RGB"), 12, BG)


def clip_badges(c):
    kind, op, mp = core.parts(c)
    if kind:
        return [(kind, BADGE.get(kind, "#7aa2ff"))]
    if len(c["kills"]) >= 2:
        return [(f"{len(c['kills'])}K", BADGE["2K"])]
    return []


# ---------------------------------------------------------------- scrollable grid
class ScrollGrid(tk.Frame):
    def __init__(self, master, bg=BG, on_cols=None):
        super().__init__(master, bg=bg)
        self.on_cols = on_cols          # called when the number of columns that fit changes
        self._cols = None
        self._cell = 320
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.sb = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview, width=10)
        self.canvas.configure(yscrollcommand=self.sb.set)
        self.sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._resized)

    def cols(self, cell):
        """How many cards of width `cell` fit across."""
        self._cell = cell
        w = self.canvas.winfo_width()
        if w < 50:
            w = self.winfo_toplevel().winfo_width() - 230 - 440 - 60
        if w < 50:
            w = 760
        self._cols = max(1, int((w - 8) // cell))
        return self._cols

    def _resized(self, e):
        if self.on_cols and self._cols is not None and max(1, int((e.width - 8) // self._cell)) != self._cols:
            self.after_idle(self.on_cols)

    def _wheel(self, e):
        self.canvas.yview_scroll(int(-e.delta / 120), "units")

    def clear(self):
        for w in self.inner.winfo_children():
            w.destroy()
        self.canvas.yview_moveto(0)


# ---------------------------------------------------------------- pages
class Page(tk.Frame):
    title = ""
    subtitle = ""

    def __init__(self, app):
        super().__init__(app.main, bg=BG)
        self.app = app
        self.head = tk.Canvas(self, height=104, bg=BG, highlightthickness=0, bd=0)
        self.head.pack(fill="x")
        self._head_img = None
        self._t = self.head.create_text(30, 44, text=self.title, fill=TEXT, font=(FONT_BOLD, 22), anchor="w")
        self._s = self.head.create_text(31, 76, text=self.subtitle, fill=MUTED, font=(FONT, 10), anchor="w")
        self.head_right = tk.Frame(self.head, bg=BG)
        self._hr = self.head.create_window(0, 52, window=self.head_right, anchor="e")
        self.head.bind("<Configure>", self._head_resize)
        self.sub = _SubLabel(self.head, self._s)

    def _head_resize(self, e):
        w = max(200, e.width)
        im = ui.backdrop(w, 104, 0.9, hexes=True, spots=(("accent", 0.0, 0.0, 0.22), ("violet", 0.35, -0.2, 0.18)))
        self._head_img = ImageTk.PhotoImage(im)
        self.head.delete("bgimg")
        self.head.create_image(0, 0, image=self._head_img, anchor="nw", tags=("bgimg",))
        self.head.tag_lower("bgimg")
        self.head.coords(self._hr, w - 24, 52)

    def on_show(self):
        pass


class _SubLabel:
    """Lets old code call page.sub.config(text=...) on a canvas text item."""

    def __init__(self, canvas, item):
        self.c, self.i = canvas, item

    def config(self, **kw):
        if "text" in kw:
            self.c.itemconfig(self.i, text=kw["text"])

    configure = config


def search_box(parent, var, placeholder="Search"):
    """Rounded search field."""
    bgc = ui._bg_of(parent)
    c = tk.Canvas(parent, width=290, height=36, bg=bgc, highlightthickness=0, bd=0)
    im = Image.new("RGBA", (290, 36), ui.rgb(bgc) + (255,))
    im.alpha_composite(ui.rounded_rect(290, 36, 18, CARD, outline=LINE, width=1))
    c._img = ImageTk.PhotoImage(im)
    c.create_image(0, 0, image=c._img, anchor="nw")
    c.create_text(18, 18, text="⌕", fill=MUTED, font=(FONT, 12))
    e = tk.Entry(c, textvariable=var, bg=CARD, fg=TEXT, insertbackground=TEXT, relief="flat", bd=0,
                 font=(FONT, 10), width=28)
    c.create_window(34, 18, window=e, anchor="w")
    hint = c.create_text(36, 18, text=placeholder, fill=DIM, font=(FONT, 10), anchor="w")

    def upd(*_):
        c.itemconfig(hint, text="" if var.get() else placeholder)
    var.trace_add("write", upd)
    e.bind("<FocusIn>", lambda ev: c.itemconfig(hint, text=""))
    e.bind("<FocusOut>", upd)
    c.bind("<Button-1>", lambda ev: e.focus_set())
    return c


class HomePage(tk.Frame):
    """Landing page after sign-in: welcome hero, stats, ready-to-edit highlights, latest videos."""

    def __init__(self, app):
        super().__init__(app.main, bg=BG)
        self.app = app
        self.scroll = ScrollGrid(self)
        self.scroll.pack(fill="both", expand=True)
        self.body = self.scroll.inner
        self._imgs = []
        self._last_w = 0
        self.scroll.canvas.bind("<Configure>", self._resized, add="+")

    def _resized(self, e):
        if abs(e.width - self._last_w) > 40 and self.winfo_ismapped():
            self.render()

    def on_show(self):
        self.render()

    def width(self):
        w = self.scroll.canvas.winfo_width()
        if w < 100:
            w = max(700, self.app.root.winfo_width() - 230 - 440 - 30)
        return w

    def render(self):
        app = self.app
        for w in self.body.winfo_children():
            w.destroy()
        self._imgs = []
        W = self.width() - 8
        self._last_w = W + 8
        info = app.user or {}
        name = info.get("name") or app.s.get("creator") or "there"
        clips = app.clips
        aces = sum(1 for c in clips if c["top"] >= 5)
        tt = len(core.list_videos(app.state, "vertical"))
        yt = len(core.list_videos(app.state, "wide"))
        ready = app.ready_highlights()

        # ---- hero
        H = 196
        hero = tk.Canvas(self.body, width=W, height=H, bg=BG, highlightthickness=0, bd=0)
        hero.pack(fill="x", padx=(4, 0))
        bg = ui.backdrop(W, H, 1.5, spots=(("accent", 0.08, 0.1, 0.32), ("violet", 0.78, -0.1, 0.30))).convert("RGBA")
        bg = ui.round_corners(bg, 0, BG)
        ph = ImageTk.PhotoImage(bg)
        self._imgs.append(ph)
        hero.create_image(0, 0, image=ph, anchor="nw")
        av = app.avatar_image(84, ring=ACCENT, bg=None)
        if av is not None:
            avp = ImageTk.PhotoImage(av)
            self._imgs.append(avp)
            hero.create_image(40, H // 2, image=avp, anchor="w")
        x = 148
        hour = datetime.now().hour
        greet = "Good morning" if hour < 12 else ("Good afternoon" if hour < 18 else "Good evening")
        hero.create_text(x, H // 2 - 34, text=greet.upper(), fill=ACCENT, font=(FONT_SEMI, 9), anchor="w")
        hero.create_text(x, H // 2 - 4, text=f"Welcome back, {name}", fill=TEXT, font=(FONT_BOLD, 24), anchor="w")
        if ready:
            sub = f"{len(ready)} new highlight{'s' if len(ready) != 1 else ''} ready to edit."
        elif clips:
            sub = "You're all caught up. Go hit some heads."
        else:
            sub = "Play some Siege with Medal running — your clips will show up here."
        hero.create_text(x, H // 2 + 30, text=sub, fill=MUTED, font=(FONT, 11), anchor="w")
        btn = RoundButton(hero, "⚡  Make them all" if ready else "🎬  Browse clips",
                          (lambda: app.make_all(ready)) if ready else (lambda: app.show("clips")),
                          kind="accent", bg=_hero_px(bg, W - 120, H // 2))
        hero.create_window(W - 40, H // 2, window=btn, anchor="e")

        # ---- stat cards
        stats = tk.Frame(self.body, bg=BG)
        stats.pack(fill="x", padx=(4, 0), pady=(18, 4))
        items = [("Siege clips", len(clips), "🎬", "clips"), ("Aces", aces, "💀", "clips"),
                 ("TikToks made", tt, "📱", "tiktok"), ("YouTube videos", yt, "▶", "youtube")]
        gap = 14
        cw = int((W - gap * 3) / 4)
        for i, (label, val, icon, page) in enumerate(items):
            c = tk.Canvas(stats, width=cw, height=96, bg=BG, highlightthickness=0, bd=0, cursor="hand2")
            c.grid(row=0, column=i, padx=(0 if i == 0 else gap, 0))
            im = Image.new("RGBA", (cw, 96), ui.rgb(BG) + (255,))
            im.alpha_composite(ui.rounded_rect(cw, 96, 16, CARD, outline=LINE, width=1))
            chip = ui.rounded_rect(34, 34, 10, ACCENT if i == 1 else "#241d31")
            im.alpha_composite(chip, (18, 18))
            p2 = ImageTk.PhotoImage(im)
            self._imgs.append(p2)
            c.create_image(0, 0, image=p2, anchor="nw")
            c.create_text(35, 35, text=icon, fill="#ffffff", font=(FONT, 12))
            c.create_text(cw - 20, 36, text=f"{val:,}", fill=TEXT, font=(FONT_BOLD, 20), anchor="e")
            c.create_text(18, 74, text=label, fill=MUTED, font=(FONT, 10), anchor="w")
            c.bind("<Button-1>", lambda e, p=page: app.show(p))

        # ---- ready to edit
        self._section("Ready to edit", f"{len(ready)} new" if ready else "",
                      ("Make all", lambda: app.make_all(ready)) if ready else None)
        row = tk.Frame(self.body, bg=BG)
        row.pack(fill="x", padx=(4, 0))
        if not ready:
            self._empty(row, "No new highlights right now. Turn on Auto-clip and they'll be edited for you.",
                        "⚡  Auto-clip", lambda: app.show("auto"))
        else:
            n = max(1, int(W // 250))
            tw = int((W - 12 * (n - 1)) / n)
            for i, c in enumerate(ready[:n]):
                self._clip_tile(row, c, tw, i)

        # ---- latest videos
        vids = core.list_videos(app.state)
        self._section("Your latest videos", f"{len(vids)} total" if vids else "",
                      ("See all", lambda: app.show("tiktok")) if vids else None)
        row2 = tk.Frame(self.body, bg=BG)
        row2.pack(fill="x", padx=(4, 0), pady=(0, 30))
        if not vids:
            self._empty(row2, "Videos you make show up here — ready to post.", "🎬  Pick a clip",
                        lambda: app.show("clips"))
        else:
            n = max(1, int(W // 150))
            tw = int((W - 12 * (n - 1)) / n)
            for i, v in enumerate(vids[:n]):
                self._video_tile(row2, v, tw, i)

    def _section(self, title, meta="", action=None):
        f = tk.Frame(self.body, bg=BG)
        f.pack(fill="x", padx=(6, 0), pady=(26, 10))
        tk.Label(f, text=title, fg=TEXT, bg=BG, font=(FONT_BOLD, 14)).pack(side="left")
        if meta:
            tk.Label(f, text="  " + meta, fg=MUTED, bg=BG, font=(FONT, 10)).pack(side="left", pady=(4, 0))
        if action:
            RoundButton(f, action[0], action[1], kind="ghost", small=True).pack(side="right")

    def _empty(self, parent, text, btn_text, cmd):
        W = self.width() - 8
        c = tk.Canvas(parent, width=W, height=110, bg=BG, highlightthickness=0, bd=0)
        c.pack()
        im = Image.new("RGBA", (W, 110), ui.rgb(BG) + (255,))
        im.alpha_composite(ui.rounded_rect(W, 110, 16, PANEL, outline=LINE, width=1))
        ph = ImageTk.PhotoImage(im)
        self._imgs.append(ph)
        c.create_image(0, 0, image=ph, anchor="nw")
        c.create_text(28, 55, text=text, fill=MUTED, font=(FONT, 11), anchor="w")
        c.create_window(W - 24, 55, window=RoundButton(c, btn_text, cmd, kind="normal", bg=PANEL), anchor="e")

    def _clip_tile(self, parent, c, tw, i):
        th = int(tw * 9 / 16)
        f = tk.Frame(parent, bg=BG, cursor="hand2")
        f.grid(row=0, column=i, padx=(0 if i == 0 else 12, 0), sticky="n")
        lab = tk.Label(f, bg=BG, width=tw, height=th, bd=0)
        lab.pack()
        tp = core.thumb_path(c)
        if os.path.exists(tp):
            dur = c.get("dur") or 0
            ph = ImageTk.PhotoImage(compose_thumb(tp, (tw, th), clip_badges(c), None))
            self._imgs.append(ph)
            lab.config(image=ph, width=tw, height=th)
        else:
            self.app.want_thumb(("clip", c))
        kind, op, mp = core.parts(c)
        tk.Label(f, text=" · ".join(x for x in (op, mp) if x) or "Clip", fg=TEXT, bg=BG, font=(FONT_SEMI, 10),
                 anchor="w").pack(fill="x", pady=(6, 0))
        tk.Label(f, text=core.clip_time(c).strftime("%a %b %d · %I:%M %p").replace(" 0", " "), fg=MUTED, bg=BG,
                 font=(FONT, 9), anchor="w").pack(fill="x")
        for w in [f] + list(f.winfo_children()):
            w.bind("<Button-1>", lambda e, c=c: (self.app.show("clips"), self.app.select_clip(c)))

    def _video_tile(self, parent, v, tw, i):
        vertical = v.get("fmt") != "wide"
        th = int(tw * 16 / 9) if vertical else int(tw * 9 / 16)
        th = min(th, 250)
        f = tk.Frame(parent, bg=BG, cursor="hand2")
        f.grid(row=0, column=i, padx=(0 if i == 0 else 12, 0), sticky="n")
        lab = tk.Label(f, bg=BG, width=tw, height=th, bd=0)
        lab.pack()
        tp = self.app.cached_video_thumb(v)
        if tp:
            secs = v.get("seconds") or 0
            badge = [("YT", "#ff4d4d")] if v.get("fmt") == "wide" else [("TT", "#7aa2ff")]
            ph = ImageTk.PhotoImage(compose_thumb(tp, (tw, th), badge, fmt_time(secs).split(".")[0] if secs else None))
            self._imgs.append(ph)
            lab.config(image=ph, width=tw, height=th)
        else:
            self.app.want_thumb(("video", v))
        tk.Label(f, text=VideosPage.short_title(v)[:34], fg=TEXT, bg=BG, font=(FONT_SEMI, 9), anchor="w",
                 wraplength=tw).pack(fill="x", pady=(6, 0))
        page = "youtube" if v.get("fmt") == "wide" else "tiktok"
        for w in [f] + list(f.winfo_children()):
            w.bind("<Button-1>", lambda e, v=v, p=page: (self.app.show(p), self.app.select_video(v)))


def _hero_px(im, x, y):
    """Colour of the backdrop under a widget (so pill corners blend in)."""
    try:
        r, g, b = im.convert("RGB").getpixel((max(0, min(im.width - 1, x)), max(0, min(im.height - 1, y))))
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return BG


class ClipsPage(Page):
    title = "Clips"
    subtitle = "Everything Medal saved from Siege. Click a clip to watch it or turn it into a TikTok / YouTube edit."
    FILTERS = ("All", "Aces", "4K+", "3K+", "Multi-kills", "Edited")

    def __init__(self, app):
        super().__init__(app)
        button(self.head_right, "⟳  Refresh", app.reload, "normal", small=True).pack(side="right")
        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=28, pady=(2, 10))
        self.filter = "All"
        self.fbtn = {}
        for f in self.FILTERS:
            b = RoundButton(bar, f, lambda f=f: self.set_filter(f), kind="chip", small=True)
            b.pack(side="left", padx=(0, 6))
            self.fbtn[f] = b
        self.search = tk.StringVar()
        search_box(bar, self.search, "Search operator, map or day").pack(side="right")
        self.search.trace_add("write", lambda *a: self.apply())
        self.count = tk.Label(bar, text="", fg=MUTED, bg=BG, font=(FONT, 9))
        self.count.pack(side="right", padx=12)
        self.grid = ScrollGrid(self, on_cols=self.render)
        self.grid.pack(fill="both", expand=True, padx=(22, 6), pady=(0, 6))
        self.empty = tk.Label(self.grid.inner, text="Loading your clips…", fg=MUTED, bg=BG, font=(FONT, 11))
        self.shown, self.limit, self.cards = [], PAGE, {}
        self._paint()

    def set_filter(self, f):
        self.filter = f
        self._paint()
        self.apply()

    def _paint(self):
        for f, b in self.fbtn.items():
            b.config(kind="chip_on" if f == self.filter else "chip")

    def apply(self):
        q = self.search.get().strip().lower()
        f = self.filter
        out = []
        for c in self.app.clips:
            if f == "Aces" and c["top"] < 5: continue
            if f == "4K+" and c["top"] < 4: continue
            if f == "3K+" and c["top"] < 3: continue
            if f == "Multi-kills" and len(c["kills"]) < 2: continue
            if f == "Edited" and not self.app.made_any(c): continue
            if q:
                hay = " ".join(core.parts(c)[1:]) + " " + c["title"] + " " + core.clip_time(c).strftime("%b %d %A")
                if q not in hay.lower(): continue
            out.append(c)
        self.shown, self.limit = out, PAGE
        self.count.config(text=f"{len(out)} clips")
        self.render()

    def render(self):
        self.grid.clear()
        self.cards = {}
        self.app.drain_thumbs()
        inner = self.grid.inner
        if not self.shown:
            msg = "No clips match." if self.app.clips else self.app.clips_msg
            tk.Label(inner, text=msg, fg=MUTED, bg=BG, font=(FONT, 11), justify="left").grid(row=0, column=0, padx=8, pady=30, sticky="w")
            return
        cols = self.grid.cols(304 + 16)
        for i, c in enumerate(self.shown[:self.limit]):
            self._card(c, i // cols, i % cols)
        if len(self.shown) > self.limit:
            button(inner, f"Show more  ({len(self.shown) - self.limit} left)", self.more).grid(
                row=self.limit // cols + 1, column=0, columnspan=cols, pady=16)

    def more(self):
        self.limit += PAGE
        self.render()

    def _card(self, c, r, col):
        f = tk.Frame(self.grid.inner, bg=BG, cursor="hand2", highlightthickness=2, highlightbackground=BG,
                     highlightcolor=ACCENT)
        f.grid(row=r, column=col, padx=6, pady=6, sticky="n")
        img = tk.Label(f, bg=BG, width=304, height=171, bd=0)
        img.pack(padx=3, pady=3)
        kind, op, mp = core.parts(c)
        name = " · ".join(x for x in (op, mp) if x) or ("Clip" if not kind else kind)
        tk.Label(f, text=name, fg=TEXT, bg=BG, font=(FONT_SEMI, 10), anchor="w").pack(fill="x", padx=6, pady=(4, 0))
        tk.Label(f, text=core.clip_time(c).strftime("%a %b %d · %I:%M %p").replace(" 0", " "), fg=MUTED, bg=BG,
                 font=(FONT, 9), anchor="w").pack(fill="x", padx=6, pady=(0, 8))
        widgets = [f] + list(f.winfo_children())
        for w in widgets:
            w.bind("<Button-1>", lambda e, c=c: self.app.select_clip(c))
            w.bind("<Double-Button-1>", lambda e, c=c: self.app.open_editor(c))
            w.bind("<Enter>", lambda e, f=f: self._hover(f, True))
            w.bind("<Leave>", lambda e, f=f: self._hover(f, False))
        self.cards[c["id"]] = (f, img, c)
        tp = core.thumb_path(c)
        if os.path.exists(tp):
            self.set_thumb(c["id"], tp)
        else:
            self.app.want_thumb(("clip", c))
        if self.app.sel_kind == "clip" and self.app.sel and self.app.sel["id"] == c["id"]:
            f.config(highlightbackground=ACCENT)

    def _hover(self, f, on):
        sel = any(v[0] is f and self.app.sel_kind == "clip" and self.app.sel and self.app.sel["id"] == v[2]["id"]
                  for v in self.cards.values())
        f.config(highlightbackground=ACCENT if sel else (LINE if on else BG))

    def set_thumb(self, cid, path):
        card = self.cards.get(cid)
        if not card:
            return
        c = card[2]
        marks = []
        d = self.app.state["done"].get(c["id"]) or {}
        if d.get("made") and os.path.exists(d["made"]):
            marks.append(("TikTok ✓", "#5ee0a0"))
        if d.get("wide") and os.path.exists(d["wide"]):
            marks.append(("YouTube ✓", "#5ee0a0"))
        dur = c.get("dur") or 0
        im = compose_thumb(path, (304, 171), clip_badges(c), fmt_time(dur).split(".")[0] if dur else None, marks)
        ph = ImageTk.PhotoImage(im)
        self.app.images[("clip", cid)] = ph
        card[1].config(image=ph, width=304, height=171)

    def mark_selected(self):
        for cid, (f, _, c) in self.cards.items():
            on = self.app.sel_kind == "clip" and self.app.sel and self.app.sel["id"] == cid
            f.config(highlightbackground=ACCENT if on else BG)


class VideosPage(Page):
    """Grid of finished videos for one format."""
    fmt = "vertical"
    empty_text = ""

    def __init__(self, app):
        super().__init__(app)
        self.grid = ScrollGrid(self, on_cols=self.render)
        self.cards = {}

    def videos(self):
        return core.list_videos(self.app.state, self.fmt)

    def on_show(self):
        self.render()

    def render(self):
        self.grid.clear()
        self.cards = {}
        vids = self.videos()
        inner = self.grid.inner
        self.sub.config(text=self.subtitle_for(len(vids)))
        if not vids:
            box = tk.Frame(inner, bg=BG)
            box.grid(row=0, column=0, padx=8, pady=40, sticky="w")
            tk.Label(box, text=self.empty_text, fg=MUTED, bg=BG, font=(FONT, 11), justify="left").pack(anchor="w")
            button(box, "Go to Clips", lambda: self.app.show("clips"), "accent").pack(anchor="w", pady=12)
            return
        vertical = self.fmt == "vertical"
        size = (162, 288) if vertical else (304, 171)
        cols = self.grid.cols(size[0] + 16)
        for i, v in enumerate(vids[:120]):
            f = tk.Frame(inner, bg=BG, cursor="hand2", highlightthickness=2, highlightbackground=BG)
            f.grid(row=i // cols, column=i % cols, padx=6, pady=6, sticky="n")
            img = tk.Label(f, bg=BG, width=size[0], height=size[1], bd=0)
            img.pack(padx=3, pady=3)
            tk.Label(f, text=self.short_title(v), fg=TEXT, bg=BG, font=(FONT_SEMI, 9), anchor="w",
                     wraplength=size[0] - 8, justify="left").pack(fill="x", padx=5, pady=(4, 0))
            when = v.get("made_at", "")[:16].replace("T", " ")
            try:
                when = datetime.fromisoformat(v["made_at"]).strftime("%b %d · %I:%M %p").replace(" 0", " ")
            except Exception:
                pass
            tk.Label(f, text=when, fg=MUTED, bg=BG, font=(FONT, 8), anchor="w").pack(fill="x", padx=5, pady=(0, 8))
            for w in [f] + list(f.winfo_children()):
                w.bind("<Button-1>", lambda e, v=v: self.app.select_video(v))
                w.bind("<Enter>", lambda e, f=f: f.config(highlightbackground=LINE))
                w.bind("<Leave>", lambda e, f=f, v=v: f.config(
                    highlightbackground=ACCENT if self.app.sel_kind == "video" and self.app.sel is not None
                    and self.app.sel.get("path") == v["path"] else BG))
            self.cards[v["path"]] = (f, img, v, size)
            tp = self.app.cached_video_thumb(v)
            if tp:
                self.set_thumb(v["path"], tp)
            else:
                self.app.want_thumb(("video", v))
        self.mark_selected()

    @staticmethod
    def short_title(v):
        t = v.get("title") or os.path.basename(v["path"])
        t = t.replace(" (YouTube)", "").replace(" (Fed)", "")
        return t[:60]

    def subtitle_for(self, n):
        return self.subtitle

    def set_thumb(self, path, tp):
        card = self.cards.get(path)
        if not card:
            return
        v, size = card[2], card[3]
        badges = [("MONTAGE", "#7aa2ff")] if v.get("kind") == "montage" else []
        secs = v.get("seconds") or 0
        im = compose_thumb(tp, size, badges, fmt_time(secs).split(".")[0] if secs else None)
        ph = ImageTk.PhotoImage(im)
        self.app.images[("video", path)] = ph
        card[1].config(image=ph, width=size[0], height=size[1])

    def mark_selected(self):
        for p, (f, _, v, _) in self.cards.items():
            on = self.app.sel_kind == "video" and self.app.sel is not None and self.app.sel.get("path") == p
            f.config(highlightbackground=ACCENT if on else BG)


class TikTokPage(VideosPage):
    title = "TikTok"
    fmt = "vertical"
    empty_text = ("No TikToks yet.\nPick a clip and hit  ★ Make TikTok,  or turn on Auto-clip and play some Siege.")

    def __init__(self, app):
        super().__init__(app)
        button(self.head_right, "📂  Open folder", lambda: app.open_dir(app.s["output_dir"]), small=True).pack(side="right")
        self.grid.pack(fill="both", expand=True, padx=(22, 6), pady=(12, 6))

    def subtitle_for(self, n):
        return f"{n} vertical video{'s' if n != 1 else ''} ready to post  ·  also work as YouTube Shorts and Reels"


class YouTubePage(VideosPage):
    title = "YouTube"

    def __init__(self, app):
        super().__init__(app)
        self.mode = "wide"
        button(self.head_right, "📂  Open folder", self.open_folder, small=True).pack(side="right")
        seg = tk.Frame(self, bg=BG)
        seg.pack(fill="x", padx=28, pady=(10, 0))
        self.segb = {}
        for key, text in (("wide", "▭  Widescreen"), ("vertical", "▯  Shorts")):
            b = RoundButton(seg, text, lambda k=key: self.set_mode(k), kind="chip", small=True)
            b.pack(side="left", padx=(0, 6))
            self.segb[key] = b
        self.grid.pack(fill="both", expand=True, padx=(22, 6), pady=(12, 6))
        self._paint()

    @property
    def fmt(self):
        return self.mode

    @property
    def empty_text(self):
        if self.mode == "wide":
            return ("No widescreen videos yet.\nPick a clip and hit  ▭ Make YouTube video,  or switch on widescreen in Auto-clip.")
        return "No Shorts yet. Your TikToks show up here too — they're the same vertical format."

    def set_mode(self, m):
        self.mode = m
        self._paint()
        self.render()

    def _paint(self):
        for k, b in self.segb.items():
            b.config(kind="chip_on" if k == self.mode else "chip")

    def open_folder(self):
        self.app.open_dir(self.app.s["youtube_dir"] if self.mode == "wide" else self.app.s["output_dir"])

    def subtitle_for(self, n):
        if self.mode == "wide":
            return f"{n} widescreen video{'s' if n != 1 else ''} (1920×1080) for regular YouTube uploads"
        return f"{n} Short{'s' if n != 1 else ''} — same vertical videos as your TikToks"


class AutoPage(Page):
    title = "Auto-clip"
    subtitle = "Breachcut watches Medal while you play and edits your best rounds by itself."

    def __init__(self, app):
        super().__init__(app)
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=28, pady=12)
        card = tk.Frame(body, bg=CARD)
        card.pack(fill="x")
        self.state_l = tk.Label(card, text="", fg=TEXT, bg=CARD, font=(FONT, 16, "bold"))
        self.state_l.pack(side="left", padx=20, pady=18)
        self.toggle_b = button(card, "", app.toggle_auto, "accent")
        self.toggle_b.pack(side="right", padx=20)

        opts = tk.Frame(body, bg=BG)
        opts.pack(fill="x", pady=(18, 0))
        tk.Label(opts, text="Make these automatically", fg=TEXT, bg=BG, font=(FONT, 12, "bold")).grid(
            row=0, column=0, sticky="w", columnspan=3)
        self.fv = {}
        self.toggles = []
        for i, (key, text) in enumerate((("vertical", "TikTok / Shorts  (vertical)"),
                                         ("wide", "YouTube widescreen  (16:9)"))):
            v = tk.BooleanVar(value=key in app.s.get("auto_formats", []))
            self.fv[key] = v
            cell = tk.Frame(opts, bg=BG)
            cell.grid(row=1, column=i, sticky="w", padx=(0, 28), pady=8)
            tg = Toggle(cell, v, command=self.save_formats, bg=BG)
            tg.pack(side="left")
            self.toggles.append(tg)
            tk.Label(cell, text="  " + text, fg=TEXT, bg=BG, font=(FONT, 10)).pack(side="left")
        tk.Label(opts, text="When I get", fg=MUTED, bg=BG, font=(FONT, 10)).grid(row=2, column=0, sticky="w", pady=6)
        self.mk = tk.StringVar(value={3: "3K or better", 4: "4K or better", 5: "Aces only"}.get(app.s["min_kills"], "3K or better"))
        om = tk.OptionMenu(opts, self.mk, "3K or better", "4K or better", "Aces only", command=lambda *_: self.save_opts())
        om.config(bg=CARD_HI, fg=TEXT, relief="flat", bd=0, highlightthickness=0, activebackground="#342b45", font=(FONT, 10))
        om.grid(row=2, column=1, sticky="w")
        tk.Label(opts, text="Check every (minutes)", fg=MUTED, bg=BG, font=(FONT, 10)).grid(row=3, column=0, sticky="w", pady=6)
        self.ev = tk.StringVar(value=str(app.s["watch_minutes"]))
        e = tk.Entry(opts, textvariable=self.ev, width=6, bg=CARD, fg=TEXT, insertbackground=TEXT, relief="flat",
                     font=(FONT, 10))
        e.grid(row=3, column=1, sticky="w", ipady=4)
        e.bind("<FocusOut>", lambda ev: self.save_opts())
        tk.Label(opts, text="Uses your default look (set it in the editor with “Save as my default look”).",
                 fg=DIM, bg=BG, font=(FONT, 9)).grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))

        tk.Label(body, text="Make some now", fg=TEXT, bg=BG, font=(FONT, 12, "bold")).pack(anchor="w", pady=(22, 6))
        row = tk.Frame(body, bg=BG)
        row.pack(fill="x")
        button(row, "⏱  From my last X days…", app.older).pack(side="left")
        button(row, "⚡  Aim montage (TikTok)", lambda: app.aim_short("vertical")).pack(side="left", padx=8)
        button(row, "⚡  Aim montage (YouTube)", lambda: app.aim_short("wide")).pack(side="left")

        tk.Label(body, text="Activity", fg=TEXT, bg=BG, font=(FONT, 12, "bold")).pack(anchor="w", pady=(22, 6))
        self.log = tk.Label(body, text="Nothing yet.", fg=MUTED, bg=CARD, font=(FONT, 9), justify="left", anchor="nw",
                            padx=14, pady=12)
        self.log.pack(fill="both", expand=True, pady=(0, 10))
        self.refresh()

    def save_formats(self):
        self.app.s["auto_formats"] = [k for k, v in self.fv.items() if v.get()]
        core.save_settings(self.app.s)

    def save_opts(self):
        self.app.s["min_kills"] = {"3K or better": 3, "4K or better": 4, "Aces only": 5}[self.mk.get()]
        try:
            self.app.s["watch_minutes"] = max(1, int(self.ev.get()))
        except ValueError:
            pass
        core.save_settings(self.app.s)

    def refresh(self):
        on = self.app.watching
        self.state_l.config(text=("●  Auto-clip is ON — keep Medal running and play" if on else "○  Auto-clip is off"),
                            fg=GOOD if on else TEXT)
        self.toggle_b.config(text="Turn off" if on else "Turn on")
        self.log.config(text="\n".join(self.app.activity[-14:]) or "Nothing yet.")

    def on_show(self):
        s = self.app.s
        for k, v in self.fv.items():
            v.set(k in (s.get("auto_formats") or []))
        self.mk.set({3: "3K or better", 4: "4K or better", 5: "Aces only"}.get(s["min_kills"], "3K or better"))
        self.ev.set(str(s["watch_minutes"]))
        for tg in self.toggles:
            tg._draw()
        self.refresh()


SettingsPage = settings_page.make_settings_page(Page, ScrollGrid)


# ---------------------------------------------------------------- sidebar widgets
class NavItem(tk.Canvas):
    W, H = 206, 42

    def __init__(self, master, icon, text, command):
        super().__init__(master, width=self.W, height=self.H, bg=SIDE, highlightthickness=0, bd=0, cursor="hand2")
        self.icon, self.text, self.command = icon, text, command
        self.active = self.hover = False
        self._imgs = {}
        self.bind("<Enter>", lambda e: self._h(True))
        self.bind("<Leave>", lambda e: self._h(False))
        self.bind("<ButtonRelease-1>", lambda e: self.command())
        self._draw()

    def _h(self, on):
        self.hover = on
        self._draw()

    def set_active(self, on):
        self.active = on
        self._draw()

    def _draw(self):
        key = "a" if self.active else ("h" if self.hover else "n")
        if key not in self._imgs:
            im = Image.new("RGBA", (self.W, self.H), ui.rgb(SIDE) + (255,))
            if key != "n":
                im.alpha_composite(ui.rounded_rect(self.W, self.H, 12, "#1c1626" if key == "a" else "#130f1a"))
            if key == "a":
                im.alpha_composite(ui.rounded_rect(4, 20, 2, ACCENT), (0, 11))
            self._imgs[key] = ImageTk.PhotoImage(im)
        self.delete("all")
        self.create_image(0, 0, image=self._imgs[key], anchor="nw")
        col = TEXT if (self.active or self.hover) else MUTED
        self.create_text(28, self.H // 2, text=self.icon, fill=ACCENT if self.active else col, font=(FONT, 12))
        self.create_text(54, self.H // 2, text=self.text, fill=col, font=(FONT_SEMI, 11), anchor="w")


class ProfileChip(tk.Canvas):
    W, H = 206, 52

    def __init__(self, master, app):
        super().__init__(master, width=self.W, height=self.H, bg=SIDE, highlightthickness=0, bd=0, cursor="hand2")
        self.app = app
        self.hover = False
        self.bind("<Enter>", lambda e: self._h(True))
        self.bind("<Leave>", lambda e: self._h(False))
        self.bind("<ButtonRelease-1>", lambda e: app.account_menu(e.x_root, e.y_root - 150))
        self.redraw()

    def _h(self, on):
        self.hover = on
        self.redraw()

    def redraw(self):
        im = Image.new("RGBA", (self.W, self.H), ui.rgb(SIDE) + (255,))
        if self.hover:
            im.alpha_composite(ui.rounded_rect(self.W, self.H, 12, "#130f1a"))
        av = self.app.avatar_image(36, bg="#130f1a" if self.hover else SIDE)
        im.alpha_composite(av.convert("RGBA"), (8, 8))
        self._img = ImageTk.PhotoImage(im)
        self.delete("all")
        self.create_image(0, 0, image=self._img, anchor="nw")
        u = self.app.user
        name = (u or {}).get("name") or "Guest"
        sub = ("Online" if self.app.online else "Offline") if u else "Not signed in"
        self.create_text(54, 18, text=name[:18], fill=TEXT, font=(FONT_SEMI, 10), anchor="w")
        self.create_oval(54, 31, 60, 37, fill=GOOD if (u and self.app.online) else DIM, width=0)
        self.create_text(65, 34, text=sub, fill=MUTED, font=(FONT, 8), anchor="w")
        self.create_text(self.W - 14, 26, text="⋯", fill=MUTED, font=(FONT, 12))


# ---------------------------------------------------------------- the app
class App:
    def __init__(self, root, session=None, online=True):
        self.root = root
        self.session = session
        self.online = online
        self.user = auth.user_info(session) if session else None
        self._avatar_src = None
        root.title(core.APP_NAME)
        root.configure(bg=BG)
        root.geometry("1440x880")
        root.minsize(1240, 740)
        for ico in ("breachcut.ico", "fed.ico"):
            p = os.path.join(core.ASSETS, ico)
            if os.path.exists(p):
                try:
                    root.iconbitmap(p)
                    break
                except tk.TclError:
                    pass
        self.s = core.load_settings()
        if not self.s.get("only_new_after"):
            self.s["only_new_after"] = datetime.now().isoformat(timespec="seconds")
            core.save_settings(self.s)
        self.state = core.load_state()
        core.apply_settings(self.s)
        self.q = queue.Queue()
        self.jobs = queue.Queue()
        self.thumbs = queue.LifoQueue()
        self.enc = None
        self.clips = []
        self.clips_msg = "Loading your clips…"
        self.images = {}
        self.sel = None
        self.sel_kind = None
        self.watching = False
        self.activity = []
        self.patch = None
        if self.user and not self.s.get("onboarded"):
            # signed in with Discord: use their name for the intro and find Medal automatically
            self.s["creator"] = self.s.get("creator") or self.user["name"]
            self.s["medal_dir"] = core.find_medal_dir()
            self.s["onboarded"] = os.path.isdir(os.path.join(self.s["medal_dir"], "Databases"))
            core.save_settings(self.s)
        self._build()
        threading.Thread(target=self._job_worker, daemon=True).start()
        threading.Thread(target=self._thumb_worker, daemon=True).start()
        self.root.after(100, self._pump)
        self.root.bind_all("<MouseWheel>", self._wheel)
        self.show("home")
        self.reload()
        threading.Thread(target=self._check_update, daemon=True).start()
        threading.Thread(target=self._storage_check, daemon=True).start()
        if self.session:
            threading.Thread(target=self._sync_profile, daemon=True).start()
        if not self.s.get("onboarded"):
            self.root.after(400, self.onboarding)

    # ---------------------------------------------------------------- layout
    def _build(self):
        side = tk.Frame(self.root, bg=SIDE, width=230)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        self.side = side
        wm = os.path.join(core.ASSETS, "breachcut_wordmark.png")
        if os.path.exists(wm):
            im = Image.open(wm)
            im = im.resize((int(im.width * 30 / im.height), 30), Image.LANCZOS)
            self._wm = ImageTk.PhotoImage(im)
            tk.Label(side, image=self._wm, bg=SIDE).pack(anchor="w", padx=22, pady=(26, 30))
        else:
            tk.Label(side, text="BREACHCUT", fg=TEXT, bg=SIDE, font=(FONT_BOLD, 16)).pack(anchor="w", padx=22, pady=(26, 30))
        tk.Label(side, text="MENU", fg=DIM, bg=SIDE, font=(FONT_SEMI, 8)).pack(anchor="w", padx=26, pady=(0, 6))
        self.nav = {}
        for key, icon, text in (("home", "⌂", "Home"), ("clips", "🎬", "Clips"), ("tiktok", "📱", "TikTok"),
                                ("youtube", "▶", "YouTube"), ("auto", "⚡", "Auto-clip"), ("settings", "⚙", "Settings")):
            item = NavItem(side, icon, text, lambda k=key: self.show(k))
            item.pack(fill="x", padx=12, pady=1)
            self.nav[key] = item
        bottom = tk.Frame(side, bg=SIDE)
        bottom.pack(side="bottom", fill="x", padx=12, pady=14)
        self.auto_pill = tk.Canvas(bottom, width=206, height=40, bg=SIDE, highlightthickness=0, bd=0, cursor="hand2")
        self.auto_pill.pack(fill="x")
        self.auto_pill.bind("<ButtonRelease-1>", lambda e: self.toggle_auto())
        tk.Frame(bottom, bg=LINE, height=1).pack(fill="x", pady=12)
        self.profile = ProfileChip(bottom, self)
        self.profile.pack(fill="x")
        self._paint_pill()

        # viewer panel (right)
        self.viewer = tk.Frame(self.root, bg=PANEL, width=440)
        self.viewer.pack(side="right", fill="y")
        self.viewer.pack_propagate(False)
        self.player = Player(self.viewer, 400, 225, bg=PANEL, fg=TEXT, accent=ACCENT)
        self.player.pack(padx=20, pady=(22, 12))
        self.v_title = tk.Label(self.viewer, text="Pick a clip", fg=TEXT, bg=PANEL, font=(FONT, 15, "bold"),
                                wraplength=396, justify="left", anchor="w")
        self.v_title.pack(fill="x", padx=22)
        self.v_info = tk.Label(self.viewer, text="Click any clip or video to watch it here.", fg=MUTED, bg=PANEL,
                               font=(FONT, 10), wraplength=396, justify="left", anchor="w")
        self.v_info.pack(fill="x", padx=22, pady=(4, 12))
        self.v_actions = tk.Frame(self.viewer, bg=PANEL)
        self.v_actions.pack(fill="x", padx=20)
        self.v_status = tk.Label(self.viewer, text="", fg=GOOD, bg=PANEL, font=(FONT, 10), wraplength=396,
                                 justify="left", anchor="w")
        self.v_status.pack(fill="x", padx=22, pady=10)

        # main area
        self.main = tk.Frame(self.root, bg=BG)
        self.main.pack(side="left", fill="both", expand=True)
        self.banner = tk.Frame(self.main, bg=ACCENT)
        self.statusbar = tk.Label(self.main, text="", fg=MUTED, bg=BG, font=(FONT, 9), anchor="w")
        self.statusbar.pack(side="bottom", fill="x", padx=28, pady=(0, 10))
        self.pages = {"home": HomePage(self), "clips": ClipsPage(self), "tiktok": TikTokPage(self),
                      "youtube": YouTubePage(self), "auto": AutoPage(self), "settings": SettingsPage(self)}
        self.current = None

    def _wheel(self, e):
        w = e.widget
        while w is not None and not isinstance(w, str):
            if isinstance(w, ScrollGrid):
                w._wheel(e)
                return
            w = getattr(w, "master", None)

    def show(self, key):
        if self.current:
            self.pages[self.current].pack_forget()
        self.current = key
        self.pages[key].pack(fill="both", expand=True)
        for k, item in self.nav.items():
            item.set_active(k == key)
        self.pages[key].on_show()

    def _paint_pill(self):
        on = self.watching
        c = self.auto_pill
        im = Image.new("RGBA", (206, 40), ui.rgb(SIDE) + (255,))
        im.alpha_composite(ui.rounded_rect(206, 40, 12, "#11291c" if on else CARD, outline="#1f5a3a" if on else LINE,
                                           width=1))
        c._img = ImageTk.PhotoImage(im)
        c.delete("all")
        c.create_image(0, 0, image=c._img, anchor="nw")
        c.create_oval(16, 16, 24, 24, fill=GOOD if on else DIM, width=0)
        c.create_text(34, 20, text="Auto-clip is on" if on else "Auto-clip is off", fill=GOOD if on else MUTED,
                      font=(FONT_SEMI, 9), anchor="w")
        c.create_text(192, 20, text="ON" if on else "OFF", fill=GOOD if on else DIM, font=(FONT_SEMI, 8), anchor="e")

    def status(self, text, log=False):
        self.statusbar.config(text=text)
        if log:
            self.activity.append(f"{datetime.now():%I:%M %p}   {text}")
            if self.current == "auto":
                self.pages["auto"].refresh()

    def open_dir(self, d):
        os.makedirs(d, exist_ok=True)
        startfile(d)

    def out_dir(self, fmt):
        return self.s["youtube_dir"] if fmt == "wide" else self.s["output_dir"]

    def brand(self):
        return core.brand_assets(self.s)

    # ---------------------------------------------------------------- data
    def reload(self):
        self.status("Reading Medal's clips…")

        def work():
            try:
                clips = core.read_clips(self.s["medal_dir"])
                clips = [c for c in clips if os.path.exists(c["path"])]
                self.q.put(("clips", clips))
            except Exception as e:
                self.q.put(("noclips", str(e)))
        threading.Thread(target=work, daemon=True).start()

    def made(self, c, fmt="vertical"):
        d = self.state["done"].get(c["id"]) or {}
        p = d.get("wide" if fmt == "wide" else "made")
        return p if p and os.path.exists(p) else None

    def made_any(self, c):
        return self.made(c) or self.made(c, "wide")

    def clip_by_id(self, cid):
        return next((c for c in self.clips if c["id"] == cid), None)

    # ---------------------------------------------------------------- thumbnails
    def want_thumb(self, item):
        self.thumbs.put(item)

    def drain_thumbs(self):
        while True:
            try:
                self.thumbs.get_nowait()
            except queue.Empty:
                break

    def cached_video_thumb(self, v):
        import hashlib
        key = hashlib.md5((v["path"] + str(os.path.getmtime(v["path"]))).encode()).hexdigest()[:12]
        p = os.path.join(core.THUMBS, f"v_{key}.jpg")
        return p if os.path.exists(p) else None

    def _thumb_worker(self):
        while True:
            kind, obj = self.thumbs.get()
            try:
                enc = self.ensure_enc()
                if kind == "clip":
                    p = core.make_thumb(enc, obj)
                    if p:
                        self.q.put(("thumb", (obj["id"], p)))
                else:
                    p = core.video_thumb(enc, obj)
                    if p:
                        self.q.put(("vthumb", (obj["path"], p)))
            except Exception:
                time.sleep(0.5)

    # ---------------------------------------------------------------- viewer
    def _actions(self, items):
        for w in self.v_actions.winfo_children():
            w.destroy()
        for text, cmd, kind in items:
            button(self.v_actions, text, cmd, kind).pack(fill="x", pady=3)

    def select_clip(self, c):
        self.sel, self.sel_kind = c, "clip"
        self.pages["clips"].mark_selected()
        self.player.resize(400, 225)
        tp = core.thumb_path(c)
        if os.path.exists(tp):
            try:
                self.player.show_image(Image.open(tp))
            except Exception:
                pass
        first = c["kills"][0] - 3.0 if c["kills"] else 0.0
        self.player.load(c["path"], start=max(0.0, first), dur=c.get("dur") or None)
        kind, op, mp = core.parts(c)
        self.v_title.config(text=" · ".join(x for x in (kind or "Clip", op, mp) if x))
        when = core.clip_time(c).strftime("%A, %b %d · %I:%M %p").replace(" 0", " ")
        kills = "   ".join(fmt_time(k).split(".")[0] for k in c["kills"]) or "none logged"
        self.v_info.config(text=f"{when}\nKills at   {kills}\n{c['mode']}")
        tt, yt = self.made(c), self.made(c, "wide")
        self._actions([
            ("★  Remake TikTok" if tt else "★  Make TikTok", lambda: self.make(c, "vertical"), "accent"),
            ("▭  Remake YouTube video" if yt else "▭  Make YouTube video", lambda: self.make(c, "wide"), "normal"),
            ("✂  Edit & customize", lambda: self.open_editor(c), "normal"),
            ("⤢  Open in Windows player", lambda: startfile(c["path"]), "ghost"),
        ])
        done = [n for n, p in (("TikTok", tt), ("YouTube", yt)) if p]
        self.v_status.config(text=("✓ Already made: " + " + ".join(done)) if done else "", fg=GOOD)

    def select_video(self, v):
        self.sel, self.sel_kind = v, "video"
        for k in ("tiktok", "youtube"):
            self.pages[k].mark_selected()
        if v.get("fmt") == "wide":
            self.player.resize(400, 225)
        else:
            self.player.resize(272, 484)
        self.player.load(v["path"], play=False)
        self.v_title.config(text=VideosPage.short_title(v))
        kind = "YouTube widescreen · 1920×1080" if v.get("fmt") == "wide" else "TikTok / Shorts · 1080×1920"
        secs = v.get("seconds") or 0
        self.v_info.config(text=f"{kind}\n{fmt_time(secs).split('.')[0]} long" if secs else kind)
        items = [("📂  Show in folder", lambda: show_in_folder(v["path"]), "accent")]
        c = self.clip_by_id(v.get("clip_id")) if v.get("clip_id") else None
        if c:
            items.append(("✂  Edit this clip again", lambda: self.open_editor(c), "normal"))
        items += [("⤢  Open in Windows player", lambda: startfile(v["path"]), "normal"),
                  ("🗑  Delete video", lambda: self.delete_video(v), "danger")]
        self._actions(items)
        self.v_status.config(text="")

    def clear_viewer(self):
        self.sel, self.sel_kind = None, None
        self.player.resize(400, 225)
        self.player.unload()
        self.v_title.config(text="Pick a clip")
        self.v_info.config(text="Click any clip or video to watch it here.")
        self._actions([])
        self.v_status.config(text="")

    def delete_video(self, v):
        if not messagebox.askyesno(core.APP_NAME, f"Delete this video?\n\n{os.path.basename(v['path'])}\n\n"
                                                  "Your original Medal clip stays.", parent=self.root):
            return
        self.player.stop()
        try:
            os.remove(v["path"])
        except OSError as e:
            messagebox.showerror(core.APP_NAME, f"Couldn't delete it: {e}")
            return
        core.remove_video(self.state, v["path"])
        self.clear_viewer()
        self.pages[self.current].on_show()
        self.status("Deleted " + os.path.basename(v["path"]))

    def open_editor(self, c):
        self.player.pause()
        Editor(self, c)

    def make(self, c, fmt):
        self.jobs.put(("one", (c, fmt)))
        self.v_status.config(text="Queued — making it now. Keep browsing.", fg=ACCENT)
        self.status(f"Queued {'YouTube' if fmt == 'wide' else 'TikTok'} edit")

    # ---------------------------------------------------------------- auto + actions
    def toggle_auto(self):
        self.watching = not self.watching
        self._paint_pill()
        self.status("Auto-clip on — checking Medal for new highlights." if self.watching else "Auto-clip off.", log=True)
        if self.watching:
            self._auto_tick()
        self.pages["auto"].refresh()

    def _auto_tick(self):
        if not self.watching:
            return
        self.jobs.put(("scan", None))
        self.root.after(max(1, int(self.s.get("watch_minutes", 5))) * 60000, self._auto_tick)

    def aim_short(self, fmt="vertical"):
        self.jobs.put(("short", fmt))
        self.status("Building an aim montage from your fastest multi-kills…", log=True)

    def older(self):
        days = simpledialog.askinteger(core.APP_NAME, "Make edits from highlights in the last how many days?",
                                       minvalue=1, maxvalue=365, initialvalue=1, parent=self.root)
        if days:
            since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
            self.jobs.put(("scan", since))
            self.status(f"Looking for highlights from the last {days} day(s)…", log=True)

    # ---------------------------------------------------------------- account
    def avatar_image(self, size, ring=None, bg=None):
        """Round avatar (Discord picture, or a letter) as a PIL image."""
        try:
            if self._avatar_src is None and self.user:
                p = auth.avatar_path(self.user)
                self._avatar_src = Image.open(p).convert("RGB") if p else False
            src = self._avatar_src or ui.letter_avatar((self.user or {}).get("name") or self.s.get("creator") or "B", 256)
            return ui.circle(src, size, ring=ring, bg=bg or SIDE)
        except Exception:
            return ui.circle(ui.letter_avatar("B", 256), size, ring=ring, bg=bg or SIDE)

    def _sync_profile(self):
        # warm the avatar cache, then tell the server we're active (best effort)
        try:
            auth.avatar_path(self.user)
            self.q.put(("profile", None))
        except Exception:
            pass
        if self.online:
            auth.upsert_profile(self.session, app_version=updater.local_version(), age_confirmed=True)

    def account_menu(self, x, y):
        m = tk.Menu(self.root, tearoff=0, bg=PANEL, fg=TEXT, activebackground=CARD_HI, activeforeground=TEXT,
                    bd=0, relief="flat", font=(FONT, 10))
        if self.user:
            m.add_command(label=f"Signed in as {self.user['name']}", state="disabled")
            m.add_separator()
        m.add_command(label="  Settings", command=lambda: self.show("settings"))
        m.add_command(label="  Check for updates", command=self.manual_update_check)
        m.add_command(label="  Privacy policy", command=lambda: __import__("webbrowser").open(auth.PRIVACY_URL))
        m.add_separator()
        if self.user:
            m.add_command(label="  Sign out", command=self.sign_out)
        else:
            m.add_command(label="  Sign in with Discord", command=self.restart_to_login)
        try:
            m.tk_popup(x, y)
        finally:
            m.grab_release()

    def sign_out(self):
        if not messagebox.askyesno(core.APP_NAME, "Sign out of Breachcut?\n\nYour clips and videos stay on this PC.",
                                   parent=self.root):
            return
        auth.sign_out(self.session)
        self.restart_to_login()

    def restart_to_login(self):
        self.player.stop()
        updater.restart()
        self.root.destroy()

    def ready_highlights(self):
        """Recent (2 weeks) 3K+ clips that haven't been made into anything yet (newest first)."""
        out = []
        cutoff = datetime.now() - timedelta(days=14)
        for c in self.clips:
            if (c["top"] >= max(3, int(self.s.get("min_kills", 3))) and not self.made_any(c)
                    and core.clip_time(c) >= cutoff):
                out.append(c)
        return out[:24]

    def make_all(self, clips):
        fmts = self.s.get("auto_formats") or ["vertical"]
        n = 0
        for c in clips:
            for f in fmts:
                self.jobs.put(("one", (c, f)))
                n += 1
        self.status(f"Queued {n} edit{'s' if n != 1 else ''} — they'll appear in TikTok / YouTube as they finish.",
                    log=True)

    # ---------------------------------------------------------------- onboarding
    def onboarding(self):
        w = tk.Toplevel(self.root, bg=PANEL)
        w.title(f"Welcome to {core.APP_NAME}")
        w.geometry("560x430")
        w.transient(self.root)
        w.grab_set()
        mk = os.path.join(core.ASSETS, "breachcut_mark.png")
        if os.path.exists(mk):
            self._ob_mark = ImageTk.PhotoImage(Image.open(mk).resize((84, 84), Image.LANCZOS))
            tk.Label(w, image=self._ob_mark, bg=PANEL).pack(pady=(22, 6))
        tk.Label(w, text=f"Welcome to {core.APP_NAME}", fg=TEXT, bg=PANEL, font=(FONT, 17, "bold")).pack()
        tk.Label(w, text="Turns your Siege clips from Medal into TikToks, Shorts and YouTube edits.",
                 fg=MUTED, bg=PANEL, font=(FONT, 10)).pack(pady=(2, 18))
        f = tk.Frame(w, bg=PANEL)
        f.pack(fill="x", padx=36)
        tk.Label(f, text="Your name or gamertag (shown in your intro)", fg=TEXT, bg=PANEL, font=(FONT, 10)).pack(anchor="w")
        name = tk.StringVar(value=self.s.get("creator", ""))
        e = tk.Entry(f, textvariable=name, bg=CARD, fg=TEXT, insertbackground=TEXT, relief="flat", font=(FONT, 12))
        e.pack(fill="x", ipady=6, pady=(4, 14))
        e.focus_set()
        tk.Label(f, text="Medal folder", fg=TEXT, bg=PANEL, font=(FONT, 10)).pack(anchor="w")
        md = tk.StringVar(value=core.find_medal_dir() if not os.path.isdir(self.s["medal_dir"]) else self.s["medal_dir"])
        r = tk.Frame(f, bg=PANEL)
        r.pack(fill="x", pady=(4, 0))
        tk.Entry(r, textvariable=md, bg=CARD, fg=TEXT, insertbackground=TEXT, relief="flat", font=(FONT, 10)).pack(
            side="left", fill="x", expand=True, ipady=5)
        button(r, "…", lambda: md.set(filedialog.askdirectory(parent=w) or md.get()), small=True).pack(side="left", padx=(6, 0))

        def go():
            self.s["creator"] = name.get().strip()
            self.s["medal_dir"] = md.get().strip()
            self.s["onboarded"] = True
            core.save_settings(self.s)
            w.grab_release()
            w.destroy()
            self.pages["settings"].vals["creator"].set(self.s["creator"])
            self.pages["settings"].vals["medal_dir"].set(self.s["medal_dir"])
            self.pages["settings"].refresh_labels()
            self.reload()
        button(w, "Let's go", go, "accent").pack(pady=22)
        w.bind("<Return>", lambda ev: go())

    # ---------------------------------------------------------------- updates
    def _check_update(self, manual=False):
        try:
            m = updater.check()
        except Exception:
            m = None
            if manual:
                self.q.put(("status", "Couldn't reach the update server — try again later."))
                return
        if m:
            self.q.put(("autoupdate" if self.s.get("auto_update", True) and not manual else "patch", m))
        elif manual:
            self.q.put(("status", f"You're on the latest version (v{updater.local_version()})."))

    def manual_update_check(self):
        self.status("Checking for updates…")
        threading.Thread(target=lambda: self._check_update(manual=True), daemon=True).start()

    def _show_patch(self, m):
        self.patch = m
        for w in self.banner.winfo_children():
            w.destroy()
        notes = m.get("notes", "")
        tk.Label(self.banner, text=f"🔥  Update {m['version']} is ready" + (f" — {notes}" if notes else ""),
                 fg="#ffffff", bg=ACCENT, font=(FONT, 10, "bold")).pack(side="left", padx=18, pady=7)
        tk.Button(self.banner, text="Update now", command=self._do_update, bg="#ffffff", fg=ACCENT, relief="flat",
                  bd=0, padx=14, pady=3, font=(FONT, 10, "bold"), cursor="hand2").pack(side="right", padx=14, pady=5)
        self.banner.pack(side="top", fill="x", before=self.pages[self.current])

    def _do_update(self):
        self.status("Updating…")

        def work():
            try:
                updater.apply(self.patch, log=lambda t: self.q.put(("status", t)))
                self.q.put(("patched", None))
            except Exception as e:
                self.q.put(("error", f"Update failed (nothing changed): {e}"))
        threading.Thread(target=work, daemon=True).start()

    def _storage_check(self):
        try:
            n, _ = core.enforce_storage(self.s, self.state, log=lambda t: self.q.put(("log", t)))
            if n:
                self.q.put(("storage", n))
        except Exception:
            pass

    def notify(self, title, body="", path=None):
        """Small pop-up in the bottom-right corner of the screen (works while the app is minimized)."""
        if self.s.get("notify_sound", True):
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except Exception:
                pass
        old = getattr(self, "_toast", None)
        if old is not None:
            try:
                old.destroy()
            except tk.TclError:
                pass
        t = tk.Toplevel(self.root)
        self._toast = t
        t.overrideredirect(True)
        t.configure(bg=ACCENT)
        try:
            t.attributes("-topmost", True)
        except tk.TclError:
            pass
        w, h = 360, 86
        sw, sh = t.winfo_screenwidth(), t.winfo_screenheight()
        t.geometry(f"{w}x{h}+{sw - w - 24}+{sh - h - 64}")
        f = tk.Frame(t, bg=PANEL)
        f.pack(fill="both", expand=True, padx=(4, 1), pady=1)
        mark = os.path.join(core.ASSETS, "breachcut_mark.png")
        if os.path.exists(mark):
            t._img = ImageTk.PhotoImage(Image.open(mark).convert("RGBA").resize((40, 40), Image.LANCZOS))
            tk.Label(f, image=t._img, bg=PANEL).pack(side="left", padx=(14, 10))
        txt = tk.Frame(f, bg=PANEL)
        txt.pack(side="left", fill="both", expand=True, pady=14)
        tk.Label(txt, text=title, fg=TEXT, bg=PANEL, font=(FONT_SEMI, 10), anchor="w").pack(fill="x")
        tk.Label(txt, text=body[:60], fg=MUTED, bg=PANEL, font=(FONT, 9), anchor="w").pack(fill="x")
        x = tk.Label(f, text="✕", fg=DIM, bg=PANEL, font=(FONT, 10), cursor="hand2")
        x.pack(side="right", anchor="n", padx=10, pady=8)
        x.bind("<Button-1>", lambda e: t.destroy())

        def click(e):
            t.destroy()
            if path and os.path.exists(path):
                show_in_folder(path)
            else:
                self.root.deiconify()
                self.root.lift()
        for wdg in (f, txt) + tuple(txt.winfo_children()):
            wdg.bind("<Button-1>", click)
        t.after(6000, lambda: t.winfo_exists() and t.destroy())

    # ---------------------------------------------------------------- workers
    def ensure_enc(self):
        if self.enc is None:
            ff = core.find_ffmpeg()
            if not ff:
                raise RuntimeError("FFmpeg is missing — run the installer again.")
            self.enc = core.Encoder(ff)
            core.apply_settings(self.s, self.enc)
        return self.enc

    def _job_worker(self):
        while True:
            kind, arg = self.jobs.get()
            try:
                enc = self.ensure_enc()
                log = lambda m: self.q.put(("status", m.strip()))
                if kind == "one":
                    c, fmt = arg
                    out = core.make_highlight(enc, c, self.out_dir(fmt), self.state, log, style=self.s.get("style"),
                                              fmt=fmt, brand=self.brand())
                    self.q.put(("made", (c["id"], out)))
                elif kind == "short":
                    out = core.make_aim_short(enc, self.clips, self.s, self.out_dir(arg), log=log, fmt=arg,
                                              brand=self.brand(), state=self.state)
                    self.q.put(("made", (None, out)))
                elif kind == "scan":
                    settings = dict(self.s)
                    if arg:
                        settings["only_new_after"] = arg
                    clips = core.read_clips(self.s["medal_dir"])
                    new = [c for c in core.pick_new_highlights(clips, settings, self.state)
                           if os.path.exists(c["path"]) and time.time() - os.path.getmtime(c["path"]) > 90]
                    core.save_state(self.state)
                    fmts = self.s.get("auto_formats") or ["vertical"]
                    if not new:
                        self.q.put(("log", "Checked Medal — no new highlights."))
                    for i, c in enumerate(new, 1):
                        for fmt in fmts:
                            self.q.put(("status", f"Editing highlight {i}/{len(new)} ({'YouTube' if fmt == 'wide' else 'TikTok'})…"))
                            out = core.make_highlight(enc, c, self.out_dir(fmt), self.state, log,
                                                      style=self.s.get("style"), fmt=fmt, brand=self.brand())
                            self.q.put(("made", (c["id"], out)))
                    if new:
                        self.q.put(("reload", None))
            except Exception as e:
                self.q.put(("error", str(e)[-400:]))

    def _pump(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "clips":
                    self.clips = val
                    self.clips_msg = "No Siege clips in Medal yet." if not val else ""
                    self.status(f"{len(val)} Siege clips from Medal.")
                    self.pages["clips"].apply()
                    if self.current == "home":
                        self.pages["home"].render()
                elif kind == "noclips":
                    self.clips = []
                    self.clips_msg = (f"Couldn't find Medal's clips.\n{val}\n\n"
                                      "Check the Medal folder in Settings (it's usually C:\\Medal).")
                    self.status("Couldn't read Medal — check Settings.")
                    self.pages["clips"].apply()
                elif kind == "thumb":
                    self.pages["clips"].set_thumb(*val)
                elif kind == "vthumb":
                    for k in ("tiktok", "youtube"):
                        self.pages[k].set_thumb(*val)
                elif kind == "status":
                    self.status(val)
                elif kind == "log":
                    self.status(val, log=True)
                elif kind == "made":
                    cid, out = val
                    self.status(f"✅ Saved {os.path.basename(out)}", log=True)
                    if self.s.get("notify_made", True):
                        self.notify("Your video is ready", os.path.basename(out), out)
                    threading.Thread(target=self._storage_check, daemon=True).start()
                    if cid and self.sel_kind == "clip" and self.sel and self.sel["id"] == cid:
                        self.select_clip(self.sel)
                        self.v_status.config(text="✅ Done! Find it in the TikTok / YouTube tab.", fg=GOOD)
                    if self.current in ("tiktok", "youtube"):
                        self.pages[self.current].render()
                    elif self.current == "clips" and cid:
                        card = self.pages["clips"].cards.get(cid)
                        tp = core.thumb_path(card[2]) if card else None
                        if tp and os.path.exists(tp):
                            self.pages["clips"].set_thumb(cid, tp)
                elif kind == "reload":
                    self.reload()
                elif kind == "profile":
                    self._avatar_src = None
                    self.profile.redraw()
                    if self.current == "home":
                        self.pages["home"].render()
                elif kind == "patch":
                    self._show_patch(val)
                    if self.s.get("notify_update", True):
                        self.notify(f"Update {val['version']} is ready", val.get("notes") or "Click Update now to install.")
                elif kind == "autoupdate":
                    self.patch = val
                    if self.jobs.empty():
                        self.status(f"Installing update {val['version']}…")
                        self._do_update()
                    else:
                        self._show_patch(val)
                elif kind == "storage":
                    if self.current in ("tiktok", "youtube"):
                        self.pages[self.current].render()
                elif kind == "patched":
                    messagebox.showinfo(core.APP_NAME, f"Updated to {self.patch['version']}! Restarting…")
                    updater.restart()
                    self.root.destroy()
                    return
                elif kind == "error":
                    self.status("⚠ " + val, log=True)
                    if self.sel_kind == "clip":
                        self.v_status.config(text="⚠ " + val[-200:], fg=WARN)
        except queue.Empty:
            pass
        self.root.after(100, self._pump)


def main():
    root = tk.Tk()
    root.title(core.APP_NAME)
    root.configure(bg=BG)

    def ready(session, online):
        app = App(root, session, online)
        if "--startup" in sys.argv and app.s.get("start_minimized"):
            root.iconify()
            return
        root.deiconify()
        try:
            root.lift()
            root.focus_force()
        except tk.TclError:
            pass
    login.launch(root, ready)
    root.mainloop()


if __name__ == "__main__":
    main()
