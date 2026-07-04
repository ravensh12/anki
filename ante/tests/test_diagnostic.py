# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for the Baseline Diagnostic (onboarding mini-test)."""

import time

from ante.diagnostic import (
    DEFAULT_MCQ_PER_SECTION,
    DEFAULT_OPEN_PER_SECTION,
    build_diagnostic,
    summarize_diagnostic,
)
from ante.outline import load_outline
from ante.performance_items import item_by_id


def test_form_samples_every_section_in_test_order():
    form = build_diagnostic()
    outline = load_outline()
    assert [s.id for s in form.sections] == [s.id for s in outline.sections]
    per_section = DEFAULT_MCQ_PER_SECTION + DEFAULT_OPEN_PER_SECTION
    for sec in form.sections:
        # rich sections get the full form; a thin bank still yields a real
        # sample (CARS may have fewer items than the full quota)
        assert 4 <= len(sec.items) <= per_section
    # ids unique across the whole form
    ids = form.item_ids
    assert len(ids) == len(set(ids))


def test_form_mixes_open_ended_into_each_section():
    form = build_diagnostic()
    for sec in form.sections:
        kinds = [it["type"] for it in sec.items]
        assert "open" in kinds, f"{sec.id} has no open-ended item"
        assert "mcq" in kinds
        # open items are spread through the section, not stacked at the end
        if kinds.count("open") >= 2:
            first_open = kinds.index("open")
            assert first_open < len(kinds) - 2


def test_form_payloads_carry_what_the_ui_needs():
    form = build_diagnostic()
    mcq = next(it for s in form.sections for it in s.items if it["type"] == "mcq")
    assert set(mcq) >= {"id", "type", "topic", "stem", "choices", "correct_index"}
    assert len(mcq["choices"]) == 4
    opn = next(it for s in form.sections for it in s.items if it["type"] == "open")
    assert set(opn) >= {"id", "type", "topic", "stem"}


def test_form_is_deterministic_for_a_seed():
    a = build_diagnostic(seed=11).item_ids
    b = build_diagnostic(seed=11).item_ids
    c = build_diagnostic(seed=12).item_ids
    assert a == b
    assert set(a) != set(c) or a != c  # different seed shuffles the sample


def _answer_form(form, right_sections: set[str], conf: float = 0.9):
    """Answer every item: correct in ``right_sections``, wrong elsewhere."""
    now = time.time()
    mcq_responses: dict[str, list] = {}
    open_responses: dict[str, list] = {}
    for sec in form.sections:
        right = sec.id in right_sections
        for it in sec.items:
            if it["type"] == "mcq":
                meta = item_by_id(it["id"])
                choice = meta.correct_index if right else (meta.correct_index + 1) % 4
                mcq_responses[it["id"]] = [[choice, now, conf, 6000]]
            else:
                open_responses[it["id"]] = [[1.0 if right else 0.0, now, conf, 20000]]
    return mcq_responses, open_responses


def test_summary_scores_sections_and_projects_a_banded_baseline():
    form = build_diagnostic()
    strong = {"bio_biochem", "chem_phys"}
    mcq, opn = _answer_form(form, right_sections=strong)
    s = summarize_diagnostic(form.item_ids, mcq, opn)
    assert s["available"]
    assert s["answered"] == form.total
    by_id = {row["id"]: row for row in s["sections"]}
    assert by_id["bio_biochem"]["accuracy"] == 1.0
    assert by_id["psych_soc"]["accuracy"] == 0.0
    # perfect section maps to the top of the scale; zero to the bottom
    assert by_id["bio_biochem"]["score"] == 132
    assert by_id["psych_soc"]["score"] == 118
    # every scored section carries an honest band
    for row in s["sections"]:
        assert row["scored"]
        lo, hi = row["band"]
        assert 0.0 <= lo <= row["accuracy"] <= hi <= 1.0
    assert s["baseline_total"] == sum(r["score"] for r in s["sections"])
    lo, hi = s["baseline_range"]
    assert lo <= s["baseline_total"] <= hi
    assert s["weakest_section"] in {"psych_soc", "cars"}
    assert s["strongest_section"] in strong


def test_summary_partial_diagnostic_stays_partial():
    form = build_diagnostic()
    mcq, opn = _answer_form(form, right_sections={"bio_biochem"})
    # keep answers only for one section
    keep = {it["id"] for it in form.sections[0].items}
    mcq = {k: v for k, v in mcq.items() if k in keep}
    opn = {k: v for k, v in opn.items() if k in keep}
    s = summarize_diagnostic(form.item_ids, mcq, opn)
    assert s["available"]
    assert s["baseline_total"] is None  # never extrapolate a total
    scored = [r for r in s["sections"] if r["scored"]]
    assert len(scored) == 1
    unscored = [r for r in s["sections"] if not r["scored"]]
    assert all("no reading" in r["reason"] for r in unscored)


def test_summary_untaken_is_honest():
    form = build_diagnostic()
    s = summarize_diagnostic(form.item_ids, {}, {})
    assert not s["available"]
    assert s["baseline_total"] is None
    assert s["headline"] == "Diagnostic not taken."


def test_summary_surfaces_weakest_topics_and_calibration():
    form = build_diagnostic()
    mcq, opn = _answer_form(form, right_sections=set(), conf=0.9)
    s = summarize_diagnostic(form.item_ids, mcq, opn)
    assert s["weakest_topics"]
    assert all(t["accuracy"] == 0.0 for t in s["weakest_topics"])
    # said "sure" on everything and missed everything -> day-one overconfidence
    assert s["calibration"] and s["calibration"]["bias"] > 0.5
