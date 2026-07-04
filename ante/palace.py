# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""The Palace — generated mnemonic scenes for exactly the cards you keep dropping.

SketchyMedical proved that visual mnemonics work for med students — but it sells
the same film to everyone. Ante already knows *which specific cards* a
student's memory keeps rejecting (lapses + low FSRS retrievability), so the
Palace commissions a bespoke scene per leech: every fact on the card becomes one
concrete object in a single hand-inked tableau (dual coding, Paivio 1986;
keyword-mnemonic, Atkinson 1975; method of loci), rendered by the Studio in one
consistent house style and animated into a short living loop.

Honesty gate: a wrong mnemonic is worse than none, so every anchor fact the
scene teaches must be verifiably supported by the card's own text — anchors
that fail verification are dropped, and if none survive the scene falls back to
the deterministic offline spec (which is built *from* the card text and thus
trivially faithful).

Pure logic + Studio calls; unit-testable offline. The Qt layer supplies leech
candidates from the revlog and persists the palace index in collection config.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

from .ai.baseline import tokenize
from .ai.studio import Studio
from .config import CONFIG, AnteConfig
from .outline import load_outline

# How much of an anchor fact's salient vocabulary must come from the card
# itself for the fact to be teachable. Below this, the anchor is dropped.
ANCHOR_SUPPORT = 0.5

# The offline bestiary: concrete, visually distinct objects the deterministic
# spec-builder hash-picks from, one per key term. Deliberately odd — bizarre
# imagery is more memorable (the bizarreness effect).
BESTIARY = (
    "a brass diving helmet",
    "a coiled emerald serpent",
    "a stack of vermilion books",
    "a wrought-iron street lamp",
    "a chess knight carved from bone",
    "an hourglass full of seawater",
    "a mechanical songbird",
    "a locksmith's ring of keys",
    "a marble bust wearing spectacles",
    "an umbrella struck by lightning",
    "a beehive made of glass",
    "a pocketwatch with three hands",
    "a candle burning at both ends",
    "a rowboat resting on a rooftop",
    "a telescope aimed at the floor",
    "a typewriter growing ivy",
)


@dataclass(frozen=True)
class Leech:
    """A card the student's memory keeps rejecting."""

    card_id: int
    front: str
    back: str
    topic: str
    lapses: int
    retrievability: float

    @property
    def severity(self) -> float:
        """Sort key: how badly this card needs an intervention."""
        return self.lapses * (1.0 - self.retrievability)


def pick_leeches(
    cards: list[dict],
    existing_card_ids: set[int] | None = None,
    cfg: AnteConfig | None = None,
    limit: int | None = None,
) -> list[Leech]:
    """Select palace candidates: enough lapses, weakest first, no re-commissions.

    ``cards`` are dicts from the Qt layer: card_id, front, back, topic, lapses,
    retrievability (0..1; missing treated as weak).
    """
    cfg = cfg or CONFIG
    existing = existing_card_ids or set()
    out: list[Leech] = []
    for c in cards:
        cid = int(c.get("card_id", 0))
        if cid in existing:
            continue
        lapses = int(c.get("lapses", 0))
        if lapses < cfg.palace_min_lapses:
            continue
        front = _plain(str(c.get("front", "")))
        back = _plain(str(c.get("back", "")))
        if not front or not back:
            continue
        out.append(
            Leech(
                card_id=cid,
                front=front,
                back=back,
                topic=str(c.get("topic", "")),
                lapses=lapses,
                retrievability=float(c.get("retrievability", 0.0) or 0.0),
            )
        )
    out.sort(key=lambda l: l.severity, reverse=True)
    if limit is not None:
        out = out[:limit]
    return out


_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _plain(html: str) -> str:
    return _WS.sub(" ", _TAGS.sub(" ", html)).strip()


# --------------------------------------------------------------------------- #
# scene specs
# --------------------------------------------------------------------------- #

_SPEC_SYSTEM = (
    "You are the scene designer for a memory palace (SketchyMedical style). "
    "Given one flashcard, design ONE vivid tableau that encodes its facts. "
    "Every fact must map to exactly one concrete object or character in the "
    "scene. Use ONLY facts stated on the card; invent imagery, never facts. "
    "Return STRICT JSON: {\"title\": <=5 words, \"scene\": one flowing visual "
    "sentence <=40 words describing the tableau, \"motion\": <=12 words of "
    "subtle motion for a 5s loop, \"caption\": one spoken line <=20 words that "
    "walks the viewer through the mnemonic, \"anchors\": [{\"fact\": a fact "
    "verbatim-supported by the card, \"object\": the concrete object encoding "
    "it}] with 1-4 anchors}. The image must contain no text or lettering."
)


