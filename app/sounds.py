"""Breachcut's own little sounds (instead of the Windows "ding").

The sounds are made by code the first time they're needed and saved as small .wav files in the app folder, so
nothing has to be downloaded. They play in the background and never pause the game.
"""
import math
import os
import struct
import threading
import wave

import autoclip_core as core

RATE = 44100
FOLDER = os.path.join(core.APP_DIR, "sounds")
VERSION = "1"

# name -> list of (frequency Hz, length s, gap after s); a soft "blip" for each note
SOUNDS = {
    "clip": [(880, 0.07, 0.02), (1318.5, 0.11, 0)],            # rising two-note chime: clip saved
    "auto": [(659.3, 0.06, 0.015), (880, 0.06, 0.015), (1318.5, 0.12, 0)],   # three notes: auto-clip saved
    "record_on": [(523.3, 0.08, 0.02), (784, 0.12, 0)],         # recording started
    "record_off": [(784, 0.08, 0.02), (523.3, 0.12, 0)],        # recording stopped
    "kill": [(1568, 0.045, 0)],                                 # tiny tick on a kill
    "made": [(784, 0.07, 0.02), (987.8, 0.07, 0.02), (1568, 0.14, 0)],  # video finished
    "error": [(311.1, 0.11, 0.05), (233.1, 0.16, 0)],           # low two-note: something went wrong
}


def _note(freq, length, volume):
    n = int(RATE * length)
    out = []
    for i in range(n):
        t = i / RATE
        attack = min(1.0, t / 0.006)
        decay = math.exp(-t * 18)
        # a sine with a little octave on top sounds like a soft bell instead of a beep
        s = math.sin(2 * math.pi * freq * t) + 0.25 * math.sin(4 * math.pi * freq * t)
        out.append(s * attack * decay * volume)
    return out


def _make(name):
    path = os.path.join(FOLDER, f"{name}_v{VERSION}.wav")
    if os.path.exists(path):
        return path
    os.makedirs(FOLDER, exist_ok=True)
    samples = []
    for freq, length, gap in SOUNDS[name]:
        samples += _note(freq, length, 0.32)
        samples += [0.0] * int(RATE * gap)
    samples += [0.0] * int(RATE * 0.03)
    tmp = path + ".tmp"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, s)) * 32000)) for s in samples))
    os.replace(tmp, path)
    return path


def play(name):
    """Play one of Breachcut's sounds without waiting for it."""
    if os.name != "nt" or name not in SOUNDS:
        return

    def run():
        try:
            import winsound
            winsound.PlaySound(_make(name), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()


def prepare():
    """Make all the sound files ahead of time (so the first clip sound has no delay)."""
    for n in SOUNDS:
        try:
            _make(n)
        except Exception:
            pass
