# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""The done-for-you study plan + calendar (Principle 2: remove the decision).

Recalibration (``recalibrate.py``) produces the *dials* — daily minutes, the
FSRS retention ramp, the shape of the day. This module turns those dials into an
actual **plan a student can just follow**: a day-by-day calendar from today to
the exam, split into learning-science phases, with each day's concrete
prescription (which topic to focus, how many flashcards, how many quiz/open
questions, how many minutes) already decided.

The whole point (Principle 2): the motivated student shouldn't have to choose *what*
to do — only *do it*. Free practice is always available on top, but the plan
answers "what should I do today?" so consistency stops leaking at the decision.

Phases across the runway (Build -> Bridge -> Sharpen) encode the arc the
evidence supports: build mastery topic-by-topic first (Bloom), shift to
application/transfer in the middle, then consolidate and tighten retention as
the exam nears (Cepeda: the optimal gap shrinks with the retention interval).

Pure logic; unit-tested without Anki.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .config import CONFIG, AnteConfig
from .fulllength import fl_offsets
from .mastery import MasteryStatus, TopicMastery
from .outline import Outline, load_outline

STUDY_DAYS_PER_WEEK = 6  # one rest day a week (matches recalibrate)
# The standing rest night is a real weekday (Python weekday: Saturday), so it
# lands on a fixed date that actually arrives. Keying it to "offset % 7" kept
# the rest night six-days-from-now on every rebuild — a break that never came.
REST_WEEKDAY = 5
# realistic per-item times for the DISPLAYED daily dose (thinking + flip/answer
# + reading the explanation), so a 2-hour day prescribes counts a student can
# actually do: ~100 cards and ~25 questions, not 85 questions.
FLASHCARD_SECONDS = 22.0
QUIZ_SECONDS = 150.0  # a transfer MCQ + reading why: ~2.5 min
OPEN_SECONDS = 300.0  # an open-ended item: ~5 min
# light/normal/heavy day rhythm — real plans breathe; keyed off days-remaining
# so the pattern shifts as the exam nears (and demo day-skip shows variety)
DAY_LOAD = (1.0, 0.8, 1.1, 0.9, 1.15, 0.85)

# phase -> (start_fraction, minute-mix as flashcards/quiz/open of the day)
# Phases key off DAYS-REMAINING (absolute), not a fraction of the re-planned
# runway — so "today's phase" advances Build -> Bridge -> Sharpen as the exam
# nears (and day-skip/time-travel visibly moves through them).
SHARPEN_WITHIN_DAYS = 14  # the final two weeks
BRIDGE_WITHIN_DAYS = 45  # the six weeks before that
# flashcards / quiz / open-ended split of each day. Application (quiz+open) is
# weighted heavily and rises toward the exam, because mastery is gated on
# APPLICATION, not flashcard recall (Principle 3). Flashcards only warm up
# retrieval; the quiz is what proves a topic.
PHASE_MIX = {
    "build": (0.50, 0.34, 0.16),
    "bridge": (0.36, 0.44, 0.20),
    "sharpen": (0.24, 0.52, 0.24),
}
PHASE_NAME = {"build": "Build", "bridge": "Bridge", "sharpen": "Sharpen"}
PHASE_BLURB = {
    "build": "Master topics one at a time — recall first, then prove you can apply them.",
    "bridge": "Shift to application: interleave quizzes and open-ended, clear corrective topics.",
    "sharpen": "Consolidate everything, tighten retention, and rehearse under exam conditions.",
}
PHASE_ORDER = ["build", "bridge", "sharpen"]


def _phase_id_for_days_out(days_out: int) -> str:
    if days_out <= SHARPEN_WITHIN_DAYS:
        return "sharpen"
    if days_out <= BRIDGE_WITHIN_DAYS:
        return "bridge"
    return "build"


_SECTION_ABBR = {
    "bio_biochem": "B/B",
    "chem_phys": "C/P",
    "psych_soc": "P/S",
    "cars": "CARS",
}

