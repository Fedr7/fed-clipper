"""Breachcut built-in recorder: replay buffer, full recordings, hotkeys, auto-start with Siege.

How it works (same idea as OBS / ShadowPlay / Medal):
  * One FFmpeg process captures the screen (Windows Desktop Duplication, falls back to GDI) plus audio and encodes on
    the graphics card when it can. It writes 2-second pieces into a scratch folder.
  * "Save clip" glues together the last N seconds of pieces - instantly, no re-encoding.
  * "Record" remembers which piece it started on and glues everything up to the stop.
  * Old pieces are deleted as it goes, so the scratch folder stays small.
Game/PC sound comes from Windows' loopback (what you hear), the mic from DirectShow.
Nothing here reads or touches the game - it only looks at the screen and the speakers, like any recorder.
"""
import ctypes
import glob
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime

import autoclip_core as core

SEG = 2                                   # seconds per piece
SCRATCH = os.path.join(core.APP_DIR, "replay")
GAME_EXES = ("rainbowsix.exe", "rainbowsix_vulkan.exe", "rainbowsix_be.exe")
ENCODER_IDS = {"NVIDIA": "h264_nvenc", "AMD": "h264_amf", "Intel": "h264_qsv", "CPU": "libx264"}
_enc_cache = {}


# ====================================================================== helpers
def seg_path(i):
    return os.path.join(SCRATCH, f"seg{i:07d}.ts")


def seg_indices():
    out = []
    for p in glob.glob(os.path.join(SCRATCH, "seg*.ts")):
        m = re.search(r"seg(\d+)\.ts$", p)
        if m:
            out.append(int(m.group(1)))
    return sorted(out)


def game_running():
    if os.name != "nt":
        return False
    try:
        r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=10,
                           creationflags=core.NO_WINDOW)
        low = r.stdout.lower()
        return any(f'"{exe}"' in low for exe in GAME_EXES)
    except Exception:
        return False


def has_filter(name):
    ff = core.find_ffmpeg()
    if not ff:
        return False
    r = subprocess.run([ff, "-hide_banner", "-h", f"filter={name}"], capture_output=True, text=True,
                       creationflags=core.NO_WINDOW)
    return r.returncode == 0 and "Unknown filter" not in (r.stdout + r.stderr)


def encoder_works(codec):
    """Try a tiny encode so we only pick a GPU encoder this PC really has."""
    if codec in _enc_cache:
        return _enc_cache[codec]
    ff = core.find_ffmpeg()
    ok = False
    if ff:
        fmt = "bgra" if codec == "h264_nvenc" else "nv12"
        r = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                            "color=black:s=256x256:r=30:d=0.2", "-vf", f"format={fmt}", "-c:v", codec, "-f", "null", "-"],
                           capture_output=True, text=True, creationflags=core.NO_WINDOW, timeout=30)
        ok = r.returncode == 0
    _enc_cache[codec] = ok
    return ok


def pick_encoder(pref):
    order = [ENCODER_IDS[pref]] if pref in ENCODER_IDS else []
    order += ["h264_nvenc", "h264_amf", "h264_qsv"]
    for c in order:
        if c != "libx264" and encoder_works(c):
            return c
    return "libx264"


def encoder_name(codec):
    return {v: k for k, v in ENCODER_IDS.items()}.get(codec, codec)


def video_args(codec, mbps, fps):
    b = [f"{mbps}M"]
    common = ["-b:v", b[0], "-maxrate", b[0], "-bufsize", f"{mbps * 2}M", "-g", str(fps * SEG),
              "-force_key_frames", f"expr:gte(t,n_forced*{SEG})"]
    if codec == "h264_nvenc":
        return ["-c:v", codec, "-preset", "p4", "-tune", "hq", "-rc", "cbr"] + common
    if codec == "h264_amf":
        return ["-c:v", codec, "-quality", "speed", "-rc", "cbr"] + common
    if codec == "h264_qsv":
        return ["-c:v", codec, "-preset", "veryfast"] + common
    return ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"] + common


def mic_name(setting):
    if setting and setting != "Default":
        return setting
    devs = core.audio_devices()
    return devs[0] if devs else None


