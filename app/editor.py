"""Breachcut editor - simple like Medal / CapCut.

Big player on top, one timeline underneath: the bright blocks are what goes in your video.
Drag a block's edges to trim, Split at the playhead, Delete what you don't want. Pick a look in one click,
then Export. Everything saves as you go and Undo always works.
"""
import json
import os
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

from PIL import Image, ImageDraw, ImageEnhance, ImageTk

import autoclip_core as core
import ui_kit as ui
from player import Player, fmt
from settings_page import Slider
from ui_kit import (ACCENT, BG, CARD, CARD_HI, DIM, FONT, FONT_BOLD, FONT_SEMI, GOOD, LINE, MUTED, PANEL, TEXT,
                    VIOLET, WARN, RoundButton, Toggle)

PREVIEWS = os.path.join(core.APP_DIR, "previews")

LOOKS = (
    ("Clean", "Crisp and bright, quick flash between kills.",
     {"transition": "Flash", "trans_len": 0.06, "glow": 10, "brightness": 4, "sharpen": True, "fade_in": 0.0,
      "fade_out": 0.0}),
    ("Hype", "Punchy glow, hard flashes. Made for TikTok.",
     {"transition": "Flash", "trans_len": 0.05, "glow": 24, "brightness": 8, "sharpen": True, "fade_in": 0.0,
      "fade_out": 0.4, "game_vol": 110}),
    ("Cinematic", "Soft haze, fades to black, slow ending.",
     {"transition": "Fade to black", "trans_len": 0.3, "glow": 30, "brightness": 2, "sharpen": True,
      "fade_in": 0.6, "fade_out": 1.2}),
    ("Raw", "Just the gameplay. No effects.",
     {"transition": "None", "trans_len": 0.06, "glow": 0, "brightness": 0, "sharpen": False, "fade_in": 0.0,
      "fade_out": 0.0}),
)

TL_H = 132          # timeline height
TRACK_Y0, TRACK_Y1 = 34, 96
PAD = 16


