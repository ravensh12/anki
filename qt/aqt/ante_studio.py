# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Qt bridge for the generative Studio: the Palace, the Viva, and asset serving.

Keeps the collection-facing plumbing for the media features out of the already
large ante.py: instantiating the Studio against a per-account cache under
the collection media dir, extracting leeches from the revlog, commissioning
palace scenes on a worker thread, running Viva sessions, and reading generated
assets back for the web view.

All heavy generation runs off the UI thread (mw.taskman); the web app polls the
payload's ``studio`` block for progress. Everything degrades to the offline
engraver when no provider keys are set, so the city is never blank.
"""

from __future__ import annotations

from pathlib import Path

from anki.collection import Collection
from aqt.ante import (
    _ensure_ante_importable,
    _get_acct,
    _set_acct,
    current_account_id,
    get_demo_state,
    get_open_responses,
)

PALACE_KEY = "ante_palace"
VIVA_KEY = "ante_viva"
_VIVA_ACTIVE_KEY = "ante_viva_active"
_LEECH_SCAN_CAP = 60
_EVENTS_CAP = 40

# guard so we don't launch overlapping background commissions
_commissioning: set[str] = set()


# --------------------------------------------------------------------------- #
# the Studio instance (per account, under the collection media dir)
# --------------------------------------------------------------------------- #


def studio_dir(col: Collection) -> Path:
    acct = current_account_id(col) or "guest"
    base = Path(col.media.dir()) / "_ante_studio" / acct
    base.mkdir(parents=True, exist_ok=True)
    return base


def studio_for(col: Collection, force_offline: bool = False):
    _ensure_ante_importable()
    from ante.ai.studio import Studio
    from ante.config import CONFIG

    return Studio(studio_dir(col), cfg=CONFIG, force_offline=force_offline)


def studio_status(col: Collection) -> dict:
    try:
        return studio_for(col).status()
    except Exception:
        return {"providers": {"offline_only": True}, "assets": {}, "budget": {}}


# --------------------------------------------------------------------------- #
# palace persistence
# --------------------------------------------------------------------------- #


def get_palace(col: Collection) -> list[dict]:
    data = _get_acct(col, PALACE_KEY, [])
    return data if isinstance(data, list) else []


def _save_palace(col: Collection, records: list[dict]) -> None:
    _set_acct(col, PALACE_KEY, records)


def palace_index(col: Collection) -> dict[int, dict]:
    from ante.palace import index_by_card

    return index_by_card(get_palace(col))


def palace_by_topic(col: Collection) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in get_palace(col):
        t = str(r.get("topic", ""))
        out[t] = out.get(t, 0) + 1
    return out


# --------------------------------------------------------------------------- #
# leech extraction from the collection
# --------------------------------------------------------------------------- #


def _card_topic(note) -> str:
    return next((t for t in note.tags if t.startswith("mcat::")), "")


def extract_leeches(col: Collection, limit: int = _LEECH_SCAN_CAP) -> list:
    """Pull leech candidates from the revlog: cards with the most lapses, with
    their rendered text and (when FSRS is on) memory-state retrievability."""
    _ensure_ante_importable()
    from ante.config import CONFIG
    from ante.palace import pick_leeches

    rows = col.db.all(
        "select id, lapses from cards where lapses >= ? order by lapses desc limit ?",
        int(CONFIG.palace_min_lapses),
        int(limit),
    )
    cards: list[dict] = []
    for cid, lapses in rows:
        try:
            card = col.get_card(cid)
            note = card.note()
        except Exception:
            continue
        topic = _card_topic(note)
        if not topic:
            continue
        r = _retrievability(col, card)
        cards.append(
            {
                "card_id": int(cid),
                "front": card.question(),
                "back": card.answer(),
                "topic": topic,
                "lapses": int(lapses),
                "retrievability": r,
            }
        )
    return pick_leeches(cards, existing_card_ids=set(palace_index(col)), cfg=CONFIG)


def _retrievability(col: Collection, card) -> float:
    """Best-effort FSRS retrievability (0..1); 0 when unavailable (treated as a
    weak card by the picker, which is the safe default for a leech)."""
    try:
        state = getattr(card, "memory_state", None)
        if not state or not state.stability:
            return 0.0
        elapsed = max(0, col.sched.today - card.due) if card.due else 0
        return float(0.9 ** (elapsed / max(1.0, state.stability)))
    except Exception:
        return 0.0


# --------------------------------------------------------------------------- #
# commissioning (background)
# --------------------------------------------------------------------------- #


def commission_palace_async(mw, count: int = 3) -> None:
    """Commission up to ``count`` new palace scenes off the UI thread."""
    col = mw.col
    if col is None or get_demo_state(col).get("enabled"):
        return
    _ensure_ante_importable()
    from ante.config import CONFIG

    if not CONFIG.studio_enabled:
        return
    acct = current_account_id(col) or "guest"
    if acct in _commissioning:
        return
    if len(get_palace(col)) >= CONFIG.palace_max_assets:
        return
    _commissioning.add(acct)

    def work() -> list[dict]:
        from ante.ai.provider import get_provider

        leeches = extract_leeches(col)[:count]
        if not leeches:
            return []
        studio = studio_for(col)
        provider = get_provider()
        return [_commission_one(leech, studio, provider) for leech in leeches]

    def done(fut) -> None:
        _commissioning.discard(acct)
        try:
            new_records = fut.result()
        except Exception:
            new_records = []
        if new_records:
            records = get_palace(col) + [r for r in new_records if r]
            _save_palace(col, records)
            _refresh_webview(mw)

    mw.taskman.run_in_background(work, done)


def _commission_one(leech, studio, provider):
    from ante.palace import commission

    try:
        return commission(leech, studio, provider)
    except Exception:
        return None


def regenerate_scene(mw, card_id: int) -> None:
    """Drop a card's palace scene and commission a fresh one (thumbs-down)."""
    col = mw.col
    if col is None:
        return
    records = [r for r in get_palace(col) if int(r.get("card_id", -1)) != int(card_id)]
    _save_palace(col, records)
    commission_palace_async(mw, count=1)