# how each day-window is presented in the paced session (icon is a UI hint)
WINDOW_META = {
    "morning": {
        "label": "Morning Game",
        "at": "08:00",
        "icon": "sun",
        "cue": "before coffee \u2014 cold recall beats warm rereading",
    },
    "during the day": {
        "label": "Midday Hold",
        "at": "14:00",
        "icon": "mid",
        "cue": "a few minutes so the House can't claw it back",
    },
    "night": {
        "label": "Midnight Game",
        "at": "21:00",
        "icon": "moon",
        "cue": "one light hand your brain banks overnight",
    },
}


@dataclass(frozen=True)
class DayPlan:
    offset: int
    iso_date: str | None
    weekday: str
    is_today: bool
    is_rest: bool
    is_exam: bool
    phase: str
    section: str | None
    section_abbr: str | None
    focus_tag: str | None
    focus_label: str | None
    minutes: int
    flashcards: int
    quiz: int
    open: int

    def as_dict(self) -> dict:
        return {
            "offset": self.offset,
            "date": self.iso_date,
            "weekday": self.weekday,
            "is_today": self.is_today,
            "is_rest": self.is_rest,
            "is_exam": self.is_exam,
            "phase": self.phase,
            "section": self.section,
            "section_abbr": self.section_abbr,
            "focus_tag": self.focus_tag,
            "focus_label": self.focus_label,
            "minutes": self.minutes,
            "flashcards": self.flashcards,
            "quiz": self.quiz,
            "open": self.open,
        }


def _leaf(tag: str) -> str:
    body = tag.split("::")
    return body[-1].replace("_", " ").title() if body else tag


def _phase_for(
    offset: int, days: int
) -> tuple[str, str, tuple[float, float, float], str]:
    # days_out = how far the exam still is on the calendar day at ``offset``
    days_out = max(0, days - offset)
    pid = _phase_id_for_days_out(days_out)
    return pid, PHASE_NAME[pid], PHASE_MIX[pid], PHASE_BLURB[pid]


def _counts(
    minutes: int, mix: tuple[float, float, float], sec_per_card: float
) -> tuple[int, int, int]:
    # use realistic display times so a 2-hour day reads like ~150 cards, not 500
    flash = round(minutes * mix[0] * 60 / FLASHCARD_SECONDS)
    quiz = round(minutes * mix[1] * 60 / QUIZ_SECONDS)
    opn = round(minutes * mix[2] * 60 / OPEN_SECONDS)
    return int(flash), int(quiz), int(opn)


def _ranked_unmastered(mastery: dict[str, TopicMastery]) -> list[TopicMastery]:
    cands = [
        m
        for m in mastery.values()
        if m.status != MasteryStatus.MASTERED and m.cards_total > 0
    ]
    # points-at-stake: exam weight x weakness; corrective topics floated up a bit
    cands.sort(
        key=lambda m: m.exam_weight * m.weakness
        + (0.15 if m.status == MasteryStatus.CORRECTIVE else 0.0),
        reverse=True,
    )
    return cands


