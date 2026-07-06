# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Generate a topic-tagged MCAT seed deck (no AI required).

By default the deck is built purely from the curated, premade Q/A in
``ante/data/seed_cards.json`` (real high-yield cards for every AAMC-outline
topic) — the same content the app self-seeds, so there is no synthetic filler.
Passing ``--per-topic N`` treats N as a *minimum* per topic and pads any topic
below it with synthetic but well-formed cards, purely to grow the deck to a
benchmarking size.

Usage (from the repo root, with the built pyenv):

    # premade curated deck (default): every real card, no padding
    PYTHONPATH=out/pylib out/pyenv/bin/python -m ante.tools.generate_seed_deck \\
        --out out/mcat_seed.anki2 --apkg out/mcat_seed.apkg

For a large benchmark deck, pass --per-topic (e.g. 720 across the topics).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from anki.collection import Collection
from anki.decks import DeckId
from ante.outline import load_outline

SEED_CARDS_PATH = Path(__file__).resolve().parent.parent / "data" / "seed_cards.json"


def _load_seed_cards() -> dict[str, list[list[str]]]:
    raw = json.loads(SEED_CARDS_PATH.read_text(encoding="utf-8"))
    return raw["cards"]


def _topic_label(topic: str, prefix: str) -> str:
    body = topic[len(prefix) :] if topic.startswith(prefix) else topic
    return body.replace("::", " / ").replace("_", " ")


def build_deck(out_path: str, per_topic: int, apkg: str | None = None) -> int:
    out = Path(out_path)
    if out.exists():
        out.unlink()
    out.parent.mkdir(parents=True, exist_ok=True)

    outline = load_outline()
    seed_cards = _load_seed_cards()

    col = Collection(str(out))
    try:
        basic = col.models.by_name("Basic")
        assert basic is not None, "Basic notetype missing"
        deck_id = DeckId(col.decks.id("MCAT"))

        total = 0
        for topic in outline.all_topics():
            label = _topic_label(topic, outline.topic_prefix)
            curated = seed_cards.get(topic, [])
            cards: list[tuple[str, str]] = [(q, a) for q, a in curated]
            # per_topic is a MINIMUM: with the default of 0 the deck is pure
            # curated content (no synthetic filler); raise it to grow the deck.
            i = 0
            while len(cards) < per_topic:
                i += 1
                cards.append(
                    (
                        f"[{label}] practice item #{i}: state a key fact.",
                        f"Synthetic placeholder fact #{i} for {label}.",
                    )
                )
            for front, back in cards:
                note = col.new_note(basic)
                note["Front"] = front
                note["Back"] = back
                note.tags = [topic]
                col.add_note(note, deck_id)
                total += 1

        if apkg:
            _export_apkg(col, apkg)
    finally:
        col.close()

    return total


def _export_apkg(col: Collection, apkg_path: str) -> None:
    from anki.collection import ExportAnkiPackageOptions

    Path(apkg_path).parent.mkdir(parents=True, exist_ok=True)
    options = ExportAnkiPackageOptions(
        with_scheduling=False,
        with_deck_configs=True,
        with_media=False,
        legacy=False,
    )
    col.export_anki_package(out_path=apkg_path, options=options, limit=None)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an MCAT seed deck.")
    parser.add_argument(
        "--out", default="out/mcat_seed.anki2", help="output .anki2 collection"
    )
    parser.add_argument(
        "--per-topic",
        type=int,
        default=0,
        help="minimum cards per topic; 0 (default) means curated only, no padding",
    )
    parser.add_argument("--apkg", default=None, help="also export an .apkg here")
    args = parser.parse_args()

    total = build_deck(args.out, args.per_topic, args.apkg)
    print(f"Generated {total} cards across the MCAT outline -> {args.out}")
    if args.apkg:
        print(f"Exported package -> {args.apkg}")
    print(f"Collection size: {os.path.getsize(args.out)} bytes")


if __name__ == "__main__":
    main()
