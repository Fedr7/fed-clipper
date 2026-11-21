"""In-app video player for Fed Clipper.

Video frames come from an ffmpeg pipe, sound from ffplay (no window).
Works for raw Medal clips (plays the game-audio track) and finished Fed videos.
"""
import os
import queue
import subprocess
import threading
import time
import tkinter as tk

from PIL import Image, ImageTk

import autoclip_core as core

FPS = 30


def fmt(t):
    t = max(0.0, t)
    return f"{int(t // 60)}:{t % 60:04.1f}"


class Player(tk.Frame):
    def __init__(self, master, w, h, bg="#08060c", fg="#f1edff", accent="#b9a6ff", on_time=None):
        super().__init__(master, bg=bg)
        self.w, self.h = w, h
        self.on_time = on_time          # callback(pos) while playing / seeking
        self.ff = core.find_ffmpeg()
        self.ffplay = core.find_tool("ffplay")
        self.screen = tk.Label(self, bg="#000000", width=w, height=h, bd=0, cursor="hand2")
        self.screen.pack()
        self.screen.bind("<Button-1>", lambda e: self.toggle())
        self._blank()
        bar = tk.Frame(self, bg=bg)
        bar.pack(fill="x", pady=(4, 0))
        self.pbtn = tk.Button(bar, text="▶", width=3, command=self.toggle, relief="flat", bd=0, bg=accent,
                              fg="#140d22", font=("Segoe UI", 10, "bold"), cursor="hand2")
        self.pbtn.pack(side="left")
        self.tlabel = tk.Label(bar, text="0:00.0 / 0:00.0", bg=bg, fg=fg, font=("Segoe UI", 9))
        self.tlabel.pack(side="right", padx=(6, 0))
        self.seek_v = tk.DoubleVar(value=0)
        self.bar = tk.Scale(bar, from_=0, to=1, resolution=0.1, orient="horizontal", showvalue=False,
                            variable=self.seek_v, bg=bg, troughcolor="#2a2040", highlightthickness=0, bd=0,
                            sliderlength=12, activebackground=accent)
        self.bar.pack(side="left", fill="x", expand=True, padx=6)
        self.bar.bind("<ButtonRelease-1>", lambda e: self.seek(self.seek_v.get()))
        self.bar.bind("<ButtonPress-1>", lambda e: setattr(self, "_dragging", True))
        self._dragging = False
        self.src = None
        self.dur = 0.0
        self.pos = 0.0
        self.end = None
        self.game_track = False
        self.playing = False
        self.vproc = self.aproc = None
        self.frames = queue.Queue(maxsize=24)
        self.gen = 0
        self._img = None
        self.bind("<Destroy>", lambda e: self.stop() if e.widget is self else None)

    # ---------------------------------------------------------------- public
    def load(self, path, start=0.0, dur=None, play=False):
        self.stop()
        self.src = path
        self.dur = dur or core.probe_duration(path) or 1.0
        self.end = None
        self.bar.config(to=max(0.1, self.dur))
        self.game_track = core.audio_streams(path) >= 2
        self.pos = max(0.0, min(start, self.dur - 0.1))
        self._update_time()
        if play:
            self.play()
        else:
            self.still(self.pos)

    def show_image(self, pil):
        im = pil.copy()
        im.thumbnail((self.w, self.h))
        canvas = Image.new("RGB", (self.w, self.h), "black")
        canvas.paste(im, ((self.w - im.width) // 2, (self.h - im.height) // 2))
        self._show(canvas)

    def toggle(self):
        if self.playing:
            self.pause()
        else:
            self.play()

    def play(self, until=None):
        if not self.src or not self.ff:
            return
        if self.pos >= self.dur - 0.05:
            self.pos = 0.0
        self.stop_procs()
        self.end = until
        self.gen += 1
        gen = self.gen
        self.playing = True
        self.pbtn.config(text="❚❚")
        self.frames = queue.Queue(maxsize=24)
        self.base = self.pos
        self.fi = 0
        self.t0 = None
        cmd = [self.ff, "-nostdin", "-v", "error", "-ss", f"{self.pos:.3f}", "-i", self.src, "-an", "-vf",
               f"fps={FPS},scale={self.w}:{self.h}:force_original_aspect_ratio=decrease,"
               f"pad={self.w}:{self.h}:(ow-iw)/2:(oh-ih)/2", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        try:
            self.vproc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                          stdin=subprocess.DEVNULL, creationflags=core.NO_WINDOW)
        except OSError:
            self.playing = False
            return
        threading.Thread(target=self._reader, args=(self.vproc, self.frames, gen), daemon=True).start()
        self.after(5, lambda: self._tick(gen))

    def pause(self):
        self.playing = False
        self.gen += 1
        self.stop_procs()
        self.pbtn.config(text="▶")

    def stop(self):
        self.pause()

    def seek(self, t, play=None):
        self._dragging = False
        was = self.playing if play is None else play
        self.pause()
        self.pos = max(0.0, min(float(t), self.dur - 0.05))
        self._update_time()
        if was:
            self.play()
        else:
            self.still(self.pos)

    def still(self, t):
        if not self.src or not self.ff:
            return
        self.gen += 1
        gen = self.gen
        cmd = [self.ff, "-nostdin", "-v", "error", "-ss", f"{t:.3f}", "-i", self.src, "-frames:v", "1", "-vf",
               f"scale={self.w}:{self.h}:force_original_aspect_ratio=decrease,pad={self.w}:{self.h}:(ow-iw)/2:(oh-ih)/2",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]

        def work():
            try:
                r = subprocess.run(cmd, capture_output=True, creationflags=core.NO_WINDOW, timeout=20)
                data = r.stdout[: self.w * self.h * 3]
                if len(data) == self.w * self.h * 3:
                    self.after(0, lambda: gen == self.gen and self._show(Image.frombytes("RGB", (self.w, self.h), data)))
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def stop_procs(self):
        for p in (self.vproc, self.aproc):
            if p and p.poll() is None:
                try:
                    p.kill()
                except Exception:
                    pass
        self.vproc = self.aproc = None

    # ---------------------------------------------------------------- internals
    def _blank(self):
        self._show(Image.new("RGB", (self.w, self.h), "black"))

    def _show(self, im):
        self._img = ImageTk.PhotoImage(im)
        self.screen.config(image=self._img, width=self.w, height=self.h)

    def _reader(self, proc, frames, gen):
        size = self.w * self.h * 3
        out = proc.stdout
        while gen == self.gen:
            buf = out.read(size)
            if not buf or len(buf) < size:
                break
            while gen == self.gen:
                try:
                    frames.put(buf, timeout=0.2)
                    break
                except queue.Full:
                    continue
        try:
            frames.put(None, timeout=0.5)
        except queue.Full:
            pass

    def _start_audio(self):
        if not self.ffplay:
            return
        cmd = [self.ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", "-ss", f"{self.pos:.3f}"]
        if self.game_track:
            cmd += ["-ast", "a:1"]
        cmd += [self.src]
        try:
            self.aproc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL, creationflags=core.NO_WINDOW)
        except OSError:
            self.aproc = None

    def _tick(self, gen):
        if gen != self.gen or not self.playing:
            return
        frame, ended = None, False
        if self.t0 is None:                       # wait for the first frame, then start clock + sound together
            try:
                frame = self.frames.get_nowait()
            except queue.Empty:
                self.after(5, lambda: self._tick(gen))
                return
            if frame is None:
                ended = True
            else:
                self._start_audio()
                self.t0 = time.perf_counter()
                self.fi = 1
        else:
            want = int((time.perf_counter() - self.t0) * FPS) + 1
            while self.fi < want:
                try:
                    f = self.frames.get_nowait()
                except queue.Empty:
                    break
                if f is None:
                    ended = True
                    break
                frame = f
                self.fi += 1
        if frame is not None:
            self._show(Image.frombytes("RGB", (self.w, self.h), frame))
            self.pos = self.base + self.fi / FPS
            self._update_time()
        if ended or self.pos >= self.dur - 0.02 or (self.end is not None and self.pos >= self.end):
            self.pause()
            return
        self.after(8, lambda: self._tick(gen))

    def _update_time(self):
        self.tlabel.config(text=f"{fmt(self.pos)} / {fmt(self.dur)}")
        if not self._dragging:
            self.seek_v.set(round(self.pos, 1))
        if self.on_time:
            try:
                self.on_time(self.pos)
            except Exception:
                pass
