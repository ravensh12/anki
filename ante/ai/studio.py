# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""The Studio — Ante's generative-media engine (Higgsfield + ElevenLabs).

Where ai/provider.py isolates the *language* model, this module isolates the
*media* models. It turns declarative scene specs into cached assets:

    still(spec)                -> parchment illustration (Higgsfield Soul)
    motion(spec, still)        -> short live-motion clip   (Higgsfield DoP)
    speech(text, persona)      -> narration audio          (ElevenLabs / OpenAI)
    talking_head(still, audio) -> speaking-avatar clip     (Higgsfield Speak v2)
    transcribe(audio)          -> text                     (ElevenLabs Scribe / Whisper)

Design contract (same rules as the card-generation AI):
  * provider-isolated — online providers are used only when keys are present
    (HF_KEY or HF_API_KEY+HF_API_SECRET; ELEVENLABS_API_KEY; OPENAI_API_KEY).
  * offline-first — a deterministic local engraver always produces a still, so
    every feature renders with AI switched off; audio/video simply absent.
  * content-addressed — every asset is keyed by the hash of its spec and cached
    forever in one directory; nothing generates twice.
  * budget-capped — a JSON ledger enforces daily/monthly generation caps
    (ANTE_STUDIO_DAILY_CAP / _MONTHLY_CAP); over budget degrades to offline.

The Higgsfield transport mirrors tools/gen_video_hf.py: submit a job-set, poll
``/v1/job-sets/{id}`` until completion. The official SDK is used when
importable (it carries auth + uploads); a raw urllib fallback covers Soul/DoP
generation without it. Endpoints/models are env-overridable (HF_*) because the
API surface moves faster than this file.

Synchronous by design; the Qt layer runs Studio calls on worker threads.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import time
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..config import CONFIG, AnteConfig

HF_BASE = os.environ.get("HF_API_BASE", "https://platform.higgsfield.ai")
HF_SOUL_ENDPOINT = os.environ.get("HF_SOUL_ENDPOINT", "/v1/text2image/soul")
HF_I2V_ENDPOINT = os.environ.get("HF_I2V_ENDPOINT", "/v1/image2video/dop")
HF_SPEAK_ENDPOINT = os.environ.get("HF_SPEAK_ENDPOINT", "/v1/speak/higgsfield")
HF_SOUL_MODEL = os.environ.get("HF_SOUL_MODEL", "soul")
HF_I2V_MODEL = os.environ.get("HF_I2V_MODEL", "dop-turbo")
# Every DoP render is pinned to one curated camera-motion preset (a locked-off
# tripod move from the Higgsfield motion library). Free-roaming AI cameras are
# what put a film rig drifting THROUGH the card table in earlier takes; a
# pinned static preset keeps the room believable. Override with
# HF_DOP_MOTION (set it empty to let the model improvise again).
HF_DOP_MOTION = os.environ.get(
    "HF_DOP_MOTION", "285f9746-ddbc-4644-b07f-7cbe2e7925ea"
)
HF_DOP_MOTION_STRENGTH = float(os.environ.get("HF_DOP_MOTION_STRENGTH", "0.7"))
HF_POLL_S = 8
HF_TIMEOUT_S = 900

ELEVEN_BASE = "https://api.elevenlabs.io/v1"

# One consistent house style for every Soul render — the whole world must look
# shot by one cinematographer in one room (consistency is half the power).
HOUSE_STYLE = (
    "Cinematic photorealistic still inside a dim members-only card den above "
    "Manhattan at night, deep green felt and dark wood, brass lamps, faint "
    "cigar haze, rain-streaked floor-to-ceiling windows with neon city bokeh, "
    "film-noir warmth, anamorphic shallow depth of field, consistent grade, "
    "no text, no lettering, no watermark"
)

