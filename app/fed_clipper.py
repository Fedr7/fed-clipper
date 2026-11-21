"""
Fed Clipper - your Rainbow Six Siege clip gallery + Fed-style TikTok maker.
Opens to every R6 clip Medal has saved. Click a clip to play it or turn it into a Fed TikTok.
"""
import os
import queue
import threading
import time
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import filedialog, messagebox, simpledialog

from PIL import Image, ImageTk

import autoclip_core as core
import updater
from editor import Editor
from player import Player

BG, PANEL, CARD, CARD_HI = "#0e0b14", "#15101e", "#1c1628", "#2a2040"
TEXT, MUTED, ACCENT, GOOD = "#f1edff", "#9d93b8", "#b9a6ff", "#7ee2a8"
BADGE = {"ACE": "#ff5d7a", "4K": "#ffa94d", "3K": "#ffd43b"}
FONT = "Segoe UI"
PAGE = 48
COLS = 3


def startfile(path):
    try:
        os.startfile(path)  # Windows
    except AttributeError:
        messagebox.showinfo("Open", path)


class App:
    def __init__(self, root):
        self.root = root
        root.title(f"Fed Clipper  v{updater.local_version()}")
        root.configure(bg=BG)
        root.geometry("1320x820")
        root.minsize(1100, 680)
        icon = os.path.join(core.ASSETS, "fed.ico")
        if os.path.exists(icon):
            try:
                root.iconbitmap(icon)
            except tk.TclError:
                pass
        self.s = core.load_settings()
        if not self.s.get("only_new_after"):
            self.s["only_new_after"] = datetime.now().isoformat(timespec="seconds")
            core.save_settings(self.s)
        self.state = core.load_state()
        self.q = queue.Queue()
        self.jobs = queue.Queue()
        self.enc = None
        self.clips = []
        self.shown = []
        self.limit = PAGE
        self.filter = "All"
        self.selected = None
        self.images = {}          # keep PhotoImage refs alive
        self.cards = {}
        self.thumb_wanted = queue.LifoQueue()
        self.watching = False
        self.busy_label = ""
        self._build()
        threading.Thread(target=self._job_worker, daemon=True).start()
        threading.Thread(target=self._thumb_worker, daemon=True).start()
        self.root.after(100, self._pump)
        self.reload()
        threading.Thread(target=self._check_update, daemon=True).start()

    # ------------------------------------------------------------------ patches
    def _check_update(self):
        try:
            m = updater.check()
            if m:
                self.q.put(("patch", m))
        except Exception:
            pass  # offline or GitHub down: just skip

    def _show_patch(self, m):
        self.patch = m
        bar = tk.Frame(self.root, bg=ACCENT)
        bar.pack(fill="x", before=self.top, padx=0, pady=0)
        notes = m.get("notes", "")
        tk.Label(bar, text=f"🔥  Patch {m['version']} is out" + (f" — {notes}" if notes else ""),
                 fg="#140d22", bg=ACCENT, font=(FONT, 10, "bold")).pack(side="left", padx=16, pady=6)
        tk.Button(bar, text="Update now", command=self._do_update, bg="#140d22", fg=TEXT, relief="flat", bd=0,
                  padx=14, pady=4, font=(FONT, 10, "bold"), cursor="hand2").pack(side="right", padx=12, pady=4)
        self.patch_bar = bar

    def _do_update(self):
        self.status.config(text="Updating…")
        def work():
            try:
                updater.apply(self.patch, log=lambda t: self.q.put(("status", t)))
                self.q.put(("patched", None))
            except Exception as e:
                self.q.put(("error", f"Update failed (nothing changed): {e}"))
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------ layout
    def btn(self, parent, text, cmd, accent=False):
        b = tk.Button(parent, text=text, command=cmd, bg=ACCENT if accent else CARD_HI,
                      fg="#140d22" if accent else TEXT, activebackground="#cfc2ff" if accent else "#3a2d58",
                      activeforeground="#140d22" if accent else TEXT, relief="flat", bd=0, padx=14, pady=7,
                      font=(FONT, 10, "bold"), cursor="hand2")
        return b

    def _build(self):
        top = tk.Frame(self.root, bg=BG)
        self.top = top
        top.pack(fill="x", padx=18, pady=(14, 6))
        logo = os.path.join(core.ASSETS, "fed_logo_glow.png")
        if os.path.exists(logo):
            im = Image.open(logo)
            im.thumbnail((90, 60))
            self.logo_img = ImageTk.PhotoImage(im)
            tk.Label(top, image=self.logo_img, bg=BG).pack(side="left")
        tk.Label(top, text="CLIPPER", fg=ACCENT, bg=BG, font=(FONT, 16, "bold")).pack(side="left", padx=(6, 24))
        self.auto_btn = self.btn(top, "▶  Auto-clip new highlights", self.toggle_auto)
        self.auto_btn.pack(side="left")
        self.btn(top, "⚡  Make Aim Short", self.aim_short).pack(side="left", padx=8)
        self.btn(top, "Make from last X days…", self.older).pack(side="left")
        self.btn(top, "⚙", self.settings).pack(side="right")
        self.btn(top, "📁  Upload folder", lambda: self.open_dir(self.s["output_dir"])).pack(side="right", padx=8)
        self.btn(top, "⟳", self.reload).pack(side="right")

        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=18, pady=(4, 8))
        self.filter_btns = {}
        for f in ("All", "Aces", "4K+", "3K+", "Multi-kills", "Made"):
            b = tk.Button(bar, text=f, relief="flat", bd=0, padx=12, pady=4, font=(FONT, 9, "bold"),
                          cursor="hand2", command=lambda f=f: self.set_filter(f))
            b.pack(side="left", padx=(0, 6))
            self.filter_btns[f] = b
        tk.Label(bar, text="  Search", fg=MUTED, bg=BG, font=(FONT, 9)).pack(side="left", padx=(16, 6))
        self.search_v = tk.StringVar()
        e = tk.Entry(bar, textvariable=self.search_v, bg=CARD, fg=TEXT, insertbackground=TEXT, relief="flat",
                     font=(FONT, 10), width=24)
        e.pack(side="left", ipady=4)
        self.search_v.trace_add("write", lambda *a: self.apply_filter())
        self.count_l = tk.Label(bar, text="", fg=MUTED, bg=BG, font=(FONT, 9))
        self.count_l.pack(side="right")

        main = tk.Frame(self.root, bg=BG)
        main.pack(fill="both", expand=True, padx=18, pady=(0, 8))

        # gallery (scrollable)
        gwrap = tk.Frame(main, bg=BG)
        gwrap.pack(side="left", fill="both", expand=True)
        self.canvas = tk.Canvas(gwrap, bg=BG, highlightthickness=0)
        sb = tk.Scrollbar(gwrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.grid = tk.Frame(self.canvas, bg=BG)
        self.canvas.create_window((0, 0), window=self.grid, anchor="nw")
        self.grid.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind_all("<MouseWheel>", self._wheel)

        # detail panel
        self.detail = tk.Frame(main, bg=PANEL, width=430)
        self.detail.pack(side="right", fill="y", padx=(14, 0))
        self.detail.pack_propagate(False)
        self.player = Player(self.detail, 394, 222, bg=PANEL, fg=TEXT, accent=ACCENT)
        self.player.pack(padx=16, pady=(16, 10))
        self.d_title = tk.Label(self.detail, text="Pick a clip", fg=TEXT, bg=PANEL, font=(FONT, 15, "bold"),
                                wraplength=390, justify="left")
        self.d_title.pack(anchor="w", padx=18)
        self.d_info = tk.Label(self.detail, text="Click any clip on the left.", fg=MUTED, bg=PANEL,
                               font=(FONT, 10), wraplength=390, justify="left")
        self.d_info.pack(anchor="w", padx=18, pady=(4, 14))
        self.d_play = self.btn(self.detail, "✂  Edit & customize", self.edit_selected)
        self.d_win = self.btn(self.detail, "⤢  Open in Windows player", self.play)
        self.d_make = self.btn(self.detail, "★  Make Fed TikTok", self.make_selected, accent=True)
        self.d_open = self.btn(self.detail, "▶  Watch my Fed video", self.open_made)
        self.d_status = tk.Label(self.detail, text="", fg=GOOD, bg=PANEL, font=(FONT, 10), wraplength=390,
                                 justify="left")

        self.status = tk.Label(self.root, text="Loading your clips…", fg=MUTED, bg=BG, font=(FONT, 9), anchor="w")
        self.status.pack(fill="x", padx=18, pady=(0, 10))
        self._paint_filters()

    def _wheel(self, e):
        self.canvas.yview_scroll(int(-e.delta / 120), "units")

    def open_dir(self, d):
        os.makedirs(d, exist_ok=True)
        startfile(d)

    # ------------------------------------------------------------------ data + filter
    def reload(self):
        self.status.config(text="Reading Medal's clips…")

        def work():
            try:
                clips = core.read_clips(self.s["medal_dir"])
                clips = [c for c in clips if os.path.exists(c["path"])]
                self.q.put(("clips", clips))
            except Exception as e:
                self.q.put(("error", f"Couldn't read Medal: {e}"))
        threading.Thread(target=work, daemon=True).start()

    def set_filter(self, f):
        self.filter = f
        self._paint_filters()
        self.apply_filter()

    def _paint_filters(self):
        for f, b in self.filter_btns.items():
            on = f == self.filter
            b.config(bg=ACCENT if on else CARD, fg="#140d22" if on else TEXT,
                     activebackground="#cfc2ff" if on else CARD_HI)

    def made(self, c):
        d = self.state["done"].get(c["id"])
        return d.get("made") if d and d.get("made") and os.path.exists(d["made"]) else None

    def apply_filter(self):
        q = self.search_v.get().strip().lower()
        f = self.filter
        out = []
        for c in self.clips:
            if f == "Aces" and c["top"] < 5: continue
            if f == "4K+" and c["top"] < 4: continue
            if f == "3K+" and c["top"] < 3: continue
            if f == "Multi-kills" and len(c["kills"]) < 2: continue
            if f == "Made" and not self.made(c): continue
            if q and q not in (c["title"] + " " + core.clip_time(c).strftime("%b %d")).lower(): continue
            out.append(c)
        self.shown = out
        self.limit = PAGE
        self.count_l.config(text=f"{len(out)} clips")
        self.render_grid()

    def render_grid(self):
        for w in self.grid.winfo_children():
            w.destroy()
        self.cards = {}
        # drain stale thumbnail requests
        while not self.thumb_wanted.empty():
            try:
                self.thumb_wanted.get_nowait()
            except queue.Empty:
                break
        for i, c in enumerate(self.shown[:self.limit]):
            self._card(c, i // COLS, i % COLS)
        if len(self.shown) > self.limit:
            more = self.btn(self.grid, f"Show more ({len(self.shown) - self.limit} left)", self.more)
            more.grid(row=self.limit // COLS + 1, column=0, columnspan=COLS, pady=14)
        self.canvas.yview_moveto(0)

    def more(self):
        self.limit += PAGE
        self.render_grid()

    def _card(self, c, r, col):
        f = tk.Frame(self.grid, bg=CARD, cursor="hand2")
        f.grid(row=r, column=col, padx=7, pady=7, sticky="n")
        img = tk.Label(f, bg="#08060c", width=320 // 8, height=180 // 18)
        img.pack()
        kind, op, mp = core.parts(c)
        line1 = tk.Frame(f, bg=CARD)
        line1.pack(fill="x", padx=10, pady=(8, 0))
        if kind:
            tk.Label(line1, text=kind, bg=BADGE.get(kind, CARD_HI), fg="#140d22" if kind in BADGE else TEXT,
                     font=(FONT, 8, "bold"), padx=6).pack(side="left")
        tk.Label(line1, text="  " + " · ".join(x for x in (op, mp) if x), fg=TEXT, bg=CARD,
                 font=(FONT, 10, "bold")).pack(side="left")
        if self.made(c):
            tk.Label(line1, text="✓ made", fg=GOOD, bg=CARD, font=(FONT, 8, "bold")).pack(side="right")
        tk.Label(f, text=core.clip_time(c).strftime("%a %b %d · %I:%M %p"), fg=MUTED, bg=CARD,
                 font=(FONT, 9)).pack(anchor="w", padx=10, pady=(2, 9))
        for w in (f, img, line1) + tuple(line1.winfo_children()) + tuple(f.winfo_children()):
            w.bind("<Button-1>", lambda e, c=c: self.select(c))
        self.cards[c["id"]] = (f, img)
        tp = core.thumb_path(c)
        if os.path.exists(tp):
            self._set_thumb(c["id"], tp)
        else:
            self.thumb_wanted.put(c)

    def _set_thumb(self, cid, path):
        card = self.cards.get(cid)
        if not card:
            return
        try:
            im = ImageTk.PhotoImage(Image.open(path))
        except Exception:
            return
        self.images[cid] = im
        card[1].config(image=im, width=320, height=180)

    # ------------------------------------------------------------------ detail
    def select(self, c):
        self.selected = c
        for cid, (f, _) in self.cards.items():
            f.config(bg=CARD_HI if cid == c["id"] else CARD)
        tp = core.thumb_path(c)
        if os.path.exists(tp):
            try:
                self.player.show_image(Image.open(tp))
            except Exception:
                pass
        first = c["kills"][0] - 3.0 if c["kills"] else 0.0
        self.player.load(c["path"], start=max(0.0, first), dur=c.get("dur") or None)
        kind, op, mp = core.parts(c)
        self.d_title.config(text=" · ".join(x for x in (kind or "Clip", op, mp) if x))
        when = core.clip_time(c).strftime("%A %b %d, %I:%M %p")
        kills = ", ".join(f"{k:.1f}s" for k in c["kills"]) or "none logged by Medal"
        self.d_info.config(text=f"{when}\nKills at: {kills}\nMode: {c['mode']}")
        for w in (self.d_play, self.d_win, self.d_make, self.d_open, self.d_status):
            w.pack_forget()
        self.d_make.pack(fill="x", padx=18, pady=4)
        self.d_play.pack(fill="x", padx=18, pady=4)
        if self.made(c):
            self.d_open.pack(fill="x", padx=18, pady=4)
            self.d_status.config(text="✓ You already made a Fed video from this clip.")
        else:
            self.d_status.config(text="")
        self.d_win.pack(fill="x", padx=18, pady=4)
        self.d_status.pack(anchor="w", padx=18, pady=10)

    def play(self):
        if self.selected:
            startfile(self.selected["path"])

    def open_made(self):
        if self.selected and self.made(self.selected) and os.path.exists(self.made(self.selected)):
            self.player.load(self.made(self.selected), play=True)

    def edit_selected(self):
        if not self.selected:
            return
        self.player.pause()
        Editor(self, self.selected)

    def make_selected(self):
        if not self.selected:
            return
        c = self.selected
        self.d_status.config(text="Queued — editing it now. You can keep browsing.", fg=ACCENT)
        self.jobs.put(("one", c))

    # ------------------------------------------------------------------ top actions
    def toggle_auto(self):
        self.watching = not self.watching
        self.auto_btn.config(text="■  Stop auto-clip" if self.watching else "▶  Auto-clip new highlights",
                             bg=ACCENT if self.watching else CARD_HI, fg="#140d22" if self.watching else TEXT)
        if self.watching:
            self._auto_tick()

    def _auto_tick(self):
        if not self.watching:
            return
        self.jobs.put(("scan", None))
        self.root.after(max(1, int(self.s.get("watch_minutes", 5))) * 60000, self._auto_tick)

    def aim_short(self):
        self.jobs.put(("short", None))
        self.status.config(text="Building an Aim Short from your fastest multi-kills…")

    def older(self):
        days = simpledialog.askinteger("Make from older clips", "Make Fed videos from highlights in the last how many days?",
                                       minvalue=1, maxvalue=365, initialvalue=1, parent=self.root)
        if days:
            since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
            self.jobs.put(("scan", since))

    def settings(self):
        w = tk.Toplevel(self.root, bg=PANEL)
        w.title("Settings")
        w.geometry("560x260")
        vals = {}
        rows = [("Medal folder", "medal_dir", True), ("Save Fed videos to", "output_dir", True)]
        for i, (lbl, key, browse) in enumerate(rows):
            tk.Label(w, text=lbl, fg=TEXT, bg=PANEL, font=(FONT, 10)).grid(row=i, column=0, sticky="w", padx=14, pady=8)
            v = tk.StringVar(value=self.s[key]); vals[key] = v
            tk.Entry(w, textvariable=v, width=44, bg=CARD, fg=TEXT, insertbackground=TEXT, relief="flat").grid(
                row=i, column=1, padx=6, ipady=3)
            tk.Button(w, text="…", relief="flat", bg=CARD_HI, fg=TEXT,
                      command=lambda v=v: v.set(filedialog.askdirectory() or v.get())).grid(row=i, column=2)
        tk.Label(w, text="Auto-clip when I get", fg=TEXT, bg=PANEL, font=(FONT, 10)).grid(row=2, column=0, sticky="w", padx=14, pady=8)
        mk = tk.StringVar(value={3: "3K or better", 4: "4K or better", 5: "Aces only"}[self.s["min_kills"]])
        tk.OptionMenu(w, mk, "3K or better", "4K or better", "Aces only").grid(row=2, column=1, sticky="w", padx=6)
        tk.Label(w, text="Check every (minutes)", fg=TEXT, bg=PANEL, font=(FONT, 10)).grid(row=3, column=0, sticky="w", padx=14, pady=8)
        ev = tk.StringVar(value=str(self.s["watch_minutes"]))
        tk.Entry(w, textvariable=ev, width=6, bg=CARD, fg=TEXT, insertbackground=TEXT, relief="flat").grid(
            row=3, column=1, sticky="w", padx=6, ipady=3)

        def save():
            for k, v in vals.items():
                self.s[k] = v.get().strip()
            self.s["min_kills"] = {"3K or better": 3, "4K or better": 4, "Aces only": 5}[mk.get()]
            try:
                self.s["watch_minutes"] = max(1, int(ev.get()))
            except ValueError:
                pass
            core.save_settings(self.s)
            w.destroy()
            self.reload()
        self.btn(w, "Save", save, accent=True).grid(row=4, column=1, sticky="e", pady=14)

    # ------------------------------------------------------------------ background workers
    def ensure_enc(self):
        if self.enc is None:
            ff = core.find_ffmpeg()
            if not ff:
                raise RuntimeError("FFmpeg is missing — run Install.bat again.")
            self.enc = core.Encoder(ff)
        return self.enc

    def _thumb_worker(self):
        while True:
            c = self.thumb_wanted.get()
            try:
                p = core.make_thumb(self.ensure_enc(), c)
                if p:
                    self.q.put(("thumb", (c["id"], p)))
            except Exception:
                time.sleep(1)

    def _job_worker(self):
        while True:
            kind, arg = self.jobs.get()
            try:
                enc = self.ensure_enc()
                log = lambda m: self.q.put(("status", m.strip()))
                if kind == "one":
                    out = core.make_highlight(enc, arg, self.s["output_dir"], self.state, log, style=self.s.get("style"))
                    self.q.put(("made", (arg["id"], out)))
                elif kind == "short":
                    out = core.make_aim_short(enc, self.clips, self.s, self.s["output_dir"], log=log)
                    self.q.put(("done", f"✅ Saved {os.path.basename(out)}"))
                elif kind == "scan":
                    settings = dict(self.s)
                    if arg:
                        settings["only_new_after"] = arg
                    clips = core.read_clips(self.s["medal_dir"])
                    new = [c for c in core.pick_new_highlights(clips, settings, self.state)
                           if os.path.exists(c["path"]) and time.time() - os.path.getmtime(c["path"]) > 90]
                    core.save_state(self.state)
                    if not new:
                        self.q.put(("done", "No new highlights to edit."))
                    for i, c in enumerate(new, 1):
                        self.q.put(("status", f"Editing highlight {i}/{len(new)}…"))
                        out = core.make_highlight(enc, c, self.s["output_dir"], self.state, log, style=self.s.get("style"))
                        self.q.put(("made", (c["id"], out)))
                    if new:
                        self.q.put(("reload", None))
            except Exception as e:
                self.q.put(("error", str(e)))

    def _pump(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "clips":
                    self.clips = val
                    self.status.config(text=f"{len(val)} clips from Medal. Click one to play it or make a Fed TikTok.")
                    self.apply_filter()
                elif kind == "thumb":
                    self._set_thumb(*val)
                    if self.selected and self.selected["id"] == val[0]:
                        self.select(self.selected)
                elif kind == "status":
                    self.status.config(text=val)
                elif kind == "done":
                    self.status.config(text=val)
                elif kind == "made":
                    cid, out = val
                    self.status.config(text=f"✅ Saved {os.path.basename(out)} to your Upload folder")
                    if self.selected and self.selected["id"] == cid:
                        self.select(self.selected)
                        self.d_status.config(text="✅ Done! Saved to your Upload folder.", fg=GOOD)
                elif kind == "reload":
                    self.reload()
                elif kind == "patch":
                    self._show_patch(val)
                elif kind == "patched":
                    messagebox.showinfo("Fed Clipper", f"Updated to {self.patch['version']}! Restarting…")
                    updater.restart()
                    self.root.destroy()
                    return
                elif kind == "error":
                    self.status.config(text="⚠ " + val)
        except queue.Empty:
            pass
        self.root.after(100, self._pump)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