# ====================================================================== game/PC audio (WASAPI loopback)
class Loopback:
    """Captures what you hear and feeds FFmpeg a steady stream (silence included, so sound never drifts)."""

    def __init__(self):
        self.ok = False
        self.rate, self.ch = 48000, 2
        self.buf = deque()
        self.buffered = 0
        self.lock = threading.Lock()
        self.stream = self.pa = None
        self.running = False

    @staticmethod
    def module():
        try:
            import pyaudiowpatch
            return pyaudiowpatch
        except ImportError:
            return None

    @staticmethod
    def install():
        """One-time: add the small audio library Breachcut uses for game sound."""
        py = sys.executable
        if py.lower().endswith("pythonw.exe"):
            alt = py[:-11] + "python.exe"
            if os.path.exists(alt):
                py = alt
        r = subprocess.run([py, "-m", "pip", "install", "--user", "--quiet", "--disable-pip-version-check",
                            "PyAudioWPatch"], capture_output=True, text=True, creationflags=core.NO_WINDOW, timeout=300)
        return r.returncode == 0

    def open(self):
        pa = self.module()
        if pa is None:
            return False
        try:
            self.pa = pa.PyAudio()
            wasapi = self.pa.get_host_api_info_by_type(pa.paWASAPI)
            spk = self.pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
            if not spk.get("isLoopbackDevice"):
                for lb in self.pa.get_loopback_device_info_generator():
                    if spk["name"] in lb["name"]:
                        spk = lb
                        break
                else:
                    return False
            self.rate = int(spk["defaultSampleRate"])
            self.ch = max(1, int(spk["maxInputChannels"]))

            def cb(data, frames, info, status):
                with self.lock:
                    self.buf.append(data)
                    self.buffered += len(data)
                return (None, pa.paContinue)
            self.stream = self.pa.open(format=pa.paInt16, channels=self.ch, rate=self.rate, input=True,
                                       input_device_index=spk["index"], frames_per_buffer=1024, stream_callback=cb)
            self.ok = True
            return True
        except Exception:
            self.close()
            return False

    def pump(self, sink):
        """Write exactly real-time amounts of audio into sink (FFmpeg's stdin) until stopped."""
        self.running = True
        frame = self.ch * 2
        start = time.monotonic()
        written = 0
        while self.running:
            due = int((time.monotonic() - start) * self.rate)
            need = (due - written) * frame
            if need > 0:
                out = bytearray()
                with self.lock:
                    while self.buf and len(out) < need:
                        chunk = self.buf.popleft()
                        take = need - len(out)
                        out += chunk[:take]
                        if len(chunk) > take:
                            self.buf.appendleft(chunk[take:])
                    self.buffered = sum(len(c) for c in self.buf)
                    # if Windows hands us more than half a second extra, drop it (keeps sound in sync)
                    while self.buffered > self.rate * frame // 2 and self.buf:
                        self.buffered -= len(self.buf.popleft())
                if len(out) < need:
                    out += b"\0" * (need - len(out))
                try:
                    sink.write(bytes(out))
                    sink.flush()
                except (OSError, ValueError):
                    break
                written = due
            time.sleep(0.01)

    def close(self):
        self.running = False
        try:
            if self.stream:
                self.stream.stop_stream()
                self.stream.close()
        except Exception:
            pass
        try:
            if self.pa:
                self.pa.terminate()
        except Exception:
            pass
        self.stream = self.pa = None