# The cast. Each persona is an ElevenLabs voice + delivery settings, all
# overridable (ANTE_VOICE_<PERSONA> / ANTE_TTS_MODEL).
PERSONAS: dict[str, tuple[str, dict]] = {
    # Sahir, the dealer: a grizzled old poker-room regular — gravel in the
    # voice, forty years on the felt, unhurried and dry (Clyde)
    "dealer": (
        "2EiwWnXFnvU5JabPnv8n",
        {"stability": 0.34, "similarity_boost": 0.78, "style": 0.62, "use_speaker_boost": True},
    ),
    # the midnight-game narrator: low, slow, hypnotic (Lily)
    "night": (
        "pFZP5JQG7iQjIQuC4Bku",
        {"stability": 0.7, "similarity_boost": 0.8, "style": 0.1, "use_speaker_boost": True},
    ),
    # The Run's chronicler: measured, quietly proud (Daniel)
    "chronicler": (
        "onwK4e9ZLuTAKqWW03F9",
        {"stability": 0.6, "similarity_boost": 0.8, "style": 0.25, "use_speaker_boost": True},
    ),
}

# The same cast on the OpenAI engine (keyless-ElevenLabs fallback). Sahir
# runs on "onyx" — deep and weathered — with instructions that keep the old
# poker-room cadence; lighter voices read too young for a dealer who's spent
# decades on the felt.
OPENAI_VOICES: dict[str, str] = {
    "dealer": "onyx",
    "night": "sage",
    "chronicler": "echo",
}


@dataclass(frozen=True)
class AssetRef:
    """A cached, content-addressed media asset."""

    key: str
    kind: str  # still | motion | speech | talking_head
    filename: str  # relative to the studio cache dir
    provider: str
    created_at: float
    meta: dict

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "kind": self.kind,
            "filename": self.filename,
            "provider": self.provider,
            "created_at": self.created_at,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AssetRef":
        return cls(
            key=str(d["key"]),
            kind=str(d["kind"]),
            filename=str(d["filename"]),
            provider=str(d.get("provider", "?")),
            created_at=float(d.get("created_at", 0.0)),
            meta=dict(d.get("meta", {})),
        )


# --------------------------------------------------------------------------- #
# provider availability
# --------------------------------------------------------------------------- #


def higgsfield_available() -> bool:
    return bool(
        os.environ.get("HF_KEY")
        or (os.environ.get("HF_API_KEY") and os.environ.get("HF_API_SECRET"))
    )


def elevenlabs_available() -> bool:
    return bool(os.environ.get("ELEVENLABS_API_KEY"))


def openai_available() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


class _HFTransport:
    """Prefer the official SDK (auth + uploads); fall back to raw urllib."""

    def __init__(self) -> None:
        self._client = None
        try:  # pragma: no cover - depends on optional SDK
            from higgsfield_client import http  # type: ignore

            self._client = http.client.SyncClient()
        except Exception:
            self._client = None

    @property
    def sdk(self) -> bool:
        return self._client is not None

    def _key(self) -> str:
        key = os.environ.get("HF_KEY", "")
        if not key:
            key = f"{os.environ.get('HF_API_KEY', '')}:{os.environ.get('HF_API_SECRET', '')}"
        return key

    def request(self, method: str, path: str, payload: dict | None = None) -> dict:
        if self._client is not None:  # pragma: no cover - live API
            r = self._client._transport.request(
                method,
                self._client.base_url.rstrip("/") + path,
                json=payload,
                timeout=90,
            )
            if r.status_code != 200:
                raise RuntimeError(f"higgsfield {path} -> {r.status_code}: {r.text[:300]}")
            return r.json()
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(HF_BASE + path, data=data, method=method)
        req.add_header("Authorization", f"Key {self._key()}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read().decode("utf-8"))

    def upload(self, path: Path) -> str | None:
        """Upload a local file, returning a URL (SDK only)."""
        if self._client is None:
            return None
        try:  # pragma: no cover - live API
            return self._client.upload_file(str(path))
        except Exception:
            return None


# --------------------------------------------------------------------------- #
# the offline engraver — a deterministic felt plate for any spec
# --------------------------------------------------------------------------- #

_INK = "#e9ddbe"
_PAPER = "#0d1f17"
_ACCENTS = ("#c9a227", "#3f8f6b", "#b5533c", "#7c9ec9", "#a06fa8")


def _seed(key: str) -> int:
    return int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:8], 16)


