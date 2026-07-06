# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for the Qt-side Ante bridge (qt/aqt/ante.py).

These cover the payload builders and account plumbing the den depends on:
study/quiz/diagnostic payloads, note adding, per-account namespacing with
legacy migration, demo-mode gating, exam-date recalibration, and the per-boot
endpoint token."""

from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta

from anki.collection import Collection
from aqt.ante import (
    ANTE_TOKEN,
    SEED_VERSION,
    _akey,
    add_note_from_payload,
    answer_current_card,
    apply_recalibration,
    build_auth_payload,
    build_diagnostic_payload,
    build_quiz_payload,
    build_study_payload,
    clear_game_state,
    current_account_id,
    dashboard_body,
    dashboard_html,
    ensure_seed_deck,
    finish_diagnostic,
    get_demo_state,
    get_diagnostic,
    get_flash_confidence,
    get_game_state,
    get_perf_responses,
    get_profile,
    map_untagged_notes,
    record_quiz_answer,
    save_game_state,
    set_demo_state,
    set_profile,
    sign_in_email,
    sign_out,
    switch_account,
)


def _empty_col() -> Collection:
    (fd, path) = tempfile.mkstemp(suffix=".anki2")
    os.close(fd)
    os.unlink(path)
    return Collection(path)


def _add_note(
    col: Collection, front: str, back: str, topic: str | None, deck: str = "MCAT"
) -> int:
    from anki.decks import DeckId

    model = col.models.by_name("Basic")
    assert model is not None
    note = col.new_note(model)
    note.fields[0] = front
    note.fields[1] = back
    if topic:
        note.tags = [topic]
    did = DeckId(col.decks.id(deck))
    col.add_note(note, did)
    return note.cards()[0].id


# --------------------------------------------------------------------------- #
# Accounts: namespacing, legacy migration, switching
# --------------------------------------------------------------------------- #


def test_auth_namespacing_and_legacy_migration() -> None:
    col = _empty_col()
    assert build_auth_payload(col)["signed_in"] is False
    assert current_account_id(col) is None

    # pre-account ("guest") data exists before the first sign-in
    col.set_config("ante_exam_date", "2099-09-01")

    first = sign_in_email(col, "Alice@Example.com", "Alice")
    assert build_auth_payload(col)["signed_in"] is True
    assert current_account_id(col) == first["id"]
    # keys are namespaced per account
    assert _akey(col, "ante_exam_date") == f"acct::{first['id']}::ante_exam_date"
    # the first account inherits the legacy pre-account data
    assert col.get_config(_akey(col, "ante_exam_date"), None) == "2099-09-01"

    # a second account starts clean (no migration after the first)
    second = sign_in_email(col, "bob@example.com", "Bob")
    assert current_account_id(col) == second["id"]
    assert col.get_config(_akey(col, "ante_exam_date"), None) is None

    # switching restores the first account's namespace
    switch_account(col, first["id"])
    assert current_account_id(col) == first["id"]
    assert col.get_config(_akey(col, "ante_exam_date"), None) == "2099-09-01"

    # signing out drops to the guest namespace without deleting anything
    sign_out(col)
    assert current_account_id(col) is None
    assert _akey(col, "ante_exam_date") == "acct::guest::ante_exam_date"
    payload = build_auth_payload(col)
    assert payload["signed_in"] is False
    assert {a["id"] for a in payload["accounts"]} == {first["id"], second["id"]}
    col.close()


# --------------------------------------------------------------------------- #
# Profile + exam-date recalibration
# --------------------------------------------------------------------------- #


def test_profile_roundtrip_and_recalibration_applies_to_deck() -> None:
    col = _empty_col()
    _add_note(col, "q", "a", "mcat::bio_biochem::enzymes")

    exam = (date.today() + timedelta(days=30)).isoformat()
    saved = set_profile(col, {"exam_date": exam, "target_score": 515})
    assert saved["exam_date"] == exam
    assert saved["target_score"] == 515
    prof = get_profile(col)
    assert prof.exam_date == exam

    result = apply_recalibration(col)
    assert result["applied"] is True
    assert result["days_remaining"] == 30
    # no card may be scheduled past test day
    assert result["max_interval_days"] == 30

    conf = col.decks.config_dict_for_deck_id(col.decks.get_current_id())
    assert conf["maximumReviewInterval"] == 30
    assert abs(conf["desiredRetention"] - result["desired_retention"]) < 1e-6
    # retention stays a sane FSRS value
    assert 0.7 <= conf["desiredRetention"] <= 0.99
    col.close()


def test_recalibration_without_exam_date_is_a_noop() -> None:
    col = _empty_col()
    result = apply_recalibration(col)
    assert result == {"applied": False, "reason": "no exam date"}
    col.close()


# --------------------------------------------------------------------------- #
# Study payload + answering
# --------------------------------------------------------------------------- #


def test_build_study_payload_empty_collection_is_done() -> None:
    col = _empty_col()
    payload = build_study_payload(col)
    assert payload["done"] is True
    assert payload["counts"] == {"new": 0, "learn": 0, "review": 0}
    col.close()


def test_build_study_payload_serves_card_and_answer_records() -> None:
    col = _empty_col()
    _add_note(
        col, "What is Km?", "Substrate conc. at half Vmax", "mcat::bio_biochem::enzymes"
    )

    payload = build_study_payload(col)
    assert payload["done"] is False
    assert payload["card"]["topic"] == "mcat::bio_biochem::enzymes"
    assert "Km" in payload["card"]["question"]
    # the study deck was pointed at the deck with cards, in points-at-stake order
    conf = col.decks.config_dict_for_deck_id(col.decks.get_current_id())
    assert conf["reviewOrder"] == 13  # REVIEW_CARD_ORDER_POINTS_AT_STAKE

    answer_current_card(col, 3, confidence=0.85)
    assert (col.db.scalar("select count() from revlog") or 0) == 1
    log = get_flash_confidence(col)
    assert len(log) == 1
    assert log[0][0] == 0.85  # said "Raise"
    assert log[0][1] == 1  # and was right (ease >= Good)
    col.close()


def test_answer_records_real_think_time_from_the_web_view() -> None:
    col = _empty_col()
    _add_note(col, "q", "a", "mcat::bio_biochem::enzymes")
    build_study_payload(col)  # serves + starts timing the card

    # The web view measures the real think-time; getCard() restarting the card
    # timer on the answer request must not clobber it, or the revlog records ~0ms
    # and the genuine-review effort gate (streak / bookends) rejects every hand.
    answer_current_card(col, 3, confidence=0.8, elapsed_ms=4200)

    recorded = col.db.scalar("select time from revlog order by id desc limit 1") or 0
    assert 4200 <= recorded < 6000  # the real duration, not the getCard reset
    assert get_flash_confidence(col)[-1][4] == 4200  # the flash log carries it too
    col.close()


# --------------------------------------------------------------------------- #
# Quiz payload + demo gating
# --------------------------------------------------------------------------- #


def test_build_quiz_payload_serves_application_item() -> None:
    col = _empty_col()
    payload = build_quiz_payload(col)
    item = payload["item"]
    assert item is not None
    assert item["type"] in ("mcq", "open")
    assert item["id"] and item["stem"] and item["topic"].startswith("mcat::")
    if item["type"] == "mcq":
        assert 0 <= item["correct_index"] < len(item["choices"])
    col.close()


def test_record_quiz_answer_persists_but_demo_never_does() -> None:
    from ante.performance_items import load_items

    col = _empty_col()
    item = load_items()[0]
    record_quiz_answer(
        col, item.id, item.correct_index, confidence=0.9, elapsed_ms=1200
    )
    responses = get_perf_responses(col)
    assert item.id in responses
    assert responses[item.id][0][0] == item.correct_index

    # demo answers are throwaway: nothing new lands in the real account
    set_demo_state(col, {"enabled": True})
    assert get_demo_state(col)["enabled"] is True
    record_quiz_answer(col, item.id, item.correct_index)
    set_demo_state(col, {"enabled": False})
    assert len(get_perf_responses(col)[item.id]) == 1
    col.close()


def test_demo_state_clamps_its_knobs() -> None:
    from ante.demo import RUNWAY

    col = _empty_col()
    state = set_demo_state(col, {"enabled": True, "day": 10_000, "hour": 99})
    assert state["day"] == RUNWAY
    assert state["hour"] == 23
    state = set_demo_state(col, {"day": -5, "hour": -1})
    assert state["day"] == 0
    assert state["hour"] == 0
    col.close()


# --------------------------------------------------------------------------- #
# Diagnostic: determinism, finishing, demo gating
# --------------------------------------------------------------------------- #


def test_diagnostic_payload_is_deterministic_and_finishable() -> None:
    col = _empty_col()
    first = build_diagnostic_payload(col)
    second = build_diagnostic_payload(col)
    assert first["form"] == second["form"]  # deterministic per seed
    assert first["taken"] is False
    assert first["summary"] is None

    finish_diagnostic(col, skipped=False)
    status = get_diagnostic(col)
    assert status["taken_at"] > 0
    assert status["skipped"] is False
    assert status["item_ids"]
    assert build_diagnostic_payload(col)["taken"] is True
    col.close()


def test_finish_diagnostic_is_gated_in_demo_mode() -> None:
    col = _empty_col()
    set_demo_state(col, {"enabled": True})
    assert finish_diagnostic(col, skipped=False) == {}
    set_demo_state(col, {"enabled": False})
    # the real account was never stamped
    assert get_diagnostic(col) == {}
    assert build_diagnostic_payload(col)["taken"] is False
    col.close()


# --------------------------------------------------------------------------- #
# In-progress game snapshots: leaving a game never restarts it
# --------------------------------------------------------------------------- #


def test_game_state_roundtrips_and_clears_per_game() -> None:
    col = _empty_col()
    assert get_game_state(col) == {}

    save_game_state(col, "quiz", {"n": 3, "total": 6, "correct": 2})
    save_game_state(col, "fl1", {"answers": {"cp_1": 1}, "open": 0})
    state = get_game_state(col)
    assert state["quiz"] == {"n": 3, "total": 6, "correct": 2}
    assert state["fl1"]["answers"] == {"cp_1": 1}

    # finishing one game clears only that game's resume point
    clear_game_state(col, "quiz")
    state = get_game_state(col)
    assert "quiz" not in state
    assert "fl1" in state
    clear_game_state(col, "never_saved")  # absent id is a quiet no-op
    col.close()


def test_game_state_is_scoped_per_account() -> None:
    col = _empty_col()
    # pre-account ("legacy") data migrates into the first account on sign-in
    col.set_config("ante_game_state", {"buyin": {"si": 1, "ii": 2, "answered": 12}})
    sign_in_email(col, "alice@example.com", "Alice")
    assert get_game_state(col)["buyin"]["answered"] == 12

    # a second account starts with no resume points of its own
    sign_in_email(col, "bob@example.com", "Bob")
    assert get_game_state(col) == {}
    save_game_state(col, "quiz", {"n": 5, "total": 6, "correct": 4})

    # switching back restores each player's own games
    auth = build_auth_payload(col)
    alice = next(a["id"] for a in auth["accounts"] if a["name"] == "Alice")
    switch_account(col, alice)
    assert "quiz" not in get_game_state(col)
    assert get_game_state(col)["buyin"]["si"] == 1

    # signing out drops to the guest namespace, which has its own games
    sign_out(col)
    assert get_game_state(col) == {}
    col.close()


def test_game_state_is_gated_in_demo_mode() -> None:
    col = _empty_col()
    save_game_state(col, "quiz", {"n": 2, "total": 6, "correct": 1})

    set_demo_state(col, {"enabled": True})
    save_game_state(col, "fl1", {"answers": {"cp_1": 0}})  # throwaway
    clear_game_state(col, "quiz")  # must not touch the real resume point
    set_demo_state(col, {"enabled": False})

    state = get_game_state(col)
    assert "fl1" not in state
    assert state["quiz"]["n"] == 2
    col.close()


# --------------------------------------------------------------------------- #
# Adding notes
# --------------------------------------------------------------------------- #


def test_add_note_from_payload_and_demo_gate() -> None:
    col = _empty_col()
    model = col.models.by_name("Basic")
    assert model is not None
    result = add_note_from_payload(
        col,
        {
            "notetype_id": model["id"],
            "deck_id": 1,
            "fields": ["front text", "back text"],
            "tags": "mcat::cars, extra",
        },
    )
    assert result["ok"] is True
    note = col.get_note(result["note_id"])
    assert note.fields[0] == "front text"
    assert set(note.tags) == {"mcat::cars", "extra"}

    result = add_note_from_payload(
        col, {"notetype_id": 999_999_999, "deck_id": 1, "fields": []}
    )
    assert result["ok"] is False

    set_demo_state(col, {"enabled": True})
    before = col.note_count()
    result = add_note_from_payload(
        col, {"notetype_id": model["id"], "deck_id": 1, "fields": ["x", "y"]}
    )
    assert result == {"ok": True, "demo": True, "note_id": 0}
    assert col.note_count() == before  # demo never writes
    col.close()


# --------------------------------------------------------------------------- #
# The per-boot endpoint token
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Seating a third-party deck onto the Circuit
# --------------------------------------------------------------------------- #


def test_map_untagged_notes_seats_confident_cards_only() -> None:
    col = _empty_col()
    # a clearly-placeable card, an ambiguous one, and an already-tagged one
    _add_note(
        col,
        "What does a competitive inhibitor do to Km?",
        "Km increases; Vmax unchanged (Michaelis-Menten).",
        None,
        deck="AnKing MCAT",
    )
    _add_note(col, "Define the term.", "It is important.", None, deck="AnKing MCAT")
    _add_note(col, "already seated", "x", "mcat::chem_phys::fluids")

    result = map_untagged_notes(col)
    assert result["ok"] is True
    assert result["tagged"] == 1
    assert result["skipped"] == 1  # the vague card is left unlisted

    # the enzymes card is now seated at its table
    seated = col.find_notes("tag:mcat::bio_biochem::enzymes")
    assert len(seated) == 1
    # re-running is idempotent: nothing left to confidently seat
    again = map_untagged_notes(col)
    assert again["tagged"] == 0
    col.close()


def test_map_untagged_notes_is_gated_in_demo_mode() -> None:
    col = _empty_col()
    _add_note(col, "Bernoulli's principle?", "faster flow, lower pressure", None)
    set_demo_state(col, {"enabled": True})
    result = map_untagged_notes(col)
    assert result.get("demo") is True
    assert result["tagged"] == 0
    set_demo_state(col, {"enabled": False})
    assert col.find_notes("tag:mcat::*") == []  # nothing was written
    col.close()


# --------------------------------------------------------------------------- #
# Premade content: the MCAT deck self-seeds (no import ever)
# --------------------------------------------------------------------------- #


def test_ensure_seed_deck_populates_every_outline_topic() -> None:
    from ante.outline import load_outline

    col = _empty_col()
    assert col.card_count() == 0

    added = ensure_seed_deck(col)
    assert added > 0
    # the collection is no longer empty and cards are seated on the Circuit
    assert col.card_count() == added
    assert len(col.find_cards('"tag:mcat::*"')) == added

    # every content topic in the AAMC outline ships with real cards
    outline = load_outline()
    for topic in outline.all_topics():
        assert col.find_cards(f'"tag:{topic}"'), f"no premade cards for {topic}"

    # the seeded deck exists and holds the cards (nothing to import)
    assert any(d.name == "MCAT" for d in col.decks.all_names_and_ids())
    col.close()


def test_ensure_seed_deck_is_idempotent_and_never_duplicates() -> None:
    col = _empty_col()
    added = ensure_seed_deck(col)
    assert added > 0
    assert int(col.get_config("ante_seed_version", 0)) == SEED_VERSION

    # a second call adds nothing (version already recorded)
    assert ensure_seed_deck(col) == 0
    assert col.card_count() == added

    # simulate shipping more premade content later: drop the recorded version so
    # the seeder runs again. Topics that still have cards must NOT be duplicated;
    # only a topic the student emptied gets refilled.
    amino = '"tag:mcat::bio_biochem::amino_acids"'
    pruned = len(col.find_cards(amino))
    for cid in list(col.find_cards(amino)):
        col.remove_notes([col.get_card(cid).nid])
    col.set_config("ante_seed_version", SEED_VERSION - 1)

    refilled = ensure_seed_deck(col)
    assert refilled == pruned  # only the emptied topic was refilled
    assert col.card_count() == added  # no duplicates for the intact topics
    assert len(col.find_cards(amino)) == pruned
    col.close()


def test_dashboard_body_embeds_token_but_public_shell_does_not() -> None:
    assert ANTE_TOKEN
    assert ANTE_TOKEN in dashboard_body()
    # the raw /_anki/ante page is fetchable by any local process, so it must
    # not leak the token
    assert ANTE_TOKEN not in dashboard_html()


# --------------------------------------------------------------------------- #
# The live table (Realtime): gating + session config honesty levers
# --------------------------------------------------------------------------- #


def test_realtime_mint_gating(monkeypatch) -> None:
    from aqt.ante import build_viva_payload, mint_realtime_secret

    col = _empty_col()

    # no key -> feature off, payload says so, mint refuses (never network)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert build_viva_payload(col)["realtime"] is False
    assert mint_realtime_secret(col) == {"ok": False, "reason": "no key"}

    # key present but no active examination -> still no mint
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    payload = build_viva_payload(col)
    assert payload["realtime"] is True
    assert "[LEDGER" in payload["rt_silence_cue"]
    assert mint_realtime_secret(col) == {
        "ok": False,
        "reason": "no active examination",
    }

    # the kill switch wins even with a key
    monkeypatch.setenv("ANTE_REALTIME_DISABLED", "1")
    assert build_viva_payload(col)["realtime"] is False
    assert mint_realtime_secret(col) == {"ok": False, "reason": "no key"}
    monkeypatch.delenv("ANTE_REALTIME_DISABLED")

    # demo mode is allowed to mint (the tour's Back Room grades + speaks for
    # real), but still refuses without an active examination
    set_demo_state(col, {"enabled": True})
    assert mint_realtime_secret(col) == {
        "ok": False,
        "reason": "no active examination",
    }
    set_demo_state(col, {"enabled": False})
    col.close()


def test_realtime_session_config_keeps_the_ledger_in_charge(monkeypatch) -> None:
    from aqt.ante import build_realtime_session_config

    monkeypatch.setenv("ANTE_REALTIME_VOICE", "marin")
    session = {
        "topic_name": "Enzymes & kinetics",
        "question": "Explain how a competitive inhibitor changes Km and Vmax.",
        "opening": "The table is yours.",
        "rounds": [],
    }
    cfg = build_realtime_session_config(session)
    assert cfg["type"] == "realtime"
    turn = cfg["audio"]["input"]["turn_detection"]
    # the honesty lever: the model may never respond on its own
    assert turn["create_response"] is False
    assert turn["type"] == "semantic_vad"
    assert cfg["audio"]["output"]["voice"] == "marin"
    assert session["question"] in cfg["instructions"]
    assert "never grade" in cfg["instructions"].lower()


def test_realtime_cues_ride_the_viva_payload(monkeypatch) -> None:
    from aqt.ante import build_viva_payload
    from aqt.ante_studio import answer_viva, start_viva

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    col = _empty_col()
    ensure_seed_deck(col)
    from ante.tools.warm_backroom import default_topics

    topic = default_topics(1)[0]
    session = start_viva(col, topic)
    assert session and session["status"] == "open"

    # before the first turn: the opening cue (greet + question, verbatim)
    payload = build_viva_payload(col)
    cue = payload["active"]["rt_cue"]
    assert session["opening"] in cue and session["question"] in cue

    # after a graded turn: the steer cue carries the deterministic fallback
    answer_viva(col, "a vague first attempt")
    payload = build_viva_payload(col)
    active = payload["active"]
    if active:  # still open -> probing
        assert active["rt_cue"].startswith("[LEDGER")
        assert active["ask"] in active["rt_cue"]
    else:  # closed -> the verdict cue rides on `last`
        assert "[LEDGER — FINAL" in payload["last"]["rt_cue"]
    col.close()


def test_ante_request_allowed_requires_the_token() -> None:
    from aqt import mediasrv

    app = mediasrv.app
    orig_dev_mode = mediasrv.dev_mode
    mediasrv.dev_mode = ""
    try:
        with app.test_request_context("/_anki/anteData"):
            assert mediasrv._ante_request_allowed() is False
        with app.test_request_context("/_anki/anteData?antetoken=wrong"):
            assert mediasrv._ante_request_allowed() is False
        with app.test_request_context(f"/_anki/anteData?antetoken={ANTE_TOKEN}"):
            assert mediasrv._ante_request_allowed() is True
        with app.test_request_context(
            "/_anki/anteData", headers={"X-Ante-Token": ANTE_TOKEN}
        ):
            assert mediasrv._ante_request_allowed() is True
        # Bearer-key callers (e.g. tooling) stay allowed
        with app.test_request_context(
            "/_anki/anteData",
            headers={"Authorization": f"Bearer {mediasrv._APIKEY}"},
        ):
            assert mediasrv._ante_request_allowed() is True
        # dev mode keeps the plain-browser workflow working
        mediasrv.dev_mode = "1"
        with app.test_request_context("/_anki/anteData"):
            assert mediasrv._ante_request_allowed() is True
    finally:
        mediasrv.dev_mode = orig_dev_mode