def build_scene_spec(leech: Leech, provider=None) -> dict:
    """Design the mnemonic scene: Claude when present, deterministic otherwise.

    The returned spec always passes ``verify_spec`` — unsupported anchors are
    dropped and an empty result falls back to the offline builder.
    """
    spec: dict | None = None
    if provider is not None and hasattr(provider, "complete"):
        try:
            raw = provider.complete(
                _SPEC_SYSTEM,
                f"FRONT: {leech.front}\nBACK: {leech.back}",
                max_tokens=500,
            )
            spec = _parse_json_obj(raw)
        except Exception:
            spec = None
    if spec:
        spec = verify_spec(spec, leech)
    if not spec or not spec.get("anchors"):
        spec = offline_scene_spec(leech)
    spec["card_id"] = leech.card_id
    spec["topic"] = leech.topic
    return spec


def offline_scene_spec(leech: Leech) -> dict:
    """A faithful-by-construction spec built from the card text alone."""
    terms = _key_terms(leech.back, n=3)
    anchors = []
    for t in terms:
        obj = BESTIARY[_stable_hash(f"{leech.card_id}:{t}") % len(BESTIARY)]
        anchors.append({"fact": t, "object": obj})
    objects = ", ".join(a["object"] for a in anchors) or "a single lit lantern"
    topic_name = _topic_name(leech.topic)
    scene = (
        f"In a lamplit corner of the den devoted to {topic_name}, arranged "
        f"across the green felt of a card table: {objects}."
    )
    return {
        "title": topic_name[:40],
        "scene": scene,
        "prompt": scene,
        "motion": "candle flame flickers, dust motes drift, shadows breathe",
        "caption": _shorten(f"{leech.front} — {leech.back}", 110),
        "anchors": anchors,
    }


def verify_spec(spec: dict, leech: Leech) -> dict:
    """Drop any anchor whose fact is not supported by the card's own text.

    Support = at least ANCHOR_SUPPORT of the fact's salient tokens appear in
    the card (front + back). The scene may be fantastical; the facts may not.
    """
    card_tokens = set(tokenize(f"{leech.front} {leech.back}"))
    kept = []
    for a in spec.get("anchors", []):
        fact = str(a.get("fact", "")).strip()
        obj = str(a.get("object", "")).strip()
        if not fact or not obj:
            continue
        sal = [t for t in tokenize(fact) if len(t) >= 3]
        if not sal:
            continue
        hit = sum(1 for t in sal if t in card_tokens) / len(sal)
        if hit >= ANCHOR_SUPPORT:
            kept.append({"fact": fact, "object": obj})
    out = dict(spec)
    out["anchors"] = kept
    out.setdefault("prompt", out.get("scene", ""))
    return out


def _parse_json_obj(raw: str) -> dict | None:
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _key_terms(text: str, n: int = 3) -> list[str]:
    toks = [t for t in tokenize(text) if len(t) >= 4]
    seen: list[str] = []
    for t in sorted(set(toks), key=lambda t: (-len(t), t)):
        seen.append(t)
        if len(seen) >= n:
            break
    return seen


def _stable_hash(s: str) -> int:
    h = 2166136261
    for ch in s:
        h = (h ^ ord(ch)) * 16777619 & 0xFFFFFFFF
    return h


def _topic_name(tag: str) -> str:
    t = load_outline().topic(tag)
    if t:
        return t.name
    return (tag.rsplit("::", 1)[-1] or "the topic").replace("_", " ")


def _shorten(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


# --------------------------------------------------------------------------- #
# commissioning
# --------------------------------------------------------------------------- #


def commission(leech: Leech, studio: Studio, provider=None) -> dict:
    """Design + render one palace scene. Returns the persistable record."""
    spec = build_scene_spec(leech, provider)
    still = studio.still(
        {
            "prompt": spec.get("prompt", spec.get("scene", "")),
            "title": spec.get("title", ""),
            "caption": spec.get("caption", ""),
            "anchors": spec.get("anchors", []),
        }
    )
    motion = studio.motion({"motion": spec.get("motion", "")}, still)
    return {
        "card_id": leech.card_id,
        "topic": leech.topic,
        "title": spec.get("title", ""),
        "scene": spec.get("scene", ""),
        "caption": spec.get("caption", ""),
        "anchors": spec.get("anchors", []),
        "still": still.filename,
        "motion": motion.filename if motion else None,
        "provider": still.provider,
        "created_at": time.time(),
    }


def index_by_card(records: list[dict]) -> dict[int, dict]:
    return {int(r["card_id"]): r for r in records if r.get("card_id") is not None}


def by_topic(records: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in records:
        out.setdefault(str(r.get("topic", "")), []).append(r)
    return out


def gallery_payload(records: list[dict], pending: int = 0) -> dict:
    """The Archive's shelf: scenes grouped by topic, newest first."""
    groups = []
    for topic, recs in sorted(by_topic(records).items()):
        recs = sorted(recs, key=lambda r: r.get("created_at", 0), reverse=True)
        groups.append(
            {
                "topic": topic,
                "topic_name": _topic_name(topic),
                "scenes": recs,
            }
        )
    return {
        "count": len(records),
        "pending": pending,
        "groups": groups,
    }