# --------------------------------------------------------------------------- #
# today's study events (Dream Seed reel)
# --------------------------------------------------------------------------- #


def events_today(col: Collection) -> list[dict]:
    """Today's genuine reviews as reel-ready events (weakest retrievals first)."""
    _ensure_ante_importable()
    from ante.config import CONFIG

    day_start_ms = (col.sched.day_cutoff - 86400) * 1000
    rows = col.db.all(
        "select cid, ease, time from revlog where id >= ? and ease > 0 "
        "and time >= ? order by id desc limit ?",
        day_start_ms,
        CONFIG.streak_min_response_ms,
        int(_EVENTS_CAP),
    )
    out: list[dict] = []
    seen: set[int] = set()
    for cid, ease, ms in rows:
        if cid in seen:
            continue
        seen.add(cid)
        try:
            card = col.get_card(cid)
            note = card.note()
        except Exception:
            continue
        topic = _card_topic(note)
        if not topic:
            continue
        out.append(
            {
                "card_id": int(cid),
                "topic": topic,
                "front": card.question(),
                "back": card.answer(),
                "correct": int(ease) >= 2,
                "elapsed_ms": int(ms),
            }
        )
    return out


# --------------------------------------------------------------------------- #
# the Viva
# --------------------------------------------------------------------------- #


def get_viva_log(col: Collection) -> list[dict]:
    if get_demo_state(col).get("enabled"):
        last = col.get_config("ante_demo_viva_last", None)
        return [last] if isinstance(last, dict) else []
    data = _get_acct(col, VIVA_KEY, [])
    return data if isinstance(data, list) else []


# Demo Vivas run the same pure grading machinery but persist to a transient
# collection-level key (never the real account log), so the tour can defend a
# topic without touching a signed-in user's progress.
_DEMO_VIVA_KEY = "ante_demo_viva_active"


def get_active_viva(col: Collection) -> dict | None:
    if get_demo_state(col).get("enabled"):
        data = col.get_config(_DEMO_VIVA_KEY, None)
        return data if isinstance(data, dict) else None
    data = _get_acct(col, _VIVA_ACTIVE_KEY, None)
    return data if isinstance(data, dict) else None


def _set_active_viva(col: Collection, session: dict | None) -> None:
    if get_demo_state(col).get("enabled"):
        col.set_config(_DEMO_VIVA_KEY, session)
        return
    _set_acct(col, _VIVA_ACTIVE_KEY, session)


def start_viva(col: Collection, topic: str) -> dict | None:
    _ensure_ante_importable()
    from ante.config import CONFIG
    from ante.viva import already_examined_today
    from ante.viva import start_viva as _start

    demo = get_demo_state(col).get("enabled")
    # demo has no real open-log; a fresh empty one keeps grading pure
    open_responses = {} if demo else get_open_responses(col)
    if not demo and already_examined_today(get_viva_log(col), topic):
        return {"blocked": "already examined today"}
    session = _start(topic, open_responses, cfg=CONFIG)
    if session is None:
        return None
    _set_active_viva(col, session)
    return session


def answer_viva(col: Collection, answer: str) -> dict | None:
    """Grade one turn of the active viva; persist + feed the open log on close."""
    _ensure_ante_importable()
    from ante.ai.provider import get_provider
    from ante.config import CONFIG
    from ante.viva import FAILED, PASSED, records_for_log, submit_answer

    session = get_active_viva(col)
    if session is None:
        return None
    provider = get_provider()
    session = submit_answer(session, answer, provider=provider, cfg=CONFIG)

    # a voice line rendered for an earlier round must not replay over the new
    # probe; drop it unless it still matches what Sahir is asking
    say = session.get("say")
    if say and say.get("text") != (session.get("ask") or session.get("opening")):
        session.pop("say", None)

    # demo: grade for real (pure logic) but never write to the account log
    if get_demo_state(col).get("enabled"):
        _set_active_viva(col, session if session.get("status") == "open" else None)
        if session.get("status") in (PASSED, FAILED):
            col.set_config("ante_demo_viva_last", session)
        return session

    if session.get("status") in (PASSED, FAILED):
        # append to the viva history
        log = get_viva_log(col)
        log.append(session)
        _set_acct(col, VIVA_KEY, log[-100:])
        _set_active_viva(col, None)
        # a finished viva is application evidence: feed the open-response log so
        # mastery/comprehension/readiness update through the same pipe.
        from aqt.ante import OPEN_RESPONSES_KEY

        responses = get_open_responses(col)
        for item_id, score, ts in records_for_log(session):
            responses.setdefault(item_id, [])
            responses[item_id].append([float(score), float(ts)])
        _set_acct(col, OPEN_RESPONSES_KEY, responses)
    else:
        _set_active_viva(col, session)
    return session


