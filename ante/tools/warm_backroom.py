# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Pre-render Sahir's Back Room voice for chosen topics (demo insurance).

The heads-up examination speaks three kinds of lines, all deterministic when
the offline probe path is used: the opening invitation, the template probe per
missed rubric point, and the pass/fail verdicts. The Studio caches speech by
content, so rendering those exact lines ahead of time means the exam plays
with zero on-stage latency — and keeps working if the venue wifi dies
mid-demo (the cache is just files on disk).

Usage (close Anki first; the collection lock is exclusive):

    PYTHONPATH=out/pylib:. out/pyenv/bin/python -m ante.tools.warm_backroom \
        --collection ~/Library/"Application Support"/Anki2/User\\ 1/collection.anki2 \
        --topics mcat::bio_biochem::enzymes,mcat::chem_phys::thermodynamics

Needs a TTS key (ELEVENLABS_API_KEY or OPENAI_API_KEY). With HF keys present,
`--clips` additionally pre-renders the verdict stills and the passed-verdict
talking-head clip. Everything is content-addressed: re-running is free.

The planning half (`plan_topic_lines`) is pure and unit-tested
(ante/tests/test_warm_backroom.py); only `main` touches a collection.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ante.openended import OpenItem, load_open_items
from ante.viva import (
    VERDICT_SPECS,
    failed_line,
    opening_line,
    passed_line,
    template_probe,
)


def default_topics(limit: int = 3) -> list[str]:
    """First distinct topics of the open-ended bank — the same ones the demo
    tour suggests in the Back Room."""
    seen: list[str] = []
    for it in load_open_items():
        if it.topic not in seen:
            seen.append(it.topic)
        if len(seen) >= limit:
            break
    return seen


def topic_display_name(topic: str) -> str:
    from ante.outline import load_outline

    t = load_outline().topic(topic)
    return t.name if t else topic.rsplit("::", 1)[-1].replace("_", " ")


def plan_topic_lines(
    topic: str, items: tuple[OpenItem, ...], name: str | None = None
) -> list[str]:
    """Every line Sahir can say for this topic on the deterministic path:
    opening, one probe per rubric point (+ the generic probe), the passed
    verdict, and the failed verdicts for every reachable ``missing[:2]``
    (rubric-ordered singles and pairs, plus the empty-miss fallback)."""
    name = name or topic_display_name(topic)
    lines: list[str] = [opening_line(name), template_probe(""), passed_line(name)]
    for item in items:
        if item.topic != topic:
            continue
        points = list(item.rubric_points)
        for p in points:
            lines.append(template_probe(p))
            lines.append(failed_line([p]))
        for i in range(len(points)):
            for j in range(i + 1, len(points)):
                lines.append(failed_line([points[i], points[j]]))
    lines.append(failed_line([]))
    # preserve order, drop duplicates (shared rubric points across items)
    out: list[str] = []
    for line in lines:
        if line not in out:
            out.append(line)
    return out


def studio_dir_for(collection_path: Path) -> Path:
    """The per-account Studio cache dir the app uses (mirrors
    qt/aqt/ante_studio.studio_dir without importing Qt)."""
    from anki.collection import Collection

    col = Collection(str(collection_path))
    try:
        auth = col.get_config("ante_auth", {}) or {}
        current = auth.get("current") if isinstance(auth, dict) else None
        acct = current if isinstance(current, str) and current else "guest"
        media_dir = Path(col.media.dir())
    finally:
        col.close()
    base = media_dir / "_ante_studio" / acct
    base.mkdir(parents=True, exist_ok=True)
    return base


def warm(studio, topics: list[str], clips: bool = False) -> dict:
    """Render (or cache-hit) every line for every topic. Returns counts."""
    items = load_open_items()
    rendered = 0
    cached = 0
    skipped = 0
    for topic in topics:
        name = topic_display_name(topic)
        for line in plan_topic_lines(topic, items, name):
            spec = {"text": line, "persona": "dealer"}
            hit = studio.lookup("speech", {**spec, "voice": studio_voice(studio)})
            ref = studio.speech(line, persona="dealer")
            if ref is None:
                skipped += 1
            elif hit:
                cached += 1
            else:
                rendered += 1
        if clips:
            for status, spec in VERDICT_SPECS.items():
                still = studio.still(spec)
                if status == "passed" and still:
                    speech = studio.speech(passed_line(name), persona="dealer")
                    if speech:
                        studio.talking_head(
                            still, speech, prompt=spec.get("motion", "")
                        )
    return {"rendered": rendered, "cached": cached, "skipped": skipped}


def studio_voice(studio) -> str:
    import os

    from ante.ai.studio import PERSONAS

    return os.environ.get("ANTE_VOICE_DEALER", PERSONAS["dealer"][0])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", required=True, help="path to collection.anki2")
    parser.add_argument(
        "--topics",
        default="",
        help="comma-separated topic tags (default: the Back Room's first suggestions)",
    )
    parser.add_argument(
        "--clips",
        action="store_true",
        help="also render verdict stills + the passed-verdict talking head (needs HF keys)",
    )
    args = parser.parse_args(argv)

    from ante.ai.studio import Studio
    from ante.config import CONFIG

    topics = [
        t.strip() for t in args.topics.split(",") if t.strip()
    ] or default_topics()
    studio = Studio(studio_dir_for(Path(args.collection)), cfg=CONFIG)
    providers = studio.providers()
    if not (providers.get("elevenlabs") or providers.get("openai")):
        print(
            "No TTS provider available (set ELEVENLABS_API_KEY or OPENAI_API_KEY) — "
            "nothing to warm; the Back Room still runs, typed and silent."
        )
        return 1
    counts = warm(studio, topics, clips=args.clips)
    print(
        f"warmed {len(topics)} topic(s): {counts['rendered']} rendered, "
        f"{counts['cached']} already cached, {counts['skipped']} skipped"
    )
    for t in topics:
        print(f"  · {t}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
