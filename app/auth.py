"""Breachcut accounts: Sign in with Discord through Supabase (OAuth + PKCE, no passwords stored).

Flow: open the browser at Supabase's /authorize -> Discord -> back to a tiny local page on 127.0.0.1
-> exchange the one-time code for a session. The session is encrypted with Windows DPAPI (only this
Windows user can read it) and refreshed automatically.
"""
import base64
import hashlib
import json
import os
import secrets
import ssl
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import autoclip_core as core

# Public project settings (the anon/publishable key is meant to ship inside apps; data is protected by RLS).
SUPABASE_URL = "https://xdpfytnlxheuyhzjphkz.supabase.co"
SUPABASE_KEY = "sb_publishable_bLLBQwL_XRSiez0FOXk_1Q_NHyPfSCo"
PRIVACY_URL = "https://github.com/Fedr7/fed-clipper/blob/main/PRIVACY.md"
TERMS_URL = "https://github.com/Fedr7/fed-clipper/blob/main/TERMS.md"

PORTS = (53682, 53683, 53684)
SESSION_FILE = os.path.join(core.APP_DIR, "session.dat")
AVATARS = os.path.join(core.APP_DIR, "avatars")
_CFG = os.path.join(core.APP_DIR, "auth_config.json")
if os.path.exists(_CFG):                       # lets a patch ship new keys without touching code
    try:
        _c = json.load(open(_CFG, encoding="utf-8"))
        SUPABASE_URL = _c.get("url", SUPABASE_URL)
        SUPABASE_KEY = _c.get("key", SUPABASE_KEY)
    except Exception:
        pass


class AuthError(Exception):
    pass


def configured():
    return bool(SUPABASE_URL and SUPABASE_KEY)


# ---------------------------------------------------------------- secure storage
def _dpapi(data, protect):
    if os.name != "nt":
        return data
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    inp = BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_char)))
    out = BLOB()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(inp), None, None, None, None, 0x01, ctypes.byref(out))  # UI_FORBIDDEN
    if not ok:
        raise AuthError("Couldn't secure the session on this PC.")
    res = ctypes.string_at(out.pbData, out.cbData)
    ctypes.windll.kernel32.LocalFree(out.pbData)
    return res


def save_session(s):
    raw = json.dumps(s).encode("utf-8")
    tmp = SESSION_FILE + ".tmp"
    with open(tmp, "wb") as f:
        f.write(_dpapi(raw, True))
    os.replace(tmp, SESSION_FILE)


def load_session():
    try:
        with open(SESSION_FILE, "rb") as f:
            return json.loads(_dpapi(f.read(), False).decode("utf-8"))
    except Exception:
        return None


def clear_session():
    try:
        os.remove(SESSION_FILE)
    except OSError:
        pass


