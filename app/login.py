"""Breachcut launch flow: splash screen -> (Discord sign-in if needed) -> app."""
import os
import threading
import tkinter as tk
import webbrowser

from PIL import Image, ImageDraw, ImageFilter, ImageTk

import auth
import autoclip_core as core
import updater
from ui_kit import (ACCENT, BG, CARD, DIM, FONT, FONT_BOLD, FONT_SEMI, GOOD, LINE, MUTED, TEXT, VIOLET, WARN,
                    CheckBox, RoundButton, backdrop, center, rgb, rounded_rect)


def _asset(name):
    p = os.path.join(core.ASSETS, name)
    return p if os.path.exists(p) else None


def glowing_mark(size):
    """The Breachcut mark with a soft pink glow behind it."""
    p = _asset("breachcut_mark.png")
    if not p:
        return None
    mk = Image.open(p).convert("RGBA").resize((size, size), Image.LANCZOS)
    pad = size // 3
    out = Image.new("RGBA", (size + pad * 2, size + pad * 2), (0, 0, 0, 0))
    g = Image.new("RGBA", out.size, (0, 0, 0, 0))
    g.paste(Image.new("RGBA", mk.size, rgb(ACCENT) + (150,)), (pad, pad), mk)
    g = g.filter(ImageFilter.GaussianBlur(size // 6))
    out = Image.alpha_composite(out, g)
    out.alpha_composite(mk, (pad, pad))
    return out


class Splash(tk.Toplevel):
    W, H = 560, 340

    def __init__(self, root):
        super().__init__(root, bg=BG)
        self.overrideredirect(True)
        center(self, self.W, self.H)
        try:
            self.attributes("-topmost", True)
        except tk.TclError:
            pass
        c = tk.Canvas(self, width=self.W, height=self.H, bg=BG, highlightthickness=0, bd=0)
        c.pack()
        bgim = backdrop(self.W, self.H, 1.3).convert("RGBA")
        border = rounded_rect(self.W, self.H, 0, (0, 0, 0, 0), outline=LINE, width=1)
        bgim.alpha_composite(border)
        self._bg = ImageTk.PhotoImage(bgim)
        c.create_image(0, 0, image=self._bg, anchor="nw")
        mk = glowing_mark(118)
        if mk:
            self._mk = ImageTk.PhotoImage(mk)
            c.create_image(self.W // 2, 128, image=self._mk)
        c.create_text(self.W // 2, 238, text="BREACHCUT", fill=TEXT, font=(FONT_BOLD, 22))
        self.msg = c.create_text(self.W // 2, 272, text="Starting up", fill=MUTED, font=(FONT, 10))
        c.create_text(self.W // 2, self.H - 18, text=f"v{updater.local_version()}", fill=DIM, font=(FONT, 8))
        # progress shimmer
        self.bar_bg = c.create_rectangle(self.W // 2 - 80, 296, self.W // 2 + 80, 299, fill="#2a2336", width=0)
        self.bar = c.create_rectangle(self.W // 2 - 80, 296, self.W // 2 - 40, 299, fill=ACCENT, width=0)
        self.c = c
        self._t = 0
        self._anim()

    def _anim(self):
        if not self.winfo_exists():
            return
        self._t = (self._t + 4) % 200
        x0 = self.W // 2 - 80 + max(0, self._t - 40)
        x1 = self.W // 2 - 80 + min(160, self._t)
        self.c.coords(self.bar, x0, 296, max(x0 + 1, x1), 299)
        self.after(16, self._anim)

    def say(self, text):
        self.c.itemconfig(self.msg, text=text)


class LoginWindow(tk.Toplevel):
    W, H = 1060, 660
    CX, CY, CW, CH = 600, 84, 400, 492      # card box

    def __init__(self, root, on_success, on_quit, message=None):
        super().__init__(root, bg=BG)
        self.root_ = root
        self.on_success, self.on_quit = on_success, on_quit
        self.title(f"Sign in · {core.APP_NAME}")
        self.resizable(False, False)
        center(self, self.W, self.H)
        for ico in ("breachcut.ico",):
            p = _asset(ico)
            if p:
                try:
                    self.iconbitmap(p)
                except tk.TclError:
                    pass
        self.protocol("WM_DELETE_WINDOW", self.quit_app)
        self.flow = None
        c = tk.Canvas(self, width=self.W, height=self.H, bg=BG, highlightthickness=0, bd=0)
        c.pack()
        self.c = c
        self._build_bg()
        self._build_left()
        self._build_card(message)

    # ---------------------------------------------------------------- drawing
    def _build_bg(self):
        im = backdrop(self.W, self.H, 1.4).convert("RGBA")
        # card shadow + card
        sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle([self.CX + 6, self.CY + 18, self.CX + self.CW + 6, self.CY + self.CH + 18],
                                             radius=22, fill=(0, 0, 0, 150))
        im = Image.alpha_composite(im, sh.filter(ImageFilter.GaussianBlur(22)))
        im.alpha_composite(rounded_rect(self.CW, self.CH, 22, CARD, outline=LINE, width=1), (self.CX, self.CY))
        # thin accent line on top of the card
        acc = Image.new("RGBA", (self.CW - 80, 3), (0, 0, 0, 0))
        grad = Image.linear_gradient("L").rotate(90).resize((self.CW - 80, 3))
        acc.paste(Image.new("RGBA", acc.size, rgb(ACCENT) + (255,)), (0, 0), grad)
        acc.paste(Image.new("RGBA", acc.size, rgb(VIOLET) + (255,)), (0, 0), grad.point(lambda v: 255 - v))
        im.alpha_composite(acc, (self.CX + 40, self.CY))
        self._bg = ImageTk.PhotoImage(im)
        self.c.create_image(0, 0, image=self._bg, anchor="nw")

    def _build_left(self):
        c = self.c
        mk = glowing_mark(96)
        x0 = 72
        if mk:
            self._mk = ImageTk.PhotoImage(mk)
            c.create_image(x0 - 30, 70, image=self._mk, anchor="nw")
        c.create_text(x0, 238, text="Clip it. Cut it.", fill=TEXT, font=(FONT_BOLD, 34), anchor="w")
        c.create_text(x0, 286, text="Post it.", fill=ACCENT, font=(FONT_BOLD, 34), anchor="w")
        c.create_text(x0, 334, text="Your Siege highlights, edited for TikTok and YouTube\nautomatically — while you keep playing.",
                      fill=MUTED, font=(FONT, 11), anchor="w")
        feats = [("⚡", "Auto-clips your 3Ks, 4Ks and Aces"),
                 ("▶", "TikTok, Shorts and YouTube widescreen"),
                 ("✂", "Edit cuts, transitions and music in seconds"),
                 ("🛡", "Reads clips only — safe with BattlEye")]
        self._chips = []
        for i, (icon, text) in enumerate(feats):
            y = 408 + i * 44
            chip = rounded_rect(32, 32, 10, "#1e1828", outline=LINE, width=1)
            ph = ImageTk.PhotoImage(chip)
            self._chips.append(ph)
            c.create_image(x0, y, image=ph, anchor="w")
            c.create_text(x0 + 16, y, text=icon, fill=ACCENT, font=(FONT_SEMI, 11))
            c.create_text(x0 + 46, y, text=text, fill=TEXT, font=(FONT, 11), anchor="w")

    def _build_card(self, message):
        c = self.c
        cx = self.CX + self.CW // 2
        wm = _asset("breachcut_wordmark.png")
        if wm:
            im = Image.open(wm)
            im = im.resize((int(im.width * 30 / im.height), 30), Image.LANCZOS)
            self._wm = ImageTk.PhotoImage(im)
            c.create_image(cx, self.CY + 56, image=self._wm)
        c.create_text(cx, self.CY + 118, text="Welcome", fill=TEXT, font=(FONT_BOLD, 24))
        c.create_text(cx, self.CY + 152, text="Sign in to get started. It takes 10 seconds.", fill=MUTED,
                      font=(FONT, 10))
        self.age = tk.BooleanVar(value=False)
        self.btn = RoundButton(self, "Continue with Discord", self.start, kind="discord", width=320, height=48,
                               bg=CARD, font=(FONT_SEMI, 11))
        c.create_window(cx, self.CY + 216, window=self.btn)
        self.chk = CheckBox(self, self.age, "I'm 13 or older and I agree to the", bg=CARD, command=self._age_changed)
        self.chk.config(width=320)
        c.create_window(cx, self.CY + 272, window=self.chk)
        t1 = c.create_text(self.CX + 68, self.CY + 293, text="Terms of Use", fill=ACCENT, font=(FONT_SEMI, 9), anchor="w",
                           tags=("link_terms",))
        bb = c.bbox(t1) or (0, 0, self.CX + 150, 0)
        c.create_text(bb[2] + 4, self.CY + 293, text="and", fill=MUTED, font=(FONT, 9), anchor="w")
        bb2 = c.bbox(c.find_all()[-1]) or (0, 0, bb[2] + 30, 0)
        c.create_text(bb2[2] + 4, self.CY + 293, text="Privacy Policy", fill=ACCENT, font=(FONT_SEMI, 9), anchor="w",
                      tags=("link_privacy",))
        for tag, url in (("link_terms", auth.TERMS_URL), ("link_privacy", auth.PRIVACY_URL)):
            c.tag_bind(tag, "<Button-1>", lambda e, u=url: webbrowser.open(u))
            c.tag_bind(tag, "<Enter>", lambda e: c.config(cursor="hand2"))
            c.tag_bind(tag, "<Leave>", lambda e: c.config(cursor=""))
        self.status = c.create_text(cx, self.CY + 340, text=message or "", fill=WARN if message else MUTED,
                                    font=(FONT, 10), width=330, justify="center")
        self.cancel_id = c.create_text(cx, self.CY + 372, text="", fill=MUTED, font=(FONT_SEMI, 9),
                                       tags=("cancel",))
        c.tag_bind("cancel", "<Button-1>", lambda e: self.cancel())
        c.create_line(self.CX + 40, self.CY + 404, self.CX + self.CW - 40, self.CY + 404, fill=LINE)
        c.create_text(cx, self.CY + 436, text="🔒  Your clips never leave your PC. We only see your\nDiscord name and picture.",
                      fill=DIM, font=(FONT, 9), justify="center")
        c.create_text(self.W - 18, self.H - 14, text=f"v{updater.local_version()}  ·  Not affiliated with Ubisoft or Medal",
                      fill=DIM, font=(FONT, 8), anchor="e")
        self.offline_id = None
        self._age_changed()

    # ---------------------------------------------------------------- actions
    def _age_changed(self):
        if not self.flow:
            self.btn.config(state="normal" if self.age.get() else "disabled")

    def say(self, text, color=MUTED):
        if self.winfo_exists():
            self.c.itemconfig(self.status, text=text, fill=color)

    def start(self):
        if not self.age.get():
            self.say("Please confirm you're 13 or older first.", WARN)
            return
        if not auth.configured():
            self.say("Sign-in isn't available yet.", WARN)
            self._offer_offline()
            return
        self.btn.config(text="Waiting for Discord…", state="disabled")
        self.say("Finish signing in in your browser, then come back here.", MUTED)
        self.c.itemconfig(self.cancel_id, text="Cancel")
        self.flow = auth.DiscordLogin(lambda ok, p: self.after(0, lambda: self._done(ok, p)))
        self.flow.start()

    def cancel(self):
        if self.flow:
            self.flow.cancel()
            self.flow = None
        self._reset("Cancelled.")

    def _reset(self, msg="", color=MUTED):
        self.flow = None
        self.btn.config(text="Continue with Discord")
        self._age_changed()
        self.c.itemconfig(self.cancel_id, text="")
        self.say(msg, color)

    def _done(self, ok, payload):
        if not self.winfo_exists():
            return
        if ok:
            name = auth.user_info(payload)["name"]
            self.btn.config(text=f"✓  Welcome, {name}!", state="disabled")
            self.say("Signed in. Loading your clips…", GOOD)
            self.c.itemconfig(self.cancel_id, text="")
            self.after(700, lambda: self._finish(payload))
        else:
            msg = str(payload)
            if "connect" in msg.lower() or "timed out" in msg.lower() or "urlopen" in msg.lower():
                self._offer_offline()
            self._reset(msg if len(msg) < 140 else "Sign-in failed. Try again.", WARN)

    def _offer_offline(self):
        if self.offline_id:
            return
        self.offline_id = self.c.create_text(self.CX + self.CW // 2, self.CY + 372 + 20,
                                             text="Continue without signing in", fill=MUTED,
                                             font=(FONT_SEMI, 9), tags=("offline",))
        self.c.tag_bind("offline", "<Button-1>", lambda e: self._finish(None))

    def _finish(self, sess):
        self.destroy()
        self.on_success(sess)

    def quit_app(self):
        if self.flow:
            self.flow.cancel()
        self.destroy()
        self.on_quit()


def launch(root, on_ready):
    """Show the splash, restore the session or ask to sign in, then call on_ready(session, online)."""
    root.withdraw()
    sp = Splash(root)
    sp.say("Checking your account…")

    def check():
        try:
            sess, online = auth.current_session() if auth.configured() else (None, True)
        except Exception:
            sess, online = None, True
        root.after(450, lambda: done(sess, online))

    def done(sess, online):
        sp.destroy()
        if sess or not auth.configured():
            on_ready(sess, online)
        else:
            LoginWindow(root, lambda s: on_ready(s, True), root.destroy)

    threading.Thread(target=check, daemon=True).start()
