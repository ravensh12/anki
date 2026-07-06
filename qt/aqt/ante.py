# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Qt-side bridge for the Ante dashboard.

Turns the GetTopicMastery RPC + collection stats into the dashboard payload
(ante.app.build_dashboard), serves the page over mediasrv, and opens it as a
native app window inside Anki. Kept thin: all real logic lives in the importable,
unit-tested ``ante`` package.
"""

from __future__ import annotations

import random
import secrets
import sys
import time
from datetime import datetime
from pathlib import Path

from anki.collection import Collection

# Per-boot access token for the den's HTTP endpoints. The in-app page embeds
# it (dashboard_body) and mediasrv requires it on ante requests, so an
# arbitrary local process can't read per-account study data off the localhost
# port. Dev mode and Bearer-key callers are exempt (see mediasrv).
ANTE_TOKEN = secrets.token_urlsafe(24)


def _ensure_ante_importable() -> None:
    if "ante" in sys.modules:
        return
    # Packaged app: ante ships in app_packages / alongside aqt -> importable.
    try:
        import ante  # noqa: F401

        return
    except ImportError:
        pass
    # Dev build: add the repo root (which contains ante/) to sys.path.
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "ante" / "__init__.py").exists():
            if str(parent) not in sys.path:
                sys.path.insert(0, str(parent))
            return


def build_dashboard_payload(col: Collection, budget_minutes: int = 75) -> dict:
    _ensure_ante_importable()
    from ante.app import build_dashboard
    from ante.config import CONFIG

    # Demo mode: a fully-populated, time-travellable instrument on synthetic data
    # (never touches the collection). Active while the demo state is on.
    demo = get_demo_state(col)
    if demo.get("enabled"):
        _ensure_ante_importable()
        from ante.demo import build_demo_dashboard

        payload = build_demo_dashboard(day=int(demo.get("day", 12)), flags=demo)
        payload["auth"] = build_auth_payload(col)
        payload["notifications"] = notification_previews(col)
        # the den uses the real cinematic plates when they exist, in demo too
        payload["world_assets"] = world_assets_present()
        # demo play is throwaway — never resume the real account's games there
        payload["game_state"] = {}
        return payload

    # Account gate: when nobody is signed in, return only the auth state so the
    # web app shows the login page instead of a (per-account) dashboard.
    auth = build_auth_payload(col)
    if not auth["signed_in"]:
        return {
            "auth": auth,
            "signed_out": True,
            "world_assets": world_assets_present(),
        }

    # Query at the strength threshold so mastered_cards == cards_at_strength
    # (FSRS retrievability >= R_THRESHOLD), which the mastery engine expects.
    resp = col._backend.get_topic_mastery(
        search="", topic_prefix="", mastery_threshold=CONFIG.r_threshold
    )
    topics = [
        {
            "topic": t.topic,
            "weight": t.weight,
            "total_cards": t.total_cards,
            "studied_cards": t.studied_cards,
            "mastered_cards": t.mastered_cards,
            "average_recall": t.average_recall,
            "coverage": t.coverage,
        }
        for t in resp.topics
    ]

    today = col.sched.today
    due_count = (
        col.db.scalar(
            "select count() from cards where queue in (1,2,3) and due<=?", today
        )
        or 0
    )
    new_count = col.db.scalar("select count() from cards where queue=0") or 0
    n_reviews = col.db.scalar("select count() from revlog") or 0

    # Performance = accuracy on APPLICATION/transfer items (not recall). This is
    # the reviewer's core point: mastery/readiness must reflect whether you can
    # use a fact on a new question, the way the MCAT tests.
    topic_performance = _topic_application_performance(col)

    # distinct days studied in the last 7 (forgiving consistency signal)
    cutoff = col.sched.day_cutoff
    week_start_ms = (cutoff - 7 * 86400) * 1000
    active_days = (
        col.db.scalar(
            "select count(distinct cast((id/1000 - ?)/86400 as int)) "
            "from revlog where id >= ?",
            cutoff,
            week_start_ms,
        )
        or 0
    )

    genuine_by_day, today_ordinal = _genuine_reviews_by_day(col)
    newly_mastered = _newly_mastered_count(col, resp, topic_performance)
    profile = get_profile(col)
    prof_dict = profile.as_dict()
    # pre-onboarding, let the live budget query drive the daily plan
    if budget_minutes and not profile.onboarded:
        prof_dict["daily_minutes"] = int(budget_minutes)
    timing_events = _card_timing_events(col) + _quiz_timing_events(col)

    # The generative Studio layer: the Palace index, Viva history, today's
    # events for the Dream Seed reel, and provider/budget status for the city.
    from aqt.ante_studio import (
        events_today,
        get_viva_log,
        palace_by_topic,
        palace_index,
        studio_status,
    )

    p_index = palace_index(col)

    payload = build_dashboard(
        topics,
        due_count=int(due_count),
        new_count=int(new_count),
        n_reviews=int(n_reviews),
        budget_minutes=budget_minutes,
        active_days=int(active_days),
        topic_performance=topic_performance or None,
        genuine_by_day=genuine_by_day,
        today_ordinal=today_ordinal,
        newly_mastered_count=newly_mastered,
        quiz_responses=get_perf_responses(col),
        open_responses=get_open_responses(col),
        flash_confidence=get_flash_confidence(col),
        hour_outcomes=_hour_outcomes(col),
        timing_events=timing_events,
        profile=prof_dict,
        now_hour=datetime.now().hour,
        hour_counts_today=_hour_counts_today(col),
        diagnostic=get_diagnostic(col),
        palace_index=p_index,
        palace_by_topic=palace_by_topic(col),
        palace_total=len(p_index),
        viva_log=get_viva_log(col),
        events_today=events_today(col),
        studio_status=studio_status(col),
        overnight=_overnight_counts(col),
        readiness_history=get_readiness_history(col),
        fl_results=get_fl_results(col),
    )
    # Persist today's posted line so the Book can score its own past guesses
    # against future full-lengths (spec section 1 honesty rule).
    record_readiness_line(col, payload.get("scores", {}).get("readiness", {}))
    payload["auth"] = auth
    payload["world_assets"] = world_assets_present()
    payload["quiz_status"] = _quiz_status(col)
    payload["notifications"] = notification_previews(col)
    payload["fl_results"] = get_fl_results(col)
    payload["game_state"] = get_game_state(col)

    from ante.palace import gallery_payload
    from aqt.ante_studio import get_active_viva, get_palace

    payload["palace_gallery"] = gallery_payload(
        get_palace(col), pending=_leech_backlog(col)
    )
    payload["viva"]["active"] = get_active_viva(col)
    return payload


def _leech_backlog(col: Collection) -> int:
    """How many un-commissioned leeches are waiting (the Archive's 'pending')."""
    try:
        from aqt.ante_studio import extract_leeches

        return len(extract_leeches(col))
    except Exception:
        return 0


def _demo_viva_suggested() -> list[dict]:
    """Eligible topics for the demo Back Room: real topics that carry an
    open-ended item, so a defense can actually be graded on the tour."""
    _ensure_ante_importable()
    from ante.openended import load_open_items
    from ante.outline import load_outline

    outline = load_outline()
    seen: list[str] = []
    for it in load_open_items():
        if it.topic not in seen:
            seen.append(it.topic)
        if len(seen) >= 5:
            break
    out = []
    for i, tag in enumerate(seen):
        t = outline.topic(tag)
        out.append(
            {
                "topic": tag,
                "name": t.name if t else tag.rsplit("::", 1)[-1].replace("_", " "),
                "section": t.section_id if t else "",
                "status": "active" if i % 2 == 0 else "corrective",
                "accuracy": round(0.55 + 0.06 * i, 2),
                "gap": round(0.25 - 0.04 * i, 2),
                "weight": 1.0,
            }
        )
    return out


def build_viva_payload(col: Collection) -> dict:
    """Active Viva session + eligible topics (for the Examination Hall loop).

    Demo mode never runs a real examination (no writable open-log), so it just
    surfaces eligible topics for the walkthrough."""
    _ensure_ante_importable()
    from ante.config import CONFIG
    from ante.mastery import compute_mastery, stats_from_mastery_response
    from ante.viva import eligible_topics
    from aqt.ante_studio import get_active_viva, get_viva_log

    rt_on = realtime_available()
    if get_demo_state(col).get("enabled"):
        # a live, playable Back Room on the tour — real rubric grading and, with
        # a key, the real live table too (the session is transient: ante_studio
        # stores it off the real account, but it grades and speaks for real)
        log = get_viva_log(col)
        active, last, silence_cue = _attach_realtime_cues(
            get_active_viva(col), log[-1] if log else None, rt_on
        )
        return {
            "active": active,
            "last": last,
            "suggested": _demo_viva_suggested(),
            "demo": True,
            "realtime": rt_on,
            "rt_silence_cue": silence_cue,
        }

    log = get_viva_log(col)
    active_session = get_active_viva(col)
    active, last, silence_cue = _attach_realtime_cues(
        active_session, log[-1] if log else None, rt_on
    )
    # The eligible-topics list is only rendered when no examination is open, but
    # the Back Room polls this endpoint every few seconds mid-exam. Skip the full
    # topic-mastery scan (and the mastery recompute) whenever a session is live.
    if active_session and active_session.get("status") == "open":
        suggested: list[dict] = []
    else:
        topic_perf = _topic_application_performance(col)
        resp = col._backend.get_topic_mastery(
            search="", topic_prefix="", mastery_threshold=CONFIG.r_threshold
        )
        perf_point = {k: v[0] for k, v in (topic_perf or {}).items()}
        mastery = compute_mastery(
            stats_from_mastery_response(resp, perf_point), cfg=CONFIG
        )
        suggested = eligible_topics(mastery, get_open_responses(col), CONFIG)
    return {
        "active": active,
        "last": last,
        "suggested": suggested,
        "demo": False,
        # the live table: heads-up over streaming speech (OpenAI Realtime).
        # Feature-gated on the key; the rubric still does all grading.
        "realtime": rt_on,
        "rt_silence_cue": silence_cue,
    }


def _attach_realtime_cues(active, last, rt_on: bool):
    """Attach the private [LEDGER] cues Sahir is fed on the live table. Every
    word comes from the same pure module that grades (ante/viva.py); the web
    page is only a pipe. Returns (active, last, silence_cue)."""
    if not rt_on:
        return active, last, None
    from ante.viva import (
        realtime_opening_cue,
        realtime_silence_cue,
        realtime_turn_context,
    )

    if active and active.get("status") == "open":
        active = dict(active)
        active["rt_cue"] = (
            realtime_opening_cue(active)
            if not active.get("rounds")
            else realtime_turn_context(active)
        )
    if last and last.get("status") in ("passed", "failed"):
        last = dict(last)
        last["rt_cue"] = realtime_turn_context(last)
    return active, last, realtime_silence_cue()


# --------------------------------------------------------------------------- #
# The live table — ephemeral Realtime session minting (Sahir speaks live;
# the deterministic ledger still grades every turn)
# --------------------------------------------------------------------------- #

REALTIME_MINT_URL = "https://api.openai.com/v1/realtime/client_secrets"


def realtime_available() -> bool:
    """Live speech needs an OpenAI key; everything degrades to tap-to-speak
    without one (Principle: AI-optional, never required)."""
    import os

    return bool(os.environ.get("OPENAI_API_KEY")) and not os.environ.get(
        "ANTE_REALTIME_DISABLED"
    )


def build_realtime_session_config(session: dict) -> dict:
    """The GA Realtime session object for one Back Room examination.

    create_response=false is the honesty lever: the model may never respond on
    its own — after every player turn the web client grades the transcript
    through the same submit_answer() path as a typed answer, injects the
    private [LEDGER] note, and only then requests a response.
    """
    import os

    _ensure_ante_importable()
    from ante.viva import realtime_instructions

    return {
        "type": "realtime",
        "model": os.environ.get("ANTE_REALTIME_MODEL", "gpt-realtime"),
        "instructions": realtime_instructions(session),
        "output_modalities": ["audio"],
        "audio": {
            "input": {
                "transcription": {
                    "model": os.environ.get(
                        "ANTE_REALTIME_STT", "gpt-4o-mini-transcribe"
                    ),
                    "language": "en",
                },
                "noise_reduction": {"type": "near_field"},
                "turn_detection": {
                    # semantic VAD with low eagerness: an examiner who lets
                    # the student think before deciding they have finished
                    "type": "semantic_vad",
                    "eagerness": "low",
                    "create_response": False,
                    "interrupt_response": True,
                },
            },
            "output": {
                "voice": os.environ.get("ANTE_REALTIME_VOICE", "cedar"),
            },
        },
    }


def mint_realtime_secret(col: Collection) -> dict:
    """Mint an ephemeral client secret bound to the active examination.

    Returns {ok, value, expires_at, model} or {ok: False, reason}. The real
    API key never reaches the web page; the page only ever sees the
    short-lived ek_ token, exactly as the Realtime WebRTC docs prescribe.
    """
    import json
    import os
    import urllib.request

    from aqt.ante_studio import get_active_viva

    # The demo Back Room grades for real (transient session, off the account),
    # so it speaks for real too — the live table is part of the tour.
    if not realtime_available():
        return {"ok": False, "reason": "no key"}
    session = get_active_viva(col)
    if not session or session.get("status") != "open":
        return {"ok": False, "reason": "no active examination"}

    body = json.dumps({"session": build_realtime_session_config(session)}).encode(
        "utf-8"
    )
    req = urllib.request.Request(REALTIME_MINT_URL, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {os.environ['OPENAI_API_KEY']}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as exc:
        return {"ok": False, "reason": f"mint failed: {exc.__class__.__name__}"}
    value = str(data.get("value", ""))
    if not value:
        return {"ok": False, "reason": "mint returned no secret"}
    return {
        "ok": True,
        "value": value,
        "expires_at": data.get("expires_at"),
        "model": (data.get("session") or {}).get("model"),
    }


FL_RESULTS_KEY = "ante_fl_results"


def get_fl_results(col: Collection) -> dict:
    """Recorded full-length results, keyed by test number ('1'/'2')."""
    data = _get_acct(col, FL_RESULTS_KEY, {})
    return data if isinstance(data, dict) else {}


READINESS_HISTORY_KEY = "ante_readiness_history"
# keep the log bounded — a rolling window is plenty to score past lines
_READINESS_HISTORY_MAX = 120


def get_readiness_history(col: Collection) -> list:
    """Past readiness lines the Book has posted (for the track record)."""
    data = _get_acct(col, READINESS_HISTORY_KEY, [])
    return data if isinstance(data, list) else []


def record_readiness_line(col: Collection, readiness: dict) -> None:
    """Append today's posted line to the history (deduped per day). Demo play is
    throwaway; abstaining lines are ignored by append_line."""
    if get_demo_state(col).get("enabled"):
        return
    _ensure_ante_importable()
    from ante.trackrecord import append_line

    history = get_readiness_history(col)
    updated = append_line(history, readiness, now=time.time())
    if updated is not history and updated != history:
        _set_acct(col, READINESS_HISTORY_KEY, updated[-_READINESS_HISTORY_MAX:])


GAME_STATE_KEY = "ante_game_state"


def get_game_state(col: Collection) -> dict:
    """In-progress game snapshots (practice quiz, full-lengths, the buy-in,
    per-day tallies), keyed by game id and namespaced per account. Leaving a
    game must never restart it — the web app saves after every answer and
    restores from here on open."""
    data = _get_acct(col, GAME_STATE_KEY, {})
    return data if isinstance(data, dict) else {}


def save_game_state(col: Collection, game_id: str, state: dict) -> None:
    """Persist one game's in-progress snapshot (demo play is throwaway)."""
    if get_demo_state(col).get("enabled"):
        return
    data = get_game_state(col)
    data[str(game_id)] = state
    _set_acct(col, GAME_STATE_KEY, data)


def clear_game_state(col: Collection, game_id: str) -> None:
    """Drop a finished/abandoned game's snapshot (completion, skip, submit)."""
    if get_demo_state(col).get("enabled"):
        return
    data = get_game_state(col)
    if data.pop(str(game_id), None) is not None:
        _set_acct(col, GAME_STATE_KEY, data)


def build_fl_payload(col: Collection, test_no: int) -> dict:
    """The form for full-length test 1 or 2 (+ any prior result)."""
    _ensure_ante_importable()
    from ante.fulllength import build_full_length

    payload = build_full_length(test_no)
    if get_demo_state(col).get("enabled"):
        payload["demo"] = True
    else:
        payload["result"] = get_fl_results(col).get(str(payload["test_no"]))
    return payload


def record_fl_result(col: Collection, test_no: int, answers: dict) -> dict:
    """Score + persist a completed full-length (demo results are throwaway)."""
    _ensure_ante_importable()
    from ante.fulllength import score_full_length

    score = score_full_length(answers, test_no)
    score["taken_at"] = time.time()
    if not get_demo_state(col).get("enabled"):
        results = get_fl_results(col)
        results[str(score["test_no"])] = score
        _set_acct(col, FL_RESULTS_KEY, results)
    return score


def tutor_answer(payload: dict) -> dict:
    """One turn of the table tutor (Sahir explains the card just played).

    Card fields arrive as rendered HTML from the web app; they're flattened to
    text before touching the prompt. Pure pass-through otherwise — the provider
    isolation (Claude or honest-offline) lives in ante.ai.tutor."""
    _ensure_ante_importable()
    from ante.ai.tutor import tutor_reply

    history = payload.get("history")
    return tutor_reply(
        front=_strip_html(str(payload.get("front", ""))),
        back=_strip_html(str(payload.get("back", ""))),
        topic=str(payload.get("topic", "")),
        history=history if isinstance(history, list) else [],
        question=str(payload.get("question", "")),
    )


def notification_previews(col: Collection) -> list[dict]:
    """Every notification type the app can send, for the Settings gallery."""
    _ensure_ante_importable()
    from ante.os_notify import preview_notifications

    return preview_notifications()


def _quiz_status(col: Collection) -> dict:
    """When are quizzes due? Surfaces the Bloom re-assessment loop for the UI:
    new items are due now, missed ones return until re-proven, and correct ones
    resurface after the spaced re-assessment window to check the learning stuck."""
    _ensure_ante_importable()
    from ante.openended import open_progress
    from ante.performance_items import quiz_progress

    now = time.time()
    qp = quiz_progress(get_perf_responses(col), now=now)
    op = open_progress(get_open_responses(col), now=now)
    reassess = [
        d for d in (qp["next_reassess_days"], op["next_reassess_days"]) if d is not None
    ]
    return {
        "total": qp["total"] + op["total"],
        "attempted": qp["attempted"] + op["attempted"],
        "proven": qp["proven"] + op["proven"],
        "due": qp["due"] + op["due"],
        "next_reassess_days": round(min(reassess), 1) if reassess else None,
    }


def world_assets_present() -> dict[str, bool]:
    """Which cinematic den assets have been generated (gen_world.py), so the
    web app uses real Higgsfield plates/loops where available and falls back
    to its built-in scene otherwise. Card text is never baked into these —
    they are backdrops; the cards stay crisp HTML composited on top."""
    _ensure_ante_importable()
    import ante

    base = Path(ante.__file__).resolve().parent / "web" / "assets"
    names = [
        # the Emerald Room, by hour (stills + optional living loops).
        # WebM comes first-class: Anki's QtWebEngine ships without proprietary
        # codecs, so H.264 mp4s decode to a black screen — VP9 does not.
        "den_dawn.jpg",
        "den_day.jpg",
        "den_dusk.jpg",
        "den_night.jpg",
        "den_dawn.webm",
        "den_day.webm",
        "den_dusk.webm",
        "den_night.webm",
        "den_dawn.mp4",
        "den_day.mp4",
        "den_dusk.mp4",
        "den_night.mp4",
        # Sahir
        "dealer.jpg",
        "dealer_idle.webm",
        "dealer_idle.mp4",
        "sahir_deal.jpg",
        "sahir_deal.webm",
        "sahir_deal.mp4",
        "felt_close.jpg",
        "felt_close.webm",
        "felt_close.mp4",
        # the Circuit's city plates + the Final Table
        "city_new_york.jpg",
        "city_monte_carlo.jpg",
        "city_havana.jpg",
        "city_macau.jpg",
        "final_table.jpg",
        # seat portraits (avatar picker)
        "avatar_1.jpg",
        "avatar_2.jpg",
        "avatar_3.jpg",
        "avatar_4.jpg",
        "avatar_5.jpg",
        "avatar_6.jpg",
        # optional dealer voice lines (played in-app, never required)
        "vo_seat.mp3",
        "vo_morning.mp3",
        "vo_midnight.mp3",
        "vo_call_open.mp3",
        "vo_call_done.mp3",
    ]
    names += [f"vo_film_{i}.mp3" for i in range(1, 9)]
    return {n: (base / n).is_file() for n in names}


MASTERED_SEEN_KEY = "ante_mastered_seen"


def _newly_mastered_count(col: Collection, resp, topic_performance: dict | None) -> int:
    """Topics that crossed into mastered since the last payload build.

    The momentum + surprise-reward surfaces must fire only on NEW mastery —
    re-announcing the running total on every refresh would turn a competence
    signal into confetti. The last-seen total is tracked per account.

    Reuses the GetTopicMastery response already fetched for the payload rather
    than issuing a second full-collection scan on every dashboard build."""
    _ensure_ante_importable()
    from ante.config import CONFIG
    from ante.mastery import (
        MasteryStatus,
        compute_mastery,
        stats_from_mastery_response,
    )

    perf_point = {k: v[0] for k, v in (topic_performance or {}).items()}
    stats = stats_from_mastery_response(resp, perf_point)
    mastery = compute_mastery(stats, cfg=CONFIG)
    total = sum(1 for m in mastery.values() if m.status == MasteryStatus.MASTERED)
    seen = _get_acct(col, MASTERED_SEEN_KEY, None)
    if seen is None or int(seen) != total:
        _set_acct(col, MASTERED_SEEN_KEY, total)
    # first look establishes the baseline quietly; drops are not "un-mastery"
    return 0 if seen is None else max(0, total - int(seen))


def _genuine_reviews_by_day(col: Collection) -> tuple[dict[int, int], int]:
    """Count GENUINELY-attempted reviews per day for the consistency streak
    (PRD 9.5.2 effort-gate): a review is genuine only if its response time is at
    least STREAK_MIN_RESPONSE_MS (tapping 'Good' in <1s does not count).

    Days are bucketed on Anki's rollover boundary (day_cutoff), not UTC
    midnight, and indexed so that reviews inside [cutoff-86400, cutoff) land
    exactly on ``today_ordinal`` — otherwise "today" never counts and the
    30-night run misreads."""
    _ensure_ante_importable()
    from ante.config import CONFIG

    day_secs = 86400
    cutoff = col.sched.day_cutoff  # end-of-today boundary in secs
    today_ordinal = cutoff // day_secs
    # BIG keeps the numerator positive so SQLite's truncating division
    # behaves like floor() for timestamps before the cutoff.
    big = 100_000
    rows = col.db.all(
        "select cast((id/1000 - ? + ?) / ? as int) as d, count() "
        "from revlog where ease > 0 and time >= ? group by d",
        cutoff,
        big * day_secs,
        day_secs,
        CONFIG.streak_min_response_ms,
    )
    shift = today_ordinal + 1 - big
    return ({int(d) + shift: int(n) for d, n in rows}, int(today_ordinal))


def _hour_counts_today(col: Collection) -> dict[int, int]:
    """Today's genuine reviews by local hour-of-day — drives the First Light /
    Last Light bookends. Uses the same effort-gate as the streak."""
    _ensure_ante_importable()
    from ante.config import CONFIG

    day_start_ms = (col.sched.day_cutoff - 86400) * 1000
    rows = col.db.all(
        "select cast(strftime('%H', id/1000, 'unixepoch', 'localtime') as int) as h, "
        "count() from revlog where ease > 0 and time >= ? and id >= ? group by h",
        CONFIG.streak_min_response_ms,
        day_start_ms,
    )
    return {int(h): int(n) for h, n in rows}


def _overnight_counts(col: Collection) -> tuple[int, int]:
    """(settled, loose) for the consolidation-night report.

    settled = distinct cards recalled successfully (ease > 1) yesterday — the
    reviews the night consolidated; loose = review cards whose due date landed
    today — what the forgetting curve pried loose overnight."""
    day_secs = 86400
    yday_start_ms = (col.sched.day_cutoff - 2 * day_secs) * 1000
    yday_end_ms = (col.sched.day_cutoff - day_secs) * 1000
    settled = (
        col.db.scalar(
            "select count(distinct cid) from revlog "
            "where id >= ? and id < ? and ease > 1",
            yday_start_ms,
            yday_end_ms,
        )
        or 0
    )
    loose = (
        col.db.scalar(
            "select count() from cards where queue = 2 and due = ?",
            col.sched.today,
        )
        or 0
    )
    return int(settled), int(loose)


# --------------------------------------------------------------------------- #
# Accounts (local, desktop): a login layer + per-account data namespacing.
#
# Ante is a local app, so "account-based" here means: each signed-in account
# (Google or email) gets its OWN profile + progress (quiz/open answers, exam
# settings) on this device, stored under an account-scoped config namespace. The
# shared Anki card collection is common; the per-account Ante data is not.
# Real Google sign-in lives in aqt.ante_auth; identity is stored here.
# --------------------------------------------------------------------------- #

AUTH_KEY = "ante_auth"
# the base keys that are namespaced per account
_ACCOUNT_SCOPED = (
    "ante_profile",
    "ante_perf_responses",
    "ante_open_responses",
    "ante_flash_confidence",
    "ante_exam_date",
    "ante_target_score",
    "ante_diagnostic",
    "ante_fl_results",
    # in-progress game snapshots (quiz / full-length / buy-in resume points)
    "ante_game_state",
    # v4 generative layer: the Palace index + Viva history/active session
    "ante_palace",
    "ante_viva",
    "ante_viva_active",
    # momentum baseline (mastered-topic count at last look)
    "ante_mastered_seen",
)


def get_auth(col: Collection) -> dict:
    data = col.get_config(AUTH_KEY, {})
    if not isinstance(data, dict):
        data = {}
    accounts = data.get("accounts")
    accounts = accounts if isinstance(accounts, dict) else {}
    current = data.get("current")
    current = current if isinstance(current, str) and current in accounts else None
    return {"accounts": accounts, "current": current}


def set_auth(col: Collection, auth: dict) -> None:
    col.set_config(
        AUTH_KEY,
        {"accounts": auth.get("accounts", {}), "current": auth.get("current")},
    )


def current_account_id(col: Collection) -> str | None:
    return get_auth(col)["current"]


def _akey(col: Collection, base: str) -> str:
    return f"acct::{current_account_id(col) or 'guest'}::{base}"


def _get_acct(col: Collection, base: str, default):
    return col.get_config(_akey(col, base), default)


def _set_acct(col: Collection, base: str, value) -> None:
    col.set_config(_akey(col, base), value)


def _account_id_for(provider: str, sub: str) -> str:
    import hashlib

    return provider + "_" + hashlib.sha1(sub.encode("utf-8")).hexdigest()[:16]


def sign_in_account(col: Collection, account: dict) -> dict:
    """Add/update an account and make it current. On first sign-in of an account,
    migrate any pre-account Ante data into it so existing progress carries
    over (a one-time convenience for the first user)."""
    auth = get_auth(col)
    first_time = account["id"] not in auth["accounts"]
    auth["accounts"][account["id"]] = account
    auth["current"] = account["id"]
    set_auth(col, auth)
    if first_time and len(auth["accounts"]) == 1:
        _migrate_legacy_into_current(col)
    return account


def _migrate_legacy_into_current(col: Collection) -> None:
    for base in _ACCOUNT_SCOPED:
        akey = _akey(col, base)
        if col.get_config(akey, None) is None:
            legacy = col.get_config(base, None)
            if legacy is not None:
                col.set_config(akey, legacy)


def sign_in_email(
    col: Collection, email: str, name: str | None = None, provider: str = "email"
) -> dict:
    email = (email or "").strip().lower() or "guest@ante.local"
    account = {
        "id": _account_id_for(provider, email),
        "name": name or email.split("@")[0].replace(".", " ").title(),
        "email": email,
        "picture": "",
        "provider": provider,
    }
    return sign_in_account(col, account)


def switch_account(col: Collection, account_id: str) -> None:
    auth = get_auth(col)
    if account_id in auth["accounts"]:
        auth["current"] = account_id
        set_auth(col, auth)


def sign_out(col: Collection) -> None:
    auth = get_auth(col)
    auth["current"] = None
    set_auth(col, auth)


GOOGLE_SECRET_KEY = "ante_google_secret"
GOOGLE_CLIENT_ID_KEY = "ante_google_client_id"


def get_google_client_id(col: Collection) -> str:
    """Device-level Google OAuth client id (not account-scoped)."""
    val = col.get_config(GOOGLE_CLIENT_ID_KEY, "")
    return val.strip() if isinstance(val, str) else ""


def set_google_client_id(col: Collection, client_id: str) -> None:
    col.set_config(GOOGLE_CLIENT_ID_KEY, (client_id or "").strip())


def ensure_google_client_id(col: Collection) -> None:
    """Persist client id from env/JSON on first detect so restarts don't need env."""
    if get_google_client_id(col):
        return
    try:
        from aqt import ante_auth

        cid, _ = ante_auth._resolve_client_from_env_or_json()
        if cid:
            set_google_client_id(col, cid)
    except Exception:
        pass


def get_google_secret(col: Collection) -> str:
    """Device-level Google OAuth client secret pasted into the login screen
    (not account-scoped). Env/JSON take precedence in _client()."""
    val = col.get_config(GOOGLE_SECRET_KEY, "")
    return val.strip() if isinstance(val, str) else ""


def set_google_secret(col: Collection, secret: str) -> None:
    col.set_config(GOOGLE_SECRET_KEY, (secret or "").strip())


def google_configured(col: Collection | None = None) -> bool:
    """True when a real Google OAuth client id is available."""
    try:
        from aqt import ante_auth

        return bool(ante_auth._client(col)[0])
    except Exception:
        import os

        return bool(os.environ.get("ANTE_GOOGLE_CLIENT_ID"))


def google_secret_present(col: Collection) -> bool:
    """Whether a client secret is available from any source (env, JSON, or the
    in-app box) — so the UI can tell the user Google is fully ready."""
    try:
        from aqt import ante_auth

        if ante_auth._client(col)[1]:
            return True
    except Exception:
        pass
    return bool(get_google_secret(col))


def build_auth_payload(col: Collection) -> dict:
    ensure_google_client_id(col)
    auth = get_auth(col)
    cur = auth["accounts"].get(auth["current"]) if auth["current"] else None
    accounts = [
        {
            "id": a.get("id"),
            "name": a.get("name"),
            "email": a.get("email"),
            "picture": a.get("picture"),
            "provider": a.get("provider"),
        }
        for a in auth["accounts"].values()
    ]
    return {
        "signed_in": cur is not None,
        "account": cur,
        "accounts": accounts,
        "google_configured": google_configured(col),
        "google_secret_present": google_secret_present(col),
    }


PERF_RESPONSES_KEY = "ante_perf_responses"


def get_perf_responses(col: Collection) -> dict[str, list]:
    """Recorded application-item attempts {item_id: [[chosen_index, ts], ...]}.

    Migrates the legacy one-shot {item_id: chosen_index} shape in place (stamping
    the current time so existing progress is preserved and the re-assessment
    clock starts now) the first time it is read.
    """
    data = _get_acct(col, PERF_RESPONSES_KEY, {})
    if not isinstance(data, dict):
        return {}
    out: dict[str, list] = {}
    migrated = False
    now = time.time()
    for k, v in data.items():
        key = str(k)
        if isinstance(v, bool):
            continue
        if isinstance(v, list):
            out[key] = v
        elif isinstance(v, int):
            out[key] = [[int(v), now]]
            migrated = True
        # anything else is dropped as malformed
    if migrated:
        _set_acct(col, PERF_RESPONSES_KEY, out)
    return out


def record_quiz_answer(
    col: Collection,
    item_id: str,
    chosen_index: int,
    confidence: float | None = None,
    elapsed_ms: int | None = None,
) -> bool:
    _ensure_ante_importable()
    from ante.performance_items import is_correct

    if get_demo_state(col).get("enabled"):
        # demo answers are throwaway — never write into the real account
        return is_correct(item_id, int(chosen_index))
    responses = get_perf_responses(col)
    attempts = list(responses.get(item_id) or [])
    attempt: list = [int(chosen_index), time.time()]
    if confidence is not None or elapsed_ms is not None:
        attempt.append(float(confidence) if confidence is not None else None)
    if elapsed_ms is not None:
        attempt.append(int(elapsed_ms))
    attempts.append(attempt)
    responses[item_id] = attempts
    _set_acct(col, PERF_RESPONSES_KEY, responses)
    return is_correct(item_id, int(chosen_index))


EXAM_DATE_KEY = "ante_exam_date"
TARGET_SCORE_KEY = "ante_target_score"


def get_forecast_settings(col: Collection) -> tuple[str | None, int | None]:
    exam_date = _get_acct(col, EXAM_DATE_KEY, None)
    target = _get_acct(col, TARGET_SCORE_KEY, None)
    exam_date = str(exam_date) if isinstance(exam_date, str) and exam_date else None
    target = int(target) if isinstance(target, (int, float)) else None
    return exam_date, target


def set_forecast_settings(
    col: Collection, exam_date: str | None, target_score: int | None
) -> None:
    _set_acct(col, EXAM_DATE_KEY, exam_date or "")
    _set_acct(col, TARGET_SCORE_KEY, int(target_score) if target_score else 0)


# --------------------------------------------------------------------------- #
# Study profile (personalization) + exam-date recalibration of FSRS
# --------------------------------------------------------------------------- #

PROFILE_KEY = "ante_profile"


def get_profile(col: Collection):
    """The StudyProfile, merging any legacy exam_date/target_score config so the
    profile is the single source of truth."""
    _ensure_ante_importable()
    from ante.profile import StudyProfile

    data = _get_acct(col, PROFILE_KEY, {})
    if not isinstance(data, dict):
        data = {}
    # fold in legacy standalone settings if the profile doesn't carry them yet
    exam_date, target_score = get_forecast_settings(col)
    data.setdefault("exam_date", exam_date)
    data.setdefault("target_score", target_score)
    return StudyProfile.from_dict(data)


def set_profile(col: Collection, updates: dict) -> dict:
    """Merge ``updates`` into the stored profile, persist it, keep the legacy
    exam/target keys in sync, and re-apply FSRS recalibration. Returns the saved
    profile dict."""
    _ensure_ante_importable()
    from ante.profile import StudyProfile

    current = get_profile(col).as_dict()
    current.update({k: v for k, v in (updates or {}).items() if v is not None})
    if "onboarded" in (updates or {}):
        current["onboarded"] = bool(updates["onboarded"])
    prof = StudyProfile.from_dict(current)
    _set_acct(col, PROFILE_KEY, prof.as_dict())
    # keep the legacy keys in sync so older surfaces still read them
    set_forecast_settings(col, prof.exam_date, prof.target_score)
    apply_recalibration(col, prof)
    return prof.as_dict()


def build_reminder_schedule(col: Collection) -> list[dict]:
    """Today's reminder schedule (lightweight; no full dashboard build) for the
    Qt notification scheduler. Empty when reminders are off or no collection.

    Includes the next marked night (quiz checkpoint / full-length) as a
    date-scoped entry: the in-app scheduler fires it only on its night, and
    the OS sync registers it as a dated job."""
    _ensure_ante_importable()
    from ante.reminders import build_schedule
    from ante.studyplan import marked_nights

    prof = get_profile(col)
    if not prof.reminders_enabled:
        return []
    today = col.sched.today
    due_count = (
        col.db.scalar(
            "select count() from cards where queue in (1,2,3) and due<=?", today
        )
        or 0
    )
    from ante.recalibrate import recalibrate

    recal = recalibrate(prof, due_count=int(due_count))
    upcoming = marked_nights(recal.days_remaining)
    schedule = build_schedule(
        prof,
        recal.slot_plan,
        due_count=int(due_count),
        days_remaining=recal.days_remaining,
        marked_night=upcoming[0] if upcoming else None,
    )
    return [r.as_dict() for r in schedule]


def sync_os_reminders(col: Collection) -> dict:
    """Register (or remove) the OS-scheduled reminders so the morning/night
    bookends fire even when Ante is closed. Driven by the profile's
    ``background_reminders`` switch; safe to call any time."""
    _ensure_ante_importable()
    from ante import os_notify

    prof = get_profile(col)
    if prof.reminders_enabled and prof.background_reminders:
        return os_notify.install(build_reminder_schedule(col))
    return os_notify.uninstall_all()


# --------------------------------------------------------------------------- #
# The Baseline Diagnostic (onboarding mini-test)
# --------------------------------------------------------------------------- #

DIAG_KEY = "ante_diagnostic"


def get_diagnostic(col: Collection) -> dict:
    data = _get_acct(col, DIAG_KEY, {})
    return data if isinstance(data, dict) else {}


def set_diagnostic(col: Collection, updates: dict) -> dict:
    data = get_diagnostic(col)
    data.update(updates or {})
    _set_acct(col, DIAG_KEY, data)
    return data


def build_diagnostic_payload(col: Collection) -> dict:
    """The diagnostic form + current status (+ summary once answered). Answers
    are recorded through the same anquiz/anopen bridge as the quiz, so they
    immediately feed mastery/comprehension/readiness."""
    _ensure_ante_importable()
    from ante.diagnostic import build_diagnostic, summarize_diagnostic

    form = build_diagnostic()
    status = get_diagnostic(col)
    item_ids = status.get("item_ids") or form.item_ids
    summary = None
    if status.get("taken_at") or status.get("started_at"):
        summary = summarize_diagnostic(
            list(item_ids), get_perf_responses(col), get_open_responses(col)
        )
    return {
        "form": form.as_dict(),
        "taken": bool(status.get("taken_at")),
        "skipped": bool(status.get("skipped")),
        "summary": summary,
    }


def finish_diagnostic(col: Collection, skipped: bool) -> dict:
    """Mark the diagnostic finished (or skipped), snapshot the item set it was
    graded on, and re-apply recalibration so the plan reflects the baseline."""
    _ensure_ante_importable()
    from ante.diagnostic import build_diagnostic

    if get_demo_state(col).get("enabled"):
        # demo answers are never recorded, so never stamp the real account's
        # diagnostic as taken (or recalibrate the real deck) from demo mode
        return {}
    form = build_diagnostic()
    data = set_diagnostic(
        col,
        {
            "taken_at": time.time(),
            "skipped": bool(skipped),
            "item_ids": form.item_ids,
        },
    )
    apply_recalibration(col)
    return data


def apply_recalibration(col: Collection, prof=None) -> dict:
    """Apply the exam-date recalibration to the MCAT deck's FSRS config: ramp the
    desired retention as the exam nears and cap review intervals so no card is
    scheduled past test day. Best-effort + fully reversible (config only)."""
    _ensure_ante_importable()
    from ante.config import CONFIG
    from ante.forecast import days_until
    from ante.recalibrate import desired_retention_for

    if prof is None:
        prof = get_profile(col)
    days = days_until(prof.exam_date)
    if days is None:
        return {"applied": False, "reason": "no exam date"}
    retention = desired_retention_for(days, CONFIG)
    max_iv = max(1, days)
    ensure_study_deck(col)
    applied = False
    try:
        did = col.decks.get_current_id()
        conf = col.decks.config_dict_for_deck_id(did)
        conf["desiredRetention"] = float(retention)
        conf["maximumReviewInterval"] = int(max_iv)
        col.decks.update_config(conf)
        applied = True
    except Exception:
        pass
    return {
        "applied": applied,
        "desired_retention": retention,
        "max_interval_days": max_iv,
        "days_remaining": days,
    }


# --------------------------------------------------------------------------- #
# Open-ended (short-answer) items: offline grading + attempt log
# --------------------------------------------------------------------------- #

OPEN_RESPONSES_KEY = "ante_open_responses"


def get_open_responses(col: Collection) -> dict[str, list]:
    data = _get_acct(col, OPEN_RESPONSES_KEY, {})
    if not isinstance(data, dict):
        return {}
    out: dict[str, list] = {}
    for k, v in data.items():
        if isinstance(v, list):
            out[str(k)] = v
    return out


def grade_open_preview(item_id: str, answer: str) -> dict:
    """Grade a free-text answer WITHOUT storing it (thread-safe; used by the GET
    preview endpoint). Returns the score, matched/missing points, feedback and the
    model answer for corrective feedback."""
    _ensure_ante_importable()
    from ante.openended import grade_open_answer, open_item_by_id

    item = open_item_by_id(item_id)
    if item is None:
        return {"ok": False, "error": "unknown item"}
    out = grade_open_answer(answer, item).as_dict()
    out.update({"ok": True, "model_answer": item.model_answer, "topic": item.topic})
    return out


def grade_and_record_open(
    col: Collection,
    item_id: str,
    answer: str,
    confidence: float | None = None,
    elapsed_ms: int | None = None,
) -> dict:
    """Grade a free-text answer offline, log the attempt (score + confidence +
    time), and return the grade plus the model answer for corrective feedback."""
    _ensure_ante_importable()
    from ante.openended import grade_open_answer, open_item_by_id

    item = open_item_by_id(item_id)
    if item is None:
        return {"ok": False, "error": "unknown item"}
    grade = grade_open_answer(answer, item)
    if not get_demo_state(col).get("enabled"):  # demo answers are throwaway
        responses = get_open_responses(col)
        attempts = list(responses.get(item_id) or [])
        attempt: list = [round(grade.score, 3), time.time()]
        attempt.append(float(confidence) if confidence is not None else None)
        attempt.append(int(elapsed_ms) if elapsed_ms else None)
        attempts.append(attempt)
        responses[item_id] = attempts
        _set_acct(col, OPEN_RESPONSES_KEY, responses)
    out = grade.as_dict()
    out.update({"ok": True, "model_answer": item.model_answer, "topic": item.topic})
    return out


def _hour_outcomes(col: Collection) -> list[tuple[int, int]]:
    """(hour_of_day, correct) for every graded review, for Peak Hours analysis.
    ``correct`` = the review was not an 'Again' (ease > 1).

    Aggregated in SQL to at most 48 (hour, correct) buckets so a large revlog is
    not marshalled a row at a time on every dashboard build; the expanded list
    is an identical multiset (peak_windows only counts per window)."""
    rows = col.db.all(
        "select cast(strftime('%H', id/1000, 'unixepoch', 'localtime') as int) as h, "
        "case when ease > 1 then 1 else 0 end as correct, count() "
        "from revlog where ease > 0 group by h, correct"
    )
    out: list[tuple[int, int]] = []
    for h, correct, n in rows:
        out.extend([(int(h), int(correct))] * int(n))
    return out


def _topic_application_performance(
    col: Collection,
) -> dict[str, tuple[float, float, float]]:
    """Per-topic performance pooled across multiple-choice AND open-ended items
    the student has answered (mastery is shown from quizzes + open-ended)."""
    _ensure_ante_importable()
    from ante.applied import combined_topic_performance

    return combined_topic_performance(get_perf_responses(col), get_open_responses(col))


def _card_timing_events(col: Collection, limit: int = 800) -> list[tuple[bool, int]]:
    """(correct, elapsed_ms) for recent flashcard reviews (ease>1 == correct)."""
    rows = col.db.all(
        "select ease, time from revlog where ease > 0 order by id desc limit ?", limit
    )
    return [(int(e) > 1, int(t)) for e, t in rows]


def _quiz_timing_events(col: Collection) -> list[tuple[bool, int]]:
    """(correct, elapsed_ms) for answered quiz + open-ended items that were timed."""
    _ensure_ante_importable()
    from ante.config import CONFIG
    from ante.openended import normalize_open_log
    from ante.performance_items import item_by_id, normalize_log

    events: list[tuple[bool, int]] = []
    for iid, attempts in normalize_log(get_perf_responses(col)).items():
        it = item_by_id(iid)
        if not it:
            continue
        for a in attempts:
            if a.elapsed_ms:
                events.append((a.choice == it.correct_index, int(a.elapsed_ms)))
    for oid, o_attempts in normalize_open_log(get_open_responses(col)).items():
        for oa in o_attempts:
            if oa.elapsed_ms:
                events.append((oa.score >= CONFIG.open_pass_score, int(oa.elapsed_ms)))
    return events


def build_quiz_payload(col: Collection) -> dict:
    """Next application item (multiple-choice OR open-ended) due for
    (re)assessment, interleaving recognition and production, plus the combined
    application-accuracy summary, progress counts, and the paraphrase gap.

    Bloom's loop: items come back after a wrong answer (corrective) or once a
    correct answer goes stale (spaced re-assessment), so the quiz is never truly
    'finished' — mastery has to be maintained, not banked once."""
    _ensure_ante_importable()
    from ante.applied import combined_topic_performance
    from ante.openended import (
        next_open_item,
        normalize_open_log,
        open_progress,
    )
    from ante.performance_items import (
        next_item,
        normalize_log,
        paraphrase_gaps,
        quiz_progress,
    )

    now = time.time()
    demo_on = bool(get_demo_state(col).get("enabled"))
    # Demo mode: answers are throwaway (never recorded), so the real account's
    # log must not leak in, and the deterministic next-due pick would serve the
    # same question forever. Rotate randomly through the full banks instead.
    responses = {} if demo_on else get_perf_responses(col)
    open_responses = {} if demo_on else get_open_responses(col)
    # per-topic recall (memory) for the paraphrase gap
    resp_m = col._backend.get_topic_mastery(
        search="", topic_prefix="", mastery_threshold=0.0
    )
    recall = {t.topic: t.average_recall for t in resp_m.topics if t.studied_cards}
    weakest = min(recall, key=lambda t: recall[t]) if recall else None

    if demo_on:
        from ante.openended import load_open_items
        from ante.performance_items import load_items

        mcq_bank = list(load_items())
        open_bank = list(load_open_items())
        mcq = random.choice(mcq_bank) if mcq_bank else None
        opn = random.choice(open_bank) if open_bank else None
        prefer_open = bool(open_bank) and random.random() < 0.35
    else:
        mcq = next_item(responses, prefer_topic=weakest, now=now)
        opn = next_open_item(open_responses, prefer_topic=weakest, now=now)
        # interleave recognition (MCQ) and production (open-ended)
        attempts_so_far = len(normalize_log(responses)) + len(
            normalize_open_log(open_responses)
        )
        prefer_open = attempts_so_far % 2 == 1

    item_payload: dict | None = None
    if opn is not None and (prefer_open or mcq is None):
        item_payload = {
            "id": opn.id,
            "type": "open",
            "topic": opn.topic,
            "stem": opn.prompt,
            "difficulty": opn.difficulty,
            "retest": bool(normalize_open_log(open_responses).get(opn.id)),
        }
    elif mcq is not None:
        item_payload = {
            "id": mcq.id,
            "type": "mcq",
            "topic": mcq.topic,
            "stem": mcq.stem,
            "choices": list(mcq.choices),
            "correct_index": mcq.correct_index,
            "retest": bool(normalize_log(responses).get(mcq.id)),
        }

    acc = combined_topic_performance(responses, open_responses)
    gaps = [
        {
            "topic": g.topic,
            "recall": round(g.card_recall, 3),
            "application": round(g.application_accuracy, 3),
            "gap": round(g.gap, 3),
        }
        for g in paraphrase_gaps(responses, recall)
    ]
    mprog = quiz_progress(responses, now=now)
    oprog = open_progress(open_responses, now=now)
    reassess = [
        d
        for d in (mprog["next_reassess_days"], oprog["next_reassess_days"])
        if d is not None
    ]

    return {
        "done": item_payload is None,
        "total": mprog["total"] + oprog["total"],
        "attempted": mprog["attempted"] + oprog["attempted"],
        "proven": mprog["proven"] + oprog["proven"],
        "due": mprog["due"] + oprog["due"],
        "next_reassess_days": min(reassess) if reassess else None,
        "item": item_payload,
        "accuracy": {t: round(v[0], 3) for t, v in acc.items()},
        "gaps": gaps,
    }


# --------------------------------------------------------------------------- #
# Free Study (Practice): unlimited flashcards / quiz / open-ended by scope
# --------------------------------------------------------------------------- #


def _practice_in_scope(topic: str, scope: str) -> bool:
    if not scope or scope == "all":
        return topic.startswith("mcat::")
    if "::" in scope:  # a specific concept tag
        return topic == scope
    return topic.startswith(f"mcat::{scope}")  # a whole section


def _practice_flash(col: Collection, scope: str, exclude: str) -> dict:
    if not scope or scope == "all":
        search = "tag:mcat::*"
    elif "::" in scope:
        search = f"tag:{scope}"
    else:
        search = f"tag:mcat::{scope}::*"
    try:
        cids = list(col.find_cards(search))
    except Exception:
        cids = []
    if not cids:
        return {"done": True, "mode": "flash", "reason": "No cards in this scope yet."}
    pool = [c for c in cids if str(c) != exclude] or cids
    card = col.get_card(random.choice(pool))
    note = card.note()
    topic = next((t for t in note.tags if t.startswith("mcat::")), "")
    return {
        "done": False,
        "mode": "flash",
        "remaining": len(cids),
        "card": {
            "id": str(card.id),
            "question": card.question(),
            "answer": card.answer(),
            "topic": topic,
        },
    }


def _demo_practice_flash(scope: str, exclude: str) -> dict:
    """Flashcard practice from the seed deck in demo mode (the real collection
    is empty there, and demo must never read or touch it)."""
    _ensure_ante_importable()
    import json as _json

    import ante

    try:
        path = Path(ante.__file__).resolve().parent / "data" / "seed_cards.json"
        cards = _json.loads(path.read_text(encoding="utf-8")).get("cards", {})
    except Exception:
        cards = {}
    pool = []
    for topic, pairs in cards.items():
        if not _practice_in_scope(topic, scope):
            continue
        for i, (front, back) in enumerate(pairs):
            pool.append(
                {
                    "id": f"demo-{topic}-{i}",
                    "question": front,
                    "answer": back,
                    "topic": topic,
                }
            )
    if not pool:
        return {"done": True, "mode": "flash", "reason": "No cards in this scope yet."}
    pick = [c for c in pool if c["id"] != exclude] or pool
    return {
        "done": False,
        "mode": "flash",
        "remaining": len(pool),
        "card": random.choice(pick),
    }


def _practice_mcq(scope: str, exclude: str) -> dict:
    _ensure_ante_importable()
    from ante.performance_items import load_items

    items = [it for it in load_items() if _practice_in_scope(it.topic, scope)]
    if not items:
        return {
            "done": True,
            "mode": "mcq",
            "reason": "No questions in this scope yet.",
        }
    pool = [it for it in items if it.id != exclude] or items
    it = random.choice(pool)
    return {
        "done": False,
        "mode": "mcq",
        "remaining": len(items),
        "item": {
            "id": it.id,
            "type": "mcq",
            "topic": it.topic,
            "stem": it.stem,
            "choices": list(it.choices),
            "correct_index": it.correct_index,
            "retest": False,
        },
    }


def _practice_open(scope: str, exclude: str) -> dict:
    _ensure_ante_importable()
    from ante.openended import load_open_items

    items = [it for it in load_open_items() if _practice_in_scope(it.topic, scope)]
    if not items:
        return {
            "done": True,
            "mode": "open",
            "reason": "No open-ended questions in this scope yet.",
        }
    pool = [it for it in items if it.id != exclude] or items
    it = random.choice(pool)
    return {
        "done": False,
        "mode": "open",
        "remaining": len(items),
        "item": {
            "id": it.id,
            "type": "open",
            "topic": it.topic,
            "stem": it.prompt,
            "difficulty": it.difficulty,
            "retest": False,
        },
    }


def build_practice_payload(
    col: Collection, mode: str = "mcq", scope: str = "", exclude: str = ""
) -> dict:
    """Free-study: an unlimited stream of cards/questions from a chosen scope
    (whole test, a section, or one concept), for practice outside scheduled time.

    Flashcard practice is non-scheduling (a pure preview, so it never disrupts
    FSRS intervals); quiz + open-ended practice DO record (extra application
    evidence only helps mastery)."""
    mode = (mode or "mcq").lower()
    if mode == "flash":
        if get_demo_state(col).get("enabled"):
            return _demo_practice_flash(scope, exclude)
        return _practice_flash(col, scope, exclude)
    if mode == "open":
        return _practice_open(scope, exclude)
    return _practice_mcq(scope, exclude)


def dashboard_html() -> str:
    _ensure_ante_importable()
    import ante

    # The den (den.html) IS the app — there is no dashboard behind it.
    web = Path(ante.__file__).resolve().parent / "web"
    return (web / "den.html").read_text(encoding="utf-8")


DEMO_KEY = "ante_demo"


def get_demo_state(col: Collection) -> dict:
    data = col.get_config(DEMO_KEY, {})
    if not isinstance(data, dict):
        return {}
    return {
        "enabled": bool(data.get("enabled")),
        "day": int(data.get("day", 12)),
        "hour": int(data.get("hour", 9)),
    }


def set_demo_state(col: Collection, updates: dict) -> dict:
    data = get_demo_state(col)
    data.update(updates or {})
    # clamp the simulator's knobs
    try:
        from ante.demo import RUNWAY

        data["day"] = max(0, min(RUNWAY, int(data.get("day", 12))))
        data["hour"] = max(0, min(23, int(data.get("hour", 9))))
    except Exception:
        pass
    col.set_config(DEMO_KEY, data)
    return data


def read_asset(name: str) -> tuple[bytes | None, str]:
    """Read a bundled Ante web asset (den plates, city plates, portraits,
    dealer voice lines) by bare filename, with strict validation against path
    traversal. Returns (bytes, mimetype) or (None, "") if invalid/missing."""
    import mimetypes
    import re

    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", name or ""):
        return None, ""
    _ensure_ante_importable()
    import ante

    base = (Path(ante.__file__).resolve().parent / "web" / "assets").resolve()
    path = (base / name).resolve()
    try:
        path.relative_to(base)
    except ValueError:
        return None, ""
    if not path.is_file():
        return None, ""
    mime, _ = mimetypes.guess_type(str(path))
    return path.read_bytes(), (mime or "application/octet-stream")


# --------------------------------------------------------------------------- #
# Premade content: the MCAT deck ships with the app and self-seeds
# --------------------------------------------------------------------------- #

SEED_DECK_NAME = "MCAT"
# bumping the version re-seeds only topics that still have no cards (never
# duplicates), so shipping more premade cards later reaches existing users
SEED_VERSION = 1
SEED_DONE_KEY = "ante_seed_version"


def ensure_seed_deck(col: Collection) -> int:
    """Guarantee the premade MCAT deck is present so the den is never empty and
    the student never has to import anything — content is ready out of the box.

    Idempotent and safe to call on every home render: it no-ops once the current
    seed version is recorded, and it only adds cards for topics that have none
    (so a student who prunes cards is never re-seeded, and re-runs never
    duplicate). Runs on the main thread (adds notes to the live collection)."""
    try:
        if int(col.get_config(SEED_DONE_KEY, 0) or 0) >= SEED_VERSION:
            return 0
    except Exception:
        pass

    _ensure_ante_importable()
    import json as _json

    import ante
    from anki.decks import DeckId
    from ante.outline import load_outline

    basic = col.models.by_name("Basic")
    if basic is None:
        return 0

    try:
        path = Path(ante.__file__).resolve().parent / "data" / "seed_cards.json"
        cards_by_topic = _json.loads(path.read_text(encoding="utf-8")).get("cards", {})
    except Exception:
        cards_by_topic = {}
    if not cards_by_topic:
        return 0

    outline = load_outline()
    deck_id = DeckId(col.decks.id(SEED_DECK_NAME))
    added = 0
    for topic in outline.all_topics():
        pairs = cards_by_topic.get(topic) or []
        if not pairs:
            continue
        # skip topics that already carry cards, so re-seeds never duplicate
        try:
            if col.find_cards(f'"tag:{topic}"'):
                continue
        except Exception:
            pass
        added += _seed_topic_cards(col, basic, deck_id, topic, pairs)

    try:
        col.set_config(SEED_DONE_KEY, SEED_VERSION)
    except Exception:
        pass
    if added:
        try:
            ensure_study_deck(col)
        except Exception:
            pass
    return added


def _seed_topic_cards(col: Collection, basic, deck_id, topic: str, pairs) -> int:
    """Add one topic's premade (front, back) pairs; returns the count added."""
    added = 0
    for pair in pairs:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            continue
        note = col.new_note(basic)
        note["Front"] = str(pair[0])
        note["Back"] = str(pair[1])
        note.tags = [topic]
        col.add_note(note, deck_id)
        added += 1
    return added


# --------------------------------------------------------------------------- #
# Custom-view data (replacing Anki's stock Study / Add / Browse screens)
# --------------------------------------------------------------------------- #


def ensure_study_deck(col: Collection) -> None:
    """Point the scheduler at the deck that actually holds cards.

    The custom Study view can be opened straight from the nav without going
    through the 'Take your seat' CTA, so the current deck may still be the empty
    'Default'. Select the top-level deck with the most cards (incl. subdecks) and
    apply the points-at-stake review order so the Rust engine change is exercised.
    """
    from anki.decks import DeckId

    name_by_did = {d.id: d.name for d in col.decks.all_names_and_ids()}
    agg: dict[str, int] = {}
    for did, cnt in col.db.all("select did, count(*) from cards group by did"):
        name = name_by_did.get(did, "")
        top = name.split("::")[0] if name else ""
        if not top:
            continue
        agg[top] = agg.get(top, 0) + int(cnt)
    if not agg:
        return
    # prefer a real deck, but fall back to Default when it's the only deck
    # holding cards (fresh installs import straight into it)
    named = {k: v for k, v in agg.items() if k != "Default"}
    pool = named or agg
    best = max(pool, key=lambda k: pool[k])
    did = DeckId(col.decks.id(best))
    if col.decks.get_current_id() != did:
        col.decks.select(did)
    try:
        conf = col.decks.config_dict_for_deck_id(did)
        if conf.get("reviewOrder") != 13:  # REVIEW_CARD_ORDER_POINTS_AT_STAKE
            conf["reviewOrder"] = 13
            col.decks.update_config(conf)
    except Exception:
        pass


def _demo_study_card() -> dict:
    """A synthetic flashcard for demo mode (the collection has no deck), so the
    session shows the recall component alongside the application quizzes."""
    _ensure_ante_importable()
    import json as _json
    import random as _random

    import ante

    try:
        path = Path(ante.__file__).resolve().parent / "data" / "seed_cards.json"
        cards = _json.loads(path.read_text(encoding="utf-8")).get("cards", {})
        topic = _random.choice(list(cards))
        q, a = _random.choice(cards[topic])
        return {"id": "demo", "question": q, "answer": a, "topic": topic}
    except Exception:
        return {
            "id": "demo",
            "question": "Recall: what does an enzyme do to activation energy?",
            "answer": "It lowers the activation energy, speeding the reaction.",
            "topic": "mcat::bio_biochem::enzymes",
        }


def build_study_payload(col: Collection) -> dict:
    """Current queued card rendered for the custom Study view."""
    if get_demo_state(col).get("enabled"):
        return {
            "done": False,
            "counts": {"new": 99, "learn": 0, "review": 0},
            "card": _demo_study_card(),
        }
    ensure_study_deck(col)
    card = col.sched.getCard()
    try:
        new, lrn, rev = col.sched.counts()
    except Exception:
        new = lrn = rev = 0
    counts = {"new": int(new), "learn": int(lrn), "review": int(rev)}
    if card is None:
        return {"done": True, "counts": counts}
    card.start_timer()
    note = card.note()
    topic = next((t for t in note.tags if t.startswith("mcat::")), "")
    return {
        "done": False,
        "counts": counts,
        "card": {
            "id": card.id,
            "question": card.question(),
            "answer": card.answer(),
            "topic": topic,
        },
    }


FLASH_CONF_KEY = "ante_flash_confidence"
_FLASH_CONF_CAP = 1000


def get_flash_confidence(col: Collection) -> list:
    """Pre-flip flashcard confidence log: [[confidence, correct, ts, topic, ms], ...].

    ``confidence`` is what the student said before seeing the answer; ``correct``
    is whether they actually recalled it (ease >= Good). This is the raw material
    for flashcard calibration (the familiarity-illusion check)."""
    data = _get_acct(col, FLASH_CONF_KEY, [])
    return data if isinstance(data, list) else []


def answer_current_card(
    col: Collection,
    ease: int,
    confidence: float | None = None,
    elapsed_ms: int | None = None,
) -> None:
    if get_demo_state(col).get("enabled"):
        return  # demo cards are synthetic; nothing to record against the deck
    card = col.sched.getCard()
    if card is None:
        return
    rating = max(1, min(4, int(ease)))
    # ``col.sched.getCard`` restarts the card timer, but the think-time actually
    # happened in the web view. When it reports the elapsed time, rebase the
    # timer so the revlog — and the genuine-review effort gate that drives the
    # streak, the bookends and the consolidation counts — records the true
    # duration instead of the ~0ms between fetching and answering the card.
    if elapsed_ms is not None and int(elapsed_ms) >= 0:
        card.timer_started = time.time() - int(elapsed_ms) / 1000.0
    # capture the pre-flip confidence vs. actual recall BEFORE answering
    if confidence is not None:
        try:
            topic = next((t for t in card.note().tags if t.startswith("mcat::")), "")
        except Exception:
            topic = ""
        if elapsed_ms is not None and int(elapsed_ms) >= 0:
            ms = int(elapsed_ms)
        else:
            try:
                ms = int(card.time_taken())
            except Exception:
                ms = 0
        log = get_flash_confidence(col)
        log.append(
            [
                round(float(confidence), 3),
                1 if rating >= 3 else 0,
                time.time(),
                topic,
                ms,
            ]
        )
        _set_acct(col, FLASH_CONF_KEY, log[-_FLASH_CONF_CAP:])
    col.sched.answerCard(card, rating)  # type: ignore[arg-type]


def add_note_from_payload(col: Collection, payload: dict) -> dict:
    from anki.decks import DeckId
    from anki.models import NotetypeId

    if get_demo_state(col).get("enabled"):
        # demo is a showcase — never write into the real collection
        return {"ok": True, "demo": True, "note_id": 0}
    model = col.models.get(NotetypeId(int(payload["notetype_id"])))
    if not model:
        return {"ok": False, "error": "unknown notetype"}
    note = col.new_note(model)
    fields = payload.get("fields", [])
    for i, val in enumerate(fields):
        if i < len(note.fields):
            note.fields[i] = val
    tags = payload.get("tags", "").strip()
    if tags:
        note.tags = [t for t in tags.replace(",", " ").split() if t]
    col.add_note(note, DeckId(int(payload["deck_id"])))
    return {"ok": True, "note_id": note.id}


def map_untagged_notes(col: Collection) -> dict:
    """Seat a third-party deck onto the Circuit: for every note that carries no
    ``mcat::`` topic tag, ask the tagger for a confident, unambiguous topic and
    add that tag. Conservative by design — cards the tagger can't place are left
    untagged (honest). One undoable operation. Returns {tagged, skipped}."""
    if get_demo_state(col).get("enabled"):
        return {"ok": True, "demo": True, "tagged": 0, "skipped": 0}
    _ensure_ante_importable()
    from ante.tagger import match_topic

    deck_name_cache: dict[int, str] = {}

    def deck_name_for(note) -> str:
        cards = note.cards()
        if not cards:
            return ""
        did = cards[0].did
        if did not in deck_name_cache:
            deck_name_cache[did] = col.decks.name(did)
        return deck_name_cache[did]

    changed = []
    skipped = 0
    # only notes that aren't already seated at a table
    nids = col.find_notes("-tag:mcat::*")
    for nid in nids:
        note = col.get_note(nid)
        flds = note.fields
        front = _strip_html(flds[0]) if flds else ""
        back = _strip_html(flds[1]) if len(flds) > 1 else ""
        m = match_topic(
            front, back, deck_name=deck_name_for(note), tags=list(note.tags)
        )
        if m is None:
            skipped += 1
            continue
        note.tags.append(m.tag)
        changed.append(note)

    if changed:
        col.update_notes(changed)
    return {"ok": True, "tagged": len(changed), "skipped": skipped}


def _strip_html(s: str) -> str:
    import re

    s = re.sub(r"<[^>]+>", " ", s)
    s = (
        s.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )
    return re.sub(r"\s+", " ", s).strip()


# Global Ante theme applied to EVERY Anki web view (reviewer, toolbars,
# congrats, overview, dialogs) so the whole app matches the den: deep felt
# green and charcoal, warm cream ink, serif display + monospace labels, a
# single brass signal, flat ruled controls. Uses !important to override
# Anki's default + night-mode CSS.
ANTE_THEME_CSS = """
<style id="ante-theme">
:root {
  --an-felt: #0c1712; --an-panel: #12211a; --an-panel2: #182b21; --an-ink: #ece4cd;
  --an-soft: #a89f83; --an-faint: #6f6a56; --an-rule: #2a3c30; --an-signal: #c9a227;
  --an-good: #3f8f6b; --an-ember: #b5533c;
  --an-serif: "Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Georgia,serif;
  --an-mono: ui-monospace,"SF Mono","SFMono-Regular",Menlo,Consolas,monospace;
  --an-sans: -apple-system,system-ui,"Segoe UI",Roboto,Helvetica,sans-serif;
}
/* Recolor Anki's entire design-token system so every Svelte screen (editor,
   library, deck options, dialogs) adopts the felt-dark palette cohesively. */
:root, :root.night-mode {
  --fg: #ece4cd !important; --fg-subtle: #a89f83 !important; --fg-disabled: #6f6a56 !important;
  --fg-faint: #6f6a56 !important; --fg-link: #c9a227 !important;
  --canvas: #0c1712 !important; --canvas-elevated: #12211a !important; --canvas-inset: #12211a !important;
  --canvas-overlay: #12211a !important; --canvas-code: #182b21 !important; --canvas-glass: rgba(12,23,18,0.6) !important;
  --border: #2a3c30 !important; --border-subtle: #22332a !important; --border-strong: #ece4cd !important;
  --border-focus: #c9a227 !important;
  --button-bg: #12211a !important; --button-gradient-start: #12211a !important; --button-gradient-end: #12211a !important;
  --button-hover-border: #ece4cd !important; --button-disabled: rgba(42,60,48,0.5) !important;
  --button-primary-bg: #c9a227 !important; --button-primary-gradient-start: #c9a227 !important;
  --button-primary-gradient-end: #c9a227 !important;
  --accent-card: #c9a227 !important; --accent-note: #3f8f6b !important; --accent-danger: #b5533c !important;
  --highlight-bg: rgba(201,162,39,0.22) !important; --highlight-fg: #ece4cd !important;
  --selected-bg: rgba(201,162,39,0.16) !important; --selected-fg: #ece4cd !important;
  --scrollbar-bg: #2a3c30 !important; --scrollbar-bg-hover: #35493c !important; --scrollbar-bg-active: #40564a !important;
  --shadow: #060d09 !important; --shadow-subtle: #0a130f !important;
  --border-radius: 2px !important; --border-radius-medium: 3px !important; --border-radius-large: 3px !important;
  color-scheme: dark !important;
}
html, body {
  background: var(--an-felt) !important; color: var(--an-ink) !important;
  font-family: var(--an-sans) !important;
}
h1, h2, h3, h4, .title { font-family: var(--an-serif) !important; color: var(--an-ink) !important; letter-spacing: -0.01em; font-weight: 700; }
a { color: var(--an-signal) !important; text-decoration: none; }
a:hover { color: var(--an-signal) !important; text-decoration: underline; }
::selection { background: var(--an-signal); color: var(--an-felt); }

/* ---- reviewer card ---- */
.card { background: var(--an-felt) !important; color: var(--an-ink) !important; font-family: var(--an-serif) !important; font-size: 22px; line-height: 1.5; }
#qa, #qa_box { background: var(--an-felt) !important; color: var(--an-ink) !important; }
hr#answer { border: none !important; border-top: 1px solid var(--an-rule) !important; margin: 22px auto !important; max-width: 720px; }

/* ---- buttons -> flat house style ---- */
button, .btn, input[type=button], input[type=submit] {
  font-family: var(--an-mono) !important; text-transform: uppercase; letter-spacing: 0.06em;
  font-size: 12px !important; background: var(--an-panel) !important; color: var(--an-ink) !important;
  border: 1px solid var(--an-ink) !important; border-radius: 0 !important; padding: 7px 13px !important;
  box-shadow: none !important; transition: background .12s, color .12s; cursor: pointer;
}
button:hover, .btn:hover { background: var(--an-signal) !important; color: var(--an-felt) !important; border-color: var(--an-signal) !important; }
button[data-ease] { border-bottom: 3px solid var(--an-signal) !important; }
button[data-ease="1"] { border-bottom-color: var(--an-ember) !important; }
button[data-ease="2"] { border-bottom-color: var(--an-soft) !important; }
button[data-ease="3"] { border-bottom-color: var(--an-good) !important; }
button[data-ease="4"] { border-bottom-color: var(--an-signal) !important; }
#ansbut { border-bottom: 3px solid var(--an-ink) !important; }

/* ---- inputs / selects / textareas ---- */
input, textarea, select, .editable, [contenteditable] {
  background: var(--an-panel) !important; color: var(--an-ink) !important;
  border: 1px solid var(--an-rule) !important; border-radius: 0 !important;
  font-family: var(--an-sans) !important; padding: 8px 10px !important;
}
input:focus, textarea:focus, select:focus, [contenteditable]:focus {
  outline: none !important; border-color: var(--an-signal) !important;
  box-shadow: inset 0 -2px 0 var(--an-signal) !important;
}

/* ---- top navigation bar ---- */
.header, .an-header {
  display: flex !important; align-items: center !important; gap: 22px !important;
  height: 54px !important; padding: 0 22px !important;
  background: var(--an-panel) !important; border-bottom: 2px solid var(--an-signal) !important;
}
.an-brand {
  font-family: var(--an-serif) !important; font-weight: 800; font-size: 21px;
  letter-spacing: 0.14em; display: flex; align-items: center; gap: 9px;
  cursor: pointer; color: var(--an-ink) !important; user-select: none;
}
.an-tick { width: 6px; height: 22px; background: var(--an-signal); display: inline-block; }
.toolbar { display: flex !important; gap: 4px !important; background: transparent !important; box-shadow: none !important; border: none !important; padding: 0 !important; }
.hitem {
  font-family: var(--an-mono) !important; color: var(--an-soft) !important;
  text-transform: uppercase; letter-spacing: 0.08em; font-size: 12px !important;
  padding: 9px 14px !important; border-bottom: 3px solid transparent !important;
}
.hitem:hover { color: var(--an-ink) !important; background: var(--an-panel2) !important; }
#sync-spinner { filter: saturate(0) invert(0.85); }
.stattxt, .stat, #time { font-family: var(--an-mono) !important; color: var(--an-soft) !important; }

/* ---- bottom bar ---- */
#outer, #header, #innertable { background: var(--an-panel) !important; border-top: 1px solid var(--an-rule) !important; }

/* ---- tables (library / browser) ---- */
table.fmenu, .sidebar, .card-list { background: var(--an-felt) !important; }
th { font-family: var(--an-mono) !important; text-transform: uppercase; letter-spacing: 0.06em;
  font-size: 10.5px !important; color: var(--an-soft) !important; border-bottom: 2px solid var(--an-signal) !important; }
td { border-bottom: 1px solid var(--an-rule) !important; }
tr:hover td { background: color-mix(in srgb, var(--an-signal) 9%, transparent) !important; }

/* ---- editor / add cards ---- */
.editor-field, .field, .rich-text-editable, .plain-text-editable {
  background: var(--an-panel) !important; color: var(--an-ink) !important;
  border: 1px solid var(--an-rule) !important; font-family: var(--an-serif) !important;
}
.label-name, .field-name { font-family: var(--an-mono) !important; text-transform: uppercase;
  letter-spacing: 0.06em; font-size: 10.5px !important; color: var(--an-soft) !important; }
.tag-editor, .tags { font-family: var(--an-mono) !important; }

/* ---- congrats / finished ---- */
.congrats, .congrats-outer { background: var(--an-felt) !important; color: var(--an-ink) !important; font-family: var(--an-serif) !important; }

/* ---- scrollbars ---- */
::-webkit-scrollbar { width: 11px; height: 11px; }
::-webkit-scrollbar-thumb { background: var(--an-rule); border: 3px solid var(--an-felt); }
::-webkit-scrollbar-track { background: var(--an-felt); }
</style>
"""


def on_webview_will_set_content(web_content, context) -> None:
    """gui_hooks.webview_will_set_content subscriber: theme every Anki web view
    with the Ante look, so the reviewer, toolbars, congrats and dialogs match
    the home screen. The home's own main content (DeckBrowser) already carries the
    richer Ante styles, so we skip it there to avoid clobbering its CTA."""
    if type(context).__name__ == "DeckBrowser":
        return
    web_content.head += ANTE_THEME_CSS


# Felt-dark palette applied to Anki's NATIVE Qt color tokens (drives the Qt
# stylesheet for the library table, menus, dialogs, buttons, combos, etc.).
_NATIVE_COLORS = {
    "FG": "#ece4cd",
    "FG_SUBTLE": "#a89f83",
    "FG_DISABLED": "#6f6a56",
    "FG_FAINT": "#6f6a56",
    "FG_LINK": "#c9a227",
    "CANVAS": "#0c1712",
    "CANVAS_ELEVATED": "#12211a",
    "CANVAS_INSET": "#12211a",
    "CANVAS_OVERLAY": "#12211a",
    "CANVAS_CODE": "#182b21",
    "BORDER": "#2a3c30",
    "BORDER_SUBTLE": "#22332a",
    "BORDER_STRONG": "#ece4cd",
    "BORDER_FOCUS": "#c9a227",
    "BUTTON_BG": "#12211a",
    "BUTTON_GRADIENT_START": "#12211a",
    "BUTTON_GRADIENT_END": "#12211a",
    "BUTTON_HOVER_BORDER": "#ece4cd",
    "BUTTON_PRIMARY_BG": "#c9a227",
    "BUTTON_PRIMARY_GRADIENT_START": "#c9a227",
    "BUTTON_PRIMARY_GRADIENT_END": "#c9a227",
    "ACCENT_CARD": "#c9a227",
    "ACCENT_NOTE": "#3f8f6b",
    "ACCENT_DANGER": "#b5533c",
    "SELECTED_BG": "#2e4033",
    "SELECTED_FG": "#ece4cd",
    "HIGHLIGHT_BG": "#2e4033",
    "HIGHLIGHT_FG": "#ece4cd",
    "SCROLLBAR_BG": "#2a3c30",
    "SCROLLBAR_BG_HOVER": "#35493c",
    "SCROLLBAR_BG_ACTIVE": "#40564a",
    "SHADOW": "#060d09",
    "SHADOW_SUBTLE": "#0a130f",
}


_recoloring = False


def _recolor_native() -> None:
    """Override Anki's native color tokens with the Ante palette (both light
    and dark) and rebuild the Qt stylesheet, so native widgets match the web.
    Guarded against re-entrancy since apply_style() re-fires theme_did_change."""
    global _recoloring
    if _recoloring:
        return
    _recoloring = True
    try:
        from aqt import colors as C
        from aqt.theme import theme_manager

        for name, val in _NATIVE_COLORS.items():
            token = getattr(C, name, None)
            if isinstance(token, dict):
                token["light"] = val
                token["dark"] = val
        theme_manager.apply_style()
    except Exception:
        pass
    finally:
        _recoloring = False


def register_theme() -> None:
    from aqt import gui_hooks

    gui_hooks.webview_will_set_content.append(on_webview_will_set_content)
    _recolor_native()
    # keep native colours applied if the OS/theme toggles
    gui_hooks.theme_did_change.append(_recolor_native)


def dashboard_body() -> str:
    """The Ante UI as a fragment (``<style>`` + body markup + ``<script>``)
    for injection into Anki's main web view via AnkiWebView.stdHtml. This renders
    Ante natively as the app's home screen rather than navigating to a URL,
    which the main view does not retain."""
    import json as _json

    full = dashboard_html()
    style = ""
    s = full.find("<style>")
    e = full.find("</style>")
    if s != -1 and e != -1:
        style = full[s : e + len("</style>")]
    b = full.find("<body>")
    be = full.rfind("</body>")
    body_inner = full[b + len("<body>") : be] if b != -1 and be != -1 else full
    # only the in-app page carries the token; the raw /_anki/ante shell must
    # not leak it to other local processes
    token = f"<script>window.ANTE_TOKEN={_json.dumps(ANTE_TOKEN)};</script>"
    return style + token + body_inner
