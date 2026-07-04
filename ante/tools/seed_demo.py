# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Seed a DEMO collection so the Ante home shows populated, realistic data.

Builds the MCAT deck and simulates a believable study history: a gradient of
mastery across sections (bio strong, chem/phys mid, psych/soc weaker, CARS
barely), by writing FSRS memory state onto cards and inserting a week of review
log entries. Everything here is clearly a demo seed; it is not fabricated
readiness — it's simulated *study history* that the real engine then measures.

Usage:
    PYTHONPATH=out/pylib:. out/pyenv/bin/python -m ante.tools.seed_demo \\
        --out "out/ante-demo-base/User 1/collection.anki2"
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

from anki.collection import Collection
from anki.decks import DeckId
from ante.outline import load_outline

# per-section study tier: (fraction of topics studied, stability days, days since
# last review, performance accuracy of simulated answers)
TIERS = {
    "bio_biochem": (1.00, 300.0, 3, 0.92),  # strong -> mastered
    "chem_phys": (0.85, 30.0, 8, 0.74),  # mid -> active
    "psych_soc": (0.55, 12.0, 12, 0.60),  # weaker -> active/corrective
    "cars": (0.20, 6.0, 16, 0.50),  # barely touched
}
CARDS_PER_TOPIC = 6
DIFFICULTY = 5.0