def build_study_plan(
    mastery: dict[str, TopicMastery],
    *,
    days_remaining: int | None,
    daily_minutes: int,
    exam_date: str | None = None,
    sec_per_card: float = 8.0,
    baseline_total: int | None = None,
    target_score: int | None = None,
    now: date | None = None,
    outline: Outline | None = None,
    cfg: AnteConfig | None = None,
    calendar_days: int = 21,
    slot_plan: list[dict] | None = None,
) -> dict:
    """Assemble the plan. Returns a dict with today's prescription, a calendar
    window, the phase timeline, and milestones. ``available`` is False (with a
    helpful message) before there's an exam date or any studyable topic."""
    cfg = cfg or CONFIG
    outline = outline or load_outline()
    today = now or date.today()
    ranked = _ranked_unmastered(mastery)

    if days_remaining is None:
        return {
            "available": False,
            "message": "Set your exam date and Ante lays out every day for you.",
        }
    days = max(1, days_remaining)

    study_days_total = max(1, round(days * STUDY_DAYS_PER_WEEK / 7))
    n_topics = len(ranked)

    # Each study day cycles through the ranked weak spots, keyed to the day's
    # absolute DATE. Two properties matter (the old spread-in-blocks assignment
    # had neither): consecutive study days CHANGE topic — interleaving beats
    # blocking, and with two topics left a block plan pinned one of them for
    # five straight weeks — and a calendar day keeps its topic across daily
    # rebuilds instead of the whole plan drifting forward each morning.
    # Counting study days (rest nights excluded) keeps the rotation strict
    # across the weekend gap.
    def topic_for_date(d: date) -> TopicMastery | None:
        if n_topics == 0:
            return None
        o = d.toordinal()
        rest_nights = (o + 6 - REST_WEEKDAY) // 7  # rest days up to and incl. d
        return ranked[(o - rest_nights) % n_topics]

    def is_rest(d: date) -> bool:
        return d.weekday() == REST_WEEKDAY

    # --- today's prescription ---
    phase_id, phase_name, mix, phase_blurb = _phase_for(0, days)
    focus = topic_for_date(today)
    flash, quiz, opn = _counts(daily_minutes, mix, sec_per_card)
    if focus is not None:
        headline = f"Today's focus: {focus.name}"
        rationale = (
            f"A high-value weak spot in {_SECTION_ABBR.get(focus.section_id, focus.section_id)}. "
            "The plan rotates your weak spots day to day — interleaving sections "
            "beats grinding one, and the leakiest topics come around most often."
        )
    else:
        headline = "You've cleared the board"
        rationale = "Every topic with cards is mastered — keep them warm with review."

    # split the day into paced windows (First Light / Midday / Last Light) so the
    # session hands the student one slice at a time instead of the whole day at
    # once — the plan spreads within the day, not just across days.
    slots = _day_slots(slot_plan, mix, sec_per_card)

    today_plan = {
        "phase": phase_id,
        "phase_name": phase_name,
        "focus_tag": focus.tag if focus else None,
        "focus_label": focus.name if focus else None,
        "section": focus.section_id if focus else None,
        "section_abbr": _SECTION_ABBR.get(focus.section_id) if focus else None,
        "minutes": int(daily_minutes),
        "flashcards": flash,
        "quiz": quiz,
        "open": opn,
        "headline": headline,
        "rationale": rationale,
        "prescription": _prescription_text(flash, quiz, opn, daily_minutes, focus),
        "slots": slots,
    }

    # --- the calendar window (today .. today+calendar_days) ---
    window = min(calendar_days, days)
    calendar: list[DayPlan] = []
    for offset in range(0, window + 1):
        d = today + timedelta(days=offset)
        rest = is_rest(d)
        exam = offset == days
        p_id, _p_name, p_mix, _b = _phase_for(offset, days)
        if exam:
            calendar.append(
                DayPlan(
                    offset,
                    d.isoformat(),
                    d.strftime("%a"),
                    offset == 0,
                    False,
                    True,
                    p_id,
                    None,
                    None,
                    None,
                    "EXAM",
                    0,
                    0,
                    0,
                    0,
                )
            )
            continue
        if rest:
            calendar.append(
                DayPlan(
                    offset,
                    d.isoformat(),
                    d.strftime("%a"),
                    offset == 0,
                    True,
                    False,
                    p_id,
                    None,
                    None,
                    None,
                    "Rest",
                    0,
                    0,
                    0,
                    0,
                )
            )
            continue
        topic = topic_for_date(d)
        # the plan breathes: light/normal/heavy days (anchored to the real date,
        # so a given calendar day keeps its load) — today shows the full dose
        load = 1.0 if offset == 0 else DAY_LOAD[(d.toordinal()) % len(DAY_LOAD)]
        day_minutes = max(20, round(daily_minutes * load))
        f, q, o = _counts(day_minutes, p_mix, sec_per_card)
        calendar.append(
            DayPlan(
                offset=offset,
                iso_date=d.isoformat(),
                weekday=d.strftime("%a"),
                is_today=offset == 0,
                is_rest=False,
                is_exam=False,
                phase=p_id,
                section=topic.section_id if topic else None,
                section_abbr=_SECTION_ABBR.get(topic.section_id) if topic else None,
                focus_tag=topic.tag if topic else None,
                focus_label=topic.name if topic else None,
                minutes=day_minutes,
                flashcards=f,
                quiz=q,
                open=o,
            )
        )

    # --- phase timeline (offsets across the runway, by days-remaining bands) ---
    # build: today .. (D-45), bridge: (D-45) .. (D-14), sharpen: final 14 days
    bounds = {
        "build": (0, max(0, days - BRIDGE_WITHIN_DAYS)),
        "bridge": (
            max(0, days - BRIDGE_WITHIN_DAYS),
            max(0, days - SHARPEN_WITHIN_DAYS),
        ),
        "sharpen": (max(0, days - SHARPEN_WITHIN_DAYS), days),
    }
    timeline = []
    for pid in PHASE_ORDER:
        start, end = bounds[pid]
        if end <= start and pid != _phase_id_for_days_out(days):
            continue  # phase doesn't exist on a short runway
        timeline.append(
            {
                "id": pid,
                "name": PHASE_NAME[pid],
                "start_offset": start,
                "end_offset": end,
                "days": max(0, end - start),
                "blurb": PHASE_BLURB[pid],
            }
        )

    # --- milestones: section mastery ETAs + practice tests + exam ---
    milestones = _milestones(ranked, study_days_total, days, today, exam_date, outline)

    return {
        "available": True,
        "days_remaining": days,
        "exam_date": exam_date,
        "daily_minutes": int(daily_minutes),
        "topics_remaining": n_topics,
        "today": today_plan,
        "calendar": [dp.as_dict() for dp in calendar],
        "timeline": timeline,
        "milestones": milestones,
        "baseline_total": baseline_total,
        "target_score": target_score,
        "adopt_note": (
            "This is your plan — you don't pick what to study, you just do the "
            "top of the stack. Free practice is always there when you want more."
        ),
    }


