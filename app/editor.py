"""Breachcut editor: tweak the cuts and the look of an edit, preview it, save it as TikTok and/or YouTube."""
import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

import autoclip_core as core
from player import Player, fmt

BG, PANEL, CARD, CARD_HI = "#0d0b12", "#13101a", "#1a1622", "#251f31"
TEXT, MUTED, ACCENT, GOOD, WARN = "#f3f0fa", "#958da8", "#ff4d6d", "#5ee0a0", "#ffb454"
FONT = "Segoe UI"
PREVIEWS = os.path.join(core.APP_DIR, "previews")


class Editor(tk.Toplevel):
    def __init__(self, app, clip):
        super().__init__(app.root, bg=BG)
        self.app, self.c = app, clip
        kind, op, mp = core.parts(clip)
        self.title("Edit · " + " · ".join(x for x in (kind or "Clip", op, mp) if x))
        self.geometry("1240x820")
        self.minsize(1100, 760)
        for ico in ("breachcut.ico", "fed.ico"):
            icon = os.path.join(core.ASSETS, ico)
            if os.path.exists(icon):
                try:
                    self.iconbitmap(icon)
                    break
                except tk.TclError:
                    pass
        self.edits = core.load_edits()
        mine = self.edits.get(clip["id"]) or {}
        self.segs = [list(x) for x in mine.get("segments") or
                     [[round(s, 2), round(e, 2)] for _, s, e in core.clip_segments(clip)]]
        self.style = dict(core.DEFAULT_STYLE)
        self.style.update(app.s.get("style") or {})
        self.style.update(mine.get("style") or {})
        self.sel = 0
        self.busy = False
        self.mode = "source"
        self.preview_path = None
        self.pfmt = tk.StringVar(value="vertical")
        self._build()
        self.show_source(play=False)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<space>", lambda e: self.player.toggle() if not isinstance(e.widget, (tk.Entry, tk.Spinbox)) else None)

    # ------------------------------------------------------------------ layout
    def btn(self, parent, text, cmd, accent=False, small=False):
        return tk.Button(parent, text=text, command=cmd, bg=ACCENT if accent else CARD_HI,
                         fg="#ffffff" if accent else TEXT, activebackground="#ff7590" if accent else "#342b45",
                         activeforeground="#ffffff" if accent else TEXT, relief="flat", bd=0,
                         padx=8 if small else 12, pady=3 if small else 6,
                         font=(FONT, 9 if small else 10, "bold"), cursor="hand2")

    def section(self, parent, text):
        tk.Label(parent, text=text, fg=ACCENT, bg=PANEL, font=(FONT, 11, "bold")).pack(anchor="w", padx=14,
                                                                                     pady=(12, 4))

    def _build(self):
        left = tk.Frame(self, bg=BG)
        left.pack(side="left", fill="y", padx=(16, 8), pady=14)
        top = tk.Frame(left, bg=BG)
        top.pack(fill="x", pady=(0, 6))
        self.src_btn = self.btn(top, "Original clip", lambda: self.show_source(), small=True)
        self.src_btn.pack(side="left")
        self.prev_btn = self.btn(top, "Preview", self.show_preview, small=True)
        self.prev_btn.pack(side="left", padx=6)
        for val, txt in (("vertical", "▯ TikTok"), ("wide", "▭ YouTube")):
            tk.Radiobutton(top, text=txt, value=val, variable=self.pfmt, indicatoron=False, bg=CARD, fg=TEXT,
                           selectcolor=ACCENT, activebackground=CARD_HI, activeforeground=TEXT, relief="flat", bd=0,
                           padx=8, pady=3, font=(FONT, 9, "bold"), cursor="hand2",
                           command=self._pfmt_changed).pack(side="left", padx=(2, 0))
        self.mode_l = tk.Label(top, text="", fg=MUTED, bg=BG, font=(FONT, 9))
        self.mode_l.pack(side="left", padx=6)
        self.player = Player(left, 600, 620, bg=BG, fg=TEXT, accent=ACCENT, on_time=self._on_time)
        self.player.pack()
        marks = tk.Frame(left, bg=BG)
        marks.pack(fill="x", pady=(8, 0))
        self.mark_s = self.btn(marks, "⟦  Start part here", self.mark_start, small=True)
        self.mark_s.pack(side="left")
        self.mark_e = self.btn(marks, "End part here  ⟧", self.mark_end, small=True)
        self.mark_e.pack(side="left", padx=6)
        self.add_b = self.btn(marks, "+ New part here", self.add_part, small=True)
        self.add_b.pack(side="left")
        kills = tk.Frame(left, bg=BG)
        kills.pack(fill="x", pady=(8, 0))
        tk.Label(kills, text="Kills:", fg=MUTED, bg=BG, font=(FONT, 9)).pack(side="left")
        for i, k in enumerate(self.c["kills"][:12], 1):
            tk.Button(kills, text=f"K{i} {fmt(k)}", command=lambda k=k: self.jump(k - 2.0), relief="flat", bd=0,
                      bg=CARD, fg=TEXT, padx=6, pady=1, font=(FONT, 8, "bold"), cursor="hand2").pack(side="left", padx=2)
        if not self.c["kills"]:
            tk.Label(kills, text="none logged", fg=MUTED, bg=BG, font=(FONT, 9)).pack(side="left", padx=4)

        right = tk.Frame(self, bg=PANEL)
        right.pack(side="right", fill="both", expand=True, padx=(8, 16), pady=14)

        # ---- parts
        self.section(right, "Cuts  (what goes in the video, in order)")
        self.parts_f = tk.Frame(right, bg=PANEL)
        self.parts_f.pack(fill="x", padx=14)
        self.total_l = tk.Label(right, text="", fg=MUTED, bg=PANEL, font=(FONT, 9))
        self.total_l.pack(anchor="w", padx=14, pady=(4, 0))

        # ---- look
        self.section(right, "Look")
        self.v = {}
        row = tk.Frame(right, bg=PANEL)
        row.pack(fill="x", padx=14)
        for key, text in (("intro", "Name intro"), ("voice", "Intro voice"), ("sharpen", "Sharpen")):
            var = tk.BooleanVar(value=bool(self.style[key]))
            self.v[key] = var
            tk.Checkbutton(row, text=text, variable=var, bg=PANEL, fg=TEXT, selectcolor=CARD, activebackground=PANEL,
                           activeforeground=TEXT, font=(FONT, 10), command=self._changed).pack(side="left", padx=(0, 14))
        trow = tk.Frame(right, bg=PANEL)
        trow.pack(fill="x", padx=14, pady=(8, 0))
        tk.Label(trow, text="Between cuts", fg=TEXT, bg=PANEL, font=(FONT, 10), width=14, anchor="w").pack(side="left")
        self.v["transition"] = tk.StringVar(value=self.style["transition"])
        om = tk.OptionMenu(trow, self.v["transition"], "Flash", "Fade to black", "None",
                           command=lambda *_: self._transition_changed())
        om.config(bg=CARD_HI, fg=TEXT, relief="flat", bd=0, highlightthickness=0, activebackground="#3a2d58",
                  font=(FONT, 10), width=12)
        om.pack(side="left")
        sliders = [("trans_len", "Transition length", 0.03, 0.6, 0.01, "s"),
                   ("fade_in", "Fade in at start", 0.0, 1.5, 0.05, "s"),
                   ("fade_out", "Fade out at end", 0.0, 2.5, 0.05, "s"),
                   ("brightness", "Brightness", -15, 25, 1, ""),
                   ("glow", "Glow / haze", 0, 40, 1, ""),
                   ("game_vol", "Game volume", 0, 150, 5, "%")]
        for key, text, lo, hi, step, unit in sliders:
            self._slider(right, key, text, lo, hi, step)

        # ---- music
        self.section(right, "Music  (optional — or add it in TikTok)")
        mrow = tk.Frame(right, bg=PANEL)
        mrow.pack(fill="x", padx=14)
        self.v["music"] = tk.StringVar(value=self.style.get("music") or "")
        self.music_l = tk.Label(mrow, text="", fg=TEXT, bg=PANEL, font=(FONT, 9), width=40, anchor="w")
        self.music_l.pack(side="left")
        self.btn(mrow, "Pick song…", self.pick_music, small=True).pack(side="left", padx=4)
        self.btn(mrow, "No music", lambda: (self.v["music"].set(""), self._music_label(), self._changed()),
                 small=True).pack(side="left")
        self._music_label()
        self._slider(right, "music_vol", "Music volume", 0, 100, 5)

        # ---- actions
        act = tk.Frame(right, bg=PANEL)
        act.pack(side="bottom", fill="x", padx=14, pady=14)
        self.status = tk.Label(right, text="", fg=MUTED, bg=PANEL, font=(FONT, 10), wraplength=560, justify="left")
        self.status.pack(side="bottom", anchor="w", padx=14)
        self.btn(act, "↺ Reset to auto", self.reset).pack(side="left")
        self.btn(act, "Save as my default look", self.save_default).pack(side="left", padx=6)
        self.save_b = self.btn(act, "★  Save video", self.save_video, accent=True)
        self.save_b.pack(side="right")
        fr = tk.Frame(right, bg=PANEL)
        fr.pack(side="bottom", fill="x", padx=14)
        tk.Label(fr, text="Save as:", fg=TEXT, bg=PANEL, font=(FONT, 10, "bold")).pack(side="left")
        auto = self.app.s.get("auto_formats") or ["vertical"]
        self.out_v = {}
        for key, text in (("vertical", "TikTok / Shorts"), ("wide", "YouTube widescreen")):
            var = tk.BooleanVar(value=key in auto)
            self.out_v[key] = var
            tk.Checkbutton(fr, text=text, variable=var, bg=PANEL, fg=TEXT, selectcolor=CARD, activebackground=PANEL,
                           activeforeground=TEXT, font=(FONT, 10)).pack(side="left", padx=(8, 0))
        self.pv_b = self.btn(act, "👁  Preview", self.make_preview)
        self.pv_b.pack(side="right", padx=6)
        self._transition_changed(silent=True)
        self.draw_parts()

    def _slider(self, parent, key, text, lo, hi, step):
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", padx=14, pady=(2, 0))
        tk.Label(row, text=text, fg=TEXT, bg=PANEL, font=(FONT, 10), width=16, anchor="w").pack(side="left")
        var = tk.DoubleVar(value=float(self.style[key]))
        self.v[key] = var
        s = tk.Scale(row, from_=lo, to=hi, resolution=step, orient="horizontal", variable=var, length=330,
                     bg=PANEL, fg=TEXT, troughcolor=CARD_HI, highlightthickness=0, bd=0, sliderlength=14,
                     activebackground=ACCENT, font=(FONT, 8), command=lambda *_: self._changed())
        s.pack(side="left")
        if key == "trans_len":
            self.trans_scale = s

    # ------------------------------------------------------------------ parts list
    def draw_parts(self):
        for w in self.parts_f.winfo_children():
            w.destroy()
        dur = self.c.get("dur") or self.player.dur or 120
        for i, (s, e) in enumerate(self.segs):
            sel = i == self.sel
            r = tk.Frame(self.parts_f, bg=CARD_HI if sel else CARD)
            r.pack(fill="x", pady=2)
            tk.Button(r, text=f"Part {i + 1}", command=lambda i=i: self.pick(i), relief="flat", bd=0,
                      bg=ACCENT if sel else CARD_HI, fg="#ffffff" if sel else TEXT, font=(FONT, 9, "bold"),
                      width=7, cursor="hand2").pack(side="left", padx=(6, 8), pady=4)
            for j, lbl in ((0, "from"), (1, "to")):
                tk.Label(r, text=lbl, fg=MUTED, bg=r["bg"], font=(FONT, 9)).pack(side="left")
                var = tk.DoubleVar(value=round(self.segs[i][j], 1))
                sp = tk.Spinbox(r, from_=0, to=dur, increment=0.1, textvariable=var, width=6, bg=CARD, fg=TEXT,
                                buttonbackground=CARD_HI, insertbackground=TEXT, relief="flat", font=(FONT, 10),
                                command=lambda i=i, j=j, var=var: self.set_time(i, j, var))
                sp.pack(side="left", padx=(4, 10))
                sp.bind("<Return>", lambda e, i=i, j=j, var=var: self.set_time(i, j, var))
                sp.bind("<FocusOut>", lambda e, i=i, j=j, var=var: self.set_time(i, j, var, redraw=False))
            tk.Label(r, text=f"{e - s:.1f}s", fg=TEXT, bg=r["bg"], font=(FONT, 9, "bold"), width=5).pack(side="left")
            tk.Button(r, text="▶", command=lambda i=i: self.play_part(i), relief="flat", bd=0, bg=CARD_HI, fg=TEXT,
                      font=(FONT, 9), width=3, cursor="hand2").pack(side="left", padx=2)
            for txt, cmd in (("↑", lambda i=i: self.move(i, -1)), ("↓", lambda i=i: self.move(i, 1)),
                             ("✕", lambda i=i: self.remove(i))):
                tk.Button(r, text=txt, command=cmd, relief="flat", bd=0, bg=r["bg"], fg=MUTED,
                          font=(FONT, 9, "bold"), width=2, cursor="hand2").pack(side="left")
        total = sum(max(0, e - s) for s, e in self.segs)
        self.total_l.config(text=f"{len(self.segs)} part(s) · video length {total:.1f}s"
                                 + ("   ·   tip: TikTok sweet spot is 10–60s" if total > 60 else ""))

    def pick(self, i):
        self.sel = i
        self.draw_parts()
        if self.mode == "source":
            self.player.seek(self.segs[i][0], play=False)

    def set_time(self, i, j, var, redraw=True):
        try:
            val = float(var.get())
        except (tk.TclError, ValueError):
            return
        if i >= len(self.segs) or abs(self.segs[i][j] - val) < 1e-6:
            return
        self.segs[i][j] = max(0.0, val)
        if self.segs[i][1] < self.segs[i][0] + 0.3:
            if j == 0:
                self.segs[i][1] = self.segs[i][0] + 0.3
            else:
                self.segs[i][0] = max(0.0, self.segs[i][1] - 0.3)
        self.sel = i
        self._changed()
        if redraw:
            self.draw_parts()
        if self.mode == "source":
            self.player.seek(self.segs[i][j], play=False)

    def mark_start(self):
        if self.mode != "source" or not self.segs:
            return self.add_part() if not self.segs else None
        t = round(self.player.pos, 2)
        self.segs[self.sel][0] = t
        if self.segs[self.sel][1] < t + 0.3:
            self.segs[self.sel][1] = t + 3.0
        self._changed()
        self.draw_parts()

    def mark_end(self):
        if self.mode != "source" or not self.segs:
            return
        t = round(self.player.pos, 2)
        self.segs[self.sel][1] = max(t, self.segs[self.sel][0] + 0.3)
        self._changed()
        self.draw_parts()

    def add_part(self):
        if self.mode != "source":
            self.show_source()
        t = round(self.player.pos, 2)
        new = [max(0.0, t), t + 3.0]
        idx = next((k for k, (s, _) in enumerate(self.segs) if s > t), len(self.segs))
        self.segs.insert(idx, new)
        self.sel = idx
        self._changed()
        self.draw_parts()

    def move(self, i, d):
        j = i + d
        if 0 <= j < len(self.segs):
            self.segs[i], self.segs[j] = self.segs[j], self.segs[i]
            self.sel = j
            self._changed()
            self.draw_parts()

    def remove(self, i):
        if len(self.segs) <= 1:
            self.say("You need at least one part.", WARN)
            return
        self.segs.pop(i)
        self.sel = min(self.sel, len(self.segs) - 1)
        self._changed()
        self.draw_parts()

    def play_part(self, i):
        self.sel = i
        self.draw_parts()
        if self.mode != "source":
            self.show_source(play=False)
        self.player.seek(self.segs[i][0], play=False)
        self.player.play(until=self.segs[i][1])

    def jump(self, t):
        if self.mode != "source":
            self.show_source(play=False)
        self.player.seek(max(0.0, t), play=True)

    # ------------------------------------------------------------------ player modes
    def show_source(self, play=False):
        self.mode = "source"
        start = self.segs[self.sel][0] if self.segs else 0.0
        self.player.load(self.c["path"], start=start, dur=self.c.get("dur") or None, play=play)
        self.mode_l.config(text="Original Medal clip — mark your cuts here")
        self._paint_mode()

    def show_preview(self):
        if not self.preview_path or not os.path.exists(self.preview_path):
            self.make_preview()
            return
        self.mode = "preview"
        self.player.load(self.preview_path, play=True)
        self.mode_l.config(text="Preview (half quality, just for checking)")
        self._paint_mode()

    def _paint_mode(self):
        for b, m in ((self.src_btn, "source"), (self.prev_btn, "preview")):
            on = self.mode == m
            b.config(bg=ACCENT if on else CARD_HI, fg="#ffffff" if on else TEXT)
        st = "normal" if self.mode == "source" else "disabled"
        for b in (self.mark_s, self.mark_e):
            b.config(state=st)

    def _on_time(self, pos):
        pass

    def _pfmt_changed(self):
        self.preview_path = None
        if self.mode == "preview":
            self.make_preview()

    # ------------------------------------------------------------------ look
    def _transition_changed(self, silent=False):
        tr = self.v["transition"].get()
        if not silent:
            # sensible default length per transition type
            self.v["trans_len"].set({"Flash": 0.06, "Fade to black": 0.25}.get(tr, self.v["trans_len"].get()))
        self.trans_scale.config(state="disabled" if tr == "None" else "normal")
        if not silent:
            self._changed()

    def _music_label(self):
        m = self.v["music"].get()
        self.music_l.config(text=os.path.basename(m) if m else "No music (add it in TikTok)")

    def pick_music(self):
        f = filedialog.askopenfilename(parent=self, title="Pick a song",
                                       filetypes=[("Audio", "*.mp3 *.wav *.m4a *.aac *.ogg *.flac"), ("All", "*.*")])
        if f:
            self.v["music"].set(f)
            self._music_label()
            self._changed()

    def current_style(self):
        st = {}
        for k, v in self.v.items():
            val = v.get()
            if isinstance(core.DEFAULT_STYLE.get(k), bool):
                val = bool(val)
            elif isinstance(core.DEFAULT_STYLE.get(k), int) and not isinstance(core.DEFAULT_STYLE.get(k), bool):
                val = int(round(float(val)))
            elif isinstance(core.DEFAULT_STYLE.get(k), float):
                val = round(float(val), 3)
            st[k] = val
        return st

    def _changed(self):
        self.preview_stale = True
        self.persist()

    def persist(self):
        self.edits[self.c["id"]] = {"segments": [[round(s, 2), round(e, 2)] for s, e in self.segs],
                                    "style": self.current_style()}
        try:
            core.save_edits(self.edits)
        except OSError:
            pass

    def set_style(self, st):
        for k, v in st.items():
            if k in self.v:
                self.v[k].set(v)
        self._music_label()
        self._transition_changed(silent=True)

    def reset(self):
        self.segs = [[round(s, 2), round(e, 2)] for _, s, e in core.clip_segments(self.c)]
        base = dict(core.DEFAULT_STYLE)
        base.update(self.app.s.get("style") or {})
        self.set_style(base)
        self.sel = 0
        self.edits.pop(self.c["id"], None)
        core.save_edits(self.edits)
        self.draw_parts()
        self.say("Back to the auto cut + your default look.", GOOD)

    def save_default(self):
        st = self.current_style()
        self.app.s["style"] = st
        core.save_settings(self.app.s)
        self.say("Saved! Auto-clips and new edits will use this look from now on.", GOOD)

    # ------------------------------------------------------------------ rendering
    def say(self, text, color=MUTED):
        if self.winfo_exists():
            self.status.config(text=text, fg=color)

    def _segments(self):
        return [(self.c["path"], s, e) for s, e in self.segs if e - s >= 0.2]

    def _run(self, label, fn, done):
        if self.busy:
            self.say("Hang on — still working on the last one…", WARN)
            return
        self.busy = True
        self.persist()
        for b in (self.pv_b, self.save_b):
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
            for b in (self.pv_b, self.save_b):
                b.config(state="normal")

    def make_preview(self):
        self.player.pause()
        os.makedirs(PREVIEWS, exist_ok=True)
        fmt = self.pfmt.get()
        out = os.path.join(PREVIEWS, f"{self.c['id']}_{fmt}.mp4")
        segs, st, brand = self._segments(), self.current_style(), self.app.brand()

        def fn(enc, log):
            return core.render(enc, segs, out, log, style=st, preview=True, fmt=fmt, brand=brand)

        def done(_):
            self.preview_path = out
            self.preview_stale = False
            self.say("Preview ready ▶  Like it? Hit Save video for the full-quality version.", GOOD)
            self.show_preview()
        self._run("Making a quick preview…", fn, done)

    def save_video(self):
        fmts = [k for k, v in self.out_v.items() if v.get()]
        if not fmts:
            self.say("Tick TikTok / Shorts or YouTube widescreen first.", WARN)
            return
        self.player.pause()
        segs, st, brand = self._segments(), self.current_style(), self.app.brand()

        def fn(enc, log):
            outs = []
            for f in fmts:
                outs.append(core.make_highlight(enc, self.c, self.app.out_dir(f), self.app.state, log,
                                                segments=segs, style=st, fmt=f, brand=brand))
            return outs

        def done(outs):
            names = " + ".join("YouTube" if "(YouTube)" in o else "TikTok" for o in outs)
            self.say(f"✅ Saved ({names}). Find it in the TikTok / YouTube tab.", GOOD)
            for o in outs:
                self.app.q.put(("made", (self.c["id"], o)))
            self.mode = "preview"
            self.player.load(outs[0], play=False)
            self.mode_l.config(text="Your finished video")
            self._paint_mode()
        what = " + ".join("YouTube" if f == "wide" else "TikTok" for f in fmts)
        self._run(f"Making the full-quality {what} video (this takes a minute)…", fn, done)

    def close(self):
        if self.busy and not messagebox.askyesno("Still working",
                                                 "A video is still being made. Close anyway? It keeps going in the background.",
                                                 parent=self):
            return
        self.persist()
        self.player.stop()
        self.destroy()