def build(out_path: str, seed: int = 7) -> dict:
    rng = random.Random(seed)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()

    col = Collection(str(out))
    try:
        basic = col.models.by_name("Basic")
        assert basic is not None
        deck_id = DeckId(col.decks.id("MCAT"))
        outline = load_outline()
        today = col.sched.today
        now = int(time.time())

        revlog_rows: list[tuple] = []
        n_cards = 0
        n_studied = 0

        for topic in outline.all_topic_objs():
            frac, stability, elapsed_days, acc = TIERS.get(
                topic.section_id, (0.3, 10.0, 14, 0.55)
            )
            for i in range(CARDS_PER_TOPIC):
                note = col.new_note(basic)
                note["Front"] = f"{topic.name}: key fact #{i + 1}?"
                note["Back"] = f"Answer #{i + 1} for {topic.name}."
                note.tags = [topic.tag]
                col.add_note(note, deck_id)
                n_cards += 1

                studied = rng.random() < frac
                if not studied:
                    continue
                n_studied += 1
                card = note.cards()[0]
                # jitter so a section isn't perfectly uniform
                s = max(1.0, stability * rng.uniform(0.6, 1.3))
                elapsed = max(0, int(elapsed_days * rng.uniform(0.5, 1.4)))
                lrt = now - elapsed * 86400
                ivl = int(s)
                card.type = 2  # review
                card.queue = 2
                card.ivl = ivl
                card.due = today + rng.randint(0, max(1, ivl // 3))
                card.reps = rng.randint(2, 8)
                card.factor = 2500
                data = {"s": round(s, 2), "d": DIFFICULTY, "lrt": lrt}
                col.db.execute(
                    "update cards set type=?, queue=?, ivl=?, due=?, reps=?, factor=?, data=? where id=?",
                    card.type,
                    card.queue,
                    card.ivl,
                    card.due,
                    card.reps,
                    card.factor,
                    json.dumps(data),
                    card.id,
                )
                # a few review-log entries per studied card, over the last 7 days
                for _ in range(rng.randint(1, 3)):
                    day_ago = rng.randint(0, 6)
                    ts = (now - day_ago * 86400 - rng.randint(0, 80000)) * 1000
                    ease = 3 if rng.random() < acc else 1
                    revlog_rows.append(
                        (
                            ts,
                            card.id,
                            -1,
                            ease,
                            ivl,
                            ivl,
                            2500,
                            rng.randint(2000, 15000),
                            1,
                        )
                    )

        col.db.executemany(
            "insert or ignore into revlog "
            "(id, cid, usn, ease, ivl, lastIvl, factor, time, type) "
            "values (?,?,?,?,?,?,?,?,?)",
            revlog_rows,
        )

        # Seed application evidence (quiz + open-ended) + a taken diagnostic +
        # a profile, so the demo dashboard shows a LIVE readiness reading and a
        # populated Atlas instead of abstaining. These go in the legacy
        # (non-account) config keys, which migrate into the first signed-in
        # account automatically (qt/aqt/ante.py::_migrate_legacy_into_current).
        seeded = _seed_application_evidence(col, rng, now)

        col.decks.set_current(deck_id)
        col.save()
        n_reviews = col.db.scalar("select count() from revlog") or 0
        return {
            "cards": n_cards,
            "studied": n_studied,
            "reviews": int(n_reviews),
            **seeded,
        }
    finally:
        col.close()


def _seed_application_evidence(col: Collection, rng: random.Random, now: int) -> dict:
    """Simulate answered application items (MCQ + open-ended) at each section's
    tier accuracy, plus a completed Baseline Diagnostic and an onboarded profile
    with rewards on, so demo mode presents a fully alive instrument."""
    from datetime import date, timedelta

    from ante.diagnostic import build_diagnostic
    from ante.openended import load_open_items
    from ante.performance_items import load_items

    def section_of(topic: str) -> str:
        body = topic[len("mcat::") :] if topic.startswith("mcat::") else topic
        return body.split("::", 1)[0]

    perf: dict[str, list] = {}
    for it in load_items():
        acc = TIERS.get(section_of(it.topic), (0.3, 10, 14, 0.55))[3]
        # only answer a believable fraction of the bank
        if rng.random() > 0.75:
            continue
        correct = rng.random() < acc
        choice = it.correct_index if correct else (it.correct_index + 1) % 4
        # confidence loosely tracks accuracy, with some overconfidence noise
        conf = round(min(0.95, max(0.2, acc + rng.uniform(-0.15, 0.2))), 2)
        ts = now - rng.randint(0, 6) * 86400
        perf[it.id] = [[choice, ts, conf, rng.randint(4000, 16000)]]

    opens: dict[str, list] = {}
    for it in load_open_items():
        acc = TIERS.get(section_of(it.topic), (0.3, 10, 14, 0.55))[3]
        if rng.random() > 0.55:
            continue
        score = round(min(1.0, max(0.0, acc + rng.uniform(-0.2, 0.2))), 2)
        conf = round(min(0.95, max(0.2, acc + rng.uniform(-0.1, 0.2))), 2)
        ts = now - rng.randint(0, 6) * 86400
        opens[it.id] = [[score, ts, conf, rng.randint(8000, 30000)]]

    col.set_config("ante_perf_responses", perf)
    col.set_config("ante_open_responses", opens)

    form = build_diagnostic()
    col.set_config(
        "ante_diagnostic",
        {"taken_at": now - 6 * 86400, "skipped": False, "item_ids": form.item_ids},
    )

    exam = (date.today() + timedelta(days=68)).isoformat()
    col.set_config(
        "ante_profile",
        {
            "exam_date": exam,
            "target_score": 515,
            "daily_minutes": 90,
            "chronotype": "lark",
            "reminders_enabled": True,
            "background_reminders": False,
            "rewards_opt_in": True,
            "onboarded": True,
        },
    )
    col.set_config("ante_exam_date", exam)
    col.set_config("ante_target_score", 515)
    return {"quiz_answers": len(perf), "open_answers": len(opens)}


def main() -> None:
    ap = argparse.ArgumentParser(description="Seed a Ante demo collection.")
    ap.add_argument("--out", default="out/ante-demo-base/User 1/collection.anki2")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    stats = build(args.out, args.seed)
    print(
        f"Seeded demo: {stats['cards']} cards, {stats['studied']} studied, "
        f"{stats['reviews']} reviews, {stats.get('quiz_answers', 0)} quiz + "
        f"{stats.get('open_answers', 0)} open-ended answers, diagnostic taken "
        f"-> {args.out}"
    )


if __name__ == "__main__":
    main()