def _day_slots(
    slot_plan: list[dict] | None,
    mix: tuple[float, float, float],
    sec_per_card: float,
) -> list[dict]:
    """Break today's dose into paced windows from the recalibration slot plan,
    each with its own bounded card/question count. Empty when there's no slot
    plan (the session then serves the whole day at once)."""
    out: list[dict] = []
    for slot in slot_plan or []:
        minutes = int(slot.get("minutes", 0) or 0)
        if minutes <= 0:
            continue
        window = slot.get("window", "")
        meta = WINDOW_META.get(
            window,
            {"label": window.title() or "Session", "at": "", "icon": "mid", "cue": ""},
        )
        f, q, o = _counts(minutes, mix, sec_per_card)
        out.append(
            {
                "key": window or meta["label"],
                "window": window,
                "label": meta["label"],
                "at": meta["at"],
                "icon": meta["icon"],
                "cue": meta["cue"],
                "role": slot.get("role", ""),
                "role_detail": slot.get("role_detail", ""),
                "minutes": minutes,
                "flashcards": f,
                "quiz": q,
                "open": o,
            }
        )
    return out


def _prescription_text(
    flash: int, quiz: int, opn: int, minutes: int, focus: TopicMastery | None
) -> str:
    bits = []
    if flash:
        bits.append(f"{flash} flashcards")
    if quiz:
        bits.append(f"{quiz} quiz questions")
    if opn:
        bits.append(f"{opn} open-ended")
    load = ", ".join(bits) if bits else "a light review"
    where = f" on {focus.name}" if focus else ""
    return f"{load}{where} \u2014 about {minutes} min, split across your day."