class Editor(tk.Toplevel):
    def __init__(self, app, clip):
        super().__init__(app.root, bg=BG)
        self.app, self.c = app, clip
        kind, op, mp = core.parts(clip)
        self.name = " · ".join(x for x in (kind or "Clip", op, mp) if x)
        self.title("Edit · " + self.name)
        ui.center(self, 1320, 860)
        self.minsize(1180, 800)
        icon = os.path.join(core.ASSETS, "breachcut.ico")
        if os.path.exists(icon):
            try:
                self.iconbitmap(icon)
            except tk.TclError:
                pass
        self.edits = core.load_edits()
        mine = self.edits.get(clip["id"]) or {}
        self.segs = [list(x) for x in mine.get("segments") or self.auto_segs()]
        self.segs.sort()
        self.style = dict(core.DEFAULT_STYLE)
        self.style.update(app.s.get("style") or {})
        self.style.update(mine.get("style") or {})
        self.dur = float(clip.get("dur") or core.probe_duration(clip["path"]) or 60.0)
        self.sel = 0
        self.busy = False
        self.mode = "edit"           # edit | preview
        self.fmt = "vertical"
        self.undo_stack, self.redo_stack = [], []
        self.drag = None
        self.strip = None            # thumbnail strip (PIL)
        self.tab = "looks"
        self._seek_job = None
        self._build()
        self.player.load(clip["path"], start=self.segs[0][0] if self.segs else 0.0, dur=self.dur, play=False)
        self.after(150, self.draw_timeline)
        threading.Thread(target=self._make_strip, daemon=True).start()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._keys()
        self.say("Drag the edges of a pink block to trim it. Space plays, S splits, Delete removes.")

    def auto_segs(self):
        return [[round(s, 2), round(e, 2)] for _, s, e in core.clip_segments(self.c)] or [[0.0, 5.0]]

    # ================================================================== layout
    def _build(self):
        # ---- top bar
        top = tk.Frame(self, bg=PANEL, height=60)
        top.pack(fill="x")
        top.pack_propagate(False)
        RoundButton(top, "←  Done", self.close, kind="ghost", small=True, bg=PANEL).pack(side="left", padx=(14, 6))
        tk.Label(top, text=self.name, fg=TEXT, bg=PANEL, font=(FONT_BOLD, 13)).pack(side="left", padx=6)
        self.export_b = RoundButton(top, "⬆  Export", self.export_menu, kind="accent", bg=PANEL, width=130)
        self.export_b.pack(side="right", padx=(6, 16))
        self.preview_b = RoundButton(top, "▶  Preview", self.make_preview, bg=PANEL, small=True)
        self.preview_b.pack(side="right", padx=6)
        fmtbox = tk.Frame(top, bg=PANEL)
        fmtbox.pack(side="right", padx=14)
        self.fmt_btns = {}
        for key, text in (("vertical", "▯  TikTok"), ("wide", "▭  YouTube")):
            b = RoundButton(fmtbox, text, lambda k=key: self.set_fmt(k), kind="chip", small=True, bg=PANEL, width=110)
            b.pack(side="left", padx=2)
            self.fmt_btns[key] = b
        self.redo_b = RoundButton(top, "↷", self.redo, kind="ghost", small=True, bg=PANEL, width=38)
        self.redo_b.pack(side="right")
        self.undo_b = RoundButton(top, "↶", self.undo, kind="ghost", small=True, bg=PANEL, width=38)
        self.undo_b.pack(side="right")
        tk.Frame(self, bg=LINE, height=1).pack(fill="x")

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True)

        # ---- right panel (looks / music / intro / adjust)
        side = tk.Frame(body, bg=PANEL, width=360)
        side.pack(side="right", fill="y")
        side.pack_propagate(False)
        tabs = tk.Frame(side, bg=PANEL)
        tabs.pack(fill="x", padx=14, pady=(14, 6))
        self.tab_btns = {}
        for key, text in (("looks", "✦ Looks"), ("music", "♪ Music"), ("intro", "★ Intro"), ("adjust", "⚙ Adjust")):
            b = RoundButton(tabs, text, lambda k=key: self.open_tab(k), kind="chip", small=True, bg=PANEL, width=78)
            b.pack(side="left", padx=2)
            self.tab_btns[key] = b
        self.side = tk.Frame(side, bg=PANEL)
        self.side.pack(fill="both", expand=True, padx=16, pady=6)

        # ---- left: player, tools, timeline
        left = tk.Frame(body, bg=BG)
        left.pack(side="left", fill="both", expand=True, padx=18, pady=(12, 10))
        self.banner = tk.Frame(left, bg=BG, height=30)
        self.banner.pack(fill="x")
        self.player = Player(left, 800, 450, bg=BG, fg=TEXT, accent=ACCENT, on_time=self._on_time)
        self.player.pack()
        try:
            self.player.bar.pack_forget()      # the timeline below replaces the plain seek bar
        except Exception:
            pass

        tools = tk.Frame(left, bg=BG)
        tools.pack(fill="x", pady=(10, 4))
        self.tool_btns = [
            RoundButton(tools, "✂  Split", self.split, small=True, bg=BG),
            RoundButton(tools, "🗑  Delete part", self.delete_part, small=True, bg=BG),
            RoundButton(tools, "+  Add part here", self.add_part, small=True, bg=BG),
            RoundButton(tools, "↺  Auto cut", self.auto_cut, kind="ghost", small=True, bg=BG),
        ]
        for b in self.tool_btns:
            b.pack(side="left", padx=(0, 6))
        self.len_l = tk.Label(tools, text="", fg=MUTED, bg=BG, font=(FONT_SEMI, 10))
        self.len_l.pack(side="right")

        self.tl = tk.Canvas(left, height=TL_H, bg=BG, highlightthickness=0, bd=0)
        self.tl.pack(fill="x", pady=(2, 0))
        self.tl.bind("<Configure>", lambda e: self.draw_timeline())
        self.tl.bind("<ButtonPress-1>", self._tl_press)
        self.tl.bind("<B1-Motion>", self._tl_drag)
        self.tl.bind("<ButtonRelease-1>", self._tl_release)
        self.tl.bind("<Motion>", self._tl_hover)

        self.status = tk.Label(left, text="", fg=MUTED, bg=BG, font=(FONT, 10), anchor="w")
        self.status.pack(fill="x", pady=(8, 0))
        self.set_fmt("vertical", quiet=True)
        self.open_tab("looks")
        self._paint_undo()

    def _keys(self):
        def typing(e):
            return isinstance(e.widget, (tk.Entry, tk.Spinbox, tk.Text))
        self.bind("<space>", lambda e: None if typing(e) else self.toggle_play())
        self.bind("<KeyPress-s>", lambda e: None if typing(e) else self.split())
        self.bind("<Delete>", lambda e: None if typing(e) else self.delete_part())
        self.bind("<Control-z>", lambda e: self.undo())
        self.bind("<Control-y>", lambda e: self.redo())
        self.bind("<Control-Shift-Z>", lambda e: self.redo())
        self.bind("<Left>", lambda e: None if typing(e) else self.nudge(-1))
        self.bind("<Right>", lambda e: None if typing(e) else self.nudge(1))
        self.bind("<Shift-Left>", lambda e: None if typing(e) else self.nudge(-5))
        self.bind("<Shift-Right>", lambda e: None if typing(e) else self.nudge(5))

    # ================================================================== timeline
    def tx(self, t):
        w = max(200, self.tl.winfo_width())
        return PAD + (t / max(0.1, self.dur)) * (w - 2 * PAD)

    def xt(self, x):
        w = max(200, self.tl.winfo_width())
        return max(0.0, min(self.dur, (x - PAD) / (w - 2 * PAD) * self.dur))

    def _make_strip(self):
        """Little thumbnails across the timeline (like every editor has)."""
        ff = core.find_ffmpeg()
        if not ff:
            return
        n = 14
        frames = []
        for i in range(n):
            t = (i + 0.5) * self.dur / n
            try:
                r = subprocess.run([ff, "-nostdin", "-v", "error", "-ss", f"{t:.2f}", "-i", self.c["path"],
                                    "-frames:v", "1", "-vf", "scale=112:63", "-f", "rawvideo", "-pix_fmt", "rgb24",
                                    "-"], capture_output=True, timeout=15, creationflags=core.NO_WINDOW)
                if len(r.stdout) >= 112 * 63 * 3:
                    frames.append(Image.frombytes("RGB", (112, 63), r.stdout[:112 * 63 * 3]))
            except Exception:
                pass
            if not self.winfo_exists():
                return
        if frames:
            strip = Image.new("RGB", (112 * len(frames), 63))
            for i, f in enumerate(frames):
                strip.paste(f, (i * 112, 0))
            self.strip = strip
            self.after(0, self.draw_timeline)

    def draw_timeline(self):
        c = self.tl
        if not c.winfo_exists():
            return
        w = max(200, c.winfo_width())
        c.delete("all")
        x0, x1 = PAD, w - PAD
        tw, th = int(x1 - x0), TRACK_Y1 - TRACK_Y0
        # track image: thumbnails, darkened where it's cut out
        if self.strip is not None:
            base = self.strip.resize((tw, th), Image.BILINEAR)
        else:
            base = Image.new("RGB", (tw, th), ui.rgb(CARD_HI))
        dim = ImageEnhance.Brightness(base).enhance(0.28)
        keep = Image.new("L", (tw, th), 0)
        d = ImageDraw.Draw(keep)
        for s, e in self.segs:
            d.rectangle([int(self.tx(s) - x0), 0, int(self.tx(e) - x0), th], fill=255)
        im = Image.composite(base, dim, keep).convert("RGBA")
        mask = ui.rounded_rect(tw, th, 8, "#ffffff").getchannel("A")
        bgim = Image.new("RGBA", (tw, th), ui.rgb(BG) + (255,))
        im = Image.composite(im, bgim, mask)
        self._tl_img = ImageTk.PhotoImage(im)
        c.create_image(x0, TRACK_Y0, image=self._tl_img, anchor="nw")
        # kept parts: outline + drag handles
        for i, (s, e) in enumerate(self.segs):
            a, b = self.tx(s), self.tx(e)
            sel = i == self.sel and self.mode == "edit"
            col = ACCENT if sel else "#ff9fb2"
            c.create_rectangle(a, TRACK_Y0, b, TRACK_Y1, outline=col, width=3 if sel else 2)
            for hx in (a, b):
                c.create_rectangle(hx - 5, TRACK_Y0 + 14, hx + 5, TRACK_Y1 - 14, fill=col, outline="")
                c.create_line(hx, TRACK_Y0 + 22, hx, TRACK_Y1 - 22, fill="#ffffff", width=2)
            if b - a > 46:
                c.create_text((a + b) / 2, TRACK_Y1 + 10, text=f"{e - s:.1f}s", fill=TEXT if sel else MUTED,
                              font=(FONT_SEMI, 8))
        # kills
        for k_i, k in enumerate(self.c.get("kills") or [], 1):
            kx = self.tx(k)
            c.create_polygon(kx, 22, kx - 6, 12, kx + 6, 12, fill=WARN, outline="")
            c.create_text(kx, 6, text=f"K{k_i}", fill=WARN, font=(FONT_SEMI, 7))
        # ruler
        step = 5 if self.dur <= 90 else 15
        t = 0.0
        while t <= self.dur:
            x = self.tx(t)
            c.create_line(x, TRACK_Y1 + 20, x, TRACK_Y1 + 25, fill=DIM)
            c.create_text(x, TRACK_Y1 + 32, text=fmt(t).split(".")[0], fill=DIM, font=(FONT, 7))
            t += step
        # playhead
        px = self.tx(self.player.pos or 0.0)
        c.create_line(px, 14, px, TRACK_Y1 + 4, fill="#ffffff", width=2, tags="ph")
        c.create_oval(px - 6, 8, px + 6, 20, fill="#ffffff", outline="", tags="ph")
        if self.mode == "preview":
            c.create_rectangle(x0, TRACK_Y0, x1, TRACK_Y1, fill=BG, stipple="gray50", outline="")
            c.create_text(w / 2, (TRACK_Y0 + TRACK_Y1) / 2, text="Watching the preview · click  Back to editing  to change it",
                          fill=TEXT, font=(FONT_SEMI, 10))
        total = sum(max(0, e - s) for s, e in self.segs)
        tip = "   ·   TikTok sweet spot is under 60s" if total > 60 and self.fmt == "vertical" else ""
        self.len_l.config(text=f"{len(self.segs)} part{'s' if len(self.segs) != 1 else ''}  ·  video length "
                               f"{total:.1f}s{tip}")

    def _hit(self, x, y):
        """('edge', i, 0|1) | ('part', i) | ('empty',)"""
        if TRACK_Y0 - 6 <= y <= TRACK_Y1 + 6:
            best = None
            for i, (s, e) in enumerate(self.segs):
                for side, t in ((0, s), (1, e)):
                    d = abs(self.tx(t) - x)
                    if d <= 9 and (best is None or d < best[0]):
                        best = (d, i, side)
            if best:
                return ("edge", best[1], best[2])
            for i, (s, e) in enumerate(self.segs):
                if self.tx(s) <= x <= self.tx(e):
                    return ("part", i)
        return ("empty",)

    def _tl_hover(self, e):
        if self.mode != "edit":
            self.tl.config(cursor="arrow")
            return
        h = self._hit(e.x, e.y)
        self.tl.config(cursor="sb_h_double_arrow" if h[0] == "edge" else "hand2")

    def _tl_press(self, e):
        if self.mode != "edit":
            return
        h = self._hit(e.x, e.y)
        self.player.pause()
        if h[0] == "edge":
            self.push_undo()
            self.sel = h[1]
            self.drag = ("edge", h[1], h[2])
        else:
            if h[0] == "part":
                self.sel = h[1]
            self.drag = ("seek",)
            self._seek_to(self.xt(e.x))
        self.draw_timeline()

    def _tl_drag(self, e):
        if not self.drag:
            return
        t = self.xt(e.x)
        if self.drag[0] == "edge":
            _, i, side = self.drag
            s, en = self.segs[i]
            lo = self.segs[i - 1][1] if i > 0 else 0.0
            hi = self.segs[i + 1][0] if i + 1 < len(self.segs) else self.dur
            # snap to kills a little, like editors snap to markers
            for k in self.c.get("kills") or []:
                if abs(self.tx(k) - e.x) < 6:
                    t = k
            if side == 0:
                self.segs[i][0] = round(max(lo, min(t, en - 0.3)), 2)
                show = self.segs[i][0]
            else:
                self.segs[i][1] = round(min(hi, max(t, s + 0.3)), 2)
                show = self.segs[i][1]
            self.draw_timeline()
            px = self.tx(show)
            self.tl.create_rectangle(px - 26, TRACK_Y0 - 22, px + 26, TRACK_Y0 - 6, fill=ACCENT, outline="")
            self.tl.create_text(px, TRACK_Y0 - 14, text=fmt(show), fill="#ffffff", font=(FONT_SEMI, 8))
            self._seek_to(show, lazy=True)
        else:
            self._seek_to(t, lazy=True)

    def _tl_release(self, e):
        if self.drag and self.drag[0] == "edge":
            self.changed()
        self.drag = None
        self.draw_timeline()

    def _seek_to(self, t, lazy=False):
        """Move the playhead now; fetch the video frame a moment later while dragging."""
        self.player.pos = t
        self.player._update_time()
        self._move_playhead(t)
        if self._seek_job:
            self.after_cancel(self._seek_job)
        self._seek_job = self.after(120 if lazy else 1, lambda: self.player.seek(t, play=False))

    def _move_playhead(self, t):
        px = self.tx(t)
        self.tl.delete("ph")
        self.tl.create_line(px, 14, px, TRACK_Y1 + 4, fill="#ffffff", width=2, tags="ph")
        self.tl.create_oval(px - 6, 8, px + 6, 20, fill="#ffffff", outline="", tags="ph")

    def _on_time(self, pos):
        if self.mode == "edit" and not self.drag:
            self._move_playhead(pos)

    def toggle_play(self):
        if self.mode == "edit" and not self.player.playing:
            # play from the playhead through the end of the part it's in
            t = self.player.pos
            part = next(((s, e) for s, e in self.segs if s - 0.05 <= t < e), None)
            if part:
                self.player.play(until=part[1])
                return
        self.player.toggle()

    def nudge(self, sec):
        if self.mode == "edit":
            self._seek_to(max(0.0, min(self.dur - 0.05, self.player.pos + sec)))

    # ================================================================== editing actions
    def split(self):
        if self.mode != "edit":
            return
        t = round(self.player.pos, 2)
        for i, (s, e) in enumerate(self.segs):
            if s + 0.3 < t < e - 0.3:
                self.push_undo()
                self.segs[i:i + 1] = [[s, t], [t, e]]
                self.sel = i + 1
                self.changed()
                self.say("Split! Now you can delete or trim either half.", GOOD)
                return
        self.say("Move the playhead inside a pink block to split it.", WARN)

    def delete_part(self):
        if self.mode != "edit":
            return
        if len(self.segs) <= 1:
            self.say("Your video needs at least one part.", WARN)
            return
        self.push_undo()
        self.segs.pop(self.sel)
        self.sel = max(0, min(self.sel, len(self.segs) - 1))
        self.changed()
        self.say("Part removed. Ctrl+Z brings it back.")

    def add_part(self):
        if self.mode != "edit":
            return
        t = round(self.player.pos, 2)
        if any(s <= t <= e for s, e in self.segs):
            self.say("That's already in your video. Move the playhead to a dark spot first.", WARN)
            return
        nxt = next((s for s, _ in self.segs if s > t), self.dur)
        end = min(t + 3.0, nxt)
        if end - t < 0.3:
            self.say("Not enough room there.", WARN)
            return
        self.push_undo()
        self.segs.append([t, round(end, 2)])
        self.segs.sort()
        self.sel = self.segs.index([t, round(end, 2)])
        self.changed()
        self.say("Added 3 seconds. Drag its edges to adjust.", GOOD)

    def auto_cut(self):
        self.push_undo()
        self.segs = self.auto_segs()
        self.sel = 0
        self.changed()
        self.say("Back to Breachcut's automatic cut.", GOOD)

    # ---- undo
    def _snap(self):
        return json.dumps({"segs": self.segs, "style": self.style})

    def push_undo(self):
        self.undo_stack.append(self._snap())
        self.undo_stack = self.undo_stack[-80:]
        self.redo_stack.clear()
        self._paint_undo()

    def _restore(self, snap):
        d = json.loads(snap)
        self.segs, self.style = d["segs"], d["style"]
        self.sel = min(self.sel, len(self.segs) - 1)
        self.changed(record=False)
        self.open_tab(self.tab)

    def undo(self):
        if self.undo_stack and self.mode == "edit":
            self.redo_stack.append(self._snap())
            self._restore(self.undo_stack.pop())
            self.say("Undone.")
        self._paint_undo()

    def redo(self):
        if self.redo_stack and self.mode == "edit":
            self.undo_stack.append(self._snap())
            self._restore(self.redo_stack.pop())
            self.say("Redone.")
        self._paint_undo()

    def _paint_undo(self):
        self.undo_b.config(state="normal" if self.undo_stack else "disabled")
        self.redo_b.config(state="normal" if self.redo_stack else "disabled")

    def changed(self, record=True):
        self.preview_path = None
        self.persist()
        self.draw_timeline()

    def persist(self):
        self.edits[self.c["id"]] = {"segments": [[round(s, 2), round(e, 2)] for s, e in self.segs],
                                    "style": self.style}
        try:
            core.save_edits(self.edits)
        except OSError:
            pass

    # ================================================================== side panel
    def open_tab(self, key):
        self.tab = key
        for k, b in self.tab_btns.items():
            b.config(kind="chip_on" if k == key else "chip")
        for w in self.side.winfo_children():
            w.destroy()
        getattr(self, "_tab_" + key)()

    def h(self, text, sub=""):
        tk.Label(self.side, text=text, fg=TEXT, bg=PANEL, font=(FONT_BOLD, 13)).pack(anchor="w", pady=(8, 0))
        if sub:
            tk.Label(self.side, text=sub, fg=MUTED, bg=PANEL, font=(FONT, 9), wraplength=320,
                     justify="left").pack(anchor="w", pady=(0, 8))

    def set_style(self, **kw):
        self.push_undo()
        self.style.update(kw)
        self.changed()

    def _current_look(self):
        for name, _, vals in LOOKS:
            if all(self.style.get(k) == v for k, v in vals.items() if k != "game_vol"):
                return name
        return None

    def _tab_looks(self):
        self.h("Looks", "One click changes the whole feel of the video.")
        cur = self._current_look()
        for name, desc, vals in LOOKS:
            on = name == cur
            f = tk.Frame(self.side, bg=CARD_HI if on else CARD, highlightthickness=2,
                         highlightbackground=ACCENT if on else LINE, cursor="hand2")
            f.pack(fill="x", pady=4)
            t = tk.Label(f, text=("✓  " if on else "") + name, fg=TEXT, bg=f["bg"], font=(FONT_SEMI, 11), anchor="w")
            t.pack(fill="x", padx=12, pady=(8, 0))
            s = tk.Label(f, text=desc, fg=MUTED, bg=f["bg"], font=(FONT, 9), anchor="w")
            s.pack(fill="x", padx=12, pady=(0, 8))

            def pick(e=None, name=name, vals=vals):
                self.set_style(**vals)
                self.open_tab("looks")
                self.say(f"Look: {name}. Hit Preview to see it.", GOOD)
            for wdg in (f, t, s):
                wdg.bind("<Button-1>", pick)
        if not cur:
            tk.Label(self.side, text="Custom look (changed in Adjust)", fg=VIOLET, bg=PANEL,
                     font=(FONT, 9)).pack(anchor="w", pady=(4, 0))
        tk.Label(self.side, text="Between parts", fg=TEXT, bg=PANEL, font=(FONT_SEMI, 10)).pack(anchor="w",
                                                                                              pady=(14, 4))
        row = tk.Frame(self.side, bg=PANEL)
        row.pack(anchor="w")
        for tr, label in (("Flash", "Flash"), ("Fade to black", "Fade"), ("None", "Cut")):
            RoundButton(row, label, lambda tr=tr: (self.set_style(transition=tr,
                                                                  trans_len={"Flash": 0.06, "Fade to black": 0.25}.get(tr, 0.06)),
                                                   self.open_tab("looks")),
                        kind="chip_on" if self.style.get("transition") == tr else "chip", small=True, bg=PANEL,
                        width=86).pack(side="left", padx=(0, 4))
        RoundButton(self.side, "Use this look for all my videos", self.save_default, kind="ghost", small=True,
                    bg=PANEL).pack(anchor="w", pady=(18, 0))

    def _tab_music(self):
        self.h("Music", "Optional. Lots of people add music in TikTok instead.")
        m = self.style.get("music") or ""
        f = tk.Frame(self.side, bg=CARD, highlightthickness=1, highlightbackground=LINE)
        f.pack(fill="x", pady=4)
        tk.Label(f, text="♪  " + (os.path.basename(m) if m else "No song"), fg=TEXT if m else MUTED, bg=CARD,
                 font=(FONT_SEMI, 10), anchor="w").pack(fill="x", padx=12, pady=10)
        row = tk.Frame(self.side, bg=PANEL)
        row.pack(anchor="w", pady=6)
        RoundButton(row, "Pick a song…", self.pick_music, small=True, bg=PANEL).pack(side="left")
        if m:
            RoundButton(row, "Remove", lambda: (self.set_style(music=""), self.open_tab("music")), kind="ghost",
                        small=True, bg=PANEL).pack(side="left", padx=6)
        self._slide("music_vol", "Music volume", 0, 100, "%")
        self._slide("game_vol", "Game volume", 0, 150, "%")

    def _tab_intro(self):
        self.h("Intro", "Your glowing name or logo at the start.")
        for key, label in (("intro", "Show my intro"), ("voice", "Play my intro voice")):
            r = tk.Frame(self.side, bg=PANEL)
            r.pack(fill="x", pady=8)
            tk.Label(r, text=label, fg=TEXT, bg=PANEL, font=(FONT_SEMI, 10)).pack(side="left")
            v = tk.BooleanVar(value=bool(self.style.get(key)))
            Toggle(r, v, command=lambda key=key, v=v: self.set_style(**{key: bool(v.get())}), bg=PANEL).pack(
                side="right")
        img, _ = core.brand_assets(self.app.s)
        if img and os.path.exists(img):
            im = Image.open(img).convert("RGBA")
            base = Image.new("RGBA", im.size, (18, 12, 24, 255))
            base.alpha_composite(im)
            base.thumbnail((320, 200))
            self._intro_ph = ImageTk.PhotoImage(base)
            tk.Label(self.side, image=self._intro_ph, bg=PANEL).pack(anchor="w", pady=8)

        def to_settings():
            self.app.show("settings")
            try:
                self.app.pages["settings"].open("intro")
            except Exception:
                pass
            self.app.root.lift()
        RoundButton(self.side, "Change my intro…", to_settings, small=True, bg=PANEL).pack(anchor="w", pady=4)

    def _tab_adjust(self):
        self.h("Adjust", "Fine-tune the look. Most people never need this.")
        self._slide("brightness", "Brightness", -15, 25)
        self._slide("glow", "Glow / haze", 0, 40)
        self._slide("trans_len", "Transition length", 0.03, 0.6, "s", 0.01)
        self._slide("fade_in", "Fade in at start", 0, 1.5, "s", 0.05)
        self._slide("fade_out", "Fade out at end", 0, 2.5, "s", 0.05)
        r = tk.Frame(self.side, bg=PANEL)
        r.pack(fill="x", pady=8)
        tk.Label(r, text="Sharpen", fg=TEXT, bg=PANEL, font=(FONT_SEMI, 10)).pack(side="left")
        v = tk.BooleanVar(value=bool(self.style.get("sharpen")))
        Toggle(r, v, command=lambda: self.set_style(sharpen=bool(v.get())), bg=PANEL).pack(side="right")

    def _slide(self, key, label, lo, hi, suffix="", step=1):
        tk.Label(self.side, text=label, fg=TEXT, bg=PANEL, font=(FONT_SEMI, 10)).pack(anchor="w", pady=(10, 2))

        def done(v, key=key):
            if isinstance(core.DEFAULT_STYLE.get(key), int):
                v = int(round(v))
            self.set_style(**{key: v})
        Slider(self.side, self.style.get(key, core.DEFAULT_STYLE.get(key, lo)), lo, hi, done, suffix=suffix,
               step=step, length=240).pack(anchor="w")

    def pick_music(self):
        f = filedialog.askopenfilename(parent=self, title="Pick a song",
                                       filetypes=[("Audio", "*.mp3 *.wav *.m4a *.aac *.ogg *.flac"), ("All", "*.*")])
        if f:
            self.set_style(music=f)
            self.open_tab("music")

    def save_default(self):
        self.app.s["style"] = dict(self.style)
        core.save_settings(self.app.s)
        self.say("Saved. Auto-clip and new edits will use this look.", GOOD)

    # ================================================================== format / preview / export
    def set_fmt(self, key, quiet=False):
        self.fmt = key
        for k, b in self.fmt_btns.items():
            b.config(kind="chip_on" if k == key else "chip")
        self.preview_path = None
        if not quiet:
            self.say("Previewing as " + ("TikTok / Shorts (vertical)." if key == "vertical" else
                                         "YouTube (widescreen)."))
            if self.mode == "preview":
                self.make_preview()

    def set_mode(self, mode):
        self.mode = mode
        for w in self.banner.winfo_children():
            w.destroy()
        if mode == "preview":
            tk.Label(self.banner, text="●  Preview of your edit", fg=GOOD, bg=BG, font=(FONT_SEMI, 10)).pack(
                side="left")
            RoundButton(self.banner, "←  Back to editing", self.back_to_edit, small=True, bg=BG).pack(side="left",
                                                                                                    padx=10)
        for b in self.tool_btns:
            b.config(state="normal" if mode == "edit" else "disabled")
        self.draw_timeline()

    def back_to_edit(self):
        self.player.pause()
        self.set_mode("edit")
        self.player.load(self.c["path"], start=self.segs[self.sel][0], dur=self.dur, play=False)
        self.draw_timeline()

    def say(self, text, color=MUTED):
        if self.winfo_exists():
            self.status.config(text=text, fg=color)

    def _segments(self):
        return [(self.c["path"], s, e) for s, e in self.segs if e - s >= 0.2]

    def _run(self, label, fn, done):
        if self.busy:
            self.say("Hang on, still working on the last one…", WARN)
            return
        self.busy = True
        self.persist()
        for b in (self.preview_b, self.export_b):
            b.config(state="disabled")
        self.say(label, ACCENT)

        def work():
            try:
                enc = self.app.ensure_enc()
                res = fn(enc, lambda m: self.after(0, lambda: self.say(f"{label}  {m.strip()}", ACCENT)))
                self.after(0, lambda: done(res))
            except Exception as e:
                msg = str(e)
                self.after(0, lambda: self.say("⚠ " + msg[-300:], WARN))
            finally:
                self.after(0, self._idle)
        threading.Thread(target=work, daemon=True).start()

    def _idle(self):
        self.busy = False
        if self.winfo_exists():
            for b in (self.preview_b, self.export_b):
                b.config(state="normal")

    def make_preview(self):
        self.player.pause()
        if self.preview_path and os.path.exists(self.preview_path):
            self.set_mode("preview")
            self.player.load(self.preview_path, play=True)
            return
        os.makedirs(PREVIEWS, exist_ok=True)
        fm = self.fmt
        out = os.path.join(PREVIEWS, f"{self.c['id']}_{fm}.mp4")
        segs, st, brand = self._segments(), dict(self.style), self.app.brand()

        def fn(enc, log):
            return core.render(enc, segs, out, log, style=st, preview=True, fmt=fm, brand=brand)

        def done(_):
            self.preview_path = out
            self.set_mode("preview")
            self.player.load(out, play=True)
            self.say("Here's your edit (quick preview quality). Happy? Hit Export.", GOOD)
        self._run("Making a quick preview…", fn, done)

    def export_menu(self):
        m = tk.Menu(self, tearoff=0, bg=CARD, fg=TEXT, activebackground=ACCENT, activeforeground="#ffffff", bd=0,
                    font=(FONT, 10))
        m.add_command(label="  ▯  TikTok / Shorts   (1080×1920)", command=lambda: self.export(["vertical"]))
        m.add_command(label="  ▭  YouTube   (1920×1080)", command=lambda: self.export(["wide"]))
        m.add_separator()
        m.add_command(label="  ★  Both", command=lambda: self.export(["vertical", "wide"]))
        try:
            m.tk_popup(self.export_b.winfo_rootx(), self.export_b.winfo_rooty() + self.export_b.h + 4)
        finally:
            m.grab_release()

    def export(self, fmts):
        self.player.pause()
        segs, st, brand = self._segments(), dict(self.style), self.app.brand()

        def fn(enc, log):
            return [core.make_highlight(enc, self.c, self.app.out_dir(f), self.app.state, log, segments=segs,
                                        style=st, fmt=f, brand=brand) for f in fmts]

        def done(outs):
            names = " + ".join("YouTube" if "(YouTube)" in o else "TikTok" for o in outs)
            for o in outs:
                self.app.q.put(("made", (self.c["id"], o)))
            self.set_mode("preview")
            self.player.load(outs[0], play=False)
            self.say(f"✅ Exported ({names}). It's in your {names} tab.", GOOD)
        what = " + ".join("YouTube" if f == "wide" else "TikTok" for f in fmts)
        self._run(f"Exporting {what} in full quality (about a minute)…", fn, done)

    def close(self):
        if self.busy and not messagebox.askyesno("Still working", "A video is still being made. Close anyway? "
                                                 "It keeps going in the background.", parent=self):
            return
        self.persist()
        self.player.stop()
        self.destroy()
