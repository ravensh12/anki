# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Google sign-in for Ante (desktop OAuth 2.0, loopback + PKCE).

Google forbids OAuth inside embedded webviews, so the real flow opens the user's
system browser, captures the redirect on a temporary localhost server, exchanges
the code (PKCE, no client secret needed for an installed app), and reads the
profile. It only runs when a client ID is configured via the environment:

    ANTE_GOOGLE_CLIENT_ID   (required for real Google sign-in)
    ANTE_GOOGLE_CLIENT_SECRET (optional; PKCE works without it)

Without a client ID the Google button shows an error instead of signing in
locally. Network + browser work happen off the main thread; the actual account
write is marshalled back onto the main thread.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

# Public OAuth client id (not a secret). Ensures "Continue with Google" always
# opens accounts.google.com instead of a local demo sign-in.
_DEFAULT_CLIENT_ID = (
    "80276813699-l57p46q7pj79ro2hfdplsag2hlh9f9qg.apps.googleusercontent.com"
)


def _log(msg: str) -> None:
    """Surface sign-in progress/failures to the terminal so a stuck Google flow
    is diagnosable instead of failing silently. Writes to the ORIGINAL stderr
    (``sys.__stderr__``): Anki replaces ``sys.stderr`` with an ErrorHandler that
    lacks ``flush``, so we must not use it here. Fully guarded — logging can
    never break the flow."""
    try:
        stream = sys.__stderr__ or sys.stderr
        stream.write(f"[ante-auth] {msg}\n")
        flush = getattr(stream, "flush", None)
        if callable(flush):
            flush()
    except Exception:
        pass


def _load_client_json() -> tuple[str, str]:
    """Best-effort read of a Google OAuth credentials JSON (the file you get from
    'Download JSON' in the Cloud console). Checked in order:
      1. the path in ANTE_GOOGLE_CLIENT_JSON, else
      2. the newest ~/Downloads/client_secret*.json.
    Supports both the ``installed`` (Desktop) and ``web`` shapes. Returns
    (client_id, client_secret) or ("", "")."""
    import glob
    from pathlib import Path

    candidates: list[str] = []
    explicit = os.environ.get("ANTE_GOOGLE_CLIENT_JSON", "").strip()
    if explicit:
        candidates.append(explicit)
    downloads = Path.home() / "Downloads"
    if downloads.is_dir():
        found = sorted(
            glob.glob(str(downloads / "client_secret*.json")),
            key=lambda p: os.path.getmtime(p),
            reverse=True,
        )
        candidates.extend(found)
    for path in candidates:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            continue
        node = data.get("installed") or data.get("web") or data
        cid = str(node.get("client_id", "")).strip()
        secret = str(node.get("client_secret", "")).strip()
        if cid:
            return cid, secret
    return "", ""


def _resolve_client_from_env_or_json() -> tuple[str, str]:
    """Client id/secret from environment or ~/Downloads JSON only."""
    env_id = os.environ.get("ANTE_GOOGLE_CLIENT_ID", "").strip()
    env_secret = os.environ.get("ANTE_GOOGLE_CLIENT_SECRET", "").strip()
    if env_id and env_secret:
        return env_id, env_secret
    json_id, json_secret = _load_client_json()
    return (env_id or json_id, env_secret or json_secret)


def _client(col=None) -> tuple[str, str]:
    """Resolve (client_id, client_secret).

    Priority: env/JSON > device-stored config > built-in default client id."""
    env_id, env_secret = _resolve_client_from_env_or_json()
    stored_id = ""
    stored_secret = ""
    if col is not None:
        try:
            from aqt.ante import get_google_client_id, get_google_secret

            stored_id = get_google_client_id(col)
            if not env_secret:
                stored_secret = get_google_secret(col)
        except Exception:
            pass
    client_id = env_id or stored_id or _DEFAULT_CLIENT_ID
    client_secret = env_secret or stored_secret
    if not client_secret:
        _, json_secret = _load_client_json()
        client_secret = json_secret
    return client_id, client_secret


def start_google_login(mw, fallback_email: str = "") -> None:
    """Kick off Google sign-in in the system browser (never a local demo account)."""
    del fallback_email  # unused; kept for call-site compatibility
    col = getattr(mw, "col", None)
    client_id, _ = _client(col)
    if not client_id:
        _log("Google OAuth not configured — set ANTE_GOOGLE_CLIENT_ID")
        return
    threading.Thread(target=_google_flow, args=(mw,), daemon=True).start()


def _run_on_main(mw, fn) -> None:
    try:
        mw.taskman.run_on_main(fn)
    except Exception:
        try:
            fn()
        except Exception:
            pass


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _open_browser(url: str) -> None:
    try:
        from aqt.qt import QDesktopServices, QUrl

        QDesktopServices.openUrl(QUrl(url))
        return
    except Exception:
        pass
    try:
        import webbrowser

        webbrowser.open(url)
    except Exception:
        pass