# ---------------------------------------------------------------- http
def _req(method, path, body=None, token=None, extra_headers=None, timeout=15):
    if not configured():
        raise AuthError("Sign-in isn't set up yet.")
    url = SUPABASE_URL.rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    h = {"apikey": SUPABASE_KEY, "Content-Type": "application/json", "User-Agent": "Breachcut"}
    if token:
        h["Authorization"] = "Bearer " + token
    h.update(extra_headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
            txt = r.read().decode("utf-8")
            return json.loads(txt) if txt.strip() else {}
    except urllib.error.HTTPError as e:
        try:
            j = json.loads(e.read().decode("utf-8"))
            msg = j.get("error_description") or j.get("msg") or j.get("message") or str(j)
        except Exception:
            msg = f"HTTP {e.code}"
        raise AuthError(msg)
    except (urllib.error.URLError, OSError) as e:
        raise ConnectionError(str(e))


def _stamp(sess):
    sess["expires_at"] = sess.get("expires_at") or int(time.time()) + int(sess.get("expires_in") or 3600)
    return sess


def refresh(sess):
    new = _req("POST", "/auth/v1/token?grant_type=refresh_token", {"refresh_token": sess["refresh_token"]})
    new = _stamp(new)
    save_session(new)
    return new


def current_session():
    """(session, online). Refreshes when close to expiry. Keeps you signed in offline."""
    s = load_session()
    if not s:
        return None, True
    if s.get("expires_at", 0) - time.time() > 300:
        return s, True
    try:
        return refresh(s), True
    except ConnectionError:
        return s, False          # offline: trust the saved session
    except AuthError:
        clear_session()          # revoked or expired: sign in again
        return None, True


def sign_out(sess=None):
    try:
        if sess:
            _req("POST", "/auth/v1/logout", {}, token=sess.get("access_token"), timeout=5)
    except Exception:
        pass
    clear_session()


# ---------------------------------------------------------------- user info
def user_info(sess):
    u = (sess or {}).get("user") or {}
    md = u.get("user_metadata") or {}
    claims = md.get("custom_claims") or {}
    name = claims.get("global_name") or md.get("full_name") or md.get("name") or md.get("user_name") or "Player"
    return {"id": u.get("id"), "name": name, "handle": md.get("name") or md.get("user_name") or "",
            "avatar": md.get("avatar_url") or md.get("picture") or "", "email": u.get("email") or "",
            "since": (u.get("created_at") or "")[:10]}


def avatar_path(info):
    """Download the Discord avatar once (cached). Returns a file path or None."""
    url = info.get("avatar")
    if not url:
        return None
    os.makedirs(AVATARS, exist_ok=True)
    p = os.path.join(AVATARS, hashlib.md5(url.encode()).hexdigest()[:12] + ".png")
    if os.path.exists(p):
        return p
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Breachcut"})
        with urllib.request.urlopen(req, timeout=10, context=ssl.create_default_context()) as r:
            data = r.read()
        with open(p, "wb") as f:
            f.write(data)
        return p
    except Exception:
        return None


def upsert_profile(sess, **fields):
    """Keep a small profile row (name, avatar, app version, last seen). Best effort."""
    info = user_info(sess)
    row = {"id": info["id"], "username": info["name"], "avatar_url": info["avatar"],
           "last_seen": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    row.update(fields)
    try:
        _req("POST", "/rest/v1/profiles?on_conflict=id", row, token=sess["access_token"],
             extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"}, timeout=8)
        return True
    except Exception:
        return False


def send_bug_report(sess, message, app_version="", log_tail="", os_name=""):
    """File a bug report. Returns (ok, message for the user)."""
    if not sess:
        return False, "Sign in with Discord first, or report it in our Discord."
    info = user_info(sess)
    row = {"user_id": info["id"], "username": info["name"], "app_version": app_version,
           "os": os_name[:120], "message": message[:4000], "log_tail": (log_tail or "")[-20000:]}
    try:
        _req("POST", "/rest/v1/bug_reports", row, token=sess["access_token"],
             extra_headers={"Prefer": "return=minimal"}, timeout=15)
        return True, "Thanks! Your report was sent."
    except ConnectionError:
        return False, "Couldn't reach the server. Check your internet and try again."
    except AuthError as e:
        m = str(e)
        if "Too many" in m:
            return False, "You've sent a lot of reports this hour. Try again later."
        return False, "Couldn't send the report: " + m[:160]


# ---------------------------------------------------------------- Discord sign-in
PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Breachcut</title><style>
html,body{{margin:0;height:100%;background:#0b0910;color:#f4f1fb;font-family:'Segoe UI',system-ui,sans-serif}}
body{{display:flex;align-items:center;justify-content:center;
background:radial-gradient(60% 50% at 15% 0%,rgba(255,77,109,.28),transparent 70%),
radial-gradient(50% 45% at 95% 0%,rgba(123,92,255,.25),transparent 70%),#0b0910}}
.card{{text-align:center;padding:48px 56px;border-radius:20px;background:rgba(23,19,31,.85);
border:1px solid #2a2336;box-shadow:0 30px 80px rgba(0,0,0,.5);max-width:420px}}
.hex{{width:64px;height:64px;margin:0 auto 18px;display:flex;align-items:center;justify-content:center;
font:900 34px Impact,'Segoe UI Black';color:#ff4d6d;border:5px solid #fff;
clip-path:polygon(50% 0,100% 25%,100% 75%,50% 100%,0 75%,0 25%);background:#16101c}}
h1{{margin:0 0 8px;font-size:24px}}p{{margin:0;color:#9a92ad;font-size:15px;line-height:1.5}}
.ok{{color:#5ee0a0}}.bad{{color:#ff8a9e}}</style></head><body><div class="card">
<div class="hex">B</div><h1 class="{cls}">{title}</h1><p>{msg}</p></div></body></html>"""


class DiscordLogin:
    """Runs the browser sign-in in the background. on_done(ok, session_or_message) is called once."""

    def __init__(self, on_done, timeout=300):
        self.on_done = on_done
        self.timeout = timeout
        self.verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).rstrip(b"=").decode()
        self.challenge = challenge
        self.server = None
        self.done = False
        self.url = None

    def start(self):
        if not configured():
            self._finish(False, "Sign-in isn't set up yet.")
            return
        flow = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                if u.path != "/callback":
                    self.send_response(404)
                    self.end_headers()
                    return
                q = urllib.parse.parse_qs(u.query)
                code = (q.get("code") or [None])[0]
                err = (q.get("error_description") or q.get("error") or [None])[0]
                if code:
                    try:
                        sess = _stamp(_req("POST", "/auth/v1/token?grant_type=pkce",
                                           {"auth_code": code, "code_verifier": flow.verifier}))
                        save_session(sess)
                        name = user_info(sess)["name"]
                        self._page("ok", f"Welcome, {name}!", "You're signed in. You can close this tab and go back to Breachcut.")
                        flow._finish(True, sess)
                    except Exception as e:
                        self._page("bad", "Sign-in didn't finish", "Something went wrong. Go back to Breachcut and try again.")
                        flow._finish(False, str(e))
                else:
                    self._page("bad", "Sign-in cancelled", "No worries. Go back to Breachcut to try again.")
                    flow._finish(False, err or "Sign-in was cancelled.")

            def _page(self, cls, title, msg):
                body = PAGE.format(cls=cls, title=title, msg=msg).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        for port in PORTS:
            try:
                self.server = HTTPServer(("127.0.0.1", port), Handler)
                break
            except OSError:
                continue
        if not self.server:
            self._finish(False, "Couldn't start sign-in (ports busy). Close other apps and try again.")
            return
        port = self.server.server_address[1]
        params = {"provider": "discord", "redirect_to": f"http://127.0.0.1:{port}/callback",
                  "code_challenge": self.challenge, "code_challenge_method": "s256", "scopes": "identify"}
        self.url = SUPABASE_URL.rstrip("/") + "/auth/v1/authorize?" + urllib.parse.urlencode(params)
        threading.Thread(target=self._serve, daemon=True).start()
        webbrowser.open(self.url)

    def _serve(self):
        end = time.time() + self.timeout
        self.server.timeout = 0.5
        while not self.done and time.time() < end:
            self.server.handle_request()
        if not self.done:
            self._finish(False, "Sign-in timed out. Try again.")
        try:
            self.server.server_close()
        except Exception:
            pass

    def cancel(self):
        if not self.done:
            self.done = True
            try:
                self.server and self.server.server_close()
            except Exception:
                pass

    def _finish(self, ok, payload):
        if self.done:
            return
        self.done = True
        try:
            self.on_done(ok, payload)
        except Exception:
            pass
