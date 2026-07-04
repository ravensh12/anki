# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from ante.fulllength import (
    SECTION_ORDER,
    build_full_length,
    fl_offsets,
    score_full_length,
)


def test_forms_cover_every_section_in_exam_order():
    for n in (1, 2):
        form = build_full_length(n)
        assert form["ok"] and form["test_no"] == n
        assert [s["id"] for s in form["sections"]] == SECTION_ORDER
        for s in form["sections"]:
            assert len(s["items"]) >= 5
            assert s["minutes"] >= 5
        assert form["total_questions"] == sum(len(s["items"]) for s in form["sections"])


def test_forms_are_disjoint_and_stable():
    f1a = build_full_length(1)
    f1b = build_full_length(1)
    f2 = build_full_length(2)
    ids = lambda f: {it["id"] for s in f["sections"] for it in s["items"]}
    assert ids(f1a) == ids(f1b)  # deterministic
    assert not (ids(f1a) & ids(f2))  # no shared questions


def test_scoring_bounds_and_blanks():
    form = build_full_length(1)
    # perfect run
    perfect = {
        it["id"]: it["correct_index"] for s in form["sections"] for it in s["items"]
    }
    top = score_full_length(perfect, 1)
    assert top["total"] == 528
    assert all(s["scaled"] == 132 for s in top["sections"])
    # all blank = floor (blanks count wrong, like the real thing)
    empty = score_full_length({}, 1)
    assert empty["total"] == 472
    # partial lands between
    some = dict(list(perfect.items())[: len(perfect) // 2])
    mid = score_full_length(some, 1)
    assert 472 < mid["total"] < 528


def test_offsets_anchor_to_exam():
    offs = fl_offsets(90)
    assert offs[2] == 80  # ~10 days out
    assert offs[1] == 58  # ~a month out
    assert offs[1] < offs[2]
    # short runway clamps to today (take the baseline now), never the past
    short = fl_offsets(12)
    assert short[1] == 0
    assert short[2] == 2