def _render_verdict_media(col: Collection, session: dict) -> dict:
    """Render the dealer's spoken verdict (+ optional talking-head clip)."""
    from ante.viva import VERDICT_SPECS, verdict_speech_text

    studio = studio_for(col)
    line = verdict_speech_text(session)
    speech = studio.speech(line, persona="dealer") if line else None
    spec = VERDICT_SPECS.get(session["status"])
    still = studio.still(spec) if spec else None
    clip = None
    if still and speech:
        clip = studio.talking_head(still, speech, prompt=spec.get("motion", ""))
    return {
        "still": still.filename if still else None,
        "speech": speech.filename if speech else None,
        "clip": clip.filename if clip else None,
    }


def set_viva_live_voice(col: Collection, on: bool) -> None:
    """Mark the active examination as running over the live (Realtime) table,
    so the turn-based TTS pipeline stays quiet while Sahir speaks in person."""
    session = get_active_viva(col)
    if not session:
        return
    session["live_voice"] = bool(on)
    _set_active_viva(col, session)


def _pending_say_line(session: dict | None) -> str | None:
    """The line Sahir should voice next, or None when no fresh speech is due
    (no open session, the live table speaks for itself, or the current line
    already carries its clip)."""
    if not session or session.get("status") != "open":
        return None
    if session.get("live_voice"):
        # the live table: Sahir already speaks every line himself
        return None
    text = str(session.get("ask") or session.get("opening") or "")
    say = session.get("say") or {}
    if not text or (say.get("text") == text and say.get("speech")):
        return None
    return text


def _say_still_current(active: dict | None, text: str) -> bool:
    """Is ``text`` still the line being asked? The student may have answered
    while the clip rendered — a stale line must stay quiet."""
    if not active or active.get("status") != "open":
        return False
    return str(active.get("ask") or active.get("opening") or "") == text


def commission_say_async(mw) -> None:
    """Give Sahir his voice mid-examination: render speech for the line he is
    currently asking (the opening, or the latest probe) and attach it to the
    active session as ``say``. Cached by content, so a pre-warmed demo topic
    (ante/tools/warm_backroom.py) plays instantly; a cold line costs one TTS
    call in the background and never blocks the exam."""
    col = mw.col
    if col is None or get_demo_state(col).get("enabled"):
        return
    text = _pending_say_line(get_active_viva(col))
    if not text:
        return
    _ensure_ante_importable()

    def work() -> dict | None:
        speech = studio_for(col).speech(text, persona="dealer")
        return {"text": text, "speech": speech.filename} if speech else None

    def done(fut) -> None:
        try:
            say = fut.result()
        except Exception:
            say = None
        if not say:
            return
        active = get_active_viva(col)
        if not _say_still_current(active, say["text"]):
            return
        active["say"] = say
        _set_active_viva(col, active)
        _refresh_webview(mw)

    mw.taskman.run_in_background(work, done)


def commission_verdict_async(mw, session: dict) -> None:
    """Render the verdict media in the background so a won/lost heads-up gains
    Sahir's voice without blocking the UI."""
    col = mw.col
    if col is None or session.get("status") not in ("passed", "failed"):
        return
    _ensure_ante_importable()

    def done(fut) -> None:
        try:
            media = fut.result()
        except Exception:
            media = None
        if media:
            active = get_viva_log(col)
            if active:
                active[-1]["media"] = media
                _set_acct(col, VIVA_KEY, active)
                _refresh_webview(mw)

    mw.taskman.run_in_background(lambda: _render_verdict_media(col, session), done)


# --------------------------------------------------------------------------- #
# asset serving (from the studio cache dir, not the bundled web/assets)
# --------------------------------------------------------------------------- #


def read_studio_asset(col: Collection, name: str) -> tuple[bytes | None, str]:
    import mimetypes
    import re

    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", name or ""):
        return None, ""
    base = studio_dir(col).resolve()
    path = (base / name).resolve()
    try:
        path.relative_to(base)
    except ValueError:
        return None, ""
    if not path.is_file():
        return None, ""
    mime, _ = mimetypes.guess_type(str(path))
    if path.suffix == ".svg":
        mime = "image/svg+xml"
    return path.read_bytes(), (mime or "application/octet-stream")


def _refresh_webview(mw) -> None:
    try:
        mw.taskman.run_on_main(
            lambda: mw.web.eval("window.anStudioReady && anStudioReady();")
        )
    except Exception:
        pass