def _study_index_to_offset(si: int) -> int:
    """Map a 0-based study-day index to a calendar offset, inserting a rest
    every 7th calendar day."""
    offset = 0
    seen = 0
    while True:
        if offset % 7 != REST_WEEKDAY:
            if seen == si:
                return offset
            seen += 1
        offset += 1


def checkpoint_offsets(days_remaining: int) -> list[int]:
    """Calendar offsets (days from today) of the quiz re-check checkpoints,
    every ~14 days ANCHORED TO THE EXAM (like the full-lengths). Anchoring to
    today would re-derive "14 days from now" on every rebuild, so the
    checkpoint drifts forward daily and never comes due; exam-anchored offsets
    shrink as real days pass, so each checkpoint lands on a fixed date that
    actually arrives (offset 0 = tonight)."""
    days = max(1, int(days_remaining))
    return sorted(off for off in range(days - 14, -1, -14))


def marked_nights(days_remaining: int | None, today: date | None = None) -> list[dict]:
    """The dated test milestones — quiz checkpoints plus the two full-lengths —
    sorted soonest-first. Pure date math off the exam runway (no mastery data),
    so reminders and the den can ask "what test lands when?" without building
    the whole plan. Empty without an exam date."""
    if days_remaining is None:
        return []
    days = max(1, int(days_remaining))
    today = today or date.today()
    out: list[dict] = []
    for off in checkpoint_offsets(days):
        out.append(
            {
                "offset": off,
                "date": (today + timedelta(days=off)).isoformat(),
                "kind": "practice_test",
                "label": "Practice checkpoint",
                "detail": "Re-take the section quizzes to re-measure your baseline.",
            }
        )
    for n, fl_off in fl_offsets(days).items():
        out.append(
            {
                "offset": fl_off,
                "date": (today + timedelta(days=fl_off)).isoformat(),
                "kind": "full_length",
                "test_no": n,
                "label": f"Full-length practice test {n}",
                "detail": (
                    "Every section, timed, in one sitting \u2014 sets your honest baseline."
                    if n == 1
                    else "The dress rehearsal \u2014 timed, scored, then taper into the exam."
                ),
            }
        )
    # soonest first; when a checkpoint and a full-length share a night, the
    # full-length is the headline event
    out.sort(key=lambda m: (m["offset"], 0 if m["kind"] == "full_length" else 1))
    return out


def _milestones(
    ranked: list[TopicMastery],
    study_days_total: int,
    days: int,
    today: date,
    exam_date: str | None,
    outline: Outline,
) -> list[dict]:
    out: list[dict] = []
    n = len(ranked)

    # section mastery ETA: the calendar offset of the last study day that lands
    # on each section (topics are spread evenly across the runway)
    if n:
        last_offset_by_section: dict[str, int] = {}
        for si in range(study_days_total):
            idx = min(n - 1, si * n // study_days_total)
            sec = ranked[idx].section_id
            off = min(days, _study_index_to_offset(si))
            last_offset_by_section[sec] = max(last_offset_by_section.get(sec, 0), off)
        for sec, off in sorted(last_offset_by_section.items(), key=lambda kv: kv[1]):
            name = next((s.name for s in outline.sections if s.id == sec), sec)
            out.append(
                {
                    "offset": off,
                    "date": (today + timedelta(days=off)).isoformat(),
                    "kind": "section_mastery",
                    "label": f"{_SECTION_ABBR.get(sec, sec)} on track to mastered",
                    "detail": f"Target: {name} locked in by here.",
                }
            )

    # the marked nights: quiz checkpoints every ~14 days + the two full-lengths,
    # all exam-anchored so each lands on a fixed date that actually arrives
    out.extend(marked_nights(days, today))

    out.append(
        {
            "offset": days,
            "date": exam_date or (today + timedelta(days=days)).isoformat(),
            "kind": "exam",
            "label": "MCAT",
            "detail": "Exam day — trust the reps you've banked.",
        }
    )
    out.sort(key=lambda m: m["offset"])
    return out
