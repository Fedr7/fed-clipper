"""Breachcut Settings - a list of sections on the left, cards of options on the right.
Every option saves as soon as you change it.
"""
import os
import sys
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from tkinter import filedialog, messagebox

from PIL import Image, ImageTk

import auth
import autoclip_core as core
import ui_kit as ui
import updater
from ui_kit import (ACCENT, BG, CARD, CARD_HI, DIM, FONT, FONT_BOLD, FONT_SEMI, GOOD, LINE, MUTED, TEXT, VIOLET,
                    WARN, RoundButton, Toggle, CheckBox)

SECTIONS = (
    ("general", "⚙", "General"),
    ("intro", "✦", "Your intro"),
    ("autoclip", "🎯", "Auto-clip"),
    ("clips", "🎬", "Clips & videos"),
    ("editing", "✂", "Editing & export"),
    ("video", "🎥", "Video"),
    ("audio", "🔊", "Audio"),
    ("hotkeys", "⌨", "Hotkeys"),
    ("storage", "🗄", "Storage"),
    ("notifications", "🔔", "Notifications"),
    ("account", "👤", "Account"),
    ("about", "ⓘ", "About"),
)

# ---------------------------------------------------------------- Windows startup
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def set_startup(on):
    """Add/remove Breachcut from 'start with Windows' (current user only). Returns True on success."""
    if os.name != "nt":
        return False
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE)
        if on:
            exe = sys.executable
            if exe.lower().endswith("python.exe"):
                w = exe[:-10] + "pythonw.exe"
                if os.path.exists(w):
                    exe = w
            main = os.path.join(core.APP_DIR, "breachcut.py")
            winreg.SetValueEx(k, core.APP_NAME, 0, winreg.REG_SZ, f'"{exe}" "{main}" --startup')
        else:
            try:
                winreg.DeleteValue(k, core.APP_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(k)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- small widgets
class Dropdown(RoundButton):
    """Pill that opens a menu of choices."""

    def __init__(self, master, value, options, command, width=180, bg=None):
        self.options = list(options)
        self.on_pick = command
        self.value = value
        super().__init__(master, self._label(value), self._open, kind="normal", small=True, width=width, bg=bg)

    def _label(self, v):
        return f"{v}   ▾"

    def _open(self):
        m = tk.Menu(self, tearoff=0, bg=CARD, fg=TEXT, activebackground=ACCENT, activeforeground="#ffffff",
                    bd=0, relief="flat", font=(FONT, 10))
        for o in self.options:
            m.add_command(label=("✓  " if o == self.value else "     ") + str(o), command=lambda o=o: self.set(o, True))
        try:
            m.tk_popup(self.winfo_rootx(), self.winfo_rooty() + self.h + 2)
        finally:
            m.grab_release()

    def set(self, v, fire=False):
        self.value = v
        self.config(text=self._label(v))
        if fire and self.on_pick:
            self.on_pick(v)


class Slider(tk.Frame):
    """Flat slider with a value label; calls command(value) when released."""

    def __init__(self, master, value, frm, to, command, suffix="", step=1, length=220, fmt=None):
        bg = ui._bg_of(master)
        super().__init__(master, bg=bg)
        self.var = tk.DoubleVar(value=value)
        self.suffix, self.fmt, self.command = suffix, fmt, command
        self.scale = tk.Scale(self, from_=frm, to=to, resolution=step, orient="horizontal", variable=self.var,
                              showvalue=0, length=length, sliderlength=16, width=8, bd=0, highlightthickness=0,
                              troughcolor=CARD_HI, bg=ACCENT, activebackground="#ff6f8a", sliderrelief="flat",
                              relief="flat", command=lambda v: self._show())
        self.scale.pack(side="left")
        self.lab = tk.Label(self, text="", fg=TEXT, bg=bg, font=(FONT_SEMI, 10), width=8, anchor="w")
        self.lab.pack(side="left", padx=(10, 0))
        self.scale.bind("<ButtonRelease-1>", lambda e: self.command(self.get()))
        self._show()

    def get(self):
        v = self.var.get()
        return int(v) if float(v).is_integer() else round(v, 2)

    def _show(self):
        v = self.get()
        self.lab.config(text=self.fmt(v) if self.fmt else f"{v}{self.suffix}")


class HotkeyButton(RoundButton):
    """Click, then press a key combo. Esc cancels, Backspace clears."""

    def __init__(self, master, value, command, bg=None):
        self.value = value or "None"
        self.on_set = command
        self.listening = False
        super().__init__(master, self.value, self._listen, kind="chip", small=True, width=170, bg=bg)

    def _listen(self):
        if self.listening:
            return
        self.listening = True
        self.config(text="Press a key…", kind="chip_on")
        top = self.winfo_toplevel()
        self._bid = top.bind("<KeyPress>", self._key, add="+")
        self.focus_set()

    def _key(self, e):
        ks = e.keysym
        if ks in ("Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R", "Win_L", "Win_R",
                  "Super_L", "Super_R", "Meta_L", "Meta_R"):
            return "break"
        if ks == "Escape":
            self._done(self.value)
        elif ks in ("BackSpace", "Delete"):
            self._done("None")
        else:
            mods = []
            if e.state & 0x4:
                mods.append("Ctrl")
            if e.state & 0x1:
                mods.append("Shift")
            if e.state & (0x20000 if os.name == "nt" else 0x8):
                mods.append("Alt")
            key = ks.upper() if len(ks) == 1 else ks.replace("_", " ")
            self._done("+".join(mods + [key]))
        return "break"

    def _done(self, v):
        try:
            self.winfo_toplevel().unbind("<KeyPress>", self._bid)
        except Exception:
            self.winfo_toplevel().unbind("<KeyPress>")
        self.listening = False
        self.value = v
        self.config(text=v, kind="chip")
        if self.on_set:
            self.on_set(v)


def usage_bar(parent, parts, total, width=560, height=14):
    """Stacked bar: parts = [(bytes, color)], scaled to total."""
    bg = ui._bg_of(parent)
    c = tk.Canvas(parent, width=width, height=height, bg=bg, highlightthickness=0, bd=0)
    im = Image.new("RGBA", (width, height), ui.rgb(bg) + (255,))
    im.alpha_composite(ui.rounded_rect(width, height, height // 2, CARD_HI))
    x = 0
    for b, col in parts:
        if total <= 0 or b <= 0:
            continue
        w = max(4, int(width * min(1.0, b / total)))
        w = min(w, width - x)
        if w <= 0:
            break
        seg = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        seg.paste(ui.rgb(col) + (255,), (x, 0, x + w, height))
        mask = ui.rounded_rect(width, height, height // 2, "#ffffff").getchannel("A")
        seg.putalpha(Image.composite(seg.getchannel("A"), Image.new("L", seg.size, 0), mask))
        im.alpha_composite(seg)
        x += w
    c._img = ImageTk.PhotoImage(im)
    c.create_image(0, 0, image=c._img, anchor="nw")
    return c


# ---------------------------------------------------------------- the page
def make_settings_page(Page, ScrollGrid):
    class SettingsPage(Page):
        title = "Settings"
        subtitle = "Make Breachcut work your way. Changes save automatically."

        def __init__(self, app):
            super().__init__(app)
            self.s = app.s
            wrap = tk.Frame(self, bg=BG)
            wrap.pack(fill="both", expand=True, padx=(22, 6), pady=(4, 8))
            nav = tk.Frame(wrap, bg=BG, width=210)
            nav.pack(side="left", fill="y")
            nav.pack_propagate(False)
            self.navs = {}
            for key, icon, text in SECTIONS:
                b = tk.Label(nav, text=f"  {icon}   {text}", fg=MUTED, bg=BG, font=(FONT_SEMI, 10), anchor="w",
                             padx=10, pady=8, cursor="hand2")
                b.pack(fill="x", pady=1)
                b.bind("<Button-1>", lambda e, k=key: self.open(k))
                b.bind("<Enter>", lambda e, b=b, k=key: b.config(bg=CARD) if k != self.cur else None)
                b.bind("<Leave>", lambda e, b=b, k=key: b.config(bg=BG) if k != self.cur else None)
                self.navs[key] = b
            tk.Frame(wrap, bg=LINE, width=1).pack(side="left", fill="y", padx=(6, 14))
            self.scroll = ScrollGrid(wrap)
            self.scroll.pack(side="left", fill="both", expand=True)
            self.body = self.scroll.inner
            self.cur = None
            self._mic_list = None
            self.open("general")

        # ------------------------------------------------ plumbing
        def open(self, key):
            self.cur = key
            for k, b in self.navs.items():
                b.config(bg=CARD_HI if k == key else BG, fg=TEXT if k == key else MUTED)
            self.scroll.clear()
            getattr(self, "_" + key)()

        def on_show(self):
            if self.cur in ("storage", "account", "clips", "autoclip"):
                self.open(self.cur)

        def save(self, key=None, value=None, msg="Saved"):
            if key is not None:
                self.s[key] = value
            core.save_settings(self.s)
            core.apply_settings(self.s, self.app.enc)
            self.app.status(f"✓ {msg}")
            if hasattr(self.app, "settings_changed"):
                self.app.settings_changed(key)

        def heading(self, text, sub=""):
            tk.Label(self.body, text=text, fg=TEXT, bg=BG, font=(FONT_BOLD, 16)).pack(anchor="w", pady=(6, 0))
            if sub:
                tk.Label(self.body, text=sub, fg=MUTED, bg=BG, font=(FONT, 10), justify="left",
                         wraplength=640).pack(anchor="w", pady=(2, 6))

        def card(self, title=None, note=None):
            outer = tk.Frame(self.body, bg=CARD, highlightthickness=1, highlightbackground=LINE)
            outer.pack(fill="x", pady=8, padx=(0, 14))
            if title:
                tk.Label(outer, text=title.upper(), fg=DIM, bg=CARD, font=(FONT_SEMI, 8)).pack(anchor="w", padx=18,
                                                                                               pady=(12, 0))
            if note:
                tk.Label(outer, text=note, fg=MUTED, bg=CARD, font=(FONT, 9), justify="left",
                         wraplength=600).pack(anchor="w", padx=18, pady=(2, 0))
            inner = tk.Frame(outer, bg=CARD)
            inner.pack(fill="x", padx=18, pady=(4, 12))
            inner._rows = 0
            return inner

        def row(self, card, label, help_text=""):
            if card._rows:
                tk.Frame(card, bg=LINE, height=1).pack(fill="x")
            card._rows += 1
            r = tk.Frame(card, bg=CARD)
            r.pack(fill="x", pady=9)
            left = tk.Frame(r, bg=CARD)
            left.pack(side="left", fill="x", expand=True)
            tk.Label(left, text=label, fg=TEXT, bg=CARD, font=(FONT_SEMI, 10), anchor="w").pack(anchor="w")
            if help_text:
                tk.Label(left, text=help_text, fg=MUTED, bg=CARD, font=(FONT, 9), anchor="w", justify="left",
                         wraplength=380).pack(anchor="w")
            right = tk.Frame(r, bg=CARD)
            right.pack(side="right")
            return right

        def toggle(self, card, key, label, help_text="", after=None):
            right = self.row(card, label, help_text)
            v = tk.BooleanVar(value=bool(self.s.get(key)))

            def changed():
                self.save(key, bool(v.get()))
                if after:
                    after(bool(v.get()))
            Toggle(right, v, command=changed, bg=CARD).pack()
            return v

        def choice(self, card, key, label, options, help_text="", conv=None, shown=None, after=None, width=180):
            right = self.row(card, label, help_text)
            cur = self.s.get(key)
            shown = shown or {}
            inv = {v: k for k, v in shown.items()}
            disp = [shown.get(o, o) for o in options]
            cur_disp = shown.get(cur, cur if cur in options else options[0])

            def pick(d):
                val = inv.get(d, d)
                val = conv(val) if conv else val
                self.save(key, val)
                if after:
                    after(val)
            Dropdown(right, cur_disp, disp, pick, width=width, bg=CARD).pack()

        def slider(self, card, key, label, frm, to, help_text="", suffix="", step=1, fmt=None, style_key=False):
            right = self.row(card, label, help_text)
            src = self.style() if style_key else self.s
            val = src.get(key, core.DEFAULT_STYLE.get(key, frm) if style_key else frm)

            def done(v):
                if style_key:
                    st = self.style()
                    st[key] = v
                    self.s["style"] = st
                    self.save()
                else:
                    self.save(key, v)
            Slider(right, val, frm, to, done, suffix=suffix, step=step, fmt=fmt).pack()

        def folder(self, card, key, label, help_text="", after=None):
            right = self.row(card, label, help_text)
            v = tk.StringVar(value=self.s.get(key, ""))
            e = tk.Entry(right, textvariable=v, width=40, bg=CARD_HI, fg=TEXT, insertbackground=TEXT, relief="flat",
                         font=(FONT, 9))
            e.pack(side="left", ipady=6, padx=(0, 6))

            def commit(path):
                path = path.strip()
                if path and path != self.s.get(key):
                    v.set(path)
                    self.save(key, path)
                    if after:
                        after(path)
            e.bind("<FocusOut>", lambda ev: commit(v.get()))
            e.bind("<Return>", lambda ev: commit(v.get()))
            RoundButton(right, "Browse", lambda: commit(filedialog.askdirectory(initialdir=v.get() or None) or ""),
                        small=True, bg=CARD).pack(side="left")
            RoundButton(right, "Open", lambda: self.app.open_dir(v.get()), kind="ghost", small=True,
                        bg=CARD).pack(side="left", padx=(4, 0))
            return v

        def style(self):
            st = dict(core.DEFAULT_STYLE)
            st.update(self.s.get("style") or {})
            return st

        def set_style(self, k, v):
            st = self.style()
            st[k] = v
            self.s["style"] = st
            self.save()

        # ------------------------------------------------ sections
        def _general(self):
            self.heading("General", "How Breachcut starts and stays up to date.")
            c = self.card("Startup")

            def startup(on):
                if not set_startup(on) and os.name == "nt":
                    messagebox.showwarning(core.APP_NAME, "Windows didn't let Breachcut change this setting.")
            self.toggle(c, "launch_on_startup", "Open Breachcut when Windows starts",
                        "So auto-clip is already running when you hop on Siege.", after=startup)
            self.toggle(c, "start_minimized", "Start minimized",
                        "When it opens with Windows, start in the taskbar instead of on screen.")
            c = self.card("Updates")
            self.toggle(c, "auto_update", "Install updates automatically",
                        "New versions install when Breachcut opens. Turn off to get a banner and choose when.")
            r = self.row(c, f"You're on version {updater.local_version()}")
            RoundButton(r, "Check for updates", self.app.manual_update_check, small=True, bg=CARD).pack()
            c = self.card("While you play")
            self.toggle(c, "notify_clip", "Pop-up when a clip is saved",
                        "A small note in the corner of your screen. It never steals focus from the game.")
            self.toggle(c, "ingame_popup", "Small pop-up in game",
                        "A slim card in the top-right that disappears after 2 seconds. It never tabs you out. "
                        "Works best with Siege in Borderless mode.")

        def _intro(self):
            self.heading("Your intro", "The glowing title at the start of every video.")
            c = self.card()
            r = self.row(c, "Name / gamertag", "Shown in your intro if you don't use a logo.")
            v = tk.StringVar(value=self.s.get("creator", ""))
            e = tk.Entry(r, textvariable=v, width=24, bg=CARD_HI, fg=TEXT, insertbackground=TEXT, relief="flat",
                         font=(FONT, 11))
            e.pack(side="left", ipady=6)

            def commit(*_):
                if v.get().strip() != self.s.get("creator", ""):
                    self.save("creator", v.get().strip())
                    self.preview()
            e.bind("<FocusOut>", commit)
            e.bind("<Return>", commit)
            self.intro_img = tk.Label(c, bg="#000000")
            self.intro_img.pack(anchor="w", pady=(10, 8))
            r = self.row(c, "Logo", "A PNG with a see-through background looks best.")
            RoundButton(r, "Use my own logo…", self.pick_intro, small=True, bg=CARD).pack(side="left")
            RoundButton(r, "Use my name", lambda: (self.save("intro_image", ""), self.open("intro")), kind="ghost",
                        small=True, bg=CARD).pack(side="left", padx=4)
            vf = self.s.get("voice_file") or ""
            r = self.row(c, "Intro voice", os.path.basename(vf) if vf and os.path.exists(vf) else "None picked")
            RoundButton(r, "Pick audio…", self.pick_voice, small=True, bg=CARD).pack(side="left")
            RoundButton(r, "None", lambda: (self.save("voice_file", ""), self.open("intro")), kind="ghost", small=True,
                        bg=CARD).pack(side="left", padx=4)
            c = self.card("In your videos")
            for k, label, help_text in (("intro", "Show the intro", "The logo / name title at the start."),
                                        ("voice", "Play the intro voice", "Your voice clip over the intro.")):
                right = self.row(c, label, help_text)
                bv = tk.BooleanVar(value=bool(self.style().get(k)))
                Toggle(right, bv, command=lambda k=k, bv=bv: self.set_style(k, bool(bv.get())), bg=CARD).pack()
            self.preview()

        def preview(self):
            img, _ = core.brand_assets(self.s)
            if img and os.path.exists(img):
                im = Image.open(img).convert("RGBA")
                base = Image.new("RGBA", im.size, (18, 12, 24, 255))
                base.alpha_composite(im)
                base = base.resize((432, 280), Image.LANCZOS)
                self._intro_ph = ImageTk.PhotoImage(base)
                self.intro_img.config(image=self._intro_ph)
            else:
                self._intro_ph = None
                self.intro_img.config(image="", text="Type your name to make an intro", fg=MUTED, width=50, height=8)

        def pick_intro(self):
            f = filedialog.askopenfilename(title="Pick a logo (PNG with transparent background works best)",
                                           filetypes=[("Images", "*.png *.jpg *.jpeg")])
            if f:
                self.save("intro_image", f)
                self.open("intro")

        def pick_voice(self):
            f = filedialog.askopenfilename(title="Pick a short voice clip for your intro",
                                           filetypes=[("Audio", "*.mp3 *.wav *.m4a *.ogg"), ("All", "*.*")])
            if f:
                self.save("voice_file", f)
                self.open("intro")

        def _autoclip(self):
            self.heading("Auto-clip", "Breachcut watches the score feed on your screen. When “+100 Kill” pops up it "
                                      "knows you got a kill, and after a good play it saves the clip by itself.")
            c = self.card("Auto-clip")
            self.toggle(c, "auto_clip", "Save my plays automatically",
                        "Works while the recorder is on. Your clip hotkey works either way.")
            self.choice(c, "auto_clip_min", "Save a clip when I get", [1, 2, 3, 4, 5],
                        shown={1: "Every kill", 2: "2 kills or more", 3: "3 kills or more", 4: "4 kills or more",
                               5: "Aces only"})
            self.choice(c, "streak_gap", "Kills count as one play if they're within", [10, 15, 25, 40, 60, 90],
                        "Longer = whole rounds land in one clip.",
                        shown={10: "10 seconds", 15: "15 seconds", 25: "25 seconds", 40: "40 seconds",
                               60: "1 minute", 90: "1.5 minutes"})
            self.slider(c, "auto_pre", "Keep before the first kill", 2, 30, "The lead-up to the play.",
                        fmt=lambda v: f"{v:.0f} s")
            self.slider(c, "auto_post", "Keep after the last kill", 1, 15, "Let the moment breathe.",
                        fmt=lambda v: f"{v:.0f} s")
            c = self.card("Make videos by themselves")
            self.toggle(c, "auto_make", "Turn big plays into videos",
                        "Edits them with your default look right after the clip saves.")
            self.choice(c, "min_kills", "Make a video when I get", [3, 4, 5],
                        shown={3: "3K or better", 4: "4K or better", 5: "Aces only"})
            for key, label, help_text in (("vertical", "TikTok / Shorts", "Vertical 1080×1920."),
                                          ("wide", "YouTube widescreen", "16:9 1920×1080.")):
                right = self.row(c, label, help_text)
                bv = tk.BooleanVar(value=key in (self.s.get("auto_formats") or []))

                def flip(key=key, bv=bv):
                    f = [x for x in (self.s.get("auto_formats") or []) if x != key]
                    if bv.get():
                        f.append(key)
                    self.save("auto_formats", f)
                Toggle(right, bv, command=flip, bg=CARD).pack()
            c = self.card("Kill detection")
            self.toggle(c, "kill_detect", "Spot my kills on screen",
                        "Only looks at the picture, like any recorder. It never touches the game, so it's BattlEye-safe.")
            self.choice(c, "kill_sensitivity", "Sensitivity", ["Low", "Normal", "High"],
                        "Missing kills? Try High. Getting fake ones? Try Low.")
            self.toggle(c, "kill_sound", "Tick sound on every kill", "Handy to check it's working.")
            self.toggle(c, "notify_kill", "Pop-up on every kill")
            self.toggle(c, "scan_old", "Find kills in older recordings",
                        "Looks through recordings that don't have kills marked yet — only while you're not playing.")
            tk.Label(self.body, text="Kill detection reads Siege's “+100 Kill” cards. Keep the score feed turned on in "
                                     "Siege's HUD settings so Breachcut can see them.", fg=MUTED, bg=BG,
                     font=(FONT, 9), wraplength=640, justify="left").pack(anchor="w", pady=(4, 8))

        def _clips(self):
            self.heading("Clips & videos", "How clips are saved and cut into videos.")
            c = self.card("Saving clips")
            self.choice(c, "replay_seconds", "Clip hotkey saves the last", [10, 15, 20, 30, 45, 60, 90, 120, 180, 300],
                        shown={10: "10 seconds", 15: "15 seconds", 20: "20 seconds", 30: "30 seconds",
                               45: "45 seconds", 60: "1 minute", 90: "1.5 minutes", 120: "2 minutes",
                               180: "3 minutes", 300: "5 minutes"})
            self.choice(c, "clip_name", "File names", ["Breachcut {kind} {date} {time}", "Siege {kind} {date} {time}",
                                                       "{date} {time} {kind}"],
                        shown={"Breachcut {kind} {date} {time}": "Breachcut Clip 2026-11-21 21-07",
                               "Siege {kind} {date} {time}": "Siege Clip 2026-11-21 21-07",
                               "{date} {time} {kind}": "2026-11-21 21-07 Clip"}, width=260)
            self.folder(c, "clips_dir", "Clips folder", "Where saved clips and auto-clips go.",
                        after=lambda p: self.app.reload())
            c = self.card("Cutting kills into videos")
            self.slider(c, "clip_pre", "Time before each kill", 1, 6, "How much lead-up to keep.", step=0.2,
                        fmt=lambda v: f"{v:.1f} s")
            self.slider(c, "clip_post", "Time after each kill", 0.2, 3, "How long to hold after the kill.", step=0.1,
                        fmt=lambda v: f"{v:.1f} s")
            since = self.s.get("only_new_after") or ""
            try:
                since_txt = datetime.fromisoformat(since).strftime("%b %d, %Y %I:%M %p")
            except ValueError:
                since_txt = "the start"
            r = self.row(c, "Only make videos from clips after", f"Auto videos ignore clips older than {since_txt}.")

            def reset():
                self.save("only_new_after", datetime.now().isoformat(timespec="seconds"), "Auto videos start from now")
                self.open("clips")
            RoundButton(r, "Start from now", reset, small=True, bg=CARD).pack()

        def _editing(self):
            self.heading("Editing & export", "Your default look and how videos are saved. "
                                             "You can still change any single video in the editor.")
            c = self.card("Default look")
            st = self.style()
            right = self.row(c, "Transition between kills")
            Dropdown(right, st.get("transition", "Flash"), ["Flash", "Fade to black", "None"],
                     lambda v: self.set_style("transition", v), bg=CARD).pack()
            self.slider(c, "glow", "Glow", 0, 40, "Soft haze over the picture.", style_key=True)
            self.slider(c, "brightness", "Brightness", -15, 25, style_key=True)
            right = self.row(c, "Sharpen")
            bv = tk.BooleanVar(value=bool(st.get("sharpen", True)))
            Toggle(right, bv, command=lambda: self.set_style("sharpen", bool(bv.get())), bg=CARD).pack()
            c = self.card("Sound")
            self.slider(c, "game_vol", "Game volume", 0, 150, suffix="%", style_key=True)
            mus = st.get("music") or ""
            right = self.row(c, "Background music", os.path.basename(mus) if mus and os.path.exists(mus) else "None")

            def pick_music():
                f = filedialog.askopenfilename(title="Pick background music",
                                               filetypes=[("Audio", "*.mp3 *.wav *.m4a *.ogg *.flac")])
                if f:
                    self.set_style("music", f)
                    self.open("editing")
            RoundButton(right, "Pick…", pick_music, small=True, bg=CARD).pack(side="left")
            RoundButton(right, "None", lambda: (self.set_style("music", ""), self.open("editing")), kind="ghost",
                        small=True, bg=CARD).pack(side="left", padx=4)
            self.slider(c, "music_vol", "Music volume", 0, 100, suffix="%", style_key=True)
            c = self.card("Export")
            self.choice(c, "export_quality", "Quality", ["High", "Balanced", "Small file"],
                        "High looks best; Small file uploads faster.")
            self.choice(c, "export_fps", "Frame rate", [60, 30], shown={60: "60 fps", 30: "30 fps"})
            gpu = "Uses your NVIDIA card when there is one." if not (self.app.enc and not self.app.enc.nvenc) else \
                "No NVIDIA encoder found on this PC — CPU is used."
            self.choice(c, "export_encoder", "Encoder", ["Auto", "GPU", "CPU"], gpu,
                        shown={"Auto": "Auto", "GPU": "GPU (NVIDIA)", "CPU": "CPU"})

        def _video(self):
            self.heading("Video", "Recording quality for the built-in recorder. Changes apply right away.")
            c = self.card("Replay buffer")
            self.choice(c, "rec_mode", "Record when", ["siege", "always", "off"],
                        "The replay buffer keeps the last moments so hotkeys and auto-clip can save them.",
                        shown={"siege": "Siege is running", "always": "Breachcut is open", "off": "Only when I press record"},
                        width=230)
            c = self.card("Quality")
            self.choice(c, "rec_resolution", "Resolution", ["Native", "2160p", "1440p", "1080p", "900p", "720p", "480p"],
                        "Native = your screen's own size.",
                        shown={"Native": "Native", "2160p": "4K (2160p)", "1440p": "1440p", "1080p": "1080p",
                               "900p": "900p", "720p": "720p", "480p": "480p"})
            self.choice(c, "rec_fps", "Frame rate", [30, 60, 90, 120, 144],
                        shown={30: "30 fps", 60: "60 fps", 90: "90 fps", 120: "120 fps", 144: "144 fps"})
            self.slider(c, "rec_bitrate", "Bitrate", 5, 150, "Higher is sharper but makes bigger files. "
                                                             "30–50 is great for 1080p, 80+ for 1440p/4K.",
                        suffix=" Mbps")
            self.choice(c, "rec_rc", "Bitrate mode", ["CBR", "VBR"],
                        shown={"CBR": "Constant (steady)", "VBR": "Variable (smaller files)"})
            c = self.card("Encoder")
            self.choice(c, "rec_encoder", "Encoder", ["Auto", "NVIDIA", "AMD", "Intel", "CPU"],
                        "Auto picks your graphics card so the game stays smooth.")
            self.choice(c, "rec_codec", "Format", ["H.264", "HEVC"],
                        "H.264 plays everywhere. HEVC is sharper at the same size (needs a newer graphics card).",
                        shown={"H.264": "H.264 (AVC)", "HEVC": "HEVC (H.265)"})
            self.choice(c, "rec_preset", "Encoder preset", ["Performance", "Balanced", "Quality"],
                        "Performance uses the least power; Quality looks best.")
            c = self.card("Capture")
            self.choice(c, "rec_capture", "Capture method", ["Auto", "Compatibility"],
                        "Try Compatibility if recordings come out black.",
                        shown={"Auto": "Auto (fastest)", "Compatibility": "Compatibility"})
            self.choice(c, "rec_monitor", "Monitor", [0, 1, 2, 3],
                        shown={0: "Main monitor", 1: "Monitor 2", 2: "Monitor 3", 3: "Monitor 4"})
            self.toggle(c, "rec_cursor", "Show the mouse cursor")
            c = self.card("Folder")
            self.folder(c, "recordings_dir", "Recordings", "Where full recordings are saved.",
                        after=lambda p: self.app.reload())

        def _audio(self):
            self.heading("Audio", "What the recorder captures.")
            c = self.card("Game")
            self.toggle(c, "rec_game_audio", "Record game & PC sound",
                        "Everything you hear: the game, Discord, music.")
            self.slider(c, "rec_game_vol", "Game volume", 0, 150, suffix="%")
            c = self.card("Microphone")
            self.toggle(c, "rec_mic", "Record my microphone")
            right = self.row(c, "Microphone", "Which mic to use.")
            dd = Dropdown(right, self.s.get("mic_device", "Default"), ["Default"],
                          lambda v: self.save("mic_device", v), width=260, bg=CARD)
            dd.pack()
            self.slider(c, "mic_vol", "Mic volume", 0, 200, suffix="%")
            self.toggle(c, "mic_noise", "Noise suppression", "Cuts fans, keyboard and background hiss.")
            self.toggle(c, "mic_gate", "Noise gate", "Mutes the mic between words.")
            self.toggle(c, "mic_mono", "Mono mic", "Puts your voice in both ears (most mics are mono).")
            c = self.card("Tracks")
            self.toggle(c, "separate_tracks", "Separate audio tracks",
                        "Keep game and mic on their own tracks so you can mute one later.")
            self.choice(c, "rec_audio_kbps", "Audio quality", [128, 160, 192, 256, 320],
                        shown={128: "128 kbps", 160: "160 kbps", 192: "192 kbps", 256: "256 kbps", 320: "320 kbps"})

            def load():
                devs = core.audio_devices()
                self._mic_list = devs
                self.after(0, lambda: self._set_mics(dd, devs))
            if self._mic_list is None:
                threading.Thread(target=load, daemon=True).start()
            else:
                self._set_mics(dd, self._mic_list)

        def _set_mics(self, dd, devs):
            try:
                dd.options = ["Default"] + list(devs)
            except tk.TclError:
                pass

        def _hotkeys(self):
            self.heading("Hotkeys", "Click a key, then press the new one. Esc cancels, Backspace clears.")
            c = self.card()
            for key, label, help_text in (
                    ("hk_clip", "Save a clip", "Saves the last moments (length is in Clips & videos)."),
                    ("hk_clip_short", "Save the last 15 seconds", "A quick clip of just that moment."),
                    ("hk_clip_long", "Save the last 2 minutes", "For the whole round."),
                    ("hk_record", "Start / stop recording", "Records until you press it again."),
                    ("hk_buffer", "Turn the replay buffer on / off", ""),
                    ("hk_autoclip", "Turn auto-clip on / off", "")):
                right = self.row(c, label, help_text)
                HotkeyButton(right, self.s.get(key) or "None",
                             lambda v, key=key, label=label: self.save(key, v, f"{label} → {v}"), bg=CARD).pack()
            tk.Label(self.body, text="Hotkeys use Windows' normal hotkey system — Breachcut never reads the game, "
                                     "so it's safe with BattlEye.", fg=DIM, bg=BG, font=(FONT, 9)).pack(anchor="w",
                                                                                                     pady=6)

        def _storage(self):
            self.heading("Storage", "Breachcut keeps its videos in its own library on this PC.")
            u = core.storage_usage(self.s)
            limit = float(self.s.get("storage_limit_gb") or 0) * 1024 ** 3
            cols = [ACCENT, VIOLET, "#4dabf7"]
            c = self.card("Library")
            top = tk.Frame(c, bg=CARD)
            top.pack(fill="x", pady=(6, 8))
            tk.Label(top, text=core.fmt_bytes(u["total"]), fg=TEXT, bg=CARD, font=(FONT_BOLD, 22)).pack(side="left")
            of = f"  of {core.fmt_bytes(limit)} limit" if limit else "  used"
            tk.Label(top, text=of, fg=MUTED, bg=CARD, font=(FONT, 11)).pack(side="left", pady=(8, 0))
            tk.Label(top, text=f"{core.fmt_bytes(u['free'])} free on disk", fg=MUTED, bg=CARD,
                     font=(FONT, 9)).pack(side="right", pady=(8, 0))
            usage_bar(c, [(b, cols[i % 3]) for i, (_, _, b, _) in enumerate(u["folders"])],
                      limit or max(u["total"], 1), width=600).pack(anchor="w")
            for i, (lab, path, b, n) in enumerate(u["folders"]):
                r = self.row(c, f"●  {lab}", f"{n} video{'s' if n != 1 else ''} · {core.fmt_bytes(b)}")
                r.master.winfo_children()[0].winfo_children()[0].config(fg=cols[i % 3])
                RoundButton(r, "Open", lambda p=path: self.app.open_dir(p), kind="ghost", small=True,
                            bg=CARD).pack()
            c = self.card("Location", "New videos go into TikTok, YouTube and Recordings folders inside this one.")
            lib = self.s.get("library_dir") or core.default_library()
            r = self.row(c, "Library folder", lib)
            RoundButton(r, "Move library…", self.move_library, small=True, bg=CARD).pack()
            outside = [lab for lab, p, _, _ in u["folders"]
                       if p and not os.path.normcase(os.path.abspath(p)).startswith(os.path.normcase(os.path.abspath(lib)))]
            if outside:
                r = self.row(c, "Some videos are outside the library",
                             f"{', '.join(outside)} still save to their old folder. Move them in to keep everything "
                             f"in one place.")
                RoundButton(r, "Move into library", lambda: self.move_library(lib), kind="accent", small=True,
                            bg=CARD).pack()
            c = self.card("Limits")
            self.choice(c, "storage_limit_gb", "Storage limit", [10, 25, 50, 100, 250, 500, 0],
                        "How much space Breachcut's videos can use.",
                        shown={0: "No limit", 10: "10 GB", 25: "25 GB", 50: "50 GB", 100: "100 GB", 250: "250 GB",
                               500: "500 GB"}, after=lambda v: self.open("storage"))
            self.toggle(c, "delete_when_full", "Delete oldest videos when full",
                        "Makes room for new ones automatically. Otherwise nothing is ever deleted for space.")
            self.choice(c, "auto_delete_days", "Delete videos older than", [0, 7, 14, 30, 60, 90],
                        shown={0: "Never", 7: "1 week", 14: "2 weeks", 30: "30 days", 60: "60 days", 90: "90 days"})
            c = self.card("Clean up")
            r = self.row(c, "Apply limits now", "Removes videos the rules above say should go.")
            RoundButton(r, "Clean up now", self.clean_now, kind="danger", small=True, bg=CARD).pack()
            r = self.row(c, "Thumbnail cache", f"{core.fmt_bytes(u['thumbs'])} · rebuilt when needed.")

            def clear():
                n = core.clear_thumbs()
                self.app.images.clear() if hasattr(self.app, "images") else None
                self.app.status(f"✓ Cleared {n} thumbnails")
                self.open("storage")
            RoundButton(r, "Clear", clear, kind="ghost", small=True, bg=CARD).pack()

        def move_library(self, target=None):
            if target is None:
                target = filedialog.askdirectory(title="Pick where Breachcut keeps its videos")
                if not target:
                    return
                if os.path.basename(os.path.normpath(target)).lower() != core.APP_NAME.lower():
                    target = os.path.join(target, core.APP_NAME)
            if not messagebox.askyesno(core.APP_NAME, f"Move all Breachcut videos to\n{target} ?\n\n"
                                                      "Your clips and videos aren't touched."):
                return
            self.app.status("Moving your videos…")

            def work():
                try:
                    n = core.move_library(self.s, target, self.app.state, log=lambda t: self.app.q.put(("status", t)))
                    self.app.q.put(("status", f"✓ Moved {n} video(s) into {target}"))
                except Exception as e:
                    self.app.q.put(("error", f"Couldn't move the library: {e}"))
                self.after(0, lambda: self.open("storage"))
            threading.Thread(target=work, daemon=True).start()

        def clean_now(self):
            if not messagebox.askyesno(core.APP_NAME, "Delete the videos your storage rules say should go?\n"
                                                      "This can't be undone."):
                return
            n, b = core.enforce_storage(self.s, self.app.state, log=lambda t: None)
            self.app.status(f"✓ Removed {n} video(s), freed {core.fmt_bytes(b)}" if n else "Nothing to clean up.")
            self.open("storage")

        def _notifications(self):
            self.heading("Notifications", "What Breachcut tells you about.")
            c = self.card()
            self.toggle(c, "notify_made", "When a video is ready", "A pop-up in the corner of your screen.")
            self.toggle(c, "notify_sound", "Play a sound")
            self.toggle(c, "notify_update", "When an update is available")
            r = self.row(c, "Try it")
            RoundButton(r, "Send a test", lambda: self.app.notify("Test notification",
                                                                  "This is what a new video looks like."),
                        small=True, bg=CARD).pack()

        def _account(self):
            self.heading("Account")
            c = self.card()
            u = self.app.user
            top = tk.Frame(c, bg=CARD)
            top.pack(fill="x", pady=8)
            self._av = ImageTk.PhotoImage(self.app.avatar_image(72, ring=ACCENT, bg=CARD))
            tk.Label(top, image=self._av, bg=CARD).pack(side="left")
            info = tk.Frame(top, bg=CARD)
            info.pack(side="left", padx=16)
            if u:
                tk.Label(info, text=u["name"], fg=TEXT, bg=CARD, font=(FONT_BOLD, 16)).pack(anchor="w")
                tk.Label(info, text=f"Discord · @{u.get('handle') or u['name']}", fg="#9aa4ff", bg=CARD,
                         font=(FONT, 10)).pack(anchor="w")
                if u.get("since"):
                    tk.Label(info, text=f"Member since {u['since']}", fg=MUTED, bg=CARD, font=(FONT, 9)).pack(anchor="w")
                state = "Online" if self.app.online else "Offline — will sync when you're back online"
                tk.Label(info, text=f"●  {state}", fg=GOOD if self.app.online else WARN, bg=CARD,
                         font=(FONT, 9)).pack(anchor="w", pady=(4, 0))
            else:
                tk.Label(info, text="Not signed in", fg=TEXT, bg=CARD, font=(FONT_BOLD, 16)).pack(anchor="w")
                tk.Label(info, text="Sign in with Discord to save your profile.", fg=MUTED, bg=CARD,
                         font=(FONT, 10)).pack(anchor="w")
            r = self.row(c, "Sign out" if u else "Sign in", "Your clips and videos stay on this PC." if u else "")
            if u:
                RoundButton(r, "Sign out", self.app.sign_out, kind="danger", small=True, bg=CARD).pack()
            else:
                RoundButton(r, "Sign in with Discord", self.app.restart_to_login, kind="discord", small=True,
                            bg=CARD).pack()
            c = self.card("Privacy")
            tk.Label(c, text="Breachcut only stores your Discord name, picture and app version on its server. "
                             "Your clips and videos never leave your PC.", fg=MUTED, bg=CARD, font=(FONT, 9),
                     wraplength=600, justify="left").pack(anchor="w", pady=(4, 8))
            r = self.row(c, "Documents")
            RoundButton(r, "Privacy policy", lambda: webbrowser.open(auth.PRIVACY_URL), kind="ghost", small=True,
                        bg=CARD).pack(side="left")
            RoundButton(r, "Terms", lambda: webbrowser.open(auth.TERMS_URL), kind="ghost", small=True,
                        bg=CARD).pack(side="left", padx=4)

        def _about(self):
            self.heading("About")
            c = self.card()
            top = tk.Frame(c, bg=CARD)
            top.pack(fill="x", pady=8)
            mark = os.path.join(core.ASSETS, "breachcut_mark.png")
            if os.path.exists(mark):
                self._mark = ImageTk.PhotoImage(Image.open(mark).convert("RGBA").resize((64, 64), Image.LANCZOS))
                tk.Label(top, image=self._mark, bg=CARD).pack(side="left")
            info = tk.Frame(top, bg=CARD)
            info.pack(side="left", padx=14)
            tk.Label(info, text=core.APP_NAME, fg=TEXT, bg=CARD, font=(FONT_BOLD, 18)).pack(anchor="w")
            tk.Label(info, text=f"Version {updater.local_version()}", fg=MUTED, bg=CARD, font=(FONT, 10)).pack(anchor="w")
            tk.Label(c, text="Records Rainbow Six Siege, spots your kills and turns them into TikToks, Shorts and YouTube videos.\n"
                             "Reads clips only — never touches the game, so it's safe with BattlEye.\n"
                             "Not affiliated with Ubisoft.", fg=MUTED, bg=CARD, font=(FONT, 9),
                     justify="left").pack(anchor="w", pady=(4, 10))
            r = self.row(c, "Updates")
            RoundButton(r, "Check for updates", self.app.manual_update_check, small=True, bg=CARD).pack()
            r = self.row(c, "App folder", core.APP_DIR)
            RoundButton(r, "Open", lambda: self.app.open_dir(core.APP_DIR), kind="ghost", small=True, bg=CARD).pack()

    return SettingsPage