# ====================================================================== recorder
class Recorder:
    """on_event(kind, value): 'state' None | 'saved' path | 'error' text | 'info' text. Called from worker threads."""

    def __init__(self, settings, on_event, video_source=None):
        self.s = settings
        self.on_event = on_event
        self.video_source = video_source       # tests can swap the screen for a test pattern
        self.proc = None
        self.loop = None
        self.running = False
        self.recording = False
        self.rec_start = None
        self.rec_started_at = None
        self.codec = None
        self.lock = threading.Lock()
        self.saving = False
        self.last_error = ""

    # ---------------------------------------------------------------- build the FFmpeg command
    def build(self, ff, loop_rate=None, loop_ch=None, mic=None):
        s = self.s
        fps = int(s.get("rec_fps") or 60)
        mbps = int(s.get("rec_bitrate") or 20)
        self.codec = self.codec or pick_encoder(s.get("rec_encoder", "Auto"))
        cmd = [ff, "-hide_banner", "-loglevel", "error", "-y"]
        vf = []
        if self.video_source:
            cmd += self.video_source
        elif has_filter("ddagrab"):
            cmd += ["-f", "lavfi", "-i", f"ddagrab=output_idx=0:framerate={fps}:draw_mouse=0"]
            vf.append("hwdownload,format=bgra")
        else:
            cmd += ["-f", "gdigrab", "-framerate", str(fps), "-draw_mouse", "0", "-i", "desktop"]
        res = {"1440p": 1440, "1080p": 1080, "720p": 720}.get(s.get("rec_resolution"))
        if res:
            vf.append(f"scale=-2:'min(ih,{res})':flags=bicubic")
        if self.codec != "h264_nvenc":
            vf.append("format=nv12" if self.codec in ("h264_amf", "h264_qsv") else "format=yuv420p")
        n = 1
        g_in = m_in = None
        if loop_rate:
            cmd += ["-thread_queue_size", "4096", "-f", "s16le", "-ar", str(loop_rate), "-ac", str(loop_ch), "-i", "pipe:0"]
            g_in, n = n, n + 1
        if mic:
            cmd += ["-thread_queue_size", "4096", "-f", "dshow", "-audio_buffer_size", "50", "-i", f"audio={mic}"]
            m_in, n = n, n + 1
        fc = [f"[0:v]{','.join(vf) if vf else 'null'}[v]"]
        gv = max(0, int(s.get("rec_game_vol", 100))) / 100
        mv = max(0, int(s.get("mic_vol", 100))) / 100
        if g_in is not None:
            fc.append(f"[{g_in}:a]aresample=48000,aformat=channel_layouts=stereo,volume={gv}[g]")
        if m_in is not None:
            fc.append(f"[{m_in}:a]aresample=48000:async=1,aformat=channel_layouts=stereo,volume={mv}[m]")
        maps = ["-map", "[v]"]
        if g_in is not None and m_in is not None:
            # track 1 = everything mixed, track 2 = game only, track 3 = mic only (same layout as Medal)
            sep = bool(s.get("separate_tracks", True))
            if sep:
                fc.append("[g]asplit[g1][g2]")
                fc.append("[m]asplit[m1][m2]")
                fc.append("[g1][m1]amix=inputs=2:normalize=0:duration=longest[mix]")
                maps += ["-map", "[mix]", "-map", "[g2]", "-map", "[m2]"]
            else:
                fc.append("[g][m]amix=inputs=2:normalize=0:duration=longest[mix]")
                maps += ["-map", "[mix]"]
        elif g_in is not None:
            maps += ["-map", "[g]"]
        elif m_in is not None:
            maps += ["-map", "[m]"]
        cmd += ["-filter_complex", ";".join(fc)] + maps
        cmd += video_args(self.codec, mbps, fps)
        if g_in is not None or m_in is not None:
            cmd += ["-c:a", "aac", "-b:a", "160k"]
        cmd += ["-f", "segment", "-segment_time", str(SEG), "-segment_format", "mpegts", seg_path(0).replace("0000000", "%07d")]
        return cmd

    # ---------------------------------------------------------------- start / stop
    def start(self):
        with self.lock:
            if self.running:
                return True
            ff = core.find_ffmpeg()
            if not ff:
                self.on_event("error", "FFmpeg is missing, so the recorder can't start. Re-run Install.bat.")
                return False
            shutil.rmtree(SCRATCH, ignore_errors=True)
            os.makedirs(SCRATCH, exist_ok=True)
            loop = None
            if self.s.get("rec_game_audio", True) and os.name == "nt":
                if Loopback.module() is None:
                    self.on_event("info", "Setting up game sound capture (one time)…")
                    Loopback.install()
                loop = Loopback()
                if not loop.open():
                    loop = None
                    self.on_event("info", "Couldn't capture game sound, recording video without it.")
            mic = mic_name(self.s.get("mic_device")) if self.s.get("rec_mic") else None
            cmd = self.build(ff, loop.rate if loop else None, loop.ch if loop else None, mic)
            try:
                self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE if loop else subprocess.DEVNULL,
                                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                             creationflags=core.NO_WINDOW)
            except OSError as e:
                if loop:
                    loop.close()
                self.on_event("error", f"Recorder couldn't start: {e}")
                return False
            self.loop = loop
            self.running = True
            if loop:
                threading.Thread(target=loop.pump, args=(self.proc.stdin,), daemon=True).start()
            threading.Thread(target=self._watch, args=(self.proc,), daemon=True).start()
            threading.Thread(target=self._prune, args=(self.proc,), daemon=True).start()
        self.on_event("state", None)
        return True

    def stop(self):
        with self.lock:
            if not self.running:
                return
            self.running = False
            proc, loop = self.proc, self.loop
            self.proc = self.loop = None
        if self.recording:
            self.stop_recording(wait=False)
        if loop:
            loop.close()
        if proc:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except OSError:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        if not self.saving:
            shutil.rmtree(SCRATCH, ignore_errors=True)
        self.on_event("state", None)

    def restart(self):
        """Apply new settings (quality, audio...) by restarting the buffer."""
        if self.running and not self.recording:
            self.stop()
            self.codec = None
            self.start()
        else:
            self.codec = None

    def _watch(self, proc):
        err = proc.stderr.read().decode("utf-8", "ignore") if proc.stderr else ""
        proc.wait()
        if proc is self.proc and self.running:
            self.running = False
            self.recording = False
            if self.loop:
                self.loop.close()
            self.last_error = err.strip()[-400:]
            self.on_event("error", "The recorder stopped: " + (self.last_error.splitlines()[-1] if self.last_error
                                                              else "unknown error"))
            self.on_event("state", None)

    def _prune(self, proc):
        while proc is self.proc and self.running:
            time.sleep(SEG)
            idx = seg_indices()
            if not idx:
                continue
            cur = idx[-1]
            keep = cur - int(self.s.get("replay_seconds", 30)) // SEG - 3
            if self.recording and self.rec_start is not None:
                keep = min(keep, self.rec_start)
            for i in idx:
                if i < keep:
                    try:
                        os.remove(seg_path(i))
                    except OSError:
                        pass

    # ---------------------------------------------------------------- saving
    def _wait_closed(self, i, timeout=SEG * 3):
        """Wait until piece i is finished (the next one has started)."""
        end = time.time() + timeout
        while time.time() < end:
            if os.path.exists(seg_path(i + 1)) or not self.running:
                return True
            time.sleep(0.1)
        return False

    def _join(self, first, last, out):
        parts = [seg_path(i) for i in range(first, last + 1) if os.path.exists(seg_path(i))]
        if not parts:
            raise RuntimeError("Nothing has been recorded yet.")
        ff = core.find_ffmpeg()
        os.makedirs(os.path.dirname(out), exist_ok=True)
        lst = os.path.join(SCRATCH, f"join_{int(time.time() * 1000)}.txt")
        with open(lst, "w", encoding="utf-8") as f:
            for p in parts:
                f.write("file '" + p.replace("'", "'\\''") + "'\n")
        r = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                            "-map", "0", "-c", "copy", "-avoid_negative_ts", "make_zero", "-movflags", "+faststart",
                            out], capture_output=True, text=True, creationflags=core.NO_WINDOW)
        try:
            os.remove(lst)
        except OSError:
            pass
        if r.returncode != 0 or not os.path.exists(out):
            raise RuntimeError((r.stderr or "couldn't save the clip").strip()[-300:])
        return out

    def out_path(self, kind):
        folder = self.s.get("recordings_dir") or os.path.join(core.default_library(), "Recordings")
        return os.path.join(folder, f"Breachcut {kind} {datetime.now():%Y-%m-%d %H-%M-%S}.mp4")

    def save_replay(self, seconds=None):
        if not self.running:
            self.on_event("error", "Turn the replay buffer on first.")
            return
        if self.saving:
            return
        self.saving = True
        seconds = int(seconds or self.s.get("replay_seconds", 30))

        def work():
            try:
                idx = seg_indices()
                if not idx:
                    raise RuntimeError("Nothing has been recorded yet.")
                cur = idx[-1]
                self._wait_closed(cur)
                n = max(1, -(-seconds // SEG))
                out = self._join(max(idx[0], cur - n + 1), cur, self.out_path("Clip"))
                self.on_event("saved", out)
            except Exception as e:
                self.on_event("error", f"Couldn't save the clip: {e}")
            finally:
                self.saving = False
        threading.Thread(target=work, daemon=True).start()

    def start_recording(self):
        if not self.running and not self.start():
            return
        idx = seg_indices()
        self.rec_start = idx[-1] if idx else 0
        self.rec_started_at = time.time()
        self.recording = True
        self.on_event("state", None)

    def stop_recording(self, wait=True):
        if not self.recording:
            return
        self.recording = False
        first = self.rec_start or 0
        self.on_event("state", None)

        def work():
            try:
                idx = seg_indices()
                if not idx:
                    raise RuntimeError("Nothing was recorded.")
                cur = idx[-1]
                if wait:
                    self._wait_closed(cur)
                out = self._join(first, cur, self.out_path("Recording"))
                self.on_event("saved", out)
            except Exception as e:
                self.on_event("error", f"Couldn't save the recording: {e}")
            finally:
                self.rec_start = None
        if wait:
            threading.Thread(target=work, daemon=True).start()
        else:
            work()

    def toggle_recording(self):
        if self.recording:
            self.stop_recording()
        else:
            self.start_recording()

    def status_text(self):
        if self.recording:
            secs = int(time.time() - (self.rec_started_at or time.time()))
            return f"Recording  {secs // 60}:{secs % 60:02d}"
        if self.running:
            return f"Replay buffer on · last {self.s.get('replay_seconds', 30)}s ready to save"
        return "Recorder is off"


# ====================================================================== global hotkeys (Windows RegisterHotKey)
VK = {"Insert": 0x2D, "Delete": 0x2E, "Home": 0x24, "End": 0x23, "Prior": 0x21, "Next": 0x22, "Page Up": 0x21,
      "Page Down": 0x22, "Pause": 0x13, "Scroll Lock": 0x91, "Print": 0x2C, "Up": 0x26, "Down": 0x28, "Left": 0x25,
      "Right": 0x27, "Tab": 0x09, "Return": 0x0D, "space": 0x20, "Space": 0x20, "Num Lock": 0x90,
      "minus": 0xBD, "equal": 0xBB, "comma": 0xBC, "period": 0xBE, "slash": 0xBF, "semicolon": 0xBA,
      "quoteleft": 0xC0, "bracketleft": 0xDB, "bracketright": 0xDD, "backslash": 0xDC, "apostrophe": 0xDE}
MOD = {"Alt": 0x1, "Ctrl": 0x2, "Shift": 0x4, "Win": 0x8}
MOD_NOREPEAT = 0x4000


def parse_hotkey(combo):
    """'Ctrl+Shift+F8' -> (mods, vk) or None."""
    if not combo or combo == "None":
        return None
    parts = combo.split("+")
    key, mods = parts[-1], 0
    for p in parts[:-1]:
        if p not in MOD:
            return None
        mods |= MOD[p]
    vk = None
    m = re.fullmatch(r"F(\d{1,2})", key)
    if m and 1 <= int(m.group(1)) <= 24:
        vk = 0x70 + int(m.group(1)) - 1
    elif len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    elif key.startswith("KP ") and key[3:].isdigit():
        vk = 0x60 + int(key[3:])
    else:
        vk = VK.get(key)
    return (mods | MOD_NOREPEAT, vk) if vk else None


class Hotkeys:
    """Registers system-wide hotkeys and calls callback(name) when one is pressed."""

    def __init__(self, callback, on_fail=None):
        self.callback = callback
        self.on_fail = on_fail
        self.thread = None
        self.tid = None

    def set(self, bindings):
        """bindings: {name: 'F10', ...}. Re-registers everything."""
        self.stop()
        if os.name != "nt":
            return
        ready = threading.Event()
        self.thread = threading.Thread(target=self._run, args=(dict(bindings), ready), daemon=True)
        self.thread.start()
        ready.wait(2)

    def _run(self, bindings, ready):
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self.tid = kernel32.GetCurrentThreadId()
        names = {}
        for i, (name, combo) in enumerate(bindings.items(), 1):
            hk = parse_hotkey(combo)
            if not hk:
                continue
            if user32.RegisterHotKey(None, i, hk[0], hk[1]):
                names[i] = name
            elif self.on_fail:
                self.on_fail(name, combo)
        ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312 and msg.wParam in names:      # WM_HOTKEY
                try:
                    self.callback(names[msg.wParam])
                except Exception:
                    pass
        for i in names:
            user32.UnregisterHotKey(None, i)

    def stop(self):
        if self.thread and self.tid and os.name == "nt":
            ctypes.windll.user32.PostThreadMessageW(self.tid, 0x0012, 0, 0)      # WM_QUIT
            self.thread.join(2)
        self.thread = self.tid = None


def recording_clip(path):
    """Make a recording look like a Medal clip so the editor can open it."""
    stamp = re.search(r"(\d{4})-(\d{2})-(\d{2}) (\d{2})-(\d{2})-(\d{2})", os.path.basename(path))
    sid = "".join(stamp.groups()) if stamp else datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y%m%d%H%M%S")
    return {"id": f"rec{sid}", "path": path, "dur": core.probe_duration(path), "kills": [], "top": 0,
            "mode": "Recording", "title": "", "created": None}


def list_recordings(s):
    folder = s.get("recordings_dir") or ""
    if not os.path.isdir(folder):
        return []
    out = []
    for p in glob.glob(os.path.join(folder, "*.mp4")):
        try:
            st = os.stat(p)
        except OSError:
            continue
        out.append({"path": p, "fmt": "wide", "clip_id": None, "title": os.path.splitext(os.path.basename(p))[0],
                    "seconds": 0, "kind": "recording",
                    "made_at": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")})
    out.sort(key=lambda v: v["made_at"], reverse=True)
    return out