def _redirect_server(holder: dict) -> tuple[http.server.HTTPServer, str]:
    """A one-shot loopback server that captures Google's redirect params."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in params or "error" in params:
                holder["code"] = (params.get("code") or [None])[0]
                holder["state"] = (params.get("state") or [None])[0]
                if "error" in params:
                    holder["error"] = (params.get("error") or [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body style='font-family:-apple-system,sans-serif;"
                b"text-align:center;padding:64px;background:#0c1712;color:#ece4cd'>"
                b"<h2 style='font-weight:800'>Signed in to Ante</h2>"
                b"<p>You can close this tab and return to the den.</p></body></html>"
            )

        def log_message(self, *args):  # silence server logging
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 5
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _auth_request_url(
    client_id: str, redirect_uri: str, challenge: str, state: str
) -> str:
    return (
        _AUTH_URL
        + "?"
        + urllib.parse.urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": "openid email profile",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
                "prompt": "select_account",
            }
        )
    )


def _google_flow(mw) -> None:
    try:
        client_id, client_secret = _client(mw.col)
        verifier = _b64url(secrets.token_bytes(48))
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        state = secrets.token_urlsafe(16)
        holder: dict = {}
        server, redirect_uri = _redirect_server(holder)
        _open_browser(_auth_request_url(client_id, redirect_uri, challenge, state))

        _log(f"waiting for redirect on {redirect_uri}")
        deadline = time.time() + 180
        while "code" not in holder and time.time() < deadline:
            server.handle_request()
        server.server_close()

        _finish_sign_in(
            mw, client_id, client_secret, holder, state, verifier, redirect_uri
        )
    except Exception as exc:
        _log(f"sign-in flow crashed: {type(exc).__name__}: {exc}")


def _finish_sign_in(  # noqa: PLR0913
    mw,
    client_id: str,
    client_secret: str,
    holder: dict,
    state: str,
    verifier: str,
    redirect_uri: str,
) -> None:
    """Validate the redirect, exchange the code, and sign the account in."""
    if holder.get("error"):
        _log(f"Google returned error on redirect: {holder['error']}")
        return
    code = holder.get("code")
    if not code:
        _log("no authorization code received (redirect never arrived)")
        return
    if holder.get("state") != state:
        _log("state mismatch — ignoring redirect (possible stale/duplicate)")
        return

    # secret can come from env/JSON (_client) or the in-app "paste secret" box
    if not client_secret:
        try:
            from aqt.ante import get_google_secret

            client_secret = get_google_secret(mw.col)
        except Exception:
            client_secret = ""
    token_body = {
        "client_id": client_id,
        "code": code,
        "code_verifier": verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    if client_secret:
        token_body["client_secret"] = client_secret
    tok = _post_token(token_body)
    if tok is None:
        return
    access = tok.get("access_token")
    if not access:
        _log(f"token response had no access_token: {tok}")
        return
    info_req = urllib.request.Request(
        _USERINFO_URL, headers={"Authorization": "Bearer " + access}
    )
    info = json.loads(
        urllib.request.urlopen(info_req, timeout=30).read().decode("utf-8")
    )
    account = {
        "id": "google_" + str(info.get("sub", "")),
        "name": info.get("name") or info.get("email") or "Google user",
        "email": info.get("email", ""),
        "picture": info.get("picture", ""),
        "provider": "google",
    }
    from aqt.ante import sign_in_account

    _log(f"signing in {account['email']}")
    _run_on_main(mw, lambda: sign_in_account(mw.col, account))


def _post_token(token_body: dict) -> dict | None:
    """Exchange the auth code for tokens, logging Google's error body on failure.

    Google's *Desktop app* client type requires the client secret in the token
    exchange even with PKCE. If we get the tell-tale 'client_secret is missing'
    error and one is available in the environment, retry once with it — so the
    flow works whether the user made a Desktop client (secret needed) or a
    public one (PKCE-only)."""
    req = urllib.request.Request(
        _TOKEN_URL,
        data=urllib.parse.urlencode(token_body).encode("ascii"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        return json.loads(
            urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
        )
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8")
        except Exception:
            body = ""
        _log(f"token exchange failed: HTTP {exc.code} {body}")
        if "client_secret" in body and "client_secret" not in token_body:
            secret = os.environ.get("ANTE_GOOGLE_CLIENT_SECRET", "").strip()
            if secret:
                _log("retrying token exchange with ANTE_GOOGLE_CLIENT_SECRET")
                return _post_token({**token_body, "client_secret": secret})
            _log(
                "This is a Google 'Desktop app' client, which requires a client "
                "secret. Set ANTE_GOOGLE_CLIENT_SECRET and relaunch, or create "
                "an 'iOS' OAuth client (no secret)."
            )
        return None
    except Exception as exc:
        _log(f"token exchange error: {type(exc).__name__}: {exc}")
        return None
