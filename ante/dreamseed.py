# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Dream Seed — the Last Light consolidation reel.

Material reviewed shortly before sleep is preferentially consolidated overnight
(Diekelmann & Born 2010; Walker) — Ante already protects that window with
the Last Light bookend. Dream Seed gives the window a payload: a 60–90 second
wind-down reel of today's hardest retrievals, each replayed as its Palace scene
(or an engraved plate) with one slow spoken line restating the fact.

Selection is evidence, not vibes: today's misses ranked by the response-time
classification (a struggled miss outranks a careless slip — analytics.py), then
effortful-but-correct answers. Five scenes, then a closing card. The reel is
sequenced in-page from cached assets — zero marginal generation cost, fully
offline once the assets exist.

Pure logic: this module selects and scripts; the Qt layer resolves Palace
assets and pre-generates narration through the Studio.
"""

from __future__ import annotations

from collections.abc import Mapping

from .analytics import CARELESS, EFFORTFUL, STRUGGLED, classify_response
from .config import CONFIG, AnteConfig
from .outline import load_outline

# selection priority by response class (higher = earlier in the reel)
_PRIORITY = {STRUGGLED: 3.0, CARELESS: 2.0, EFFORTFUL: 1.0}

CLOSING_LINE = "That's the last hand. Lights down — your brain plays the night shift."


def build_reel(
    events_today: list[Mapping],
    palace_by_card: Mapping[int, Mapping] | None = None,
    now_hour: int = 21,
    cfg: AnteConfig | None = None,
) -> dict:
    """Script tonight's reel from today's genuine study events.

    ``events_today`` dicts carry: card_id (or item id), topic, front, back,
    correct (bool), elapsed_ms. ``palace_by_card`` maps card_id -> palace
    record (from palace.index_by_card).
    """
    cfg = cfg or CONFIG
    palace_by_card = palace_by_card or {}

    scored: list[tuple[float, Mapping]] = []
    seen: set = set()
    for e in events_today:
        key = e.get("card_id") or e.get("id") or e.get("front")
        if key in seen:
            continue
        seen.add(key)
        kind = classify_response(bool(e.get("correct")), e.get("elapsed_ms"), cfg)
        pri = _PRIORITY.get(kind, 0.0)
        if not e.get("correct"):
            pri += 0.5  # any miss outranks any hit at equal class
        if pri > 0:
            scored.append((pri, e))

    scored.sort(key=lambda t: -t[0])
    picks = [e for _, e in scored[: cfg.dreamseed_scenes]]

    if not picks:
        return {
            "available": False,
            "reason": "no study events today — the reel is cut from real work",
        }

    # a pre-sleep artifact: the reel plays in the evening window, when the
    # consolidation it feeds is actually about to happen
    if now_hour < 19:
        return {
            "available": False,
            "reason": (
                f"{len(picks)} hard retrievals are queued for tonight's reel — "
                "it plays at the midnight game"
            ),
        }

    outline = load_outline()
    scenes = []
    for i, e in enumerate(picks):
        cid = e.get("card_id")
        rec = palace_by_card.get(int(cid)) if cid is not None else None
        topic = str(e.get("topic", ""))
        t = outline.topic(topic)
        line = _narration_line(str(e.get("front", "")), str(e.get("back", "")))
        scenes.append(
            {
                "n": i + 1,
                "topic": topic,
                "topic_name": t.name if t else topic.rsplit("::", 1)[-1],
                "front": e.get("front", ""),
                "back": e.get("back", ""),
                "line": line,
                # media: the palace scene when one exists; else the UI renders
                # an engraved caption plate locally
                "still": rec.get("still") if rec else None,
                "motion": rec.get("motion") if rec else None,
                "caption": rec.get("caption") if rec else None,
            }
        )

    return {
        "available": True,
        "title": f"The last hand — {len(scenes)} cards, replayed",
        "persona": "night",
        "scenes": scenes,
        "closing": CLOSING_LINE,
        "seconds_per_scene": 14,
    }


def _narration_line(front: str, back: str) -> str:
    """One slow spoken line: the retrieval restated as a settled fact."""
    f = front.rstrip("?.! ").strip()
    b = back.strip().rstrip(".")
    if not f:
        return f"{b}. Let it settle."
    if not b:
        return f"{f}. Hold that one."
    return f"{f}? {b}. Let it settle."


def narration_texts(reel: Mapping) -> list[str]:
    """Every line the Studio should pre-voice for tonight (persona: night)."""
    if not reel.get("available"):
        return []
    lines = [str(s["line"]) for s in reel.get("scenes", [])]
    lines.append(str(reel.get("closing", CLOSING_LINE)))
    return lines