def _wrap(text: str, width: int = 34) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines[:4]


def render_offline_plate(spec: dict, key: str) -> str:
    """A deterministic engraved SVG plate: honest, styled, zero network.

    Not a placeholder box — a real piece of the visual language (deep felt,
    hatching, a chip-ring sigil derived from the spec) so the den is beautiful
    with AI off, just quieter.
    """
    rnd = _seed(key)
    accent = _ACCENTS[rnd % len(_ACCENTS)]
    title = str(spec.get("title", ""))[:64]
    caption = str(spec.get("caption", spec.get("scene", "")))
    anchors = spec.get("anchors", [])[:5]

    # sigil ring: one glyph per anchor, angle/radius/shape from the hash
    glyphs = []
    for i, a in enumerate(anchors or [{"object": title or "plate"}]):
        h = _seed(f"{key}:{i}:{a.get('object', '')}")
        ang = (h % 360) * math.pi / 180
        r = 88 + (h >> 4) % 40
        cx = 256 + r * math.cos(ang)
        cy = 250 + r * math.sin(ang)
        size = 14 + (h >> 8) % 22
        shape = (h >> 3) % 3
        if shape == 0:
            glyphs.append(
                f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{size}" fill="none" '
                f'stroke="{_INK}" stroke-width="2.2"/>'
            )
        elif shape == 1:
            glyphs.append(
                f'<rect x="{cx - size:.0f}" y="{cy - size:.0f}" width="{size * 2}" '
                f'height="{size * 2}" fill="none" stroke="{_INK}" stroke-width="2.2" '
                f'transform="rotate({h % 45} {cx:.0f} {cy:.0f})"/>'
            )
        else:
            glyphs.append(
                f'<path d="M {cx:.0f} {cy - size:.0f} L {cx + size:.0f} {cy + size:.0f} '
                f'L {cx - size:.0f} {cy + size:.0f} Z" fill="none" stroke="{_INK}" '
                f'stroke-width="2.2"/>'
            )
    cap_lines = "".join(
        f'<tspan x="256" dy="{18 if i else 0}">{_esc(line)}</tspan>'
        for i, line in enumerate(_wrap(caption))
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
<defs>
 <pattern id="hatch" width="7" height="7" patternTransform="rotate(38)" patternUnits="userSpaceOnUse">
   <line x1="0" y1="0" x2="0" y2="7" stroke="{_INK}" stroke-width="0.55" opacity="0.24"/>
 </pattern>
 <radialGradient id="glow" cx="50%" cy="46%" r="62%">
   <stop offset="0%" stop-color="#16301f"/><stop offset="100%" stop-color="{_PAPER}"/>
 </radialGradient>
</defs>
<rect width="512" height="512" fill="url(#glow)"/>
<rect width="512" height="512" fill="url(#hatch)"/>
<rect x="18" y="18" width="476" height="476" fill="none" stroke="{_INK}" stroke-width="2.5"/>
<rect x="26" y="26" width="460" height="460" fill="none" stroke="{_INK}" stroke-width="0.8" opacity="0.55"/>
<circle cx="256" cy="250" r="132" fill="none" stroke="{accent}" stroke-width="2" opacity="0.85"/>
<circle cx="256" cy="250" r="64" fill="none" stroke="{_INK}" stroke-width="1" opacity="0.5"/>
{''.join(glyphs)}
<text x="256" y="66" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif"
 font-size="24" fill="{_INK}" font-style="italic">{_esc(title)}</text>
<text x="256" y="418" text-anchor="middle" font-family="Georgia, serif" font-size="15"
 fill="{_INK}" opacity="0.9">{cap_lines}</text>
<text x="256" y="486" text-anchor="middle" font-family="Georgia, serif" font-size="11"
 fill="{accent}" letter-spacing="3">ANTE · PLATE {rnd % 997:03d}</text>
</svg>"""


def _esc(s: str) -> str:
    return (
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _log(msg: str) -> None:
    """Studio diagnostics to stderr — generation runs minutes-long jobs and
    silent fallbacks make failures undebuggable."""
    import sys

    print(f"[studio] {msg}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
# the studio
# --------------------------------------------------------------------------- #


class Studio:
    """Content-addressed generative-media cache over Higgsfield + ElevenLabs."""

    LEDGER = "studio_ledger.json"

    def __init__(
        self,
        cache_dir: str | Path,
        cfg: AnteConfig | None = None,
        force_offline: bool = False,
        now: float | None = None,
    ) -> None:
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cfg = cfg or CONFIG
        self.force_offline = force_offline
        self._now = now
        self._hf: _HFTransport | None = None
        self._ledger = self._load_ledger()

    # ---- ledger / budget ----

    def _load_ledger(self) -> dict:
        p = self.dir / self.LEDGER
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            data = {}
        data.setdefault("assets", {})
        data.setdefault("spend", {})
        return data

    def _save_ledger(self) -> None:
        (self.dir / self.LEDGER).write_text(
            json.dumps(self._ledger, indent=1), encoding="utf-8"
        )

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(self._now or time.time()))

    def _month(self) -> str:
        return self._today()[:7]

    def budget(self) -> dict:
        spend = self._ledger["spend"]
        day = int(spend.get(self._today(), 0))
        month = int(spend.get(self._month(), 0))
        return {
            "daily_used": day,
            "daily_cap": self.cfg.studio_daily_cap,
            "monthly_used": month,
            "monthly_cap": self.cfg.studio_monthly_cap,
            "allowed": (
                day < self.cfg.studio_daily_cap and month < self.cfg.studio_monthly_cap
            ),
        }

    def _spend(self, n: int = 1) -> None:
        spend = self._ledger["spend"]
        spend[self._today()] = int(spend.get(self._today(), 0)) + n
        spend[self._month()] = int(spend.get(self._month(), 0)) + n
        self._save_ledger()

    def providers(self) -> dict:
        return {
            "higgsfield": higgsfield_available() and not self.force_offline,
            "elevenlabs": elevenlabs_available() and not self.force_offline,
            "openai": openai_available() and not self.force_offline,
            "offline_only": self.force_offline
            or not (higgsfield_available() or elevenlabs_available() or openai_available()),
        }

    # ---- cache ----

    @staticmethod
    def key_for(kind: str, spec: dict) -> str:
        blob = json.dumps({"kind": kind, "spec": spec}, sort_keys=True)
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:20]

    def lookup(self, kind: str, spec: dict) -> AssetRef | None:
        key = self.key_for(kind, spec)
        rec = self._ledger["assets"].get(key)
        if not rec:
            return None
        ref = AssetRef.from_dict(rec)
        return ref if (self.dir / ref.filename).is_file() else None

    def forget(self, kind: str, spec: dict) -> None:
        """Drop a cached asset (file + ledger entry) so the next call
        re-renders — e.g. when a keyless run cached an offline plate and a
        real provider has since become available."""
        key = self.key_for(kind, spec)
        rec = self._ledger["assets"].pop(key, None)
        if rec:
            (self.dir / str(rec.get("filename", ""))).unlink(missing_ok=True)
            self._save_ledger()

    def _store(self, ref: AssetRef) -> AssetRef:
        self._ledger["assets"][ref.key] = ref.as_dict()
        self._save_ledger()
        return ref

    def path_of(self, ref: AssetRef) -> Path:
        return self.dir / ref.filename

    def assets(self, kind: str | None = None) -> list[AssetRef]:
        out = [AssetRef.from_dict(d) for d in self._ledger["assets"].values()]
        if kind:
            out = [a for a in out if a.kind == kind]
        return sorted(out, key=lambda a: a.created_at)

    # ---- higgsfield plumbing ----

    def _transport(self) -> _HFTransport:
        if self._hf is None:
            self._hf = _HFTransport()
        return self._hf

    def _hf_job(self, endpoint: str, params: dict) -> str:
        """Submit -> poll -> return the result URL (mirrors the old film tool).

        Generation jobs run for minutes; a single transient poll failure must
        not kill the job, so poll errors are logged and retried until the
        deadline. Only an explicit terminal status (or the deadline) fails."""
        t = self._transport()
        job = t.request("POST", endpoint, {"params": params})
        jid = job["id"]
        deadline = time.time() + HF_TIMEOUT_S
        while time.time() < deadline:  # pragma: no cover - live API
            time.sleep(HF_POLL_S)
            try:
                body = t.request("GET", f"/v1/job-sets/{jid}")
            except Exception as exc:
                _log(f"poll hiccup on {jid} (retrying): {exc}")
                continue
            jobs = body.get("jobs", [])
            status = jobs[0].get("status") if jobs else "missing"
            if status == "completed":
                results = jobs[0].get("results") or {}
                url = (results.get("raw") or results.get("min") or {}).get("url")
                if not url:
                    raise RuntimeError(f"job {jid} completed without a URL")
                return url
            if status in ("failed", "nsfw", "canceled"):
                raise RuntimeError(f"job {jid} ended: {status}")
        raise RuntimeError(f"job {jid} timed out")

    def _download(self, url: str, filename: str) -> None:  # pragma: no cover - live
        # the result CDN rejects the default Python-urllib user agent with 403
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (ante)"})
        with urllib.request.urlopen(req, timeout=300) as r:
            (self.dir / filename).write_bytes(r.read())

    # ---- public: stills ----

    def still(self, spec: dict) -> AssetRef:
        """A parchment illustration for the spec. Soul online; engraver offline.

        The spec should carry ``prompt`` (scene description; the house style is
        appended) plus ``title``/``caption``/``anchors`` for the offline plate.
        """
        cached = self.lookup("still", spec)
        if cached:
            return cached
        key = self.key_for("still", spec)

        if higgsfield_available() and not self.force_offline and self.budget()["allowed"]:
            try:  # pragma: no cover - live API
                prompt = f"{spec.get('prompt', spec.get('scene', ''))}. {HOUSE_STYLE}"
                url = self._hf_job(
                    HF_SOUL_ENDPOINT,
                    {
                        "model": HF_SOUL_MODEL,
                        "prompt": prompt,
                        "width_and_height": os.environ.get("HF_SOUL_SIZE", "1536x1536"),
                        "quality": os.environ.get("HF_SOUL_QUALITY", "1080p"),
                        "batch_size": 1,
                    },
                )
                filename = f"still_{key}.jpg"
                self._download(url, filename)
                self._spend()
                return self._store(
                    AssetRef(key, "still", filename, "higgsfield-soul",
                             self._now or time.time(), {"url": url})
                )
            except Exception:
                pass  # fall through to the engraver — never block the feature

        filename = f"still_{key}.svg"
        (self.dir / filename).write_text(render_offline_plate(spec, key), encoding="utf-8")
        return self._store(
            AssetRef(key, "still", filename, "offline-engraver",
                     self._now or time.time(), {})
        )

    # ---- public: motion ----

    def motion(self, spec: dict, still: AssetRef) -> AssetRef | None:
        """Animate a still into a short clip (DoP). None when offline — the UI
        falls back to the still with local motion (Ken Burns), same as the film."""
        # the pinned camera preset is part of the spec: re-pinning the camera
        # invalidates stale takes instead of replaying the drifting ones
        mspec = {"motion": spec.get("motion", ""), "of": still.key}
        if HF_DOP_MOTION:
            mspec["camera"] = HF_DOP_MOTION
        cached = self.lookup("motion", mspec)
        if cached:
            return cached
        if (
            self.force_offline
            or not higgsfield_available()
            or not self.budget()["allowed"]
        ):
            return None
        # Always re-upload the local still: the URL stored at generation time is
        # a signed CDN link that expires, and a DoP job fed an expired URL dies
        # minutes later with a server-side fetch failure.
        image_url = self._transport().upload(self.path_of(still)) or still.meta.get(
            "url"
        )
        if not image_url:
            return None
        try:  # pragma: no cover - live API
            key = self.key_for("motion", mspec)
            params: dict = {
                "model": HF_I2V_MODEL,
                "prompt": spec.get("motion", "subtle living movement, camera almost still"),
                "input_images": [{"type": "image_url", "image_url": image_url}],
            }
            if HF_DOP_MOTION:
                params["motions"] = [
                    {"id": HF_DOP_MOTION, "strength": HF_DOP_MOTION_STRENGTH}
                ]
            url = self._hf_job(HF_I2V_ENDPOINT, params)
            filename = f"motion_{key}.mp4"
            self._download(url, filename)
            self._spend()
            return self._store(
                AssetRef(key, "motion", filename, "higgsfield-dop",
                         self._now or time.time(), {"url": url, "still": still.key})
            )
        except Exception as exc:
            _log(f"motion failed for still {still.key}: {type(exc).__name__}: {exc}")
            return None

    # ---- public: speech ----

    def speech(self, text: str, persona: str = "night") -> AssetRef | None:
        """Narration audio for a line. ElevenLabs preferred; OpenAI fallback;
        None offline (features must render without audio)."""
        # Resolve the actor the way the render will: engine + that engine's
        # voice are part of the spec, so a cached take can never impersonate
        # a different casting. (An OpenAI fallback once cached itself under
        # the ElevenLabs voice id — half the film then spoke in the wrong
        # actor until the mixed takes were regenerated.)
        if elevenlabs_available() and not self.force_offline:
            engine = "elevenlabs"
            voice = PERSONAS.get(persona, PERSONAS["night"])[0]
        else:
            engine = "openai"
            voice = OPENAI_VOICES.get(persona, "ash")
        voice = os.environ.get(f"ANTE_VOICE_{persona.upper()}", voice)
        spec: dict = {"text": text, "persona": persona, "voice": voice, "engine": engine}
        # delivery settings are part of the spec so a recast invalidates stale
        # takes instead of replaying the old warmth from cache
        if engine == "elevenlabs":
            _, settings = PERSONAS.get(persona, PERSONAS["night"])
            spec["voice_settings"] = settings
        elif persona == "dealer":
            spec["casting"] = "poker-v1"
        cached = self.lookup("speech", spec)
        if cached:
            return cached
        if self.force_offline or not (elevenlabs_available() or openai_available()):
            # No way to render a fresh take — replay a legacy casting if one
            # is in the can (pre-"engine" cache entries: a warmed demo must
            # not go silent because today's keys differ). Otherwise, quiet.
            legacy_voice = PERSONAS.get(persona, PERSONAS["night"])[0]
            legacy_voice = os.environ.get(f"ANTE_VOICE_{persona.upper()}", legacy_voice)
            return self.lookup(
                "speech", {"text": text, "persona": persona, "voice": legacy_voice}
            )
        key = self.key_for("speech", spec)
        filename = f"speech_{key}.mp3"
        try:
            if elevenlabs_available():
                self._eleven_tts(text, persona, self.dir / filename)
                provider = "elevenlabs"
            elif openai_available():
                filename = f"speech_{key}.wav"
                self._openai_tts(text, persona, self.dir / filename)
                provider = "openai-tts"
            else:
                return None
        except Exception:
            return None
        self._spend()
        return self._store(
            AssetRef(key, "speech", filename, provider,
                     self._now or time.time(), {"persona": persona, "text": text})
        )

    def _eleven_tts(self, text: str, persona: str, out: Path) -> None:
        voice, settings = PERSONAS.get(persona, PERSONAS["night"])
        voice = os.environ.get(f"ANTE_VOICE_{persona.upper()}", voice)
        model = os.environ.get("ANTE_TTS_MODEL", "eleven_multilingual_v2")
        body = json.dumps(
            {"text": text, "model_id": model, "voice_settings": settings}
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{ELEVEN_BASE}/text-to-speech/{voice}?output_format=mp3_44100_128",
            data=body,
            method="POST",
        )
        req.add_header("xi-api-key", os.environ["ELEVENLABS_API_KEY"])
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=120) as r:
            out.write_bytes(r.read())

    def _openai_tts(self, text: str, persona: str, out: Path) -> None:
        instructions = {
            "dealer": "You're a 65-year-old poker dealer who's worked the "
            "same high-stakes room for forty years. Gravelly, unhurried, dry "
            "wit — like you're talking to a regular at your table. Low voice "
            "with a little smoke in it, never rushed, every line sounds like "
            "you've said it a thousand times before the cards even hit the felt.",
            "night": "Very slow, low, and soft — a wind-down narration right "
            "before sleep. Almost a whisper.",
            "chronicler": "Measured documentary narration, quietly proud.",
        }.get(persona, "Calm and warm.")
        body = json.dumps(
            {
                "model": os.environ.get("ANTE_TTS_MODEL", "gpt-4o-mini-tts"),
                "voice": os.environ.get(
                    f"ANTE_VOICE_{persona.upper()}", OPENAI_VOICES.get(persona, "ash")
                ),
                "input": text,
                "instructions": instructions,
                "response_format": "wav",
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            "https://api.openai.com/v1/audio/speech", data=body, method="POST"
        )
        req.add_header("Authorization", f"Bearer {os.environ['OPENAI_API_KEY']}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=120) as r:
            out.write_bytes(r.read())

    # ---- public: talking head ----

    def talking_head(self, still: AssetRef, speech: AssetRef, prompt: str = "") -> AssetRef | None:
        """A speaking-avatar clip (Speak v2: portrait + WAV). Requires the SDK
        for uploads and WAV audio; returns None otherwise (UI plays audio over
        the still instead — same scene, cheaper theatre)."""
        spec = {"of": still.key, "audio": speech.key, "prompt": prompt}
        cached = self.lookup("talking_head", spec)
        if cached:
            return cached
        if (
            self.force_offline
            or not higgsfield_available()
            or not self.budget()["allowed"]
        ):
            return None
        # fresh uploads only — stored result URLs are signed and expire
        image_url = self._transport().upload(self.path_of(still))
        wav = self._as_wav(self.path_of(speech))
        audio_url = self._transport().upload(wav) if wav else None
        if not (image_url and audio_url):
            return None
        try:  # pragma: no cover - live API
            key = self.key_for("talking_head", spec)
            url = self._hf_job(
                HF_SPEAK_ENDPOINT,
                {
                    "input_image": {"type": "image_url", "image_url": image_url},
                    "input_audio": {"type": "audio_url", "audio_url": audio_url},
                    "prompt": prompt or "speaking naturally, steady eye contact",
                    "quality": os.environ.get("HF_SPEAK_QUALITY", "mid"),
                    "duration": os.environ.get("HF_SPEAK_DURATION", "short"),
                },
            )
            filename = f"speak_{key}.mp4"
            self._download(url, filename)
            self._spend(2)  # talking video is the expensive tier
            return self._store(
                AssetRef(key, "talking_head", filename, "higgsfield-speak",
                         self._now or time.time(), {"url": url})
            )
        except Exception:
            return None

    @staticmethod
    def _as_wav(path: Path) -> Path | None:
        """Speak v2 accepts WAV only; convert mp3 via afconvert (macOS)."""
        if path.suffix == ".wav":
            return path
        out = path.with_suffix(".wav")
        if out.is_file():
            return out
        try:
            subprocess.run(
                ["afconvert", "-f", "WAVE", "-d", "LEI16@22050", str(path), str(out)],
                check=True,
                capture_output=True,
            )
            return out
        except Exception:
            return None

    # ---- public: transcription ----

    def transcribe(self, audio: bytes, mime: str = "audio/webm") -> dict | None:
        """Speech -> text for the Viva. ElevenLabs Scribe preferred, Whisper
        fallback; None offline (the Viva accepts typed answers instead)."""
        if self.force_offline or not audio:
            return None
        try:
            if elevenlabs_available():
                text = self._eleven_stt(audio, mime)
                return {"text": text, "provider": "elevenlabs-scribe"}
            if openai_available():
                text = self._openai_stt(audio, mime)
                return {"text": text, "provider": "openai-whisper"}
        except Exception:
            return None
        return None

    def _eleven_stt(self, audio: bytes, mime: str) -> str:
        fields = {"model_id": "scribe_v1"}
        body, ctype = _multipart(fields, "file", f"viva.{_ext(mime)}", mime, audio)
        req = urllib.request.Request(f"{ELEVEN_BASE}/speech-to-text", data=body, method="POST")
        req.add_header("xi-api-key", os.environ["ELEVENLABS_API_KEY"])
        req.add_header("Content-Type", ctype)
        with urllib.request.urlopen(req, timeout=120) as r:
            return str(json.loads(r.read().decode("utf-8")).get("text", "")).strip()

    def _openai_stt(self, audio: bytes, mime: str) -> str:
        fields = {"model": "whisper-1"}
        body, ctype = _multipart(fields, "file", f"viva.{_ext(mime)}", mime, audio)
        req = urllib.request.Request(
            "https://api.openai.com/v1/audio/transcriptions", data=body, method="POST"
        )
        req.add_header("Authorization", f"Bearer {os.environ['OPENAI_API_KEY']}")
        req.add_header("Content-Type", ctype)
        with urllib.request.urlopen(req, timeout=120) as r:
            return str(json.loads(r.read().decode("utf-8")).get("text", "")).strip()

    # ---- status for the UI ----

    def status(self) -> dict:
        return {
            "providers": self.providers(),
            "budget": self.budget(),
            "assets": {
                k: sum(1 for a in self._ledger["assets"].values() if a["kind"] == k)
                for k in ("still", "motion", "speech", "talking_head")
            },
        }


def _ext(mime: str) -> str:
    return {
        "audio/webm": "webm",
        "audio/mp4": "mp4",
        "audio/mpeg": "mp3",
        "audio/wav": "wav",
        "audio/x-wav": "wav",
        "audio/ogg": "ogg",
    }.get(mime, "webm")


def _multipart(
    fields: dict[str, str], file_field: str, filename: str, mime: str, data: bytes
) -> tuple[bytes, str]:
    """Minimal multipart/form-data encoder (stdlib only)."""
    boundary = f"----ante{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            (
                f"--{boundary}\r\nContent-Disposition: form-data; "
                f'name="{name}"\r\n\r\n{value}\r\n'
            ).encode("utf-8")
        )
    parts.append(
        (
            f"--{boundary}\r\nContent-Disposition: form-data; "
            f'name="{file_field}"; filename="{filename}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(data)
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


_WORD = re.compile(r"[A-Za-z0-9']+")


def spoken_lines(text: str, max_words: int = 42) -> list[str]:
    """Split copy into TTS-sized lines on sentence boundaries."""
    sents = re.split(r"(?<=[.!?])\s+", text.strip())
    out: list[str] = []
    cur = ""
    for s in sents:
        if not s:
            continue
        if len(_WORD.findall(f"{cur} {s}")) > max_words and cur:
            out.append(cur.strip())
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur.strip())
    return out
