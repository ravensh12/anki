# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Loader for the AAMC MCAT content outline + prerequisite/unlock graph (PRD 4).

Topics are encoded as note tags ``mcat::<section>::<id>``; the CARS skills
section uses the bare ``mcat::cars`` bucket. Section weights mirror the Rust
``section_weight`` so the Python and engine views of exam value agree. Each topic
also carries an in-section ``exam_weight`` and a ``prereqs`` list that defines the
unlock graph used by the mastery-gating engine (ante/mastery.py).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DATA_PATH = Path(__file__).with_name("data") / "mcat_outline.json"


@dataclass(frozen=True)
class Topic:
    # full tag, e.g. "mcat::bio_biochem::enzymes"
    tag: str
    # short id within section, e.g. "enzymes"
    id: str
    name: str
    section_id: str
    # share of emphasis within the section (normalized to ~1.0 per section)
    exam_weight: float
    # prerequisite topic tags that must be mastered before this unlocks
    prereqs: tuple[str, ...] = ()


@dataclass(frozen=True)
class Section:
    id: str
    code: str
    name: str
    weight: float
    topic_objs: tuple[Topic, ...] = field(default_factory=tuple)

    @property
    def topics(self) -> tuple[str, ...]:
        """Full topic tags (back-compat with the coverage map)."""
        return tuple(t.tag for t in self.topic_objs)


@dataclass(frozen=True)
class Outline:
    exam: str
    topic_prefix: str
    scale: dict
    sections: tuple[Section, ...]

    def all_topics(self) -> list[str]:
        return [t for s in self.sections for t in s.topics]

    def all_topic_objs(self) -> list[Topic]:
        return [t for s in self.sections for t in s.topic_objs]

    def topic(self, tag: str) -> Topic | None:
        for t in self.all_topic_objs():
            if t.tag == tag:
                return t
        return None

    def section_of(self, topic: str) -> Section | None:
        for s in self.sections:
            if topic in s.topics:
                return s
        body = (
            topic[len(self.topic_prefix) :]
            if topic.startswith(self.topic_prefix)
            else ""
        )
        section_id = body.split("::", 1)[0] if body else ""
        return next((s for s in self.sections if s.id == section_id), None)

    def topic_weight(self, topic: str) -> float:
        s = self.section_of(topic)
        return s.weight if s else 1.0


def _full_tag(prefix: str, section_id: str, category: str) -> str:
    # CARS (and any single-bucket section) uses the bare section tag.
    if category == section_id:
        return f"{prefix}{section_id}"
    return f"{prefix}{section_id}::{category}"


@lru_cache(maxsize=4)
def load_outline(path: str | None = None) -> Outline:
    p = Path(path) if path else DATA_PATH
    raw = json.loads(p.read_text(encoding="utf-8"))
    prefix = raw["topic_prefix"]

    sections = []
    for s in raw["sections"]:
        topic_objs = []
        for t in s["topics"]:
            # support both the rich object schema and a bare string (legacy)
            if isinstance(t, str):
                tid, name, weight, prereqs = t, t, 0.0, []
            else:
                tid = t["id"]
                name = t.get("name", tid)
                weight = float(t.get("exam_weight", 0.0))
                prereqs = t.get("prereqs", [])
            topic_objs.append(
                Topic(
                    tag=_full_tag(prefix, s["id"], tid),
                    id=tid,
                    name=name,
                    section_id=s["id"],
                    exam_weight=weight,
                    prereqs=tuple(_full_tag(prefix, s["id"], p) for p in prereqs),
                )
            )
        sections.append(
            Section(
                id=s["id"],
                code=s.get("code", s["id"]),
                name=s["name"],
                weight=float(s["weight"]),
                topic_objs=tuple(topic_objs),
            )
        )

    return Outline(
        exam=raw["exam"],
        topic_prefix=prefix,
        scale=raw["scale"],
        sections=tuple(sections),
    )
